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


def pre_filter(sig: dict, min_rr: float, spread: float = None, max_spread_sl_pct: float = None) -> tuple:
    """Stage 0. Returns (block_reason | None, kind). Spread check only when a spread is known (FX static spec)."""
    r = rr(sig)
    if min_rr and r < float(min_rr):
        return f"R:R {r:.2f} menor al mínimo {float(min_rr):.2f}", "RR_BAJO"
    if spread and max_spread_sl_pct:
        pct = spread / max(abs(sig["price"] - sig["sl"]), 1e-12) * 100
        if pct > float(max_spread_sl_pct):
            return f"spread {pct:.0f} % de la distancia al stop (máx. {float(max_spread_sl_pct):.0f} %)", "SPREAD"
    return None, None


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


async def quick_filter(symbol: str, timeframe: str, sig: dict, ctx: dict, kind: str, model_override: str = "") -> dict:
    """Stage 1. Raises on provider error/timeout (caller rejects: fail-safe)."""
    cfg = fast_cfg(model_override)
    t0 = time.monotonic()
    chat = _chat(FAST_SYSTEM.get(kind, FAST_SYSTEM["fx"]) + FAST_FORMAT, f"{kind}-fast", cfg)
    chat._probe = False
    text = await asyncio.wait_for(_ask(chat, _fast_prompt(symbol, timeframe, sig, ctx)), FAST_TIMEOUT_SEC)
    data = parse_json(text, "filtro rápido")
    conf = int(max(0, min(100, int(data.get("confidence", 0) or 0))))
    passed = bool(data.get("pass_filter")) and conf >= FAST_MIN_CONFIDENCE
    out = {"pass": passed, "confidence": conf, "reason": str(data.get("discard_reason") or "")[:160], "model": label(cfg["model"]),
           "ms": int((time.monotonic() - t0) * 1000)}
    logger.info(f"[STAGE 1 {'PASS' if passed else 'REJECT'}] {symbol} {sig['side']} · {cfg['model']} · {out['ms']} ms · {conf}% {out['reason']}")
    return out


async def final_eval(evaluate, *args) -> dict:
    """Stage 2 wrapper: timeout + latency/model stamping around the existing evaluate_signal of each bot."""
    t0 = time.monotonic()
    res = await asyncio.wait_for(evaluate(*args), FINAL_TIMEOUT_SEC)
    res["ms"], res["model"] = int((time.monotonic() - t0) * 1000), label(_cfg["model"])
    logger.info(f"[STAGE 2 {'APPROVE' if res['decision'] == 'APROBADA' else 'REJECT'}] {args[0]} · {_cfg['model']} · {res['ms']} ms · {res['confidence']}%")
    return res
