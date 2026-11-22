"""Maker (post-only limit) entries: an approved signal waits up to `maker_wait_bars` candles at the best price (fee 0.02 % instead of
0.055 %); if it is not filled the order is cancelled and the entry discarded (never a blind market order). Paper mode simulates the fill (price must trade through the limit)."""
import logging
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from models import now_iso
from market import TF_SECONDS
from notify import notify as tg

logger = logging.getLogger("maker")
OFFSET = 0.0002
RETRY_MAX_ATR = 0.15   # a rejected limit order is re-placed once at the new best maker price if it moved less than this
MAX_WAIT_SEC = 86400


def limit_price(side: str, price: float) -> float:
    return price * (1 - OFFSET) if side == "long" else price * (1 + OFFSET)


def wait_seconds(tf: str, bars: int) -> int:
    return min(TF_SECONDS.get(tf, 3600) * max(1, bars), MAX_WAIT_SEC)


def paper_filled(side: str, limit: float, price: float) -> bool:
    return price < limit if side == "long" else price > limit


async def queue_entry(engine, symbol: str, sig: dict, settings, signal_id: str, risk_factor: float, risk_reason: str, horizon: str, tf: str):
    """Places the post-only order (REAL) or registers the simulated one (PAPER). A rejected order is re-placed once at the new best
    maker price (within 0.15 ATR of the original); a second failure discards the entry. Never falls back to a market order."""
    price = engine.market.tickers.get(symbol, {}).get("price") or sig["price"]
    side, limit = sig["side"], limit_price(sig["side"], price)
    sl, tp = limit - (sig["price"] - sig["sl"]), limit + (sig["tp"] - sig["price"])
    wait = wait_seconds(tf, settings.maker_wait_bars)
    doc = {"symbol": symbol, "side": side, "strategy": sig["strategy"], "horizon": horizon, "limit_price": limit, "sl": sl, "tp": tp, "sig": sig,
           "signal_id": signal_id, "risk_factor": risk_factor, "risk_reason": risk_reason, "tf": tf, "wait_bars": settings.maker_wait_bars,
           "created_at": now_iso(), "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(), "live": engine.live,
           "order_id": None, "link_id": None, "qty": None}
    if engine.live:
        async def place(px: float):
            s, t = px - (sig["price"] - sig["sl"]), px + (sig["tp"] - sig["price"])
            size = await engine.live_size(symbol, px, s, settings.risk_per_trade * risk_factor, settings.leverage)
            if not size:
                return None
            r = await engine.broker.open_limit(symbol, side, size["qty"], px, s, t, settings.leverage)
            doc.update(limit_price=px, sl=s, tp=t, order_id=r["order_id"], link_id=r["link_id"], qty=r["qty"])
            return r
        try:
            if not await place(limit):
                return None   # below the minimum lot / margin: live_size already logged it
        except Exception as e:
            why = await _retry(engine, symbol, side, limit, sig, place, str(e)[:100])
            if why:
                engine._open_fail = why
                await engine.log("RISK", f"{symbol}: {why} · entrada descartada (sin orden a mercado)")
                return None
        limit, sl, tp = doc["limit_price"], doc["sl"], doc["tp"]
    await engine.db.pending_entries.insert_one(doc)
    await engine.db.signals.update_one({"_id": ObjectId(signal_id)}, {"$set": {"note": f"Orden límite maker en espera @ {limit:.6g} (hasta {settings.maker_wait_bars} velas)"}})
    await engine.log("ORDER", f"{symbol}: orden LÍMITE maker {side.upper()} @ {limit:.6g} · espera máx. {settings.maker_wait_bars} velas de {tf} · si no se llena se cancela")
    return doc


async def _retry(engine, symbol: str, side: str, limit: float, sig: dict, place, error: str):
    """One more post-only attempt at the new best maker price. Returns the discard reason, or None when the order is resting."""
    head = f"orden límite maker rechazada por Bybit ({error})"
    try:
        book = await engine.broker.client.best(symbol)
        px = book["bid"] if side == "long" else book["ask"]
        atr = sig.get("atr") or 0
        if not px or not atr or abs(px - limit) > RETRY_MAX_ATR * atr:
            return f"{head} y el precio se alejó más de {RETRY_MAX_ATR} ATR ({limit:.6g} → {px:.6g})"
        await engine.log("ORDER", f"{symbol}: {head} · reintento único al mejor precio maker {px:.6g}")
        return None if await place(px) else f"{head} · {engine._open_fail or 'tamaño insuficiente en el reintento'}"
    except Exception as e:
        return f"{head} y el reintento también falló ({str(e)[:100]})"


