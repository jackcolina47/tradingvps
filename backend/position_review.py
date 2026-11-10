"""Periodic AI review of OPEN positions (both engines). Every `ai_position_review_minutes` the AI looks at recent candles + price-action facts for each
open position and answers MANTENER / BREAKEVEN / CERRAR. In «auto» mode it acts when confidence ≥ threshold; in «notify» mode it only logs + Telegram."""
import time
import logging
from datetime import datetime, timezone
from bson import ObjectId

import ai_advisor
import price_action
from notify import notify as tg
from models import Position

logger = logging.getLogger("position_review")
CANDLES = 60


def _rows(rows: list) -> list:
    return [{"time": int(float(r[0])) // 1000, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows or []]


def due(engine, settings) -> bool:
    if not (getattr(settings, "ai_position_review_enabled", False) and settings.ai_enabled and engine.running):
        return False
    last = getattr(engine, "_ai_review_last", 0.0)
    return time.time() - last >= max(5, int(settings.ai_position_review_minutes)) * 60


async def _breakeven(engine, p: Position, price: float) -> bool:
    long = p.side == "long"
    better = (price > p.entry_price > p.sl) if long else (price < p.entry_price < p.sl)
    if not better:
        return False
    if p.live:
        try:
            if hasattr(engine, "profile"):
                await engine.broker.set_stop(p.symbol, p.order_id, p.entry_price, p.tp)
            else:
                await engine.broker.set_stop(p.symbol, p.entry_price)
        except Exception as e:
            await engine.log("ERROR", f"{p.symbol}: la IA quiso mover el stop a breakeven pero el broker falló · {str(e)[:120]}")
            return False
    await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": {"sl": p.entry_price, "breakeven": True}})
    return True


async def review_open_positions(engine, settings, currency: str, bot_name: str) -> list:
    """Runs one review cycle. Returns the list of reviews (also stored on each position as `ai_review`)."""
    engine._ai_review_last = time.time()
    docs = await engine.db.positions.find().to_list(200)
    if not docs:
        return []
    tf = getattr(settings, "timeframe", "1h")
    mode, min_conf = settings.ai_position_review_mode, int(settings.ai_position_review_min_confidence)
    crypto = currency == "USDT"
    btc15 = []
    if crypto and any(not d["symbol"].startswith("BTC") for d in docs):
        try:
            btc15 = await engine.market.fetch_klines("BTCUSDT", "15m", 6, cache_sec=60)
        except Exception as e:
            logger.warning(f"BTC context unavailable: {e}")
    out = []
    for doc in docs:
        p = Position.from_mongo(doc)
        try:
            candles = _rows(await engine.market.fetch_klines(p.symbol, tf, CANDLES + 1, cache_sec=60))
            if len(candles) < 15:
                continue
            price = candles[-1]["close"]
            pa = price_action.facts(candles[:-1], p.side, p.entry_price, p.atr or 0, p.sl, p.tp)
            btc_line = ai_advisor.btc_context_line(btc15, p.symbol) if crypto else ""
            review = await ai_advisor.review_position(p.model_dump(), candles[-CANDLES:], pa, tf, currency, price, "crypto" if crypto else "fx", btc_line)
        except Exception as e:
            await engine.log("ERROR", f"{p.symbol}: revisión IA de la posición fallida · {ai_advisor.friendly_error(e)}")
            continue
        acted = None
        strong = review["confidence"] >= min_conf
        if mode == "auto" and strong and review["action"] == "CERRAR":
            await engine.close_position(p.id, reason="IA_REVISION")
            acted = "cerrada"
        elif mode == "auto" and strong and review["action"] == "BREAKEVEN" and not p.breakeven:
            acted = "stop a breakeven" if await _breakeven(engine, p, price) else "breakeven no aplicable (precio no a favor)"
        review["acted"] = acted
        review["mode"] = mode
        if acted != "cerrada":
            await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": {"ai_review": review}})
        out.append({"symbol": p.symbol, "side": p.side, **review})
        icon = {"MANTENER": "🟢", "BREAKEVEN": "🟡", "CERRAR": "🔴"}[review["action"]]
        await engine.log("AI_REVIEW", f"{p.symbol} {p.side.upper()}: {icon} {review['action']} ({review['confidence']} %) · {review['reasoning'][:160]}" + (f" → {acted}" if acted else ""))
        if review["action"] != "MANTENER" or acted:
            await tg(f"{icon} <b>{bot_name}</b> · revisión IA de {p.symbol} {p.side.upper()}\n<b>{review['action']}</b> ({review['confidence']} %) · {review['r_now']:+.2f} R\n{review['reasoning']}"
                     + (f"\n→ Acción: <b>{acted}</b>" if acted else "\n→ Solo aviso (modo manual)" if mode != "auto" else f"\n→ Sin acción: confianza por debajo del mínimo ({min_conf} %)"), "ai")
    await engine.db.ai_reviews.update_one({"_id": "last"}, {"$set": {"at": datetime.now(timezone.utc).isoformat(), "reviews": out}}, upsert=True)
    return out
