"""Signal funnel: stage 0 = deterministic pre-filter ($0), stage 1 = quick technical filter with a cheap model, stage 2 = the full risk-manager
evaluation that already existed (ai_advisor.evaluate_signal / fx.ai.evaluate_signal). Every stage logs `[STAGE n] model · ms`."""
import time
import json
import asyncio
import logging

from ai_advisor import _chat, _ask, _cfg, parse_json, label, classify, CONFIG_CODES

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


SL_BAND, TP_MAX = (0.6, 1.8), 2.5  # AI levels: stop within 0.6–1.8× the strategy stop distance, target at most 2.5× the strategy target distance


def apply_levels(sig: dict, sl, tp, min_rr: float = 1.0, spread: float = 0.0, digits: int = None):
    """Applies AI-proposed SL/TP to the signal inside safe bounds (right side of the entry, stop band, minimum R:R, target cap).
    Returns a short description of what changed, or None when the strategy defaults are kept."""
    d = 1 if sig["side"] == "long" else -1
    price, sl0, tp0 = sig["price"], sig["sl"], sig["tp"]
    risk0, reward0 = abs(price - sl0), abs(tp0 - price)
    if risk0 <= 0 or reward0 <= 0 or (sl is None and tp is None):
        return None
    risk = risk0
    if sl is not None and (price - sl) * d > 0:
        risk = min(max((price - sl) * d, SL_BAND[0] * risk0, 4 * spread), max(SL_BAND[1] * risk0, 4 * spread))
    lo, hi = max(min_rr, 1.0) * risk, TP_MAX * reward0
    reward = (tp - price) * d if tp is not None and (tp - price) * d > 0 else reward0
    reward = max(reward, lo) if lo <= hi else None
    if reward is None:
        return None
    reward = min(reward, hi)
    new_sl, new_tp = price - risk * d, price + reward * d
    if digits is not None:
        new_sl, new_tp = round(new_sl, digits), round(new_tp, digits)
    if abs(new_sl - sl0) < 1e-9 * price and abs(new_tp - tp0) < 1e-9 * price:
        return None
    sig.update(sl=new_sl, tp=new_tp, default_sl=sl0, default_tp=tp0)
    return f"SL {sl0:.6g} → {new_sl:.6g} · TP {tp0:.6g} → {new_tp:.6g} (R:R {reward / risk:.2f})"


RULE_MIN_RR = 1.5
MAJORS = ("BTCUSDT", "ETHUSDT", "EURUSD", "GBPUSD", "XAUUSD")


def high_conviction(sig: dict, d1_trend: str) -> bool:
    return (sig.get("score") or 0) >= 5 and d1_trend == ("ALCISTA" if sig["side"] == "long" else "BAJISTA")


def apply_rule_levels(sig: dict, sl, tp, spread: float = 0.0, digits: int = None):
    """Levels proposed by the rules check: only applied when the stop is not tighter than the strategy one and R:R stays >= 1.5; otherwise the strategy levels are kept."""
    d = 1 if sig["side"] == "long" else -1
    price, risk0 = sig["price"], abs(sig["price"] - sig["sl"])
    risk = (price - sl) * d if sl is not None else risk0
    reward = (tp - price) * d if tp is not None else abs(sig["tp"] - price)
    if risk < risk0 * (1 - 1e-9) or reward <= 0 or reward / risk < RULE_MIN_RR:
        return None
    return apply_levels(sig, sl, tp, RULE_MIN_RR, spread, digits)


