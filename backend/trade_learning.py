"""Post-mortem of closed trades (shared by every bot) + the crypto bot's automatic learning loop: every closed trade is analysed,
avoidable losses become learned rules and winners that a current rule would have blocked count against that rule."""
import time
import logging
from datetime import datetime, timezone
from bson import ObjectId

import ai_advisor
import ai_funnel
import ai_rules
import price_action
from models import Trade, Signal

logger = logging.getLogger("learning")
TRADE_TFS = [("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)]
CHART_PAD = 25
RULE_VERDICTS = ("EVITABLE", "MALA")


def no_tp_bot(engine, t: Trade = None) -> bool:
    """Pepperstone, the Bybit Labs and any EMA-cross test trade have no take profit: the stored `tp` is a virtual sizing reference."""
    return bool(getattr(getattr(engine, "profile", None), "ema_only", False) or getattr(engine, "close_beyond_exit_ema", False) or (t is not None and t.exit_mode == "cross"))


async def window(engine, t: Trade, pad: int = CHART_PAD, before_unit: str = "ms") -> dict:
    """Candles around a trade (timeframe picked so the hold spans ~≤ 90 candles); widens the timeframe when the window is empty (market closed)."""
    o = datetime.fromisoformat(t.opened_at).timestamp()
    c = datetime.fromisoformat(t.closed_at).timestamp()
    dur = max(c - o, 60)
    start = next((i for i, (_, s) in enumerate(TRADE_TFS) if dur / s <= 90), len(TRADE_TFS) - 1)
    candles, tf, step = [], None, None
    for tf, step in TRADE_TFS[start:]:
        n = min(int(dur / step) + 2 * pad, 600)
        end = min(int(c) + pad * step, int(time.time()) + step)
        rows = await engine.market.fetch_before(t.symbol, tf, end * 1000 if before_unit == "ms" else end, n)
        lo = int(o) - pad * step
        candles = [{"time": int(float(r[0])) // 1000, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows or []]
        candles = [x for x in candles if x["time"] >= lo]
        if candles:
            break
    snap = lambda ts: int(ts) - int(ts) % step  # noqa: E731
    return {"tf": tf, "step": step, "candles": candles, "opened": snap(o), "closed": snap(c)}


async def _signal(engine, t: Trade) -> dict | None:
    if t.signal_id and ObjectId.is_valid(t.signal_id):
        sdoc = await engine.db.signals.find_one({"_id": ObjectId(t.signal_id)})
        return Signal.from_mongo(sdoc).model_dump() if sdoc else None
    return None


async def review(engine, t: Trade, currency: str, before_unit: str = "ms", known_rules: list = None) -> dict:
    """AI post-mortem of a closed trade, stored on the trade."""
    signal = await _signal(engine, t)
    w = await window(engine, t, 60, before_unit)
    before = [x for x in w["candles"] if x["time"] < w["opened"]]
    during = [x for x in w["candles"] if w["opened"] <= x["time"] <= w["closed"]]
    no_tp = no_tp_bot(engine, t)
    if no_tp and signal:
        signal = {k: v for k, v in signal.items() if k != "tp"}
    pa = price_action.facts(before, t.side, t.entry_price, t.atr or 0, t.initial_sl or t.sl, None if no_tp else t.tp)
    out = await ai_advisor.analyze_trade(t.model_dump(), signal, pa, before[-12:], during, w["tf"], currency, known_rules, no_tp)
    await engine.db.trades.update_one({"_id": ObjectId(t.id)}, {"$set": {"ai_review": out}})
    return out


FX_MARKET = "forex y metales (CFD con lotes y pips)"


async def learn(engine, trade: Trade, currency: str = "USDT", before_unit: str = "ms"):
    """Runs after every closed trade of a rules-only bot (never raises)."""
    try:
        if trade.pnl >= 0:  # winners are not analysed (saves the expensive call): only the cheap check of rules that would have blocked them
            if trade.pnl > 0:
                await _check_winner(engine, trade, currency)
            return
        rules = await ai_rules.list_rules(engine.db)
        out = await review(engine, trade, currency, before_unit, rules)
        await engine.log("AI_EVAL", f"{trade.symbol}: operación cerrada analizada · {out['verdict']} · {out['verdict_reason']}")
        src = {"trade_id": trade.id, "symbol": trade.symbol, "pnl": trade.pnl}
        for a in out.get("rule_actions") or []:
            n, action = a.get("n"), str(a.get("action", "")).upper()
            if not isinstance(n, int) or not 1 <= n <= len(rules) or action not in ai_rules.ACTIONS:
                continue
            line = await ai_rules.apply_action(engine.db, rules[n - 1], action, str(a.get("text") or ""), str(a.get("reason") or ""), src)
            if line:
                await engine.log("AI_EVAL", f"{line} (análisis de {trade.symbol} {trade.pnl:+.2f} {currency})")
                await engine.tg(f"📚 <b>{line[:350]}</b>\nTras analizar {trade.side.upper()} {trade.symbol} ({trade.pnl:+.2f} {currency})", "ai")
        if trade.pnl < 0 and out["verdict"] in RULE_VERDICTS and len(out["lesson"]) >= 5:
            sc = out.get("lesson_scope") or {}
            rule = await ai_rules.auto_add(engine.db, out["lesson"], trade.id, out["verdict"], symbol=trade.symbol if sc.get("symbol") else "",
                                           side=trade.side if sc.get("side") else "", strategy=trade.strategy if sc.get("strategy") else "")
            if rule:
                await ai_rules.event(engine.db, "NUEVA", rule["text"], out["verdict_reason"], src)
                await engine.log("AI_EVAL", f"Regla aprendida de {trade.symbol} ({trade.pnl:+.2f} {currency}): «{rule['text']}»")
                await engine.tg(f"📚 <b>Regla aprendida</b> · {trade.side.upper()} {trade.symbol} ({trade.pnl:+.2f} {currency} · {out['verdict']})\n«{rule['text']}»", "ai")
        await _weekly_cleanup(engine)
    except Exception as e:
        logger.exception("learn")
        await engine.log("ERROR", f"{trade.symbol}: no se pudo analizar la operación cerrada · {ai_advisor.friendly_error(e)}")


async def _check_winner(engine, trade: Trade, currency: str = "USDT"):
    """A winner that a currently active rule would have blocked is a miss for that rule (auto-off after MAX_MISSES)."""
    active = await ai_rules.active(engine.db, trade.symbol, trade.side, trade.strategy)
    signal = await _signal(engine, trade)
    if not active or not signal:
        return
    ctx = signal.get("context") or {}
    sig = {"side": signal["side"], "strategy": signal["strategy"], "price": signal["price"], "sl": signal["sl"], "tp": signal["tp"],
           "score": signal["score"], "reasons": signal["reasons"], "indicators": signal["indicators"]}
    res = await ai_funnel.rules_check(trade.symbol, signal["timeframe"], sig, ctx, active, **({} if currency == "USDT" else {"market": FX_MARKET}))
    if res["violates"]:
        r = await ai_rules.miss(engine.db, res["rule"]["id"])
        if not r:
            return
        off = " · regla APAGADA automáticamente" if r["auto_off"] else ""
        await engine.log("AI_EVAL", f"{trade.symbol}: operación ganadora ({trade.pnl:+.2f} {currency}) que la regla «{r['text']}» habría bloqueado · {r['misses']} fallos (mín. {ai_rules.MAX_MISSES}) · balance en sombras {r['shadow_pnl']:+.2f}{off}")
        if off:
            await ai_rules.event(engine.db, "DESACTIVAR", r["text"], f"habría bloqueado {r['misses']} operaciones ganadoras", {"trade_id": trade.id, "symbol": trade.symbol, "pnl": trade.pnl})
            await engine.tg(f"📚 <b>Regla apagada</b>: habría bloqueado {r['misses']} operaciones ganadoras\n«{r['text']}»", "ai")


CLEANUP_DAYS, CLEANUP_MIN_RULES = 7, 8


async def _weekly_cleanup(engine):
    """Once a week merges the rules the AI flags as clear DUPLICATES (same scope). Contradictions are left for the manual «Limpiar con IA»."""
    meta = await engine.db.ai_rule_meta.find_one({"_id": "cleanup"}) or {}
    if meta.get("at") and (datetime.now(timezone.utc) - datetime.fromisoformat(meta["at"])).days < CLEANUP_DAYS:
        return
    rules = await ai_rules.list_rules(engine.db)
    if len(rules) < CLEANUP_MIN_RULES:
        return
    await engine.db.ai_rule_meta.update_one({"_id": "cleanup"}, {"$set": {"at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    by_id, merged = {r["id"]: r for r in rules}, 0
    for g in await ai_advisor.cleanup_rules(rules):
        group = [by_id[r["id"]] for r in g["rules"]]
        if g["type"] != "DUPLICADAS" or not g["merged"] or len({(r["side"], r["strategy"]) for r in group}) > 1:
            continue
        await ai_rules.merge(engine.db, ai_rules.MergeIn(ids=[r["id"] for r in group], merged=g["merged"], reason=f"Limpieza semanal automática: {g['reason']}"))
        merged += len(group) - 1
    if merged:
        await engine.log("AI_EVAL", f"Limpieza semanal de reglas: {merged} regla(s) duplicada(s) fusionada(s)")
        await engine.tg(f"📚 <b>Limpieza semanal de reglas</b>: {merged} regla(s) duplicada(s) fusionada(s)", "ai")
