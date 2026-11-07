"""'Optimizar todo con IA' for HotForex: FX optimizer + walk-forward + backtest sessions per pair → Claude decides within the FX risk profile."""
import asyncio
import logging
from models import now_iso
from performance import performance
from fx.shadow import shadow_stats as fx_shadow_stats
from shadow import recent_rejections
import evolution
from ai_advisor import advise, FX_MARKET, label as ai_label
import review
from fx.models import FxSettingsUpdate
from fx.symbols import AVAILABLE
from fx.market import market_status, SESSION_NAMES
from fx.profiles import ai_policy, PROFILES
from fx.optimizer import optimize, walk_forward, WF_CANDLES
from fx.backtest import run_backtest
from fx.plan import plan_stats
from fx import filters
from fx import regime as fx_regime
from fx.risk import trade_rules
from performance import excursion_stats

logger = logging.getLogger("fx.review")
FX_GATE_KEYS = {"currency_exposure_limit", "rollover_guard", "vol_filter_enabled", "vol_min_pct", "vol_high_pct", "seasonality_bias", "seasonality_block_pairs",
                "seasonality_size_factor", "usd_regime_filter", "usd_regime_sma", "fomc_rule", "max_long_positions",
                "be_start_r", "trail_start_r", "trail_atr_mult", "partial_tp_enabled", "partial_tp_r", "partial_tp_pct"}
ALLOWED_AI_KEYS = review.ALLOWED_AI_KEYS | {"sessions_allowed", "ai_news_weight"} | FX_GATE_KEYS

def new_job() -> dict:
    return {"status": "idle", "started_at": None, "finished_at": None, "result": None, "error": None, "step": ""}


async def build_context(engine, settings, acc: dict, with_backtest: bool = False) -> dict:
    pol = ai_policy(settings.risk_profile)
    bk = engine.profile.broker
    ctx = {"mercado": f"forex y metales ({bk}, paper trading)" if not engine.live else f"forex y metales ({bk}, cuenta REAL vía MetaApi)",
           "settings_actuales": settings.model_dump(exclude={"bot_running"}), "cuenta": acc,
           "perfil_riesgo": {"nivel": pol["level"], "nombre": pol["name"], "topes_ia": pol["caps"]},
           "rendimiento_real": await performance(engine.db, fx_shadow_stats), "chequeo_plan": await engine.checkin(settings, acc),
           "sesiones": {"ahora": market_status()["sessions"], "permitidas": settings.sessions_allowed or "todas", "ids": SESSION_NAMES},
           "motivos_rechazo_recientes_ia": await recent_rejections(engine.db)}
    ctx["rendimiento_real"]["shadow"]["_explicacion"] = (
        f"Operaciones sombra: qué habría pasado si se hubieran ejecutado las señales no operadas, simuladas con lotes, spread {bk}, trailing y cierre de fin de semana. "
        f"Motivos: RECHAZADA / BAJA_CONFIANZA (filtro IA), BLOQUEADA (tope de posiciones, par abierto, límite diario, {bk} desconectado), NOTICIAS, "
        "SESION (fuera de las sesiones permitidas), ERROR (fallo de IA), NO_EJECUTADA (orden no abierta: lote mínimo, bróker). "
        "filter_value > 0 = el filtro evitó pérdidas netas; < 0 = dejó escapar ganancias. by_confidence calibra ai_min_confidence; "
        "by_session indica si conviene ampliar o recortar sessions_allowed; undersized = sombras simuladas con lote mínimo forzado (capital insuficiente). "
        "ai_by_news_risk = sombras de señales rechazadas por la IA según el riesgo general de mercado por noticias (ALTO/MEDIO/BAJO) en el momento del rechazo: "
        "pnl > 0 en ALTO con muestra suficiente = la IA rechaza señales rentables en días de alertas generales → ai_news_weight 'bajo'; pnl < 0 = el filtro acierta → 'normal'.")
    ctx["rendimiento_real"]["shadow"]["_explicacion"] += (
        " Motivos nuevos: EXPOSICION (tope de posiciones en la misma dirección por divisa), ROLLOVER (ventana 16:50–17:15 Nueva York), VOLATILIDAD (ATR bajo el percentil mínimo), "
        "ESTACIONAL (señal contra el sesgo horario de la divisa), REGIMEN_USD (par con USD contra el régimen del índice USD), FOMC (continuación en las 24 h tras la Fed), "
        "PRECIO_EXTENDIDO (entrada tras N velas grandes seguidas o pegada a un swing contrario; ajustable con price_action_block / price_action_max_candles / price_action_swing_atr). "
        "filter_value > 0 en un motivo = el filtro ahorra dinero; < 0 con ≥ 20 sombras = relajarlo.")
    ctx["filtros_fx"] = await gates_context(engine, settings)
    if engine.news and engine.news.latest:
        ctx["noticias"] = {"mercado": engine.news.latest["market"], "alertas": engine.news.latest.get("alerts", [])[:5]}
    ctx["evolucion"] = await evolution.ai_context(engine)
    wf_latest = await engine.db.wf_checks.find_one({"_id": "latest"}, {"_id": 0, "ran_at": 1, "results": 1})
    if wf_latest and wf_latest.get("results"):
        ctx["walk_forward_pares_aplicados"] = {"revisado": wf_latest["ran_at"], "pares": {s: {k: r[k] for k in ("verdict", "wfe", "oos", "previous_verdict")}
                                                                                       for s, r in wf_latest["results"].items()},
                                               "_explicacion": "Revalidación walk-forward de los pares con symbol_params aplicados; SOBREAJUSTADO/NO_RENTABLE = quitar sus symbol_params."}
    if with_backtest:
        bt = await plan_stats(engine, settings, acc["initial_balance"])
        ctx["backtest"] = {k: bt[k] for k in ("per_symbol", "by_strategy", "monte_carlo", "trades_per_month", "days", "total_pips", "rejected_lot")}
        ctx["backtest"]["_explicacion"] = f"Backtest por par en lotes con spread {bk}{' y comisión' if engine.costs.commission_lot_usd else ''}; total_pips = pips netos de spread; rejected_lot = señales sin operar por lote mínimo."
    return ctx


