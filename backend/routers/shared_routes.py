"""Endpoints shared by both bots (session, password, Telegram/AI connection tests). Registered on each protected router."""
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel, Field
from bson import ObjectId

import notify
import cashflow
from auth import hash_password, verify_password, create_token
from ai_advisor import ping as ai_ping, current as ai_current, friendly_error
from deps import store
from models import Trade, Signal


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


class CashFlowRequest(BaseModel):
    amount: float = Field(ge=-10_000_000, le=10_000_000)
    note: str = Field(default="", max_length=120)


def add_shared_routes(router, engine, label: str, currency: str = "USDT"):
    @router.get("/auth/me")
    async def auth_me():
        return {"ok": True}

    @router.get("/trades/{trade_id}/detail")
    async def trade_detail(trade_id: str):
        if not ObjectId.is_valid(trade_id):
            raise HTTPException(404, "Operación no encontrada")
        doc = await engine.db.trades.find_one({"_id": ObjectId(trade_id)})
        if not doc:
            raise HTTPException(404, "Operación no encontrada")
        trade = Trade.from_mongo(doc).model_dump()
        signal = None
        sid = trade.get("signal_id")
        if sid and ObjectId.is_valid(sid):
            sdoc = await engine.db.signals.find_one({"_id": ObjectId(sid)})
            if sdoc:
                signal = Signal.from_mongo(sdoc).model_dump()
        return {"trade": trade, "signal": signal, "currency": currency}

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
            await notify.send(f"✅ <b>{label}</b> conectado.\nEquidad ${acc['equity']:,.2f} · {acc['open_positions']} posiciones abiertas · bot {'ACTIVO' if engine.running else 'DETENIDO'}")
        except Exception as e:
            raise HTTPException(424, str(e)[:200])
        return {"ok": True}

    @router.post("/ai/test")
    async def ai_test(body: AiTestRequest = None):
        try:
            rt = store.ai_runtime(body.model_dump() if body else None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            return {"ok": True, "reply": await ai_ping(rt), **ai_current(rt), "saved": body is None or not body.model_dump(exclude_none=True)}
        except Exception as e:
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")
