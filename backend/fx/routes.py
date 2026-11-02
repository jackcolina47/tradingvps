"""/api/fx/* and /api/pepper/* — FX bots (HotForex, Pepperstone): forex & metals, paper trading, MetaApi/MetaTrader account link. One router per engine (build_router).

This module keeps the bot/dashboard/account/positions/news/config endpoints; the rest is split by concern:
  routes_analysis.py  backtest, optimizer, walk-forward, AI advisor, growth plan, weekly summary, FX gates context
  routes_hfm.py       MetaApi/HFM account link & checks, paper ↔ REAL mode
"""
import time
import logging
from fastapi import HTTPException, Request, Query
from bson import ObjectId

from models import Position, Trade, Signal, LogEntry, ResetRequest, SIGNAL_MODES
from strategies import MODE_INFO
import chart
from performance import performance
from fx.shadow import shadow_stats as fx_shadow_stats
import evolution
from config import ConfigUpdate
from ai_advisor import current as ai_current
import notify
from deps import store as gstore, protected_router, ai_usage
from routers.shared_routes import add_shared_routes
from fx.models import FxSettingsUpdate, FxProfileRequest, FX_TIMEFRAMES, FX_STRATEGY_KEYS, FX_SLOW_KEYS
from fx.config import FxConfigUpdate
from fx.symbols import SPECS, AVAILABLE, CURRENCIES, CURRENCY_NAMES, pips
from fx.market import TF_SECONDS, market_status, SESSIONS
from fx.engine import FX_STRATEGY_INFO
from fx.profiles import PROFILES, detect_profile, profile_patch
from fx import review as fx_review
from fx import routes_analysis, routes_hfm, lab

logger = logging.getLogger(__name__)


