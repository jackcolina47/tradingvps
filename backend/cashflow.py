"""Deposits and withdrawals ("cash flows"). In REAL mode they are detected from the broker (Bybit transaction log · MetaApi balance deals); in
paper they are entered from the panel. Every flow is stored in db.cash_flows and neutralised in the statistics: it moves day_start_equity (daily
PnL / daily loss limit), net_deposits (total PnL and return over contributed capital) and the deposit-adjusted peak used for the drawdown."""
import time
import logging
from datetime import datetime, timezone
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from models import now_iso

logger = logging.getLogger("cashflow")
POLL_SEC = 60
OVERLAP_MS = 10 * 60 * 1000
JUMP_PCT, JUMP_ABS = 0.005, 20.0
SOURCE_LABEL = {"paper": "cuenta paper", "bybit": "Bybit", "hfm": "HFM"}


def fields(acc: dict, equity: float) -> dict:
    """Summary fields derived from the account doc: contributed capital, total PnL and return excluding deposits/withdrawals."""
    nd = float(acc.get("net_deposits") or 0.0)
    capital = acc["initial_balance"] + nd
    total_pnl = equity - capital
    return {"net_deposits": round(nd, 2), "capital": round(capital, 2), "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_pnl / capital * 100, 2) if capital > 0 else 0, "peak_equity": round(acc["peak_equity"] + nd, 2)}


def jumped(prev: float, cur: float) -> bool:
    """Equity moved more than a position could explain in one tick → check the broker for a transfer before touching the metrics."""
    return prev is not None and abs(cur - prev) > max(JUMP_PCT * abs(prev), JUMP_ABS)


async def apply(engine, amount: float, source: str, note: str = "", ext_id: str = None, ts: str = None, currency: str = "USDT") -> dict | None:
    """Records one flow (idempotent on ext_id) and neutralises it in the account metrics. Returns None if it was already known."""
    amount = round(float(amount), 4)
    if not amount:
        return None
    doc = {"_id": ext_id or str(ObjectId()), "ts": ts or now_iso(), "amount": amount, "kind": "deposit" if amount > 0 else "withdrawal",
           "source": source, "note": (note or "")[:160], "mode": engine.mode_label, "detected_at": now_iso()}
    try:
        await engine.db.cash_flows.insert_one(doc)
    except DuplicateKeyError:
        return None
    inc = {"net_deposits": amount, "day_start_equity": amount}
    if source == "paper":
        inc["balance"] = amount
    await engine.db.account.update_one({"_id": "main"}, {"$inc": inc})
    label = "Depósito" if amount > 0 else "Retiro"
    where = SOURCE_LABEL.get(source, source)
    await engine.log("INFO", f"💰 {label} {'detectado en ' + where if source != 'paper' else 'en la ' + where} {amount:+,.2f} {currency}"
                             f"{' · ' + note if note else ''} · excluido del PnL, del límite diario y del drawdown")
    tg = engine.tg if hasattr(engine, "tg") else None
    text = (f"💰 <b>{label} {'detectado' if source != 'paper' else 'registrado'}</b> {amount:+,.2f} {currency} · {where}"
            f"{' · ' + note if note else ''}\nLas estadísticas lo excluyen: no cuenta como ganancia ni pérdida, ni para el límite diario. "
            "El tamaño de las próximas órdenes ya usa la nueva equidad.")
    if tg:
        await tg(text, "plan")
    else:
        from notify import notify
        await notify(text, "plan")
    return doc


async def manual(engine, amount: float, note: str = "", currency: str = "USDT") -> dict:
    if engine.live:
        raise ValueError("En modo REAL los depósitos y retiros se detectan automáticamente desde el bróker")
    if amount < 0:
        s = await engine.account_summary()
        if -amount > s["available"] + 1e-9:
            raise ValueError(f"El retiro supera el disponible de la cuenta paper ({s['available']:,.2f} {currency})")
    return await apply(engine, amount, "paper", note, currency=currency)


def _ms(iso: str) -> int:
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)


async def poll(engine, source: str, force: bool = False, currency: str = "USDT") -> bool:
    """REAL mode: asks the broker for transfers since the last check (every POLL_SEC, or now if `force`). Returns True if a new flow was applied."""
    if not (engine.live and engine.broker):
        return False
    now = time.time()
    if not force and now - getattr(engine, "_cash_checked", 0.0) < POLL_SEC:
        return False
    engine._cash_checked = now
    acc = await engine.db.account.find_one({"_id": "main"}, {"cash_checked_at": 1, "created_at": 1})
    # Baseline = when the current statistics started (last reset or switch to REAL, whichever is later): the starting capital was read from the
    # broker at that moment and already contains every earlier transfer, so older balance operations must never be counted again.
    live_since = getattr(engine.cfg, "live_since", None) if engine.cfg else None
    base_ms = max(_ms(x) for x in (acc["created_at"], live_since) if x)
    since_ms = max(_ms(acc["cash_checked_at"]) - OVERLAP_MS, base_ms) if acc.get("cash_checked_at") else base_ms
    try:
        flows = await engine.broker.cash_flows(since_ms)
    except Exception as e:
        logger.warning(f"cash flows {source}: {e}")
        return False
    applied = False
    for f in flows:
        if f.get("ts") and _ms(f["ts"]) < base_ms:
            continue
        if await apply(engine, f["amount"], source, f.get("note", ""), ext_id=f["id"], ts=f.get("ts"), currency=currency):
            applied = True
    if applied and hasattr(engine.broker, "invalidate"):
        engine.broker.invalidate()
    await engine.db.account.update_one({"_id": "main"}, {"$set": {"cash_checked_at": datetime.now(timezone.utc).isoformat()}})
    return applied


async def history(engine, limit: int = 30) -> list:
    rows = await engine.db.cash_flows.find({}).sort("ts", -1).limit(limit).to_list(limit)
    return [{"id": str(r.pop("_id")), **r} for r in rows]
