import logging
from models import SettingsUpdate, AVAILABLE_SYMBOLS, STRATEGY_KEYS, CRYPTO_STRATEGY_KEYS, ENTRY_ORDER_TYPES, REGIME_SMAS, now_iso
from performance import maker_stats, excursion_stats
from risk import trade_rules
from performance import performance
from ai_advisor import advise
from ai_advisor import DEFAULT_POLICY


def ai_policy(_level=None) -> dict:
    """Single set of AI caps now that the risk profiles are gone."""
    return DEFAULT_POLICY
from shadow import recent_rejections
import evolution
from fx.market import SESSION_NAMES
import notify

logger = logging.getLogger("review")
ALLOWED_AI_KEYS = {"symbols", "disabled_strategies", "risk_per_trade", "max_positions", "leverage", "ai_min_confidence",
                   "trailing_enabled", "breakeven_enabled", "htf_filter", "symbol_params"}
CRYPTO_AI_KEYS = ALLOWED_AI_KEYS | {"btc_regime_filter", "btc_regime_sma", "rotation_enabled", "entry_order_type", "max_long_positions",
                                    "be_start_r", "trail_start_r", "trail_atr_mult", "partial_tp_enabled", "partial_tp_r", "partial_tp_pct"}
EXIT_RANGES = {"be_start_r": (0.5, 3.0), "trail_start_r": (0.5, 3.0), "trail_atr_mult": (0.5, 3.0), "partial_tp_r": (0.5, 3.0), "partial_tp_pct": (25, 75)}
LABELS = {"symbols": "Pares", "strategy": "Estrategia", "disabled_strategies": "Estrategias apagadas", "risk_per_trade": "Riesgo/op %",
          "max_positions": "Máx. posiciones", "leverage": "Apalancamiento", "ai_min_confidence": "Confianza mín. IA", "trailing_enabled": "Trailing", "breakeven_enabled": "Stop a breakeven",
          "htf_filter": "Filtro TF superior", "symbol_params": "Parámetros SL/TP", "sessions_allowed": "Sesiones permitidas", "ai_news_weight": "Peso de las noticias en la IA",
          "timeframe": "Temporalidad", "signal_mode": "Criterios de entrada", "daily_loss_limit": "Límite pérdida diaria %", "ai_enabled": "Filtro IA",
          "scan_interval_sec": "Intervalo de escaneo", "close_before_weekend": "Cierre fin de semana", "min_lot_max_risk": "Tope riesgo lote mínimo %",
          "risk_profile": "Perfil de riesgo", "btc_regime_filter": "Filtro de régimen de BTC", "btc_regime_sma": "SMA diaria del régimen",
          "rotation_enabled": "Rotación semanal por momentum", "entry_order_type": "Tipo de orden de entrada", "max_long_positions": "Máx. posiciones largo plazo",
          "maker_wait_bars": "Velas de espera orden límite", "rotation_lookback_days": "Días de la rotación",
          "be_start_r": "Breakeven (R)", "trail_start_r": "Inicio del trailing (R)", "trail_atr_mult": "Distancia del trailing (ATR)",
          "partial_tp_enabled": "Cierre parcial", "partial_tp_r": "Nivel del cierre parcial (R)", "partial_tp_pct": "% del cierre parcial",
          "currency_exposure_limit": "Tope de exposición por divisa", "rollover_guard": "Ventana de rollover", "vol_filter_enabled": "Filtro de volatilidad",
          "vol_min_pct": "Percentil mín. de volatilidad", "vol_high_pct": "Percentil alto de volatilidad", "seasonality_bias": "Estacionalidad intradía",
          "seasonality_block_pairs": "Pares con bloqueo estacional", "seasonality_size_factor": "Tamaño contra el sesgo estacional", "usd_regime_filter": "Filtro régimen USD",
          "usd_regime_sma": "SMA del régimen USD", "fomc_rule": "Regla FOMC"}
