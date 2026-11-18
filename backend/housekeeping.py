"""Crypto-bot self-maintenance run from the monitor loop (no external cron): automatic pruning of pairs that lost money for 30 days
(warn by Telegram first, switch off 24 h later if still bad)."""
import asyncio
import time
import logging
from datetime import datetime, timezone, timedelta

import notify
import performance
from models import now_iso

logger = logging.getLogger("housekeeping")
DAY = 86400
_lock = asyncio.Lock()


def _short(s: str) -> str:
    return s[:-4] if s.endswith("USDT") else s


async def _meta(db, key: str) -> dict:
    return await db.meta.find_one({"_id": key}) or {"_id": key}


def _due(doc: dict, every: int) -> bool:
    last = doc.get("last_at")
    return not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() >= every


async def tick(engine, settings):
    """Called every monitor cycle; cheap unless something is due."""
    if _lock.locked() or time.time() - getattr(engine, "_hk_checked", 0.0) < 300:
        return
    engine._hk_checked = time.time()
    async with _lock:
        if settings.auto_prune_enabled and _due(await _meta(engine.db, "prune"), DAY):
            await prune(engine, settings)


async def prune(engine, settings) -> dict:
    """Daily: pairs with > min_trades closed trades in 30 days and PF < pf get a warning; 24 h later, if still bad, they are switched off."""
    rank = await performance.pair_ranking(engine.db, settings.symbols, 30)
    doc = await _meta(engine.db, "prune")
    warned = dict(doc.get("warned") or {})
    now = datetime.now(timezone.utc)
    bad, off, cleared = [], [], []
    for r in rank["rows"]:
        sym = r["key"]
        if not r["active"]:
            warned.pop(sym, None)
            continue
        pf = r["profit_factor"] if r["profit_factor"] is not None else (0.0 if r["trades"] and r["pnl"] <= 0 else None)
        is_bad = r["trades"] > settings.auto_prune_min_trades and pf is not None and pf < settings.auto_prune_pf
        if not is_bad:
            if sym in warned:
                cleared.append(sym)
                warned.pop(sym)
            continue
        if sym in warned and now - datetime.fromisoformat(warned[sym]) >= timedelta(hours=20):
            off.append({**r, "pf": pf})
            warned.pop(sym)
        elif sym not in warned:
            warned[sym] = now.isoformat()
            bad.append({**r, "pf": pf})
    if off:
        keep = [s for s in settings.symbols if s not in {o["key"] for o in off}]
        if keep:
            await engine.update_settings({"symbols": keep}, "auto_prune", "Poda automática: apagado " + ", ".join(_short(o["key"]) for o in off))
            for o in off:
                await engine.log("RISK", f"{o['key']}: apagado por poda automática · 30 días: {o['trades']} ops, PF {o['pf']}, PnL {o['pnl']:+.2f} USDT")
        else:
            off = []
    await engine.db.meta.update_one({"_id": "prune"}, {"$set": {"last_at": now_iso(), "warned": warned, "last_off": [o["key"] for o in off], "last_warned": [b["key"] for b in bad]}}, upsert=True)
    if bad or off or cleared:
        await notify.notify(_prune_tg(bad, off, cleared, settings), "review", bot="bybit")
    return {"warned": [b["key"] for b in bad], "switched_off": [o["key"] for o in off], "cleared": cleared}


def _prune_tg(bad: list, off: list, cleared: list, settings) -> str:
    lines = ["✂️ <b>Poda automática de pares · Bybit</b>"]
    for b in bad:
        lines.append(f"⚠️ <b>{_short(b['key'])}</b> lleva 30 días perdiendo: {b['trades']} ops, PF {b['pf']}, PnL {b['pnl']:+.2f} USDT. "
                     f"Se apagará mañana si sigue con PF &lt; {settings.auto_prune_pf:g}. Para evitarlo, apaga la poda automática en Ajustes o acepta el cambio.")
    for o in off:
        lines.append(f"⛔ <b>{_short(o['key'])} APAGADO</b>: {o['trades']} ops, PF {o['pf']}, PnL {o['pnl']:+.2f} USDT en 30 días. Puedes reactivarlo desde «Pares 30d».")
    for c in cleared:
        lines.append(f"✅ {_short(c)} ha mejorado y ya no está en la lista de poda.")
    return "\n".join(lines)
