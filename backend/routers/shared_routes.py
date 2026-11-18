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
import price_action
import ai_advisor
import position_review
import ai_rules
import trade_learning
import trade_guard
from fx.market import market_open as fx_market_open
from models import ManualOrder
import performance
from auth import hash_password, verify_password, create_token
from ai_advisor import ping as ai_ping, current as ai_current, friendly_error, key_hint, ORIGIN_LABELS
from deps import store
from models import Trade, Signal, AVAILABLE_SYMBOLS
from fx.symbols import SPECS



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

    @router.get("/guard")
    async def guard_status():
        """Operating hours (New York) and the red-news calendar with the pairs each event blocks."""
        return trade_guard.status(engine.guard_kind, (await engine.get_settings()).symbols)

    @router.post("/guard/refresh")
    async def guard_refresh():
        if not await trade_guard.calendar.refresh():
            raise HTTPException(424, trade_guard.calendar.error)
        return trade_guard.status(engine.guard_kind, (await engine.get_settings()).symbols)

    @router.get("/auth/me")
    async def auth_me():
        return {"ok": True}

    @router.get("/ai-rules")
    async def ai_rules_list():
        return {"rules": await ai_rules.list_rules(engine.db), "max": None, "events": await ai_rules.events(engine.db)}

    @router.post("/manual-order")
    async def manual_order(body: ManualOrder):
        """Manual market order from the chart: SL/TP chosen by the user, size by fixed-fractional risk. Bypasses AI, rules and filters; risk limits still apply."""
        settings = await engine.get_settings()
        price = (engine.market.tickers.get(body.symbol) or {}).get("price")
        if not price:
            raise HTTPException(400, "Sin precio en vivo para ese par")
        if getattr(engine, "profile", None) and not fx_market_open():
            raise HTTPException(400, "Mercado cerrado")
        d = 1 if body.side == "long" else -1
        if (price - body.sl) * d <= 0 or (body.tp - price) * d <= 0:
            raise HTTPException(400, f"Niveles inválidos para el precio actual ({price:.6g}): el SL debe quedar {'por debajo' if d > 0 else 'por encima'} y el TP al otro lado")
        if (await engine.account_summary())["daily_paused"]:
            raise HTTPException(400, "Límite de pérdida diaria alcanzado")
        sig = {"side": body.side, "strategy": "manual", "price": price, "sl": body.sl, "tp": body.tp, "atr": body.atr, "score": 0,
               "reasons": ["Operación manual desde el gráfico"], "indicators": {"atr": body.atr}, "key_ts": int(time.time() * 1000), "tp_mode": "fixed"}
        await engine.log("ORDER", f"{body.symbol}: orden MANUAL {body.side.upper()} · riesgo {body.risk_pct:g}% · SL {body.sl:.6g} · TP {body.tp:.6g}")
        engine._open_fail = None
        res = await engine.open_position(body.symbol, sig, settings.model_copy(update={"risk_per_trade": body.risk_pct}), None)
        pos, reason = res if isinstance(res, tuple) else (res, getattr(engine, "_open_fail", None))
        if not pos:
            raise HTTPException(400, f"No se abrió: {reason or 'apertura cancelada'}")
        return pos.model_dump()

    @router.post("/ai-rules/cleanup")
    async def ai_rules_cleanup():
        rules = await ai_rules.list_rules(engine.db)
        if len(rules) < 2:
            return {"proposals": [], "checked": len(rules)}
        try:
            return {"proposals": await ai_advisor.cleanup_rules(rules), "checked": len(rules)}
        except Exception as e:
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")

    @router.post("/ai-rules/merge")
    async def ai_rules_merge(body: ai_rules.MergeIn):
        return await ai_rules.merge(engine.db, body)

    @router.post("/ai-rules")
    async def ai_rules_add(body: ai_rules.RuleIn):
        return await ai_rules.add(engine.db, body)

    @router.patch("/ai-rules/{rule_id}")
    async def ai_rules_patch(rule_id: str, body: ai_rules.RulePatch):
        return await ai_rules.patch(engine.db, rule_id, body)

    @router.delete("/ai-rules/{rule_id}")
    async def ai_rules_delete(rule_id: str):
        await ai_rules.remove(engine.db, rule_id)
        return {"ok": True}

    @router.get("/performance/pairs")
    async def performance_pairs(days: int = 30):
        """PnL ranking per pair over the last `days` (closed trades), including configured pairs without trades, to prune the list."""
        settings = await engine.get_settings()
        available = list(SPECS) if hasattr(engine, "profile") else AVAILABLE_SYMBOLS
        return await performance.pair_ranking(engine.db, settings.symbols, max(1, min(int(days), 365)), available)

    @router.post("/positions/ai-review")
    async def positions_ai_review():
        """Manual trigger of the periodic AI review of open positions (same logic as the scheduled one)."""
        settings = await engine.get_settings()
        if not settings.ai_enabled:
            raise HTTPException(400, "El filtro IA está desactivado en Ajustes")
        bot_name = engine.profile.bot_name if hasattr(engine, "profile") else "Bybit"
        try:
            reviews = await position_review.review_open_positions(engine, settings, currency, bot_name)
        except Exception as e:
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")
        return {"ok": True, "reviews": reviews, "count": len(reviews)}

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

    async def _trade_window(t: Trade, pad: int = trade_learning.CHART_PAD) -> dict:
        try:
            return await trade_learning.window(engine, t, pad, before_unit)
        except Exception as e:
            raise HTTPException(502, f"No se pudieron cargar las velas: {e}")

    @router.get("/trades/{trade_id}/candles")
    async def trade_candles(trade_id: str):
        t = await _trade(trade_id)
        w = await _trade_window(t)
        candles = w["candles"]
        return {**w, "digits": chart.price_digits(t.symbol, candles[-1]["close"] if candles else t.entry_price),
                "side": t.side, "entry": t.entry_price, "exit": t.exit_price, "sl": t.sl, "initial_sl": t.initial_sl, "tp": t.tp}

    @router.post("/trades/{trade_id}/analyze")
    async def trade_analyze(trade_id: str, force: bool = False):
        """AI post-mortem of a closed trade: why it opened, how the chart looked, what could have been done better. Cached on the trade."""
        t = await _trade(trade_id)
        if t.ai_review and not force:
            return {"review": t.ai_review, "cached": True}
        try:
            review = await trade_learning.review(engine, t, currency, before_unit)
        except Exception as e:
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")
        return {"review": review, "cached": False}

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