STRATEGY_NAMES = {"trend_ema": "Tendencia EMA", "mean_reversion": "Reversión media", "breakout_turtle": "Turtle",
                  "supertrend_momentum": "Supertrend", "ema_pullback": "Pullback EMA21", "ema_cross": "Cruce EMA 10/20", "multi_horizon": "Multi-Horizonte", "double_divergence": "Divergencia doble"}
VALUE_LABELS = {"market": "a mercado", "maker": "límite maker"}


ARCH_NOTE = ("El bot ya NO usa estrategias estáticas, backtest, optimizador ni perfiles de riesgo. Las entradas las decide un embudo de IA cada 60 s dentro del horario "
             "operativo de Nueva York: filtro técnico → modelo rápido (≤3 candidatos) → modelo potente (BUY/SELL/PASS + confianza). Solo se ejecuta si la confianza supera "
             "ai_min_confidence (10..90 en pasos de 10); el resto son sombras BAJA_CONFIANZA. Eventos rojos del calendario bloquean ±15 min. Basa tus recomendaciones SOLO en "
             "operaciones reales y sombras: riesgo, pares, apalancamiento, max_positions, ai_min_confidence, SL/TP por ATR, breakeven/trailing/parcial. No propongas cambios de "
             "strategy, signal_mode, disabled_strategies, symbol_params, filtros de régimen/rotación/estacionalidad ni perfiles.")


async def build_context(engine, db, settings, acc: dict, with_backtest: bool) -> dict:
    pol = ai_policy(settings.risk_profile)
    ctx = {"settings_actuales": settings.model_dump(exclude={"bot_running"}), "cuenta": acc,
           "perfil_riesgo": {"nivel": pol["level"], "nombre": pol["name"], "topes_ia": pol["caps"]},
           "rendimiento_real": await performance(db), "chequeo_plan": await engine.checkin(settings, acc),
           "motivos_rechazo_recientes_ia": await recent_rejections(db),
           "arquitectura": ARCH_NOTE}
    ctx["rendimiento_real"]["shadow"]["_explicacion"] = ("Operaciones sombra: qué habría pasado si se hubieran ejecutado las señales rechazadas por la IA "
                                                         "(RECHAZADA, BAJA_CONFIANZA), bloqueadas por riesgo (BLOQUEADA), por noticias (NOTICIAS), por funding extremo en contra (FUNDING), "
                                                         "con error de IA (ERROR) o aprobadas pero no abiertas (NO_EJECUTADA: lote mínimo, margen, bróker). "
                                                         "filter_value > 0 = el filtro evitó pérdidas netas; < 0 = el filtro dejó escapar ganancias. "
                                                         "by_confidence sirve para calibrar ai_min_confidence con evidencia.")
    ctx["evolucion"] = await evolution.ai_context(engine)
    if hasattr(settings, "btc_regime_filter"):
        try:
            ctx["entradas_maker"] = {**await maker_stats(db), "tipo_actual": settings.entry_order_type, "velas_espera": settings.maker_wait_bars,
                                     "_explicacion": "Entradas con orden límite post-only (0,02 %) frente a mercado (0,055 %): fill_rate = % de señales llenadas como maker; "
                                                     "market_fallbacks = pasaron a mercado tras esperar. by_entry_type en rendimiento_real compara el PnL de cada tipo."}
            ctx["gestion_salidas"] = {"reglas_actuales": trade_rules(settings), "excursion": await excursion_stats(db),
                                      "_explicacion": "Recorrido de cada operación cerrada: mfe_r = máximo a favor en R (1 R = distancia al stop inicial), mae_r = máximo en contra, "
                                                      "mfe_tp_pct = % del camino al TP que llegó a recorrer. near_miss = cerradas sin tocar el TP tras alcanzar ≥ 70 % de él (rebotes). "
                                                      "partial = operaciones con cierre parcial ejecutado (partial_tp_pct % cerrado a +partial_tp_r R; el resto sigue con trailing)."}
        except Exception as e:
            logger.warning(f"contexto régimen/rotación: {e}")
    return ctx


