"""Crypto-bot self-maintenance run from the monitor loop (no external cron): weekly re-optimisation of chosen pairs and the automatic
pruning of pairs that lost money for 30 days (warn by Telegram first, switch off 24 h later if still bad)."""
import asyncio
import time
import logging
from datetime import datetime, timezone, timedelta

import notify
import performance
from optimizer import optimize
from risk import trade_rules
from models import now_iso

logger = logging.getLogger("housekeeping")
WEEK = 7 * 86400
DAY = 86400
PROFITABLE = {"min_trades": 15, "min_pf": 1.1}
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
        if settings.reopt_enabled and settings.reopt_symbols and _due(await _meta(engine.db, "reopt"), WEEK):
            await reoptimize(engine, settings, "auto")
        if settings.auto_prune_enabled and _due(await _meta(engine.db, "prune"), DAY):
            await prune(engine, settings)


# ---------- weekly re-optimisation ----------
def _profitable(best: dict) -> bool:
    return bool(best and best["trades"] >= PROFITABLE["min_trades"] and best["return_pct"] > 0 and best["profit_factor"] >= PROFITABLE["min_pf"] and best["robust"] > 0)


async def reoptimize(engine, settings, source: str = "manual") -> dict:
    """Runs the SL/TP/ATR grid for `settings.reopt_symbols`; applies the best params when robust and reports pairs that turned profitable."""
    acc = await engine.db.account.find_one({"_id": "main"}, {"initial_balance": 1})
    results, applied, turned = [], {}, []
    for sym in settings.reopt_symbols:
        try:
            klines = await engine.market.fetch_klines(sym, settings.timeframe, 1000, cache_sec=300)
            res = await asyncio.to_thread(optimize, klines, settings.strategy, settings.risk_per_trade, settings.leverage, trade_rules(settings),
                                          acc["initial_balance"], sym, settings.signal_mode, settings.disabled_strategies)
        except Exception as e:
            logger.exception(f"reopt {sym}")
            await engine.log("ERROR", f"{sym}: re-optimización semanal fallida · {str(e)[:120]}")
            continue
        best, base = res.get("best"), res["baseline"]
        current = settings.symbol_params.get(sym)
        prof = _profitable(best)
        row = {"symbol": sym, "best": best, "baseline": base, "profitable": prof, "active": sym in settings.symbols, "previous": current,
               "period": [res["period_start"], res["period_end"]], "candles": res["candles"]}
        if best and best["robust"] > 0:
            new = {"sl_atr": float(best["sl_atr"]), "tp_atr": float(best["tp_atr"]), "atr_period": float(best["atr_period"])}
            if new != {k: float(v) for k, v in (current or {}).items()}:
                applied[sym] = new
            row["applied"] = new
        if prof and sym not in settings.symbols:
            turned.append(row)
        results.append(row)
    if applied:
        params = {**settings.symbol_params, **applied}
        await engine.update_settings({"symbol_params": params}, "reopt", "Re-optimización semanal: " + ", ".join(f"{_short(s)} SL {p['sl_atr']}/TP {p['tp_atr']}/ATR {p['atr_period']}" for s, p in applied.items()))
    doc = {"last_at": now_iso(), "source": source, "results": results}
    await engine.db.meta.update_one({"_id": "reopt"}, {"$set": doc}, upsert=True)
    await engine.log("INFO", f"Re-optimización {'semanal' if source == 'auto' else 'manual'}: " + (" · ".join(_line(r) for r in results) or "sin pares"))
    if results:
        await notify.notify(_tg(results, applied, turned), "review")
    return {"results": results, "applied": applied, "turned_profitable": [r["symbol"] for r in turned]}


def _line(r: dict) -> str:
    b = r["best"]
    if not b:
        return f"{_short(r['symbol'])}: sin combinación válida"
    return f"{_short(r['symbol'])} {b['return_pct']:+.2f} % · PF {b['profit_factor']} · {b['trades']} ops ({'rentable' if r['profitable'] else 'no rentable'})"


def _tg(results: list, applied: dict, turned: list) -> str:
    lines = ["🔁 <b>Re-optimización semanal · Bybit</b>"]
    for r in results:
        b = r["best"]
        state = "activo" if r["active"] else "apagado"
        if b:
            lines.append(f"• <b>{_short(r['symbol'])}</b> ({state}): mejor SL {b['sl_atr']} / TP {b['tp_atr']} / ATR {b['atr_period']} → {b['return_pct']:+.2f} %, PF {b['profit_factor']}, "
                         f"{b['trades']} ops, DD {b['max_drawdown']} % · base {r['baseline']['return_pct']:+.2f} %" + (" · <b>parámetros aplicados</b>" if r["symbol"] in applied else ""))
        else:
            lines.append(f"• <b>{_short(r['symbol'])}</b> ({state}): sin combinación con suficientes operaciones")
    for r in turned:
        lines.append(f"\n✅ <b>{_short(r['symbol'])} ha pasado a ser RENTABLE</b> con los parámetros nuevos (PF ≥ {PROFITABLE['min_pf']}, ≥ {PROFITABLE['min_trades']} ops, robusto). "
                     "Sigue apagado: actívalo desde la pestaña «Pares 30d» si quieres operarlo.")
    return "\n".join(lines)


# ---------- automatic pruning ----------
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
        await notify.notify(_prune_tg(bad, off, cleared, settings), "review")
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