async def _finish(engine, d: dict, fill: dict = None, entry_type: str = "maker"):
    from shadow import open_shadow
    settings = await engine.get_settings()
    await engine.db.pending_entries.delete_one({"_id": d["_id"]})
    pos = await engine.open_position(d["symbol"], d["sig"], settings, d["signal_id"], d["risk_factor"], d["risk_reason"],
                                     horizon=d.get("horizon", "short"), entry_type=entry_type, fill=fill)
    if not pos:
        acc = await engine.account_summary()
        await open_shadow(engine, d["signal_id"], d["symbol"], d["sig"], settings, "NO_EJECUTADA", acc["equity"], risk_factor=d["risk_factor"], horizon=d.get("horizon", "short"))
    return pos


async def _discard(engine, d: dict, why: str):
    """Unfilled limit order: the entry is dropped, never chased at market."""
    await engine.db.pending_entries.delete_one({"_id": d["_id"]})
    await engine.db.signals.update_one({"_id": ObjectId(d["signal_id"])}, {"$set": {"note": f"No ejecutada: {why}"}})
    await engine.log("ORDER", f"{d['symbol']}: {why} · entrada descartada (sin orden a mercado)")


async def monitor_pending(engine, tickers: dict):
    now = datetime.now(timezone.utc)
    async for d in engine.db.pending_entries.find():
        try:
            if d.get("live"):
                await _monitor_live(engine, d, now)
                continue
            t = tickers.get(d["symbol"])
            if not t:
                continue
            if paper_filled(d["side"], d["limit_price"], t["price"]):
                await _finish(engine, d, {"entry": d["limit_price"]}, "maker")
            elif now >= datetime.fromisoformat(d["expires_at"]):
                await _discard(engine, d, f"orden límite no llenada en {d['wait_bars']} velas, cancelada")
        except Exception as e:
            logger.exception(f"pending entry {d.get('symbol')}: {e}")


async def _monitor_live(engine, d: dict, now):
    if not engine.broker:
        return
    o = await engine.broker.client.order(d["symbol"], d["order_id"])
    status = o.get("status")
    if status == "Filled":
        await _finish(engine, d, {"entry": o["avg_price"], "qty": o["exec_qty"], "fee": o["fee"], "order_id": d["order_id"], "link_id": d["link_id"]}, "maker")
        return
    if status in ("Cancelled", "Rejected", "Deactivated") and not o.get("exec_qty"):
        await _discard(engine, d, f"orden límite {status} en Bybit")
        return
    if now >= datetime.fromisoformat(d["expires_at"]):
        try:
            await engine.broker.cancel(d["symbol"], d["order_id"])
        except Exception as e:
            logger.warning(f"cancel {d['symbol']}: {e}")
        o = await engine.broker.client.order(d["symbol"], d["order_id"])
        if o.get("exec_qty"):
            await engine.log("ORDER", f"{d['symbol']}: orden límite llenada parcialmente ({o['exec_qty']}) · resto cancelado")
            await _finish(engine, d, {"entry": o["avg_price"], "qty": o["exec_qty"], "fee": o["fee"], "order_id": d["order_id"], "link_id": d["link_id"]}, "maker")
        else:
            await _discard(engine, d, f"orden límite no llenada en {d['wait_bars']} velas, cancelada")


async def cancel_all(engine, reason: str = "bot detenido", symbols: list = None):
    n = 0
    async for d in engine.db.pending_entries.find({"symbol": {"$in": symbols}} if symbols is not None else {}):
        if d.get("live") and engine.broker and d.get("order_id"):
            try:
                await engine.broker.cancel(d["symbol"], d["order_id"])
            except Exception as e:
                logger.warning(f"cancel {d['symbol']}: {e}")
        await engine.db.pending_entries.delete_one({"_id": d["_id"]})
        await engine.db.signals.update_one({"_id": ObjectId(d["signal_id"])}, {"$set": {"note": f"Orden límite cancelada: {reason}"}})
        n += 1
    if n:
        await engine.log("ORDER", f"{n} orden(es) límite pendiente(s) cancelada(s) · {reason}")
        await tg(f"🧾 {n} orden(es) límite pendiente(s) cancelada(s) · {reason}", "orders")
    return n


async def pending_view(db) -> list:
    out = []
    async for d in db.pending_entries.find({}, {"sig": 0}).sort("created_at", -1):
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out
