"""AI advisor and automatic AI review for the FX bots (real trades and shadow trades only)."""
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
from review import ai_policy, ARCH_NOTE

logger = logging.getLogger("fx.review")
FX_GATE_KEYS = {"currency_exposure_limit", "rollover_guard", "vol_filter_enabled", "vol_min_pct", "vol_high_pct", "seasonality_bias", "seasonality_block_pairs",
                "seasonality_size_factor", "usd_regime_filter", "usd_regime_sma", "fomc_rule", "max_long_positions",
                "be_start_r", "trail_start_r", "trail_atr_mult", "partial_tp_enabled", "partial_tp_r", "partial_tp_pct"}
ALLOWED_AI_KEYS = review.ALLOWED_AI_KEYS | {"sessions_allowed", "ai_news_weight"} | FX_GATE_KEYS

async def build_context(engine, settings, acc: dict, with_backtest: bool = False) -> dict:
    pol = ai_policy(settings.risk_profile)
    bk = engine.profile.broker
    ctx = {"mercado": f"forex y metales ({bk}, paper trading)" if not engine.live else f"forex y metales ({bk}, cuenta REAL vía puente MetaTrader)",
           "settings_actuales": settings.model_dump(exclude={"bot_running"}), "cuenta": acc,
           "perfil_riesgo": {"nivel": pol["level"], "nombre": pol["name"], "topes_ia": pol["caps"]},
           "rendimiento_real": await performance(engine.db, fx_shadow_stats), "chequeo_plan": await engine.checkin(settings, acc),
           "sesiones": {"ahora": market_status()["sessions"], "permitidas": settings.sessions_allowed or "todas", "ids": SESSION_NAMES},
           "motivos_rechazo_recientes_ia": await recent_rejections(engine.db), "arquitectura": ARCH_NOTE}
    ctx["rendimiento_real"]["shadow"]["_explicacion"] = (
        f"Operaciones sombra: qué habría pasado si se hubieran ejecutado las señales no operadas, simuladas con lotes, spread {bk}, trailing y cierre de fin de semana. "
        f"Motivos: RECHAZADA / BAJA_CONFIANZA (filtro IA), BLOQUEADA (tope de posiciones, par abierto, límite diario, {bk} desconectado), NOTICIAS, "
        "SESION (fuera de las sesiones permitidas), ERROR (fallo de IA), NO_EJECUTADA (orden no abierta: lote mínimo, bróker). "
        "filter_value > 0 = el filtro evitó pérdidas netas; < 0 = dejó escapar ganancias. by_confidence calibra ai_min_confidence; "
        "by_session indica si conviene ampliar o recortar sessions_allowed; undersized = sombras simuladas con lote mínimo forzado (capital insuficiente). "
        "ai_by_news_risk = sombras de señales rechazadas por la IA según el riesgo general de mercado por noticias (ALTO/MEDIO/BAJO) en el momento del rechazo: "
        "pnl > 0 en ALTO con muestra suficiente = la IA rechaza señales rentables en días de alertas generales → ai_news_weight 'bajo'; pnl < 0 = el filtro acierta → 'normal'.")
    ctx["evolucion"] = await evolution.ai_context(engine)
    return ctx


def _apply(engine, res, before, settings, source: str = "optimize_all"):
    pol = ai_policy(settings.risk_profile)
    return review.apply_ai_changes(engine, res, before, settings, policy=pol, available=AVAILABLE, model=FxSettingsUpdate, allowed=ALLOWED_AI_KEYS, source=source)


async def advise_scope(engine, scope: str) -> dict:
    settings = await engine.get_settings()
    before = settings.model_dump(exclude={"bot_running"})
    acc = await engine.account_summary()
    ctx = await build_context(engine, settings, acc, scope == "plan")
    await engine.log("AI_EVAL", f"{ai_label()} analizando plan y rendimiento real...")
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
    await engine.tg(f"🧠 <b>Revisión IA automática</b>\n{res['summary']}\n\n"
                    + (f"<b>Cambios aplicados</b>\n{review.changes_text(applied)}" if applied
                       else ("<b>Sin cambios</b> en la configuración" if store.cfg.auto_review_apply else "<b>Modo solo aviso</b>: no se aplicó nada"))
                    + (("\n\n" + "\n".join(f"– {n}" for n in res["notes"])) if res["notes"] else "")
                    + review._footer(acc), "review")
    return {**res, "applied": applied, "previous": previous, "ran_at": store.cfg.auto_review_last_at}
