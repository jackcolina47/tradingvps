"""Shadow trades for HotForex: every signal that was NOT executed (AI rejection, low confidence, risk/session/news blocks, broker/AI errors)
is simulated like a paper position (lots, HFM spread, trailing, weekend flat, 72 h cap) so the value of each filter can be measured."""
from datetime import datetime, timezone
from models import now_iso
from fx import risk
from fx.risk import MIN_LOT
from fx.symbols import SPECS, pips
from fx.market import closing_soon, market_status
from shadow import _agg, _by, BUCKETS

MAX_AGE_H = 72
KINDS = ("RECHAZADA", "BAJA_CONFIANZA", "BLOQUEADA", "NOTICIAS", "SESION", "EXPOSICION", "ROLLOVER", "VOLATILIDAD", "ESTACIONAL", "REGIMEN_USD", "FOMC", "PRECIO_EXTENDIDO", "ERROR", "NO_EJECUTADA")
AI_KINDS = ("RECHAZADA", "BAJA_CONFIANZA")


def _costs(engine):
    from fx.broker import DEFAULT_COSTS
    return getattr(engine, "costs", None) or DEFAULT_COSTS


async def open_shadow(engine, signal_id: str, symbol: str, sig: dict, settings, kind: str, acc: dict,
                      ai_confidence: int = 0, ai_risk: str = "-", risk_factor: float = 1.0, note: str = ""):
    tickers = engine.market.tickers
    news_ctx = engine.news.context(symbol) if getattr(engine, "news", None) else None
    news_risk = ((news_ctx or {}).get("mercado") or {}).get("risk") or "SIN_DATO"
    entry = tickers.get(symbol, {}).get("price") or sig["price"]
    sl, tp = entry - (sig["price"] - sig["sl"]), entry + (sig["tp"] - sig["price"])
    lots, margin, real_risk = risk.position_size(acc["equity"], acc["available"], settings.risk_per_trade * risk_factor, entry, sl,
                                                 settings.leverage, symbol, tickers, settings.min_lot_max_risk)
    undersized = lots <= 0
    if undersized:  # keep the data point anyway: simulate the minimum lot and flag it
        lots = MIN_LOT
        margin = lots * SPECS[symbol]["contract"] * risk.base_usd(symbol, tickers, entry) / settings.leverage
        real_risk = risk.min_lot_risk_pct(acc["equity"], entry, sl, symbol, tickers)
    doc = {"signal_id": signal_id, "symbol": symbol, "side": sig["side"], "strategy": sig["strategy"], "timeframe": settings.timeframe, "horizon": "long" if sig["strategy"] == "fix_reversion" else "short",
           "kind": kind, "note": note[:200], "ai_confidence": ai_confidence, "ai_risk": ai_risk, "entry_price": entry, "qty": lots,
           "leverage": settings.leverage, "margin": margin, "risk_pct": real_risk, "undersized": undersized, "sl": sl, "tp": tp, "initial_sl": sl,
           "atr": sig["atr"], "fees": risk.spread_cost(symbol, lots, tickers, _costs(engine)), "breakeven": False, "risk_factor": risk_factor,
           "session": "+".join(x["id"] for x in market_status()["sessions"] if x["open"]) or "ninguna", "news_risk": news_risk, "live": engine.live,
           "mark_price": entry, "unrealized_pnl": 0.0, "opened_at": now_iso()}
    await engine.db.shadow_positions.insert_one(doc)
    return doc


async def monitor_shadows(engine, tickers: dict, settings, now: datetime):
    weekend = settings.close_before_weekend and closing_soon(30)
    rules = risk.trade_rules(settings)
    async for d in engine.db.shadow_positions.find():
        t = tickers.get(d["symbol"])
        if not t:
            continue
        price = t["price"]
        hit, reason = risk.check_exit(d["side"], d["sl"], d["tp"], price)
        if hit is None and (now - datetime.fromisoformat(d["opened_at"])).total_seconds() / 3600 >= MAX_AGE_H:
            hit, reason = price, "TIEMPO"
        if hit is None and weekend:
            hit, reason = price, "FIN_SEMANA"
        if hit is not None:
            if reason == "SL" and d["breakeven"]:
                reason = "TRAILING"
            exit_fee = risk.spread_cost(d["symbol"], d["qty"], tickers, _costs(engine))
            net = risk.pnl(d["symbol"], d["side"], d["entry_price"], hit, d["qty"], tickers) - d["fees"] - exit_fee
            move = (hit - d["entry_price"]) * (1 if d["side"] == "long" else -1)
            trade = {k: v for k, v in d.items() if k not in ("_id", "mark_price", "unrealized_pnl", "sl", "tp", "initial_sl", "breakeven")}
            trade.update(exit_price=hit, pnl=round(net, 4), pnl_pct=round(net / d["margin"] * 100, 2) if d["margin"] else 0,
                         pips=round(pips(d["symbol"], move - _costs(engine).spread(d["symbol"])), 1), fees=round(d["fees"] + exit_fee, 4),
                         reason=reason, closed_at=now_iso())
            await engine.db.shadow_trades.insert_one(trade)
            await engine.db.shadow_positions.delete_one({"_id": d["_id"]})
            continue
        upd = {"mark_price": price, "unrealized_pnl": round(risk.pnl(d["symbol"], d["side"], d["entry_price"], price, d["qty"], tickers), 4)}
        if rules["trailing_enabled"]:
            new_sl, be = risk.update_trailing(d["side"], d["entry_price"], d["sl"], d["initial_sl"], d["atr"], price, _costs(engine).spread(d["symbol"]),
                                              rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"])
            if new_sl != d["sl"]:
                upd.update(sl=new_sl, breakeven=be)
        await engine.db.shadow_positions.update_one({"_id": d["_id"]}, {"$set": upd})


async def shadow_stats(db) -> dict:
    rows = await db.shadow_trades.find({}, {"_id": 0}).to_list(5000)
    ai_rows = [r for r in rows if r["kind"] in AI_KINDS]
    total = _agg(rows)
    return {
        "open": await db.shadow_positions.count_documents({}),
        "total": {**total, "pips": round(sum(r.get("pips", 0) for r in rows), 1)}, "filter_value": round(-total["pnl"], 2),
        "ai": {**_agg(ai_rows), "filter_value": round(-sum(r["pnl"] for r in ai_rows), 2)},
        "by_kind": [{"key": k, **_agg([r for r in rows if r["kind"] == k])} for k in KINDS if any(r["kind"] == k for r in rows)],
        "by_confidence": [{"key": label, **_agg([r for r in ai_rows if lo <= r["ai_confidence"] <= hi])} for lo, hi, label in BUCKETS],
        "by_strategy": _by(rows, "strategy"), "by_symbol": _by(rows, "symbol"),
        "by_session": _by([r for r in rows if r.get("session")], "session"),
        "ai_by_news_risk": _by([r for r in ai_rows if r.get("news_risk")], "news_risk"),
        "undersized": sum(1 for r in rows if r.get("undersized")),
    }