async def gates_context(engine, settings) -> dict:
    """Current state of the FX gates + exit rules + trade-path analytics, for the AI and the weekly summary."""
    open_docs = await engine.db.positions.find({}, {"symbol": 1, "side": 1, "horizon": 1}).to_list(100)
    rg = await fx_regime.usd_regime(engine.market, settings.usd_regime_sma) if settings.usd_regime_filter else {"state": "apagado"}
    vol = {s: {"pct": a.get("vol_pct"), "regimen": a.get("vol_regime")} for s, a in engine.analysis.items() if a.get("vol_pct") is not None}
    return {
        "exposicion_por_divisa": {"tope": settings.currency_exposure_limit, "neta_actual": filters.exposure_counts(open_docs)},
        "rollover": {"activo": settings.rollover_guard, **filters.rollover_status()},
        "volatilidad": {"activo": settings.vol_filter_enabled, "percentil_min": settings.vol_min_pct, "percentil_alto": settings.vol_high_pct, "por_par": vol},
        "estacionalidad": {"activo": settings.seasonality_bias, "pares_bloqueo": settings.seasonality_block_pairs, "factor_tamano": settings.seasonality_size_factor,
                           "sesgo_ahora": {s: filters.seasonality_bias(s) for s in settings.symbols}},
        "regimen_usd": {"activo": settings.usd_regime_filter, **rg},
        "fomc": {"activo": settings.fomc_rule},
        "largo_plazo": {"max_long_positions": settings.max_long_positions, "estrategia": "fix_reversion (reversión fin de mes del fix de Londres, ≈1 día, no cuenta en max_positions)"},
        "gestion_salidas": {"reglas_actuales": trade_rules(settings), "excursion": await excursion_stats(engine.db)},
        "_explicacion": ("exposicion: cuenta neta de posiciones por divisa (long EURUSD = +1 EUR, −1 USD); el tope evita apostar 3 veces al mismo dólar. "
                         "volatilidad: percentil del ATR actual frente a las últimas 200 velas; bajo el mínimo no se abre, sobre el alto se opera a la mitad. "
                         "estacionalidad: una divisa tiende a depreciarse en su horario local (Breedon & Ranaldo); en pares_bloqueo la señal contraria se bloquea, en el resto se reduce el tamaño. "
                         "regimen_usd: índice USD sintético (EUR, JPY, GBP, CAD, CHF) frente a su SMA diaria; con USD FUERTE los pares xxxUSD solo cortos y USDxxx solo largos. "
                         "gestion_salidas.excursion: recorrido real de cada operación (mfe_r máximo a favor, mae_r máximo en contra, mfe_tp_pct % del TP alcanzado, near_miss = rozó el TP sin tocarlo)."),
    }


