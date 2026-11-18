"""Compact live snapshot of the three bots (Bybit, HotForex, Pepperstone) for the AI expert chat (refreshed on every message)."""
import logging
from datetime import datetime, timezone
from models import Position
from ai_advisor import current as ai_current
import trade_guard
from fx.market import market_status
from deps import engine, store, FX_BOTS, ai_usage

PANEL_LABEL = {"crypto": "Bybit (cripto)", "fx": "HotForex (forex/metales)", "pepper": "Pepperstone (forex/metales)"}

logger = logging.getLogger("chat.context")
SKIP_SETTINGS = {"bot_running", "symbol_params", "strategy", "disabled_strategies", "signal_mode", "risk_profile"}


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


async def _bot(eng, cfg_keys: tuple, cfg, extra: dict) -> dict:
    settings = await eng.get_settings()
    acc = await eng.account_summary()
    s = settings.model_dump(exclude=SKIP_SETTINGS)
    positions = [_pos(d) async for d in eng.db.positions.find().sort("_id", -1).limit(20)]
    signals = [{"ts": d.get("created_at", "")[:16], "symbol": d["symbol"], "side": d["side"], "strategy": d["strategy"], "decision": d.get("ai_decision"),
                "confidence": d.get("ai_confidence"), "executed": d.get("executed", False), "note": (d.get("note") or d.get("ai_reasoning") or "")[:140]}
               async for d in eng.db.signals.find().sort("_id", -1).limit(6)]
    logs = [f"{d.get('ts', '')[:16]} [{d['level']}] {d['message'][:160]}" async for d in eng.db.logs.find({"level": {"$in": ["ERROR", "RISK", "ORDER", "PLAN"]}}).sort("_id", -1).limit(8)]
    changes = [{"ts": d["ts"][:16], "fuente": d["source"], "cambios": {k: v.get("despues") for k, v in (d.get("changes") or {}).items()}}
               async for d in eng.db.changes.find({}, {"ts": 1, "source": 1, "changes": 1}).sort("ts", -1).limit(5)]
    n_shadow = await eng.db.shadow_trades.count_documents({})
    health = eng.broker_health()
    out = {"bot": {"running": eng.running, "scanning": eng.scanning, "last_scan": eng.last_scan, "modo": eng.mode_label, "entorno": eng.env,
                   "broker_health": health, "horario_y_eventos_rojos": trade_guard.status(eng.guard_kind, settings.symbols)},
           "cuenta": _acc(acc), "ajustes": s, "posiciones_abiertas": positions, "senales_recientes": signals, "logs_recientes": logs,
           "cambios_recientes_evolucion": changes, "operaciones_sombra_total": n_shadow,
           "config": {k: getattr(cfg, k, None) for k in cfg_keys}, **extra}
    return out


async def snapshot(panel: str) -> dict:
    c_cfg = ("telegram_enabled", "telegram_alerts", "auto_review_enabled", "auto_review_frequency", "auto_review_day", "auto_review_apply", "auto_review_last_at",
             "trading_mode", "bybit_env", "bybit_verified_at", "bybit_alert_after_sec")
    f_cfg = ("trading_mode", "account_type", "mt_server", "mt_platform", "hfm_state", "hfm_connection", "hfm_verified_at", "hfm_trade_allowed", "hfm_alert_after_sec", "mt_symbol_suffix",
             "hfm_symbols_bad", "auto_review_enabled", "auto_review_frequency", "auto_review_apply", "auto_review_last_at")
    out = {"ahora_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "panel_abierto_por_el_usuario": PANEL_LABEL.get(panel, PANEL_LABEL["crypto"]),
           "ia_activa": {k: v for k, v in ai_current().items() if k != "health"}, "ia_salud": ai_current()["health"].get("ok"),
           "llamadas_ia_hoy": ai_usage.snapshot(panel if panel in PANEL_LABEL else "crypto"),
           "bots": {"crypto": "Bybit AI Trader (clave de bot: crypto)", **{e.profile.panel: f"{e.profile.bot_name} · bróker {e.profile.broker} vía MetaApi (clave de bot: {e.profile.panel})" for e, _ in FX_BOTS}}}
    try:
        out["cripto"] = await _bot(engine, c_cfg, store.cfg, {"fuente_datos": engine.market.provider, "exchange_error": engine.exchange_error})
    except Exception as e:
        logger.warning(f"snapshot cripto: {e}")
        out["cripto"] = {"error": str(e)[:160]}
    ms = market_status()
    out["mercado_forex"] = {"abierto": ms["open"], "proximo_cambio": ms["next_change"], "sesiones_abiertas": [x["id"] for x in ms["sessions"] if x["open"]]}
    for fx_engine, fx_store in FX_BOTS:
        key = "forex_hotforex" if fx_engine.profile.panel == "fx" else f"forex_{fx_engine.profile.key}"
        try:
            out[key] = await _bot(fx_engine, f_cfg, fx_store.cfg,
                                  {"clave_de_bot": fx_engine.profile.panel, "broker": fx_engine.profile.broker,
                                   "costes": {"tipo_cuenta": fx_engine.costs.label, "factor_spread": fx_engine.costs.spread_factor, "comision_usd_por_lote_ida_y_vuelta": fx_engine.costs.commission_lot_usd},
                                   "embudo_por_par": {k: v.get("funnel") for k, v in fx_engine.analysis.items() if v.get("funnel")}})
        except Exception as e:
            logger.warning(f"snapshot {key}: {e}")
            out[key] = {"error": str(e)[:160]}
    return out