RULES_SYSTEM = (
    "Eres el filtro de reglas de un bot de trading de {market}. Recibes una señal de entrada y una lista numerada de REGLAS "
    "aprendidas de operaciones pasadas. Tu ÚNICA tarea es decidir si la señal incumple claramente alguna regla con los datos recibidos. "
    "No juzgues si la señal es buena o mala por ningún otro motivo: si no incumple ninguna regla, pasa. Si los datos no permiten comprobar una regla, "
    "considérala cumplida. Una regla marcada «(solo SÍMBOLO)» únicamente aplica a ese par. "
    "Además, si la señal pasa y alguna regla habla de dónde colocar el stop o el objetivo (stop demasiado ajustado, detrás de un swing, objetivo antes de una "
    "resistencia, etc.) y con los datos recibidos procede aplicarla, propón el SL y/o TP ajustados como precios; en cualquier otro caso deja sl y tp en null "
    "y se usarán los niveles por defecto de la estrategia. "
    "ALTA CONVICCIÓN: cuando la señal viene marcada «ALTA CONVICCIÓN: SÍ» (5 o más confirmaciones y tendencia diaria 1D alineada con la dirección) y la regla "
    "que aplicaría solo advierte de un swing, soporte/resistencia o nivel cercano, NO rechaces la señal: responde violates=false y propón un tp y/o sl "
    "ajustado que respete esa estructura (objetivo antes del nivel, stop detrás del swing). "
    "PARES PRINCIPALES: cuando la señal viene marcada «PAR PRINCIPAL DE ALTA LIQUIDEZ: SÍ» (BTCUSDT, ETHUSDT, EURUSD, GBPUSD, XAUUSD), una regla genérica (sin «(solo SÍMBOLO)») "
    "aprendida en altcoins u otros pares más volátiles no la veta por pequeños roces de volumen (volumen algo por debajo o por encima de lo que pide la regla): solo la "
    "incumple si la diferencia es clara y amplia. "
    'Responde EXCLUSIVAMENTE con JSON válido, sin texto adicional: {{"violates":true|false,"rule":número de la regla incumplida (0 si ninguna),'
    '"reason":"máx. 25 palabras en español citando el dato concreto que incumple la regla, o por qué ajustas los niveles; vacío si pasa sin cambios",'
    '"sl":número o null,"tp":número o null}}'
)


RULES_RETRY_DELAYS = (2, 5)  # seconds before each quick retry of a failed rules check


async def rules_check_retry(*args, **kwargs) -> dict:
    """rules_check with quick retries on one-off failures; balance/auth/model errors are not retried."""
    for delay in (*RULES_RETRY_DELAYS, None):
        try:
            return await rules_check(*args, **kwargs)
        except Exception as e:
            if delay is None or classify(e)[0] in CONFIG_CODES:
                raise
            logger.warning(f"[RULES RETRY] {args[0]} · {(str(e).strip() or type(e).__name__)[:100]} · reintento en {delay}s")
            await asyncio.sleep(delay)


async def rules_check(symbol: str, timeframe: str, sig: dict, ctx: dict, rules: list, market: str = "criptomonedas (perpetuos USDT)") -> dict:
    """The only entry gate of the crypto bot: does the signal break a learned rule? Cheap model; raises on provider error/timeout."""
    cfg = fast_cfg()
    t0 = time.monotonic()
    chat = _chat(RULES_SYSTEM.format(market=market), "signal", cfg)
    chat._probe = False
    lines = "\n".join(f"{i}. {r['text']}" + (f" (solo {r['symbol']})" if r.get("symbol") else "") for i, r in enumerate(rules, 1))
    pa = ctx.get("price_action") or {}
    d1 = ctx.get("d1_trend") or pa.get("d1_trend") or "N/D"
    prompt = (
        f"SEÑAL: {sig['side'].upper()} {symbol} | {sig['strategy']} | TF {timeframe} | horizonte {ctx.get('horizon', 'short')} | R:R {rr(sig):.2f}\n"
        f"Entrada {sig['price']} · SL {sig['sl']} · TP {sig['tp']}\n"
        f"Tendencia 1D: {d1} | ALTA CONVICCIÓN: {'SÍ' if high_conviction(sig, d1) else 'NO'} | PAR PRINCIPAL DE ALTA LIQUIDEZ: {'SÍ' if symbol in MAJORS else 'NO'}\n"
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
