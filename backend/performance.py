from datetime import datetime, timedelta, timezone

from shadow import shadow_stats
from risk import FEE_RATE, MAKER_FEE


async def daily_pnl(db, tz_min: int = 0) -> list:
    """PnL per calendar day (day boundaries shifted to the viewer's timezone, `tz_min` = minutes east of UTC) with running cumulative. All history."""
    rows = await db.trades.find({}, {"closed_at": 1, "pnl": 1}).sort("closed_at", -1).limit(5000).to_list(5000)
    acc = {}
    for r in rows:
        try:
            day = (datetime.fromisoformat(r["closed_at"]) + timedelta(minutes=tz_min)).strftime("%Y-%m-%d")
        except (KeyError, TypeError, ValueError):
            continue
        a = acc.setdefault(day, {"day": day, "pnl": 0.0, "trades": 0, "wins": 0})
        pnl = float(r.get("pnl") or 0)
        a["pnl"] += pnl
        a["trades"] += 1
        a["wins"] += pnl > 0
    out = sorted(acc.values(), key=lambda x: x["day"])
    cum = 0.0
    for a in out:
        a["pnl"] = round(a["pnl"], 2)
        cum += a["pnl"]
        a["cum"] = round(cum, 2)
    return out


def _row(r: dict) -> dict:
    n, gl = r["n"], abs(r["gl"])
    return {
        "key": r["_id"], "trades": n, "wins": r["wins"],
        "win_rate": round(r["wins"] / n * 100, 1) if n else 0,
        "pnl": round(r["pnl"], 2), "fees": round(r["fees"], 2),
        "avg_pnl": round(r["pnl"] / n, 2) if n else 0,
        "profit_factor": round(r["gw"] / gl, 2) if gl else None,
    }


async def _group(db, field: str) -> list:
    pipe = [{"$addFields": {"horizon": {"$ifNull": ["$horizon", "short"]}, "entry_type": {"$ifNull": ["$entry_type", "market"]}}}, {"$group": {
        "_id": f"${field}", "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"}, "fees": {"$sum": "$fees"},
        "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
        "gw": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
        "gl": {"$sum": {"$cond": [{"$lte": ["$pnl", 0]}, "$pnl", 0]}},
    }}, {"$sort": {"pnl": -1}}]
    return [_row(r) for r in await db.trades.aggregate(pipe).to_list(50)]


async def maker_stats(db) -> dict:
    """Fill rate of the post-only entries and the fee saved vs. taker (closed trades + open positions)."""
    pipe = [{"$addFields": {"entry_type": {"$ifNull": ["$entry_type", "market"]}}},
            {"$group": {"_id": "$entry_type", "n": {"$sum": 1}, "notional": {"$sum": {"$multiply": ["$entry_price", "$qty"]}}}}]
    rows = {}
    for col in (db.trades, db.positions):
        for r in await col.aggregate(pipe).to_list(10):
            cur = rows.setdefault(r["_id"], {"n": 0, "notional": 0.0})
            cur["n"] += r["n"]
            cur["notional"] += r["notional"]
    maker, fb = rows.get("maker", {"n": 0, "notional": 0.0}), rows.get("market_fallback", {"n": 0, "notional": 0.0})
    attempts = maker["n"] + fb["n"]
    return {"maker_fills": maker["n"], "market_fallbacks": fb["n"], "market_direct": rows.get("market", {"n": 0})["n"],
            "fill_rate": round(maker["n"] / attempts * 100, 1) if attempts else None,
            "fee_saved": round(maker["notional"] * (FEE_RATE - MAKER_FEE), 2), "pending": await db.pending_entries.count_documents({})}