def _apply(engine, res, before, settings, source: str = "optimize_all"):
    pol = ai_policy(settings.risk_profile)
    return review.apply_ai_changes(engine, res, before, settings, policy=pol, available=AVAILABLE, model=FxSettingsUpdate, allowed=ALLOWED_AI_KEYS, source=source)


async def advise_scope(engine, scope: str, optimizer_results: list = None) -> dict:
    settings = await engine.get_settings()
    before = settings.model_dump(exclude={"bot_running"})
    acc = await engine.account_summary()
    ctx = await build_context(engine, settings, acc, scope == "plan")
    if scope == "optimizer":
        ctx["optimizador"] = [{k: r.get(k) for k in ("symbol", "best", "baseline", "timeframe")} for r in (optimizer_results or [])]
    await engine.log("AI_EVAL", f"{ai_label()} analizando {'optimizador' if scope == 'optimizer' else 'plan'} y rendimiento real...")
    res = await advise(scope, ctx, ai_policy(settings.risk_profile), FX_MARKET, kind=f"{engine.profile.ai_kind}-advisor")
    applied, previous = await _apply(engine, res, before, settings, scope)
    await engine.log("AI_EVAL", f"IA: {res['summary'][:160]}" + (f" · cambios: {', '.join(applied)}" if applied else " · sin cambios"))
    return {**res, "applied": applied, "previous": previous}


async def run_auto_review(engine, db, store) -> dict:
    settings = await engine.get_settings()
    acc = await engine.account_summary()
    before = settings.model_dump(exclude={"bot_running"})
    await engine.log("AI_EVAL", "Revisión IA automática iniciada...")
    ctx = await build_context(engine, settings, acc, True)
    res = await advise("revision_automatica_programada", ctx, ai_policy(settings.risk_profile), FX_MARKET, kind=f"{engine.profile.ai_kind}-advisor")
    applied, previous = ({}, {})
    if store.cfg.auto_review_apply:
        applied, previous = await _apply(engine, res, before, settings, "auto_review")
    await store.update({"auto_review_last_at": now_iso(), "auto_review_last_summary": res["summary"], "auto_review_last_changes": applied})
    await engine.log("AI_EVAL", f"Revisión automática: {res['summary'][:160]}" + (f" · cambios: {', '.join(applied)}" if applied else " · sin cambios"))
    await engine.tg(f"🧠 <b>Revisión IA automática</b> (perfil {PROFILES[settings.risk_profile]['name']})\n{res['summary']}\n\n"
                    + (f"<b>Cambios aplicados</b>\n{review.changes_text(applied)}" if applied
                       else ("<b>Sin cambios</b> en la configuración" if store.cfg.auto_review_apply else "<b>Modo solo aviso</b>: no se aplicó nada"))
                    + (("\n\n" + "\n".join(f"– {n}" for n in res["notes"])) if res["notes"] else "")
                    + review._footer(acc), "review")
    return {**res, "applied": applied, "previous": previous, "ran_at": store.cfg.auto_review_last_at}


