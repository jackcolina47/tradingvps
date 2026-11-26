"""Endpoints shared by both bots (session, password, Telegram/AI connection tests, trade detail/notes/candles). Registered on each protected router."""
import time
import csv
import io
from datetime import datetime, timezone, timedelta
from fastapi.responses import Response
from typing import Optional, List
from fastapi import HTTPException
from pydantic import BaseModel, Field
from bson import ObjectId

import notify
import cashflow
import chart
import ai_advisor
import position_review
import ai_rules
import trade_learning
import trade_guard
from fx.market import market_open as fx_market_open
from models import ManualOrder, TradeRules, LevelsUpdate, Position
import performance
from auth import hash_password, verify_password, create_token
from ai_advisor import ping as ai_ping, current as ai_current, friendly_error, key_hint, ORIGIN_LABELS
from deps import store
from models import Trade, Signal, AVAILABLE_SYMBOLS
from fx.symbols import SPECS



def log_phase(level: str, message: str) -> str:
    """Funnel phase a log line belongs to: 0 = technical filter, 1 = fast model, 2 = strong model."""
    low = message.lower()
    if level == "FILTER":
        return "1" if "fase 1" in low else "0"
    if level == "AI_EVAL" or "modelo potente" in low or "ia potente" in low:
        return "2"
    if "modelo rápido" in low or "filtro rápido" in low or "candidato" in low:
        return "1"
    if low.startswith("embudo") or "fuera del horario" in low or "evento rojo" in low:
        return "0"
    return ""


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

    @router.get("/logs/export")
    async def export_logs(hours: int = 24, fmt: str = "txt"):
        """Every log line of the last 24 / 48 / 72 h (funnel phases 0, 1 and 2 included) as a .txt or .csv file."""
        if hours not in (24, 48, 72) or fmt not in ("txt", "csv"):
            raise HTTPException(400, "Usa hours = 24, 48 o 72 y fmt = txt o csv")
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = [(d["ts"], d["level"], log_phase(d["level"], d["message"]), d["message"]) async for d in engine.db.logs.find({"ts": {"$gte": since}}).sort("_id", 1)]
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(["fecha_utc", "nivel", "fase", "mensaje"])
            w.writerows((ts[:19].replace("T", " "), lv, ph, msg.replace("\n", " ")) for ts, lv, ph, msg in rows)
            body, media = "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8"
        else:
            head = f"{label} · registro de las últimas {hours} h · {len(rows)} líneas · horas en UTC\n\n"
            body = head + "\n".join(f"{ts[:19].replace('T', ' ')} [{lv}]{' [FASE ' + ph + ']' if ph else ''} {msg}" for ts, lv, ph, msg in rows) + "\n"
            media = "text/plain; charset=utf-8"
        return Response(body, media_type=media, headers={"X-Log-Lines": str(len(rows)), "Access-Control-Expose-Headers": "X-Log-Lines"})

    @router.get("/ai-rules")
    async def ai_rules_list():
        return {"rules": await ai_rules.list_rules(engine.db), "max": None, "events": await ai_rules.events(engine.db)}

    async def _position(pos_id: str) -> Position:
        doc = await engine.db.positions.find_one({"_id": ObjectId(pos_id)}) if ObjectId.is_valid(pos_id) else None
        if not doc:
            raise HTTPException(404, "Posición no encontrada")
        return Position.from_mongo(doc)

    @router.put("/positions/{pos_id}/levels")
    async def position_levels(pos_id: str, body: LevelsUpdate):
        """Moves the SL and/or TP of an open position (dragged on its chart). In REAL the broker is modified first."""
        p = await _position(pos_id)
        if p.exit_mode == "cross":
            raise HTTPException(400, "Las posiciones del test de cruce EMA no tienen SL/TP editables")
        price = (engine.market.tickers.get(p.symbol) or {}).get("price") or p.mark_price
        d = 1 if p.side == "long" else -1
        sl, tp = body.sl or p.sl, body.tp or p.tp
        if (price - sl) * d <= 0 or (tp - price) * d <= 0:
            raise HTTPException(400, f"Niveles inválidos para el precio actual ({price:.6g}): el SL debe quedar {'por debajo' if d > 0 else 'por encima'} y el TP al otro lado")
        if p.live:
            try:
                if getattr(engine, "profile", None):
                    await engine.broker.set_stop(p.symbol, p.order_id, sl, tp)
                else:
                    await engine.broker.set_stop(p.symbol, sl, tp)
            except Exception as e:
                raise HTTPException(424, f"El bróker rechazó el cambio: {str(e)[:160]}")
        be = (sl - p.entry_price) * d >= 0
        await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": {"sl": sl, "tp": tp, "breakeven": be}})
        await engine.log("ORDER", f"{p.symbol}: niveles movidos a mano{' (REAL)' if p.live else ''} · SL {p.sl:.6g} → {sl:.6g} · TP {p.tp:.6g} → {tp:.6g}")
        return {**p.model_dump(), "sl": sl, "tp": tp, "breakeven": be}

    @router.put("/positions/{pos_id}/rules")
    async def position_rules(pos_id: str, body: TradeRules):
        """Replaces the own exit management of an open position (fields left empty follow the bot settings; no ai_review_minutes = no own AI review)."""
        p = await _position(pos_id)
        own = {"rules": body.rules() or None, "ai_review_minutes": body.ai_review_minutes}
        await engine.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": own})
        r = own["rules"] or {}
        on = [n for k, n in (("breakeven_enabled", "breakeven"), ("trailing_enabled", "trailing"), ("partial_tp_enabled", "parcial")) if r.get(k)]
        await engine.log("ORDER", f"{p.symbol}: gestión de la posición editada · activo: {', '.join(on) or 'nada'}"
                                  + (f" · revisión IA cada {body.ai_review_minutes} min" if body.ai_review_minutes else " · sin revisión IA propia"))
        return {**p.model_dump(), **own}

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
        own = {"rules": body.rules() or None, "ai_review_minutes": body.ai_review_minutes}
        if any(own.values()):
            await engine.db.positions.update_one({"_id": ObjectId(pos.id)}, {"$set": own})
            r = own["rules"] or {}
            txt = [f"breakeven {'a +' + format(r['be_start_r'], 'g') + 'R' if r.get('breakeven_enabled') else 'OFF'}" if "breakeven_enabled" in r else "",
                   f"trailing {'desde +' + format(r.get('trail_start_r', 0), 'g') + 'R a ' + format(r.get('trail_atr_mult', 0), 'g') + ' ATR' if r.get('trailing_enabled') else 'OFF'}" if "trailing_enabled" in r else "",
                   f"parcial {str(r.get('partial_tp_pct')) + '% a +' + format(r.get('partial_tp_r', 0), 'g') + 'R' if r.get('partial_tp_enabled') else 'OFF'}" if "partial_tp_enabled" in r else "",
                   f"revisión IA cada {body.ai_review_minutes} min" if body.ai_review_minutes else ""]
            await engine.log("ORDER", f"{body.symbol}: gestión propia de la orden manual · {' · '.join(t for t in txt if t)}")
        return {**pos.model_dump(), **own}

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
