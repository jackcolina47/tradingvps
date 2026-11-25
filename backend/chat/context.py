"""Compact live snapshot of the three bots (Bybit, HotForex, Pepperstone) for the AI expert chat (refreshed on every message)."""
import logging
from datetime import datetime, timezone
from models import Position
from ai_advisor import current as ai_current
from profiles import PROFILES, detect_profile
from fx.profiles import PROFILES as FX_PROFILES, detect_profile as fx_detect_profile
from fx.market import market_status
from deps import engine, store, news, FX_BOTS, ai_usage

PANEL_LABEL = {"crypto": "Bybit (cripto)", "fx": "HotForex (forex/metales)", "pepper": "Pepperstone (forex/metales/cripto CFD · estrategia única: Cruce EMA 9 y 21 Personalizado, sin IA en entradas ni gestión; salida por cruce contrario, SL N×ATR y, opcionales, cierre de vela al otro lado de la EMA 27, cierre parcial, trailing y breakeven)"}

logger = logging.getLogger("chat.context")
SKIP_SETTINGS = {"bot_running", "symbol_params"}


def _acc(a: dict) -> dict:
    keys = ("mode", "equity", "balance", "available", "unrealized_pnl", "daily_pnl", "daily_pnl_pct", "daily_paused", "total_trades", "win_rate",
            "profit_factor", "realized_pnl", "max_drawdown", "open_positions", "capital", "net_deposits", "total_pnl", "total_return_pct", "currency",
            "exchange_synced", "exchange_error", "account_kind")
    return {k: a.get(k) for k in keys if k in a}


def _pos(d: dict) -> dict:
    p = Position.from_mongo(d)
    return {"id": p.id, "symbol": p.symbol, "side": p.side, "strategy": p.strategy, "qty": p.qty, "entry": p.entry_price, "mark": p.mark_price,
            "pnl": round(p.unrealized_pnl, 2), "sl": p.sl, "tp": p.tp, "breakeven": p.breakeven, "mfe_r": p.mfe_r, "mae_r": p.mae_r, "horizon": p.horizon,
            "live": p.live, "opened_at": p.opened_at[:16]}


async def _bot(eng, profiles, detect, cfg_keys: tuple, cfg, nws, extra: dict) -> dict:
    settings = await eng.get_settings()
    acc = await eng.account_summary()
    s = settings.model_dump(exclude=SKIP_SETTINGS)
    s["symbol_params_pares"] = sorted(settings.symbol_params or {})
    prof = detect(settings)
    positions = [_pos(d) async for d in eng.db.positions.find().sort("_id", -1).limit(20)]
    signals = [{"ts": d.get("created_at", "")[:16], "symbol": d["symbol"], "side": d["side"], "strategy": d["strategy"], "decision": d.get("ai_decision"),
                "confidence": d.get("ai_confidence"), "executed": d.get("executed", False), "note": (d.get("note") or d.get("ai_reasoning") or "")[:140]}
               async for d in eng.db.signals.find().sort("_id", -1).limit(6)]
    logs = [f"{d.get('ts', '')[:16]} [{d['level']}] {d['message'][:160]}" async for d in eng.db.logs.find({"level": {"$in": ["ERROR", "RISK", "ORDER", "PLAN"]}}).sort("_id", -1).limit(8)]
    changes = [{"ts": d["ts"][:16], "fuente": d["source"], "cambios": {k: v.get("despues") for k, v in (d.get("changes") or {}).items()}}
               async for d in eng.db.changes.find({}, {"ts": 1, "source": 1, "changes": 1}).sort("ts", -1).limit(5)]
    n_shadow = await eng.db.shadow_trades.count_documents({})
    n_rules, n_rules_on = await eng.db.ai_rules.count_documents({}), await eng.db.ai_rules.count_documents({"enabled": {"$ne": False}})
    health = eng.broker_health()
    out = {"bot": {"running": eng.running, "scanning": eng.scanning, "last_scan": eng.last_scan, "modo": eng.mode_label, "entorno": eng.env,
                   "perfil_riesgo": (profiles[prof]["name"] if prof is not None else "Personalizado"), "broker_health": health},
           "cuenta": _acc(acc), "ajustes": s, "posiciones_abiertas": positions, "senales_recientes": signals, "logs_recientes": logs,
           "cambios_recientes_evolucion": changes, "operaciones_sombra_total": n_shadow,
           "reglas_aprendidas": {"total": n_rules, "activas": n_rules_on, "detalle": "usa la herramienta get_learned_rules para verlas completas"},
           "config": {k: getattr(cfg, k, None) for k in cfg_keys}, "noticias": nws.summary() if nws else None, **extra}
    return out