async def apply_ai_reverts(engine, res: dict, source: str) -> dict:
    """Undo the evolution events the AI asked for, only when the timeline backs it (verdict PEOR with enough trades)."""
    applied = {}
    ids = res.get("revert") or []
    if not ids:
        return applied
    by_id = {e["id"]: e for e in (await evolution.timeline(engine))["events"]}
    for eid in ids:
        ev = by_id.get(eid)
        if not ev:
            res["notes"].append(f"La IA pidió deshacer un cambio que no existe ({eid[:8]}…); descartado.")
            continue
        if ev["verdict"] != "PEOR":
            res["notes"].append(f"No se deshizo el cambio «{ev['source_label']}» del {evolution._fmt_day(ev['ts'])}: sin evidencia suficiente de que empeorara el rendimiento ({ev['verdict'].lower()}).")
            continue
        try:
            out = await evolution.revert(engine, eid, source, "la IA lo deshizo por empeorar el rendimiento")
        except ValueError as e:
            res["notes"].append(f"No se pudo deshacer el cambio del {evolution._fmt_day(ev['ts'])}: {e}")
            continue
        for k in out["reverted"]:
            applied[k] = {"antes": ev["changes"][k]["despues"], "despues": ev["changes"][k]["antes"]}
        res["notes"].insert(0, f"Deshecho el cambio «{ev['source_label']}» del {evolution._fmt_day(ev['ts'])} ({ev['after']['trades']} operaciones después con PnL medio "
                                f"{ev['after']['avg_pnl']:+.2f} frente a {ev['before']['avg_pnl']:+.2f} antes).")
    return applied


async def apply_ai_changes(engine, res: dict, before: dict, settings, *, policy: dict = None, available=AVAILABLE_SYMBOLS,
                           model=SettingsUpdate, allowed=ALLOWED_AI_KEYS, source: str = "optimize_all", strategy_keys=STRATEGY_KEYS):
    applied = await apply_ai_reverts(engine, res, source)
    original = before
    before = {**before, **{k: v["despues"] for k, v in applied.items()}}
    patch = {k: v for k, v in res["changes"].items() if k in allowed}
    for k, cap in (policy or ai_policy(settings.risk_profile))["caps"].items():
        if k in patch:
            patch[k] = min(float(patch[k]), cap)
    if "symbols" in patch and (not isinstance(patch["symbols"], list) or len([x for x in patch["symbols"] if x in available]) < 2):
        patch.pop("symbols")
    if "sessions_allowed" in patch and not isinstance(patch["sessions_allowed"], list):
        patch.pop("sessions_allowed")
    if "ai_news_weight" in patch and patch["ai_news_weight"] not in ("normal", "bajo"):
        patch.pop("ai_news_weight")
    if "entry_order_type" in patch and patch["entry_order_type"] not in ENTRY_ORDER_TYPES:
        patch.pop("entry_order_type")
    if "btc_regime_sma" in patch and patch["btc_regime_sma"] not in REGIME_SMAS:
        patch.pop("btc_regime_sma")
    for k in ("btc_regime_filter", "rotation_enabled", "partial_tp_enabled", "rollover_guard", "vol_filter_enabled", "seasonality_bias", "usd_regime_filter", "fomc_rule"):
        if k in patch and not isinstance(patch[k], bool):
            patch.pop(k)
    for k, (lo, hi) in (("vol_min_pct", (0, 50)), ("vol_high_pct", (60, 100)), ("currency_exposure_limit", (1, 6)), ("seasonality_size_factor", (0.25, 1.0))):
        if k in patch:
            try:
                v = float(patch[k])
                patch[k] = round(min(max(v, lo), hi), 2) if k == "seasonality_size_factor" else int(min(max(v, lo), hi))
            except (TypeError, ValueError):
                patch.pop(k)
    for k, (lo, hi) in EXIT_RANGES.items():
        if k in patch:
            try:
                v = float(patch[k])
                patch[k] = int(min(max(v, lo), hi)) if k == "partial_tp_pct" else round(min(max(v, lo), hi), 2)
            except (TypeError, ValueError):
                patch.pop(k)
    if "trail_start_r" in patch or "be_start_r" in patch:
        be = patch.get("be_start_r", getattr(settings, "be_start_r", 1.0))
        if patch.get("trail_start_r", getattr(settings, "trail_start_r", 1.5)) < be:
            patch["trail_start_r"] = be
    if "disabled_strategies" in patch:
        dis = [x for x in (patch["disabled_strategies"] if isinstance(patch["disabled_strategies"], list) else []) if x in strategy_keys and x != "all"]
        if len(dis) >= len(strategy_keys) - 1:
            patch.pop("disabled_strategies")
        else:
            patch["disabled_strategies"] = dis
            patch["strategy"] = "all"
    if patch:
        try:
            after = (await engine.update_settings(model.model_validate(patch).model_dump(), source, res.get("summary", ""))).model_dump(exclude={"bot_running"})
            applied.update({k: {"antes": before[k], "despues": after[k]} for k in patch if before.get(k) != after.get(k)})
        except Exception as e:
            res["notes"].append(f"Cambios propuestos no válidos y descartados: {str(e)[:120]}")
    return applied, {k: original.get(k, before.get(k)) for k in applied}


