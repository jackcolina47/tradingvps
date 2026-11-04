"""Endpoints shared by both bots (session, password, Telegram/AI connection tests, trade detail/notes/candles). Registered on each protected router."""
import time
from datetime import datetime
from typing import Optional, List
from fastapi import HTTPException
from pydantic import BaseModel, Field
from bson import ObjectId

import notify
import cashflow
import chart
from auth import hash_password, verify_password, create_token
from ai_advisor import ping as ai_ping, current as ai_current, friendly_error, key_hint, ORIGIN_LABELS
from deps import store
from models import Trade, Signal

TRADE_TFS = [("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)]
CHART_PAD = 25


class TradeNote(BaseModel):
    note: str = Field(default="", max_length=2000)
    tags: List[str] = Field(default_factory=list)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=8, max_length=200)


class ChatsRequest(BaseModel):
    token: str = ""


class AiTestRequest(BaseModel):
    ai_source: Optional[str] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = Field(default=None, max_length=80)
    gemini_model: Optional[str] = Field(default=None, max_length=80)
    gemini_api_key: Optional[str] = Field(default=None, max_length=200)
    emergent_llm_key: Optional[str] = Field(default=None, max_length=200)


class CashFlowRequest(BaseModel):
    amount: float = Field(ge=-10_000_000, le=10_000_000)
    note: str = Field(default="", max_length=120)


def add_shared_routes(router, engine, label: str, currency: str = "USDT", before_unit: str = "ms"):
    async def _trade(trade_id: str) -> Trade:
        doc = await engine.db.trades.find_one({"_id": ObjectId(trade_id)}) if ObjectId.is_valid(trade_id) else None
        if not doc:
            raise HTTPException(404, "Operación no encontrada")
        return Trade.from_mongo(doc)

    @router.get("/auth/me")
    async def auth_me():
        return {"ok": True}

    @router.get("/trades/{trade_id}/detail")
    async def trade_detail(trade_id: str):
        trade = (await _trade(trade_id)).model_dump()
        signal = None
        sid = trade.get("signal_id")
        if sid and ObjectId.is_valid(sid):
            sdoc = await engine.db.signals.find_one({"_id": ObjectId(sid)})
            if sdoc:
                signal = Signal.from_mongo(sdoc).model_dump()
        return {"trade": trade, "signal": signal, "currency": currency}

    @router.put("/trades/{trade_id}/note")
    async def trade_note(trade_id: str, body: TradeNote):
        await _trade(trade_id)
        tags = list(dict.fromkeys(t.strip()[:30] for t in body.tags if t.strip()))[:10]
        note = body.note.strip()
        await engine.db.trades.update_one({"_id": ObjectId(trade_id)}, {"$set": {"note": note, "tags": tags}})
        return {"ok": True, "id": trade_id, "note": note, "tags": tags}

    @router.get("/trades/{trade_id}/candles")
    async def trade_candles(trade_id: str):
        """Candles around the trade (timeframe picked so the hold spans ~≤ 90 candles) plus entry/exit/SL/TP levels for the mini chart."""
        t = await _trade(trade_id)
        o = datetime.fromisoformat(t.opened_at).timestamp()
        c = datetime.fromisoformat(t.closed_at).timestamp()
        dur = max(c - o, 60)
        start = next((i for i, (_, s) in enumerate(TRADE_TFS) if dur / s <= 90), len(TRADE_TFS) - 1)
        candles, tf, step = [], None, None
        for tf, step in TRADE_TFS[start:]:  # widen the timeframe when the window has no candles (weekend / market closed)
            n = min(int(dur / step) + 2 * CHART_PAD, 600)
            end = min(int(c) + CHART_PAD * step, int(time.time()) + step)
            try:
                rows = await engine.market.fetch_before(t.symbol, tf, end * 1000 if before_unit == "ms" else end, n)
            except Exception as e:
                raise HTTPException(502, f"No se pudieron cargar las velas: {e}")
            lo = int(o) - CHART_PAD * step
            candles = [{"time": int(float(r[0])) // 1000, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows or []]
            candles = [x for x in candles if x["time"] >= lo]
            if candles:
                break
        snap = lambda ts: int(ts) - int(ts) % step  # noqa: E731
        return {"tf": tf, "step": step, "digits": chart.price_digits(t.symbol, candles[-1]["close"] if candles else t.entry_price), "candles": candles,
                "opened": snap(o), "closed": snap(c), "side": t.side, "entry": t.entry_price, "exit": t.exit_price, "sl": t.sl, "initial_sl": t.initial_sl, "tp": t.tp}

    @router.get("/account/cashflows")
    async def cashflows():
        acc = await engine.account_summary()
        return {"net_deposits": acc["net_deposits"], "capital": acc["capital"], "total_pnl": acc["total_pnl"], "mode": acc["mode"],
                "currency": currency, "items": await cashflow.history(engine)}

    @router.post("/account/cashflow")
    async def add_cashflow(body: CashFlowRequest):
        if abs(body.amount) < 0.01:
            raise HTTPException(422, "Indica un importe distinto de cero")
        try:
            flow = await cashflow.manual(engine, body.amount, body.note.strip(), currency)
        except ValueError as e:
            raise HTTPException(409 if engine.live else 422, str(e))
        flow = {"id": str(flow.pop("_id")), **flow}
        return {"flow": flow, "account": await engine.account_summary()}

    @router.post("/auth/password")
    async def change_password(body: PasswordChange):
        if not verify_password(body.current, store.cfg.password_hash):
            raise HTTPException(401, "La clave actual no es correcta")
        version = store.cfg.token_version + 1
        await store.update({"password_hash": hash_password(body.new), "token_version": version})
        await engine.log("INFO", "Clave de acceso cambiada · sesiones anteriores cerradas")
        return {"token": create_token(version)}

    @router.post("/telegram/chats")
    async def telegram_chats(body: ChatsRequest):
        token = body.token.strip() if body.token and not body.token.startswith("••") else store.cfg.telegram_bot_token
        if not token:
            raise HTTPException(400, "Introduce el token del bot primero")
        try:
            return {"bot": await notify.get_me(token), "chats": await notify.get_chats(token)}
        except Exception as e:
            raise HTTPException(424, str(e)[:200])

    @router.post("/telegram/test")
    async def telegram_test():
        acc = await engine.account_summary()
        try:
            results = await notify.send_detailed(f"✅ <b>{label}</b> conectado.\nEquidad ${acc['equity']:,.2f} · {acc['open_positions']} posiciones abiertas · bot {'ACTIVO' if engine.running else 'DETENIDO'}")
        except Exception as e:
            raise HTTPException(424, str(e)[:200])
        return {"ok": True, "results": results, "sent": sum(1 for r in results if r["ok"]), "total": len(results)}

    @router.post("/ai/test")
    async def ai_test(body: AiTestRequest = None):
        try:
            rt = store.ai_runtime(body.model_dump() if body else None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if rt["source"] == "emergent" and not rt["api_key"]:
            raise HTTPException(400, "No hay clave universal de Emergent: introdúcela en el campo o define EMERGENT_LLM_KEY en el servidor")
        used = f"clave {key_hint(rt['api_key'])} · {ORIGIN_LABELS.get(rt.get('origin', ''), rt.get('origin', ''))}"
        try:
            return {"ok": True, "reply": await ai_ping(rt), **ai_current(rt), "saved": body is None or not body.model_dump(exclude_none=True)}
        except Exception as e:
            raise HTTPException(424, f"Error IA ({used}): {friendly_error(e)}")
