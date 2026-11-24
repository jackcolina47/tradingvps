"""Database upkeep shared by every bot: indexes at startup, a user-triggered clean-up of old logs and equity points, cached trade totals and a bounded set of recent signal keys."""
import asyncio
import time
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("maintenance")
INDEXES = {"signals": ["key", "created_at", "ai_decision"], "trades": ["closed_at"], "logs": ["ts"], "equity": ["ts"], "shadow_trades": ["rule_id", "closed_at"]}
LOG_DAYS = 30          # log lines older than this are removed (the panel exports 72 h at most)
EQUITY_FULL_DAYS = 14  # the per-minute equity curve is kept this long; older points are thinned to one per hour
TOTALS_TTL = 60
BATCH = 1000


class RecentKeys:
    """Insertion-ordered set capped at `maxlen`: the oldest signal keys fall out instead of growing for the whole uptime."""

    def __init__(self, maxlen: int = 5000):
        self._d, self._max = {}, maxlen

    def add(self, key):
        self._d.pop(key, None)
        self._d[key] = None
        if len(self._d) > self._max:
            del self._d[next(iter(self._d))]

    def clear(self):
        self._d.clear()

    def __contains__(self, key):
        return key in self._d

    def __len__(self):
        return len(self._d)


async def trade_totals(engine) -> dict:
    """All-time closed-trade totals. Cached until the next close (engine._trade_totals = None) with a 60 s refresh, instead of scanning every trade on each monitor tick."""
    hit = getattr(engine, "_trade_totals", None)
    if hit and time.time() - hit[0] < TOTALS_TTL:
        return hit[1]
    agg = await engine.db.trades.aggregate([{"$group": {
        "_id": None, "n": {"$sum": 1}, "realized": {"$sum": "$pnl"},
        "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
        "gw": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
        "gl": {"$sum": {"$cond": [{"$lte": ["$pnl", 0]}, "$pnl", 0]}},
    }}]).to_list(1)
    st = agg[0] if agg else {"n": 0, "realized": 0, "wins": 0, "gw": 0, "gl": 0}
    engine._trade_totals = (time.time(), st)
    return st


async def claim_partial(db, pos_id) -> bool:
    """Atomically marks a PAPER position's partial close as taken. False when the position was closed (or already split) meanwhile, so the balance is never credited twice."""
    res = await db.positions.update_one({"_id": pos_id, "partial_done": {"$ne": True}}, {"$set": {"partial_done": True}})
    return res.modified_count == 1


async def ensure_indexes(db):
    for col, fields in INDEXES.items():
        for f in fields:
            await db[col].create_index(f)


async def prune_logs(db, days: int = LOG_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return (await db.logs.delete_many({"ts": {"$lt": cutoff}})).deleted_count


async def thin_equity(db, days: int = EQUITY_FULL_DAYS) -> int:
    """Keeps the first equity point of every hour for anything older than `days`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    hour, drop, removed = None, [], 0
    async for d in db.equity.find({"ts": {"$lt": cutoff}}, {"ts": 1}).sort("ts", 1):
        h = d["ts"][:13]
        if h == hour:
            drop.append(d["_id"])
            if len(drop) >= BATCH:
                removed += (await db.equity.delete_many({"_id": {"$in": drop}})).deleted_count
                drop = []
        hour = h
    if drop:
        removed += (await db.equity.delete_many({"_id": {"$in": drop}})).deleted_count
    return removed


async def ensure_all(engines):
    for eng in engines:
        try:
            await ensure_indexes(eng.db)
        except Exception:
            logger.exception("índices")


async def preview(engines) -> dict:
    """What a clean-up would remove, without touching anything."""
    logs_cut = (datetime.now(timezone.utc) - timedelta(days=LOG_DAYS)).isoformat()
    eq_cut = (datetime.now(timezone.utc) - timedelta(days=EQUITY_FULL_DAYS)).isoformat()
    out = {"log_days": LOG_DAYS, "equity_days": EQUITY_FULL_DAYS, "old_logs": 0, "old_equity": 0, "logs": 0, "equity": 0, "signals": 0, "shadow_trades": 0}
    for eng in engines:
        out["old_logs"] += await eng.db.logs.count_documents({"ts": {"$lt": logs_cut}})
        out["old_equity"] += await eng.db.equity.count_documents({"ts": {"$lt": eq_cut}})
        for col in ("logs", "equity", "signals", "shadow_trades"):
            out[col] += await eng.db[col].estimated_document_count()
    return out


async def prune(engines) -> dict:
    """Explicit user action: removes log lines older than LOG_DAYS and thins the equity curve older than EQUITY_FULL_DAYS to one point per hour."""
    total = {"logs": 0, "equity": 0}
    for eng in engines:
        total["logs"] += await prune_logs(eng.db)
        total["equity"] += await thin_equity(eng.db)
    logger.info(f"Limpieza manual: {total['logs']} líneas de registro y {total['equity']} puntos de equidad antiguos eliminados")
    return total
