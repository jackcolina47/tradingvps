from datetime import datetime, timezone
from models import now_iso
from risk import FEE_RATE, SLIPPAGE, position_size, pnl, check_exit, update_trailing, trade_rules, move_in_r


def shadow_be(rules: dict, d: dict, price: float) -> bool:
    """Shadows don't take partials: breakeven is allowed once the partial level would have been reached (mirrors the real rule)."""
    return bool(rules["breakeven_enabled"] and rules["partial_tp_enabled"] and move_in_r(d["side"], d["entry_price"], d["initial_sl"], price) >= rules["partial_tp_r"])

MAX_AGE_H = 72
KINDS = ("RECHAZADA", "BAJA_CONFIANZA", "FILTRO_IA", "BLOQUEADA", "NOTICIAS", "FUNDING", "REGIMEN", "ROTACION", "RR_BAJO", "SPREAD", "ERROR", "NO_EJECUTADA")
AI_KINDS = ("RECHAZADA", "BAJA_CONFIANZA")
BUCKETS = [(0, 39, "<40%"), (40, 59, "40-59%"), (60, 79, "60-79%"), (80, 100, "80-100%")]


async def open_shadow(engine, signal_id: str, symbol: str, sig: dict, settings, kind: str, equity: float,
                      ai_confidence: int = 0, ai_risk: str = "-", risk_factor: float = 1.0, horizon: str = "short"):
    price = engine.market.tickers.get(symbol, {}).get("price") or sig["price"]
    long = sig["side"] == "long"
    entry = price * (1 + SLIPPAGE) if long else price * (1 - SLIPPAGE)
    sl, tp = entry - (sig["price"] - sig["sl"]), entry + (sig["tp"] - sig["price"])
    qty, margin = position_size(equity, equity, settings.risk_per_trade * risk_factor, entry, sl, settings.leverage, symbol)
    if qty <= 0:
        return None
    doc = {"signal_id": signal_id, "symbol": symbol, "side": sig["side"], "strategy": sig["strategy"], "timeframe": settings.timeframe,
           "kind": kind, "ai_confidence": ai_confidence, "ai_risk": ai_risk, "entry_price": entry, "qty": qty, "leverage": settings.leverage,
           "margin": margin, "sl": sl, "tp": tp, "initial_sl": sl, "atr": sig["atr"], "fees": entry * qty * FEE_RATE, "breakeven": False,
           "risk_factor": risk_factor, "mark_price": price, "unrealized_pnl": 0.0, "opened_at": now_iso(), "horizon": horizon}
    await engine.db.shadow_positions.insert_one(doc)
    return doc


async def monitor_shadows(engine, tickers: dict, rules):
    rules = trade_rules(rules)
    now = datetime.now(timezone.utc)
    async for d in engine.db.shadow_positions.find():
        t = tickers.get(d["symbol"])
        if not t:
            continue
        price, long = t["price"], d["side"] == "long"
        hit, reason = check_exit(d["side"], d["sl"], d["tp"], price, price)
        if hit is None and (now - datetime.fromisoformat(d["opened_at"])).total_seconds() / 3600 >= (MAX_AGE_H * 5 if d.get("horizon") == "long" else MAX_AGE_H):
            hit, reason = price, "TIMEOUT"
        if hit is not None:
            if reason == "SL" and d["breakeven"]:
                reason = "TRAILING"
            exit_price = price if reason == "TP" else (price * (1 - SLIPPAGE) if long else price * (1 + SLIPPAGE))
            exit_fee = exit_price * d["qty"] * FEE_RATE
            net = pnl(d["side"], d["entry_price"], exit_price, d["qty"]) - d["fees"] - exit_fee
            trade = {k: v for k, v in d.items() if k not in ("_id", "mark_price", "unrealized_pnl", "sl", "tp", "initial_sl", "breakeven")}
            trade.update(exit_price=exit_price, pnl=round(net, 4), pnl_pct=round(net / d["margin"] * 100, 2) if d["margin"] else 0,
                         fees=round(d["fees"] + exit_fee, 4), reason=reason, closed_at=now_iso())
            await engine.db.shadow_trades.insert_one(trade)
            await engine.db.shadow_positions.delete_one({"_id": d["_id"]})
            continue
        upd = {"mark_price": price, "unrealized_pnl": round(pnl(d["side"], d["entry_price"], price, d["qty"]), 4)}
        if rules["trailing_enabled"] or rules["breakeven_enabled"]:
            new_sl, be = update_trailing(d["side"], d["entry_price"], d["sl"], d["initial_sl"], d["atr"], price,
                                         rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"],
                                         be_enabled=shadow_be(rules, d, price), trail_enabled=rules["trailing_enabled"])
            if new_sl != d["sl"]:
                upd.update(sl=new_sl, breakeven=be)
        await engine.db.shadow_positions.update_one({"_id": d["_id"]}, {"$set": upd})


def _agg(rows: list) -> dict:
    n = len(rows)
    total = sum(r["pnl"] for r in rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    gw = sum(r["pnl"] for r in rows if r["pnl"] > 0)
    gl = sum(r["pnl"] for r in rows if r["pnl"] <= 0)
    return {"trades": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else 0, "pnl": round(total, 2),
            "avg_pnl": round(total / n, 2) if n else 0, "profit_factor": round(gw / abs(gl), 2) if gl else None,
            "missed": round(gw, 2), "avoided": round(-gl, 2)}


def _by(rows: list, field: str) -> list:
    keys = sorted({r[field] for r in rows})
    return sorted([{"key": k, **_agg([r for r in rows if r[field] == k])} for k in keys], key=lambda x: x["pnl"], reverse=True)


async def shadow_stats(db) -> dict:
    rows = await db.shadow_trades.find({}, {"_id": 0}).to_list(5000)
    ai_rows = [r for r in rows if r["kind"] in AI_KINDS]
    total = _agg(rows)
    return {
        "open": await db.shadow_positions.count_documents({}),
        "total": total, "filter_value": round(-total["pnl"], 2),
        "ai": {**_agg(ai_rows), "filter_value": round(-sum(r["pnl"] for r in ai_rows), 2)},
        "by_kind": [{"key": k, **_agg([r for r in rows if r["kind"] == k])} for k in KINDS if any(r["kind"] == k for r in rows)],
        "by_confidence": [{"key": label, **_agg([r for r in ai_rows if lo <= r["ai_confidence"] <= hi])} for lo, hi, label in BUCKETS],
        "by_strategy": _by(rows, "strategy"), "by_symbol": _by(rows, "symbol"),
        "by_horizon": _by([{**r, "horizon": r.get("horizon") or "short"} for r in rows], "horizon"),
    }


async def recent_rejections(db, limit: int = 10) -> list:
    out = []
    async for s in db.signals.find({"ai_decision": {"$in": ["RECHAZADA", "APROBADA"]}, "executed": False},
                                   {"symbol": 1, "side": 1, "strategy": 1, "ai_confidence": 1, "ai_reasoning": 1, "note": 1}).sort("_id", -1).limit(limit):
        out.append({"par": s["symbol"], "lado": s["side"], "estrategia": s["strategy"], "confianza": s.get("ai_confidence"),
                    "motivo": (s.get("ai_reasoning") or s.get("note") or "")[:220]})
    return out