def _fmt(k, v) -> str:
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, list):
        if k == "disabled_strategies":
            return ", ".join(STRATEGY_NAMES.get(s, s) for s in v) or "ninguna"
        if k == "sessions_allowed":
            return ", ".join(SESSION_NAMES.get(s, s) for s in v) or "todas"
        return ", ".join(s.replace("USDT", "") for s in v) or "ninguno"
    if isinstance(v, dict):
        return "; ".join(f"{s.replace('USDT', '')} SL {p.get('sl_atr')}× TP {p.get('tp_atr')}× ATR {p.get('atr_period')}" for s, p in v.items()) or "por defecto"
    return VALUE_LABELS.get(v, str(v)) if isinstance(v, str) else str(v)


def changes_text(applied: dict) -> str:
    return "\n".join(f"• <b>{LABELS.get(k, k)}</b>: {_fmt(k, v['antes'])} → {_fmt(k, v['despues'])}" for k, v in applied.items())


def _footer(acc: dict) -> str:
    return f"\n\n💰 Equidad ${acc['equity']:,.2f} · {acc['total_trades']} ops · PF {acc['profit_factor'] or '—'} · DD {acc['max_drawdown']}%"


async def run_auto_review(engine, db, store) -> dict:
    settings = await engine.get_settings()
    acc = await engine.account_summary()
    before = settings.model_dump(exclude={"bot_running"})
    await engine.log("AI_EVAL", "Revisión IA automática iniciada...")
    ctx = await build_context(engine, db, settings, acc, True)
    res = await advise("revision_automatica_programada", ctx, ai_policy(settings.risk_profile))
    applied, previous = ({}, {})
    if store.cfg.auto_review_apply:
        applied, previous = await apply_ai_changes(engine, res, before, settings, source="auto_review", allowed=CRYPTO_AI_KEYS, strategy_keys=CRYPTO_STRATEGY_KEYS)
    await store.update({"auto_review_last_at": now_iso(), "auto_review_last_summary": res["summary"], "auto_review_last_changes": applied})
    await engine.log("AI_EVAL", f"Revisión automática: {res['summary'][:160]}" + (f" · cambios: {', '.join(applied)}" if applied else " · sin cambios"))
    msg = (f"🧠 <b>Revisión IA automática</b>\n{res['summary']}\n\n"
           + (f"<b>Cambios aplicados</b>\n{changes_text(applied)}" if applied else ("<b>Sin cambios</b> en la configuración" if store.cfg.auto_review_apply else "<b>Modo solo aviso</b>: no se aplicó nada"))
           + (("\n\n" + "\n".join(f"– {n}" for n in res["notes"])) if res["notes"] else "")
           + _footer(acc))
    await notify.notify(msg, "review", bot="bybit")
    return {**res, "applied": applied, "previous": previous, "ran_at": store.cfg.auto_review_last_at}