async def snapshot(panel: str) -> dict:
    c_cfg = ("telegram_enabled", "telegram_alerts", "auto_review_enabled", "auto_review_frequency", "auto_review_day", "auto_review_apply", "auto_review_last_at",
             "news_enabled", "news_interval_min", "news_block_events", "news_block_critical", "news_reduce_on_high_risk", "wf_auto_remove",
             "funding_enabled", "funding_block_pct", "funding_reduce_pct", "funding_size_factor", "trading_mode", "bybit_env", "bybit_verified_at", "bybit_alert_after_sec")
    f_cfg = ("trading_mode", "account_type", "mt_server", "mt_platform", "hfm_state", "hfm_connection", "hfm_verified_at", "hfm_trade_allowed", "hfm_alert_after_sec", "mt_symbol_suffix",
             "hfm_symbols_bad", "news_enabled", "news_interval_min", "news_trading_enabled", "news_trade_min_confidence", "news_trade_risk_factor",
             "auto_review_enabled", "auto_review_frequency", "auto_review_apply", "auto_review_last_at", "wf_auto_remove")
    out = {"ahora_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "panel_abierto_por_el_usuario": PANEL_LABEL.get(panel, PANEL_LABEL["crypto"]),
           "ia_activa": {k: v for k, v in ai_current().items() if k != "health"}, "ia_salud": ai_current()["health"].get("ok"),
           "llamadas_ia_hoy": ai_usage.snapshot(panel if panel in PANEL_LABEL else "crypto"),
           "bots": {"crypto": "Bybit AI Trader (clave de bot: crypto)", **{e.profile.panel: f"{e.profile.bot_name} · bróker {e.profile.broker} vía puente MT propio (clave de bot: {e.profile.panel})" for e, _, _ in FX_BOTS}}}
    try:
        out["cripto"] = await _bot(engine, PROFILES, detect_profile, c_cfg, store.cfg, news, {"fuente_datos": engine.market.provider, "exchange_error": engine.exchange_error})
    except Exception as e:
        logger.warning(f"snapshot cripto: {e}")
        out["cripto"] = {"error": str(e)[:160]}
    ms = market_status()
    out["mercado_forex"] = {"abierto": ms["open"], "proximo_cambio": ms["next_change"], "sesiones_abiertas": [x["id"] for x in ms["sessions"] if x["open"]]}
    for fx_engine, fx_store, fx_news in FX_BOTS:
        key = "forex_hotforex" if fx_engine.profile.panel == "fx" else f"forex_{fx_engine.profile.key}"
        try:
            out[key] = await _bot(fx_engine, FX_PROFILES, fx_detect_profile, f_cfg, fx_store.cfg, fx_news,
                                  {"clave_de_bot": fx_engine.profile.panel, "broker": fx_engine.profile.broker,
                                   "costes": {"tipo_cuenta": fx_engine.costs.label, "factor_spread": fx_engine.costs.spread_factor, "comision_usd_por_lote_ida_y_vuelta": fx_engine.costs.commission_lot_usd},
                                   "analisis_filtros_por_par": {k: {x: v.get(x) for x in ("vol_regime", "season_bias", "trend", "signal")} for k, v in fx_engine.analysis.items()}})
        except Exception as e:
            logger.warning(f"snapshot {key}: {e}")
            out[key] = {"error": str(e)[:160]}
    return out
