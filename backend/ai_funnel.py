"""Signal funnel: stage 0 = deterministic pre-filter ($0), stage 1 = quick technical filter with a cheap model, stage 2 = the full risk-manager
evaluation that already existed (ai_advisor.evaluate_signal / fx.ai.evaluate_signal). Every stage logs `[STAGE n] model · ms`."""
import time
import json
import asyncio
import logging

from ai_advisor import _chat, _ask, _cfg, parse_json, label

logger = logging.getLogger("ai.funnel")

FAST_TIMEOUT_SEC = 20
FINAL_TIMEOUT_SEC = 60
FAST_MIN_CONFIDENCE = 65
FAST_MODELS = {("emergent", "anthropic"): "claude-haiku-4-5-20251001", ("emergent", "openai"): "gpt-5.4-mini",
               ("emergent", "gemini"): "gemini-2.5-flash", ("gemini", "gemini"): "gemini-2.5-flash-lite"}
FAST_MODEL_OPTIONS = {"anthropic": ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"], "openai": ["gpt-5.4-mini", "gpt-5-mini"],
                      "gemini": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.8-flash"]}

FAST_SYSTEM = {
    "crypto": ("Eres un filtro técnico rápido para un bot de criptomonedas (perpetuos USDT, mercado 24/7, movimientos direccionales rápidos). "
               "Solo compruebas si la tendencia inmediata, el momentum y los indicadores básicos (EMAs, RSI, ATR, volumen) están alineados con la "
               "dirección de la señal y descartas el ruido evidente (entrada tardía con RSI extremo, señal contra la tendencia del TF superior, "
               "movimiento ya extendido). No evalúas riesgo ni noticias: eso lo hace otro modelo después. "),
    "fx": ("Eres un filtro técnico rápido para un bot de forex y metales (CFD, sesiones Londres/Nueva York/Asia). "
           "Solo compruebas si la tendencia inmediata, el momentum y los indicadores básicos (EMAs, RSI, ATR) están alineados con la dirección de la "
           "señal y descartas el ruido evidente (entrada tardía con RSI extremo, señal contra la tendencia del TF superior, stop demasiado estrecho "
           "frente al spread, movimiento ya extendido). No evalúas riesgo ni noticias: eso lo hace otro modelo después. "),
}
FAST_FORMAT = ('Responde EXCLUSIVAMENTE con JSON válido, sin texto adicional: {"pass_filter":true|false,"confidence":entero 1-100,'
               '"discard_reason":"máx. 12 palabras en español, vacío si pasa"}')


_override = {"model": ""}


def configure(fast_model: str = ""):
    """Called by the global config store: user-chosen cheap model ('' = automatic by provider)."""
    _override["model"] = fast_model or ""


def fast_cfg(override: str = "") -> dict:
    """Runtime config for the cheap model: same key/provider as the main model, cheaper model (configurable override)."""
    override = override or _override["model"]
    model = override or FAST_MODELS.get((_cfg["source"], _cfg["provider"]), _cfg["model"])
    return {**_cfg, "model": model}


def rr(sig: dict) -> float:
    return abs(sig["tp"] - sig["price"]) / max(abs(sig["price"] - sig["sl"]), 1e-12)


def _fast_prompt(symbol: str, timeframe: str, sig: dict, ctx: dict) -> str:
    pa = ctx.get("price_action") or {}
    return (
        f"SEÑAL: {sig['side'].upper()} {symbol} | {sig['strategy']} | TF {timeframe} | R:R {rr(sig):.2f}\n"
        f"Confirmaciones ({sig['score']}): {'; '.join(sig['reasons'])}\n"
        f"Indicadores: {json.dumps(sig['indicators'])}\n"
        f"Tendencia TF superior ({ctx.get('htf_tf')}): {ctx.get('htf_trend')} | cambio 24h {ctx.get('change_24h', 0):.2f}%\n"
        f"Últimas velas (% por vela): {ctx.get('recent_changes')}\n"
        + (f"Acción del precio: {' '.join(pa.get('summary') or [])}\n" if pa.get("summary") else "")
        + "¿Pasa el filtro técnico?"
    )


SL_BAND, TP_MAX = (0.6, 1.8), 2.5  # AI levels: stop within 0.6–1.8× the strategy stop distance, target at most 2.5× the strategy target distance


RULES_SYSTEM = (
    "Eres el filtro de reglas de un bot de trading de {market}. Recibes una señal de entrada y una lista numerada de REGLAS "
    "aprendidas de operaciones pasadas. Tu ÚNICA tarea es decidir si la señal incumple claramente alguna regla con los datos recibidos. "
    "No juzgues si la señal es buena o mala por ningún otro motivo: si no incumple ninguna regla, pasa. Si los datos no permiten comprobar una regla, "
    "considérala cumplida. Una regla marcada «(solo SÍMBOLO)» únicamente aplica a ese par. "
    "Además, si la señal pasa y alguna regla habla de dónde colocar el stop o el objetivo (stop demasiado ajustado, detrás de un swing, objetivo antes de una "
    "resistencia, etc.) y con los datos recibidos procede aplicarla, propón el SL y/o TP ajustados como precios; en cualquier otro caso deja sl y tp en null "
    "y se usarán los niveles por defecto de la estrategia. "
    'Responde EXCLUSIVAMENTE con JSON válido, sin texto adicional: {{"violates":true|false,"rule":número de la regla incumplida (0 si ninguna),'
    '"reason":"máx. 25 palabras en español citando el dato concreto que incumple la regla, o por qué ajustas los niveles; vacío si pasa sin cambios",'
    '"sl":número o null,"tp":número o null}}'
)


async def rules_check(symbol: str, timeframe: str, sig: dict, ctx: dict, rules: list, market: str = "criptomonedas (perpetuos USDT)") -> dict:
    """The only entry gate of the crypto bot: does the signal break a learned rule? Cheap model; raises on provider error/timeout."""
    cfg = fast_cfg()
    t0 = time.monotonic()
    chat = _chat(RULES_SYSTEM.format(market=market), "signal", cfg)
    chat._probe = False
    lines = "\n".join(f"{i}. {r['text']}" + (f" (solo {r['symbol']})" if r.get("symbol") else "") for i, r in enumerate(rules, 1))
    pa = ctx.get("price_action") or {}
    prompt = (
        f"SEÑAL: {sig['side'].upper()} {symbol} | {sig['strategy']} | TF {timeframe} | horizonte {ctx.get('horizon', 'short')} | R:R {rr(sig):.2f}\n"
        f"Entrada {sig['price']} · SL {sig['sl']} · TP {sig['tp']}\n"
        f"Confirmaciones ({sig['score']}): {'; '.join(sig['reasons'])}\n"
        f"Indicadores: {json.dumps(sig['indicators'])}\n"
        f"Tendencia TF superior ({ctx.get('htf_tf')}): {ctx.get('htf_trend')} | cambio 24h {ctx.get('change_24h') or 0:.2f}% | funding {ctx.get('funding_rate')}\n"
        + (f"Sesiones abiertas: {ctx.get('sessions')} | stop {ctx.get('sl_pips')} pips · spread {ctx.get('spread_pips')} pips\n" if ctx.get("sl_pips") else "")
        + f"Hora UTC: {time.strftime('%A %H:%M', time.gmtime())} | posiciones abiertas {ctx.get('open_positions')} | PnL del día {ctx.get('daily_pnl_pct')}%\n"
        + (f"Últimas velas (% por vela): {ctx.get('recent_changes')}\n" if ctx.get("recent_changes") else "")
        + (f"Acción del precio: {' '.join(pa.get('summary') or [])} · datos: {json.dumps({k: v for k, v in pa.items() if k != 'summary'})}\n" if pa else "")
        + f"REGLAS:\n{lines}\n¿Incumple alguna?"
    )
    data = parse_json(await asyncio.wait_for(_ask(chat, prompt), FAST_TIMEOUT_SEC), "filtro de reglas")
    try:
        n = int(data.get("rule") or 0)
    except (TypeError, ValueError):
        n = 0
    violates = bool(data.get("violates")) and 1 <= n <= len(rules)
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None  # noqa: E731
    out = {"violates": violates, "rule": rules[n - 1] if violates else None, "reason": str(data.get("reason") or "")[:200],
           "sl": None if violates else num(data.get("sl")), "tp": None if violates else num(data.get("tp")),
           "model": label(cfg["model"]), "ms": int((time.monotonic() - t0) * 1000)}
    logger.info(f"[RULES {'BLOCK' if violates else 'PASS'}] {symbol} {sig['side']} · {cfg['model']} · {out['ms']} ms · {len(rules)} reglas · {out['reason']}")
    return out