async def excursion_stats(db, since: str = None) -> dict:
    """Trade path analytics: how far trades went in favour (MFE) / against (MAE) in R, by exit reason; «near misses» = closed
    not at TP after reaching ≥ 70 % of the TP distance; partial take-profit usage."""
    match = {"mfe_r": {"$exists": True}}
    if since:
        match["closed_at"] = {"$gte": since}
    pipe = [{"$match": match},
            {"$group": {"_id": "$reason", "n": {"$sum": 1}, "mfe": {"$avg": "$mfe_r"}, "mae": {"$avg": "$mae_r"}, "mfe_tp": {"$avg": "$mfe_tp_pct"},
                        "near": {"$sum": {"$cond": [{"$and": [{"$ne": ["$reason", "TP"]}, {"$gte": ["$mfe_tp_pct", 70]}]}, 1, 0]}},
                        "pnl": {"$sum": "$pnl"}, "hold": {"$avg": "$hold_min"}}}, {"$sort": {"n": -1}}]
    rows = await db.trades.aggregate(pipe).to_list(20)
    by_reason = [{"key": r["_id"], "trades": r["n"], "avg_mfe_r": round(r["mfe"] or 0, 2), "avg_mae_r": round(r["mae"] or 0, 2),
                  "avg_mfe_tp_pct": round(r["mfe_tp"] or 0, 1), "near_miss": r["near"], "pnl": round(r["pnl"], 2), "avg_hold_min": round(r["hold"] or 0)} for r in rows]
    total = sum(r["trades"] for r in by_reason)
    near = sum(r["near_miss"] for r in by_reason)
    part = await db.trades.aggregate([{"$match": {**match, "partial_tp": True}}, {"$group": {"_id": None, "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"}, "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}}}}]).to_list(1)
    p = part[0] if part else {"n": 0, "pnl": 0.0, "wins": 0}
    return {"tracked": total, "by_reason": by_reason, "near_miss": near, "near_miss_pct": round(near / total * 100, 1) if total else 0,
            "avg_mfe_r": round(sum(r["avg_mfe_r"] * r["trades"] for r in by_reason) / total, 2) if total else 0,
            "avg_mae_r": round(sum(r["avg_mae_r"] * r["trades"] for r in by_reason) / total, 2) if total else 0,
            "partial": {"trades": p["n"], "pnl": round(p["pnl"], 2), "win_rate": round(p["wins"] / p["n"] * 100, 1) if p["n"] else 0},
            "open_partials": await db.positions.count_documents({"partial_done": True, "partial_gross": {"$ne": 0}})}


async def pair_ranking(db, symbols: list, days: int = 30, available: list = None) -> dict:
    """Per-pair PnL over the last `days` (closed trades), ranked best→worst; configured pairs without trades are listed with zeros,
    `available` (selectable but switched off) pairs come last so they can be re-enabled from the ranking."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    pipe = [{"$match": {"closed_at": {"$gte": since}}}, {"$group": {
        "_id": "$symbol", "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"}, "fees": {"$sum": "$fees"},
        "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
        "gw": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
        "gl": {"$sum": {"$cond": [{"$lte": ["$pnl", 0]}, "$pnl", 0]}},
    }}]
    rows = {r["_id"]: _row(r) for r in await db.trades.aggregate(pipe).to_list(100)}
    open_by = {r["_id"]: r["n"] async for r in db.positions.aggregate([{"$group": {"_id": "$symbol", "n": {"$sum": 1}}}])}
    for s in list(symbols) + list(available or []):
        rows.setdefault(s, {"key": s, "trades": 0, "wins": 0, "win_rate": 0, "pnl": 0.0, "fees": 0.0, "avg_pnl": 0, "profit_factor": None})
    out = sorted(rows.values(), key=lambda r: (r["key"] not in symbols and not r["trades"], -r["pnl"], -r["trades"]))
    for r in out:
        r["active"] = r["key"] in symbols
        r["open_positions"] = open_by.get(r["key"], 0)
    total = round(sum(r["pnl"] for r in out), 2)
    return {"days": days, "since": since, "rows": out, "total_pnl": total, "trades": sum(r["trades"] for r in out)}


async def performance(db, shadow=shadow_stats, with_excursion: bool = True, tz_min: int = 0) -> dict:
    decisions = {r["_id"]: r["n"] async for r in db.signals.aggregate([{"$group": {"_id": "$ai_decision", "n": {"$sum": 1}}}])}
    return {
        "by_strategy": await _group(db, "strategy"), "by_symbol": await _group(db, "symbol"),
        "by_side": await _group(db, "side"), "by_reason": await _group(db, "reason"),
        "by_horizon": await _group(db, "horizon"), "by_entry_type": await _group(db, "entry_type"),
        "daily": await daily_pnl(db, tz_min),
        "maker": await maker_stats(db), "excursion": await excursion_stats(db) if with_excursion else None,
        "signals": {"total": sum(decisions.values()), "executed": await db.signals.count_documents({"executed": True}),
                    "by_decision": decisions},
        "shadow": await shadow(db),
    }