def register(router, engine, store, news):
    SESSION_LIST = [{"id": s[0], "name": s[1]} for s in SESSIONS]

    def _status() -> dict:
        return {"running": engine.running, "scanning": engine.scanning, "provider": "yahoo", "last_scan": engine.last_scan,
                "next_scan_in": max(0, int(engine.next_scan_ts - time.time())) if engine.next_scan_ts else None,
                "mode": engine.mode_label, "env": engine.env, "account_kind": engine.account_kind(), "market": "Forex · Metales", "telegram": notify.is_on(), "ai": ai_current(), "ai_usage": ai_usage.snapshot(engine.profile.panel), "broker": engine.profile.broker, "bot_name": engine.profile.bot_name, "costs": {"label": engine.costs.label, "commission_lot_usd": engine.costs.commission_lot_usd, "spread_factor": engine.costs.spread_factor},
                "exchange_error": engine.market.error, "broker_error": engine.broker_error if engine.live else None, "broker_health": engine.broker_health(),
                "market_hours": market_status()}

    async def _equity_curve(limit: int = 1440) -> list:
        rows = await engine.db.equity.find({}, {"_id": 0}).sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        step = max(1, len(rows) // 300)
        return rows[::step] + (rows[-1:] if rows and (len(rows) - 1) % step else [])

    @router.get("/meta")
    async def meta():
        return {"symbols": AVAILABLE, "symbol_info": {s: {k: v for k, v in SPECS[s].items() if k in ("name", "group", "digits", "pip")} for s in AVAILABLE},
                "timeframes": FX_TIMEFRAMES, "strategies": FX_STRATEGY_KEYS, "strategy_keys": FX_STRATEGY_KEYS[1:], "long_horizon_keys": list(FX_SLOW_KEYS),
                "strategy_info": FX_STRATEGY_INFO, "signal_modes": SIGNAL_MODES, "mode_info": MODE_INFO,
                "profiles": PROFILES, "currencies": CURRENCIES, "currency_names": CURRENCY_NAMES, "sessions": SESSION_LIST}

    @router.post("/profile")
    async def set_profile(req: FxProfileRequest):
        settings = await engine.get_settings()
        p = PROFILES[req.level]
        before = PROFILES[settings.risk_profile]["name"] if detect_profile(settings) is not None else "Personalizado"
        extra = await lab.end_by_profile(engine, store, p["settings"])
        if extra:
            await engine.log("INFO", "Modo laboratorio finalizado al aplicar un perfil de riesgo · el perfil define sus ajustes y el resto (estrategia, trailing, filtros de mercado, noticias, revisión IA) vuelve a su valor anterior")
        s = await engine.update_settings({**profile_patch(req.level), **extra}, "profile", f"Perfil de riesgo aplicado: {PROFILES[req.level]['name']}")
        st = p["settings"]
        await engine.log("INFO", f"Perfil de riesgo: {before} → {p['name']} · {len(st['symbols'])} pares · {st['timeframe']} · riesgo {st['risk_per_trade']}% · 1:{st['leverage']}")
        await engine.tg(
            f"🎚 <b>Perfil de riesgo cambiado</b>: {before} → <b>{p['name']}</b>\n"
            f"• {len(st['symbols'])} pares · velas {st['timeframe']} · criterios {MODE_INFO[st['signal_mode']]['name'].lower()}\n"
            f"• Riesgo {st['risk_per_trade']}%/op · máx {st['max_positions']} posiciones · 1:{st['leverage']} · límite diario {st['daily_loss_limit']}%\n"
            f"• Filtro TF superior: {'sí' if st['htf_filter'] else 'no'} · IA: {'sí (≥' + str(st['ai_min_confidence']) + '%)' if st['ai_enabled'] else 'NO'}"
            + (f"\n⚠️ {p['warning']}" if p["warning"] else ""), "review")
        return {"settings": s.model_dump(), "profile_active": detect_profile(s)}

    @router.get("/dashboard")
    async def dashboard():
        settings = await engine.get_settings()
        db = engine.db
        positions = [Position.from_mongo(d).model_dump() async for d in db.positions.find().sort("_id", -1)]
        trades = [Trade.from_mongo(d).model_dump() async for d in db.trades.find().sort("_id", -1).limit(50)]
        signals = [Signal.from_mongo(d).model_dump() async for d in db.signals.find().sort("_id", -1).limit(30)]
        logs = [LogEntry.from_mongo(d).model_dump() async for d in db.logs.find().sort("_id", -1).limit(100)]
        watchlist = []
        for sym in settings.symbols:
            t = engine.market.tickers.get(sym, {})
            watchlist.append({"symbol": sym, "name": SPECS[sym]["name"], "group": SPECS[sym]["group"], "price": t.get("price"), "change_pct": t.get("change_pct"),
                              "high": t.get("high"), "low": t.get("low"), "spread_pips": round(pips(sym, engine.costs.spread(sym)), 1), "digits": SPECS[sym]["digits"],
                              **engine.analysis.get(sym, {})})
        return {"status": _status(), "settings": settings.model_dump(), "account": await engine.account_summary(), "positions": positions, "trades": trades,
                "signals": signals, "logs": logs, "watchlist": watchlist, "equity": await _equity_curve(), "profile_active": detect_profile(settings), "news": news.summary(),
                "checkin": await engine.checkin(settings), "optimize_job": {k: engine.review_job[k] for k in ("status", "step")}, "lab": await lab.state(engine, settings),
                "hfm": {"configured": store.public()["hfm_configured"], "state": store.cfg.hfm_state, "connection": store.cfg.hfm_connection,
                        "verified_at": store.cfg.hfm_verified_at, "trade_allowed": store.cfg.hfm_trade_allowed, "live": engine.live}}

    @router.get("/lab")
    async def lab_state():
        return await lab.state(engine)

    @router.post("/lab/{action}")
    async def lab_toggle(action: str):
        if action not in ("start", "stop"):
            raise HTTPException(404, "Acción no válida")
        if action == "start" and engine.live:
            raise HTTPException(409, "El modo laboratorio solo se activa en paper trading")
        return await (lab.start if action == "start" else lab.stop)(engine, store)

    CHART_TIMEFRAMES = FX_TIMEFRAMES + ["1d"]

    async def _chart_rows(symbol: str, tf: str, n: int, before: int | None):
        """Chart candles: the HFM terminal's own (what MetaTrader shows) while the MetaApi account is deployed; Yahoo otherwise or on failure
        (60 s back-off kept on the broker instance → cleared on relink/test/reconnect)."""
        b = engine.broker
        if b and store.cfg.hfm_state == "DEPLOYED" and time.time() - b.chart_fail_ts > 60:
            try:
                rows = await b.candles(symbol, tf, before, n)
                if rows:
                    return rows, "hfm"  # chart source label: MetaTrader terminal candles
            except Exception as e:
                b.chart_fail_ts = time.time()
                logger.warning(f"velas {engine.bk} {symbol} {tf}: {e} → usando Yahoo")
        if before:
            return await engine.market.fetch_before(symbol, tf, before, n), "yahoo"
        return await engine.market.fetch_klines(symbol, tf, n, cache_sec=20), "yahoo"

    @router.get("/klines")
    async def klines(symbol: str, tf: str = "1h", limit: int = chart.DEFAULT_LIMIT, before: int | None = Query(None, ge=1)):
        if symbol not in AVAILABLE or tf not in CHART_TIMEFRAMES:
            raise HTTPException(400, "Símbolo o temporalidad inválida")
        limit = chart.clamp(limit)
        rows, source = await _chart_rows(symbol, tf, limit + chart.WARMUP, before)
        return await chart.build(engine.db, symbol, tf, rows, TF_SECONDS[tf], limit, before, source)

    @router.post("/bot/start")
    async def bot_start():
        if engine.live:
            c = store.cfg
            if not (c.metaapi_token and c.metaapi_account_id and c.hfm_verified_at):
                raise HTTPException(409, f"Modo REAL: vincula y verifica la cuenta {engine.bk} (Ajustes → {engine.bl}) antes de iniciar el bot")
            if c.hfm_trade_allowed is False:
                raise HTTPException(409, f"Modo REAL: la cuenta {engine.bk} no permite operar (trading deshabilitado o contraseña investor)")
        await engine.start()
        return _status()

    @router.post("/bot/stop")
    async def bot_stop():
        await engine.stop()
        return _status()

    @router.post("/bot/scan")
    async def bot_scan():
        engine.scan_event.set()
        return {"ok": True, "message": "Escaneo solicitado"}

    @router.get("/market")
    async def market():
        return market_status()

    @router.get("/overview")
    async def overview():
        acc = await engine.account_summary()
        return {"running": engine.running, "mode": engine.mode_label, "equity": acc["equity"], "total_return_pct": acc["total_return_pct"],
                "open_positions": acc["open_positions"], "market_open": market_status()["open"]}

    @router.get("/settings")
    async def get_settings():
        return (await engine.get_settings()).model_dump()

    @router.put("/settings")
    async def put_settings(body: FxSettingsUpdate):
        try:
            s = await engine.update_settings(body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))
        return s.model_dump()

    @router.get("/positions")
    async def list_positions():
        return [Position.from_mongo(d).model_dump() async for d in engine.db.positions.find().sort("_id", -1)]

    @router.post("/positions/{pos_id}/close")
    async def close_pos(pos_id: str):
        if not ObjectId.is_valid(pos_id):
            raise HTTPException(400, "ID inválido")
        trade = await engine.close_position(pos_id, reason="MANUAL")
        if not trade:
            raise HTTPException(404, "Posición no encontrada")
        return trade.model_dump()

    @router.post("/positions/close-all")
    async def close_all():
        ids = [str(d["_id"]) async for d in engine.db.positions.find({}, {"_id": 1})]
        closed = [pid for pid in ids if await engine.close_position(pid, reason="MANUAL")]
        await engine.log("RISK", f"Cierre de emergencia · {len(closed)} posiciones cerradas")
        return {"closed": len(closed), "failed": len(ids) - len(closed)}

    @router.get("/trades")
    async def list_trades(limit: int = 100):
        return [Trade.from_mongo(d).model_dump() async for d in engine.db.trades.find().sort("_id", -1).limit(min(limit, 500))]

    @router.get("/signals")
    async def list_signals(limit: int = 50):
        return [Signal.from_mongo(d).model_dump() async for d in engine.db.signals.find().sort("_id", -1).limit(min(limit, 200))]

    @router.get("/account")
    async def account():
        return await engine.account_summary()

    @router.post("/account/reset")
    async def reset_account(body: ResetRequest = None):
        if await engine.db.positions.count_documents({"live": True}):
            raise HTTPException(409, f"Hay posiciones REALES abiertas en {engine.bk}: ciérralas antes de reiniciar las estadísticas")
        await engine.reset_account(body.initial_balance if body else None)
        return await engine.account_summary()

    @router.post("/account/rebase")
    async def rebase_account():
        """Switch of HFM account (demo → real money): re-base the capital to the new account's equity keeping trades, counters and strategy knowledge."""
        c = store.cfg
        if not engine.live:
            raise HTTPException(409, "Cambiar de cuenta solo aplica en modo REAL; en paper usa «Reiniciar»")
        if not (c.metaapi_account_id and c.hfm_verified_at):
            raise HTTPException(409, "Vincula y prueba la conexión de la cuenta nueva antes de cambiar de cuenta")
        if engine.running:
            raise HTTPException(409, "Detén el bot antes de cambiar de cuenta")
        if await engine.db.positions.count_documents({}):
            raise HTTPException(409, "Cierra las posiciones abiertas antes de cambiar de cuenta")
        try:
            out = await engine.rebase_account()
        except ValueError as e:
            raise HTTPException(424, str(e))
        return {**out, "account": await engine.account_summary()}

    @router.get("/performance")
    async def get_performance():
        return await performance(engine.db, fx_shadow_stats)

    @router.get("/evolution")
    async def get_evolution():
        return await evolution.timeline(engine)

    @router.post("/evolution/{event_id}/revert")
    async def revert_evolution(event_id: str):
        if (await lab.state(engine))["active"]:
            raise HTTPException(409, "Desactiva el modo laboratorio antes de deshacer cambios (se restauran solos al desactivarlo)")
        try:
            out = await evolution.revert(engine, event_id)
        except ValueError as e:
            raise HTTPException(409, str(e))
        await engine.log("INFO", f"Cambio deshecho manualmente: {', '.join(out['reverted'])}")
        return out

    @router.delete("/logs")
    async def clear_logs():
        await engine.db.logs.delete_many({})
        return {"ok": True}

    @router.get("/news")
    async def get_news():
        return {"latest": news.latest, "summary": news.summary()}

    @router.post("/news/refresh")
    async def refresh_news():
        try:
            await news.refresh(force=True)
        except Exception as e:
            logger.exception("fx news refresh")
            raise HTTPException(424, f"No se pudo actualizar las noticias: {str(e)[:160]}")
        return {"latest": news.latest, "summary": news.summary()}

    @router.get("/news/trades")
    async def news_trades(limit: int = 20):
        return [{k: v for k, v in d.items() if k != "_id"} async for d in engine.db.news_trades.find().sort("_id", -1).limit(min(limit, 100))]

    @router.get("/config")
    async def get_config():
        return {**gstore.public(), **store.public()}

    @router.put("/config")
    async def put_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Cuerpo inválido")
        g = {k: v for k, v in body.items() if k in ConfigUpdate.model_fields and k not in FxConfigUpdate.model_fields}
        f = {k: v for k, v in body.items() if k in FxConfigUpdate.model_fields}
        if body.get("clear_hfm") and store.cfg.trading_mode == "live":
            raise HTTPException(409, f"Vuelve a modo paper antes de quitar la cuenta {engine.bk}")
        try:
            gp = gstore.patch_from(ConfigUpdate(**g)) if g else {}
            fp = store.patch_from(FxConfigUpdate(**f)) if f else {}
        except ValueError as e:
            raise HTTPException(422, str(e))
        if gp:
            await gstore.update(gp)
        if fp:
            await store.update(fp)
        if gp or fp:
            await engine.log("INFO", f"Ajustes actualizados: {', '.join(list(gp) + list(fp))}")
        return {**gstore.public(), **store.public()}



def build_router(engine, store, news):
    router = protected_router()
    router.prefix = engine.profile.api_prefix
    add_shared_routes(router, engine, engine.profile.bot_name, "USD", before_unit="s")
    register(router, engine, store, news)
    routes_analysis.register(router, engine, store, news)
    routes_hfm.register(router, engine, store)
    return router