async def _safe_walk_forward(engine, settings, initial: float, sym: str):
    try:
        klines = await engine.market.fetch_klines(sym, settings.timeframe, WF_CANDLES, cache_sec=600)
        wf = await asyncio.to_thread(walk_forward, klines, settings, initial, sym, dict(engine.market.tickers), engine.costs)
        out = {k: wf[k] for k in ("verdict", "verdict_text", "wfe", "oos", "baseline_oos", "is_expectancy_r", "beats_baseline", "params_stable", "params_unique")} | {"folds": len(wf["folds"])}
        if wf["applied"]:
            out["symbol_params_aplicados"] = {k: wf["applied"][k] for k in ("params", "verdict", "verdict_text", "wfe", "oos")}
        return out
    except Exception as e:
        logger.warning(f"fx walk-forward {sym}: {e}")
        return None


async def run_global_optimization(engine, db) -> dict:
    settings = await engine.get_settings()
    acc = await engine.account_summary()
    before = settings.model_dump(exclude={"bot_running"})
    initial = acc["initial_balance"]
    tickers = dict(engine.market.tickers)
    await engine.log("AI_EVAL", f"Optimización global IA iniciada · {len(settings.symbols)} pares · perfil {PROFILES[settings.risk_profile]['name']}")
    results, job = [], engine.review_job
    for sym in settings.symbols:
        job["step"] = f"Optimizando {sym}"
        klines = await engine.market.fetch_klines(sym, settings.timeframe, 1000, cache_sec=300)
        res = await asyncio.to_thread(optimize, klines, settings, initial, sym, tickers, engine.costs)
        bt = await asyncio.to_thread(run_backtest, klines, settings.strategy, settings.risk_per_trade, settings.leverage, trade_rules(settings), initial,
                                     sym, settings.signal_mode, settings.disabled_strategies, tickers, settings.min_lot_max_risk, settings.close_before_weekend, costs=engine.costs)
        job["step"] = f"Validando {sym} (walk-forward)"
        wf = await _safe_walk_forward(engine, settings, initial, sym)
        results.append({**res, "timeframe": settings.timeframe, "applied": settings.symbol_params.get(sym), "walk_forward": wf,
                        "session_stats": bt["session_stats"], "rejected_lot": bt["rejected_lot"]})
    job["step"] = f"{ai_label()} analizando todos los datos"
    ctx = await build_context(engine, settings, acc)
    ctx["optimizador"] = [{k: r.get(k) for k in ("symbol", "best", "baseline", "timeframe", "applied", "walk_forward", "session_stats", "rejected_lot")} for r in results]
    ctx["optimizador_explicacion"] = ("session_stats = operaciones del backtest (sin filtro de sesión) agrupadas por la sesión abierta al entrar; "
                                      "rejected_lot = señales sin operar porque el lote mínimo 0.01 arriesgaba más que min_lot_max_risk.")
    res = await advise("optimizacion_global_boton_usuario", ctx, ai_policy(settings.risk_profile), FX_MARKET, kind=f"{engine.profile.ai_kind}-advisor")
    applied, previous = await _apply(engine, res, before, settings)
    await engine.log("AI_EVAL", f"Optimización global: {res['summary'][:160]}" + (f" · cambios: {', '.join(applied)}" if applied else " · sin cambios"))
    await engine.tg(f"🎯 <b>Optimización global con IA</b> (perfil {PROFILES[settings.risk_profile]['name']})\n{res['summary']}\n\n"
                    + (f"<b>Cambios aplicados</b>\n{review.changes_text(applied)}" if applied else "<b>Sin cambios</b> en la configuración")
                    + (("\n\n" + "\n".join(f"– {n}" for n in res["notes"])) if res["notes"] else "")
                    + review._footer(acc), "review")
    return {**res, "applied": applied, "previous": previous, "optimizer": results, "ran_at": now_iso()}


async def start(engine, db) -> bool:
    return await review.start_global_optimization(engine, db, runner=run_global_optimization, state=engine.review_job)


def job_state(engine) -> dict:
    return review.job_state(engine.review_job)
