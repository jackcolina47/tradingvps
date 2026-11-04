import os
import re
import json
import uuid
import logging
from datetime import datetime, timezone
from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
from llm_json import parse_llm_json
import funding

logger = logging.getLogger("ai")
_cfg = {"api_key": os.environ.get("EMERGENT_LLM_KEY", ""), "provider": "anthropic", "model": "claude-sonnet-5-5", "source": "emergent"}
EMERGENT_MODELS = {
    "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-sonnet-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-5.6-terra", "gpt-6-luna", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5-mini"],
    "gemini": ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
}
GEMINI_MODELS = ["gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
_FAMILY = {"claude": "Claude", "gpt": "GPT", "gemini": "Gemini"}
ORIGIN_LABELS = {"config": "guardada en Ajustes", "env": "variable del servidor", "typed": "escrita en el formulario", "gemini": "tu clave de Google", "none": "ninguna"}


def key_hint(api_key: str) -> str:
    return f"••••{api_key[-4:]}" if api_key and len(api_key) > 8 else ("••••" if api_key else "sin clave")


def configure(api_key: str, provider: str, model: str, source: str = "emergent", origin: str = ""):
    changed = (api_key, provider, model) != (_cfg["api_key"], _cfg["provider"], _cfg["model"])
    _cfg.update(api_key=api_key, provider=provider, model=model, source=source, origin=origin)
    if changed:
        logger.info(f"IA configurada: {label(model)} · {provider} · clave {key_hint(api_key)}" + (f" ({ORIGIN_LABELS.get(origin, origin)})" if origin else ""))
        # New key/model → assume healthy again, but still announce the recovery when the next call succeeds.
        pending = bool(_health["alerted_at"] or _health["recovery_pending"])
        _health.update(_fresh_health(), recovery_pending=pending, since=_health["since"] if pending else None)


def label(model: str = None) -> str:
    out = []
    for p in re.sub(r"-(preview|\d{8})$", "", model or _cfg["model"]).split("-"):
        if out and re.fullmatch(r"\d+", p) and re.fullmatch(r"\d+(\.\d+)?", out[-1]):
            out[-1] += f".{p}"
        else:
            out.append(_FAMILY.get(p.lower(), p.capitalize()))
    return " ".join(out)


def current(cfg: dict = None) -> dict:
    c = cfg or _cfg
    origin = c.get("origin", "")
    return {"provider": c["provider"], "model": c["model"], "source": c["source"], "label": label(c["model"]), "health": health(),
            "key_hint": key_hint(c["api_key"]), "key_origin": origin, "key_origin_label": ORIGIN_LABELS.get(origin, origin)}


# ---------- health: detects auth / balance / quota / model / network failures of the active provider ----------
CONFIG_CODES = ("auth", "balance", "model")  # won't heal by themselves → alert on the first failure
TRANSIENT_FAILS = 3  # consecutive failures before alerting for rate/network/other errors
TRANSIENT_MINUTES = 15  # …or this long without a single successful call
CODE_LABELS = {"auth": "API key inválida", "balance": "saldo o cuota agotados", "model": "modelo no disponible", "rate": "límite de peticiones", "network": "sin conexión con el proveedor", "other": "error del proveedor"}
_health_hook = None


def _fresh_health() -> dict:
    return {"ok": True, "code": None, "error": None, "since": None, "fails": 0, "alerted_at": None, "last_ok": None, "last_error_at": None, "recovery_pending": False}


_health = _fresh_health()


def set_health_hook(fn):
    """fn(event: 'down'|'up', health: dict) async — Telegram/log notifications live outside this module."""
    global _health_hook
    _health_hook = fn


def health() -> dict:
    return {**_health, "label": CODE_LABELS.get(_health["code"]) if _health["code"] else None}


def classify(e: Exception) -> tuple:
    low = f"{type(e).__name__} {e}".lower()
    if any(k in low for k in ("api_key_invalid", "api key not valid", "authenticationerror", "invalid x-api-key", "incorrect api key", "permission_denied", "unauthorized", " 401", "invalid api key", "invalid_api_key", "invalid credentials", "insufficient permissions")):
        return "auth", "API key inválida o sin permisos: revisa la clave en Ajustes → Inteligencia artificial"
    if any(k in low for k in ("insufficient", "credit balance", "credits", "budget", " 402", "payment required", "exceeded your current quota", "insufficient_quota", "billing", "out of tokens", "no tokens", "balance")):
        return "balance", "Saldo insuficiente o cuota agotada: recarga el saldo de la clave (Emergent: Perfil → Manage plan → Universal Key → Add Balance) o revisa la facturación del proveedor"
    if any(k in low for k in ("resource_exhausted", "ratelimit", "rate limit", "rate_limit", "quota", " 429", "too many requests")):
        return "rate", "Cuota o límite de peticiones agotado (429): espera unos minutos o revisa el plan y la facturación del proveedor"
    if any(k in low for k in ("notfound", "not found", "does not exist", "unknown model", "model_not_found")):
        return "model", "Modelo no encontrado: comprueba el nombre del modelo en Ajustes → Inteligencia artificial"
    if any(k in low for k in ("timeout", "timed out", "connecterror", "connection", "service unavailable", "503", "502", "overloaded", "apiconnectionerror")):
        return "network", "El proveedor de IA no responde (red, timeout o servicio saturado): se reintentará con la siguiente señal"
    return "other", str(e)[:200]


def friendly_error(e: Exception) -> str:
    return classify(e)[1]


async def _mark_failure(e: Exception):
    code, msg = classify(e)
    now = _now_iso()
    first = _health["ok"]
    _health.update(ok=False, code=code, error=msg, fails=_health["fails"] + 1, last_error_at=now, since=_health["since"] or now)
    if first:
        logger.error(f"IA sin servicio ({code}): {msg}")
    down_min = _minutes_since(_health["since"])
    should_alert = _health["alerted_at"] is None and (code in CONFIG_CODES or _health["fails"] >= TRANSIENT_FAILS or down_min >= TRANSIENT_MINUTES)
    if should_alert and _health_hook:
        _health["alerted_at"] = now
        try:
            await _health_hook("down", health())
        except Exception as ex:
            logger.warning(f"ai health hook: {ex}")


async def _mark_ok():
    was_down, announce, since = not _health["ok"], _health["alerted_at"] or _health["recovery_pending"], _health["since"]
    _health.update(_fresh_health(), last_ok=_now_iso())
    if was_down:
        logger.info("IA operativa de nuevo")
    if announce and _health_hook:
        try:
            await _health_hook("up", {**health(), "down_since": since})
        except Exception as ex:
            logger.warning(f"ai health hook: {ex}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _minutes_since(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60 if iso else 0.0


def _chat(system: str, prefix: str, cfg: dict = None) -> LlmChat:
    c = cfg or _cfg
    chat = LlmChat(api_key=c["api_key"], session_id=f"{prefix}-{uuid.uuid4()}", system_message=system).with_model(c["provider"], c["model"])
    chat._kind = prefix
    chat._probe = cfg is not None and (cfg["api_key"], cfg["provider"], cfg["model"]) != (_cfg["api_key"], _cfg["provider"], _cfg["model"])
    return chat


_usage_hook = None


def set_usage_hook(fn):
    """fn(kind: str, skipped: bool) async — called after every LLM call (and for skipped news analyses)."""
    global _usage_hook
    _usage_hook = fn


async def record_usage(kind: str, skipped: bool = False):
    if _usage_hook:
        try:
            await _usage_hook(kind, skipped)
        except Exception as e:
            logger.warning(f"ai usage: {e}")


async def _ask(chat: LlmChat, prompt: str) -> str:
    text, done = "", None
    probe = getattr(chat, "_probe", False)
    try:
        async for ev in chat.stream_message(UserMessage(text=prompt)):
            if isinstance(ev, TextDelta):
                text += ev.content
            elif isinstance(ev, StreamDone):
                done = ev
                break
    except Exception as e:
        if not probe:
            await _mark_failure(e)
        raise
    if not probe:
        await _mark_ok()
    await record_usage(getattr(chat, "_kind", "other"))
    if done is not None and done.finish_reason == "length":
        logger.warning(f"LLM {getattr(chat, '_kind', '?')}: respuesta cortada por el límite de tokens de salida "
                       f"({done.usage.output_tokens} tokens, {len(text)} caracteres de texto)")
    elif done is None:
        logger.warning(f"LLM {getattr(chat, '_kind', '?')}: el stream terminó sin cierre ({len(text)} caracteres recibidos)")
    return text


def parse_json(text: str, what: str = "IA") -> dict:
    """Tolerant JSON extraction; the full invalid answer goes to the server log (the panel only shows the first characters)."""
    data = parse_llm_json(text)
    if data is None:
        logger.warning(f"{what}: respuesta no válida ({len(text or '')} caracteres): {text!r}")
        raise ValueError(f"Respuesta IA no válida: {(text or '')[:200]}")
    return data


async def ping(cfg: dict = None) -> str:
    text = await _ask(_chat("Responde en español con una sola frase breve.", "ping", cfg), "Confirma que estás operativo para analizar señales de trading.")
    return text.strip()[:200]


def tool_chat(system: str, prefix: str, history: list, tools: list = None) -> LlmChat:
    """Multi-turn chat seeded with stored history; tools (OpenAI function schema) are optional."""
    c = _cfg
    chat = LlmChat(api_key=c["api_key"], session_id=f"{prefix}-{uuid.uuid4()}", system_message=system,
                   initial_messages=[{"role": "system", "content": system}, *history]).with_model(c["provider"], c["model"])
    chat._kind, chat._probe = prefix, False
    if tools:
        chat.with_tools(tools, tool_choice="auto")
    return chat


def _as_dict(obj):
    if obj is None or isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj) if hasattr(obj, "keys") else None


def _normalize_blocks(blocks):
    """litellm 1.80 only drops its own (unsigned) copy of a function call when the signed part matches it exactly; Gemini 3.1 Pro adds an `id` inside
    functionCall that breaks that match and leaves an unsigned duplicate → 400 «missing thought_signature». Strip the id so the parts match."""
    out = []
    for b in blocks:
        try:
            part = json.loads(b.get("thinking") or "")
            fc = part.get("functionCall") if isinstance(part, dict) else None
            if isinstance(fc, dict) and "id" in fc:
                b = {**b, "thinking": json.dumps({**part, "functionCall": {k: v for k, v in fc.items() if k != "id"}})}
        except Exception:
            pass
        out.append(b)
    return out


def _keep_thought_signatures(chat: LlmChat, resp) -> None:
    """Gemini 3 rejects function calls echoed back without their thought_signature. LlmChat rebuilds the assistant turn with bare tool_calls, so copy
    everything litellm uses to restore the signature onto the stored message: `thinking_blocks` (litellm ≤1.80) and the per-tool-call / per-message
    `provider_specific_fields` (newer litellm). No-op for providers without signatures."""
    try:
        msg = resp.raw.choices[0].message
        last = chat.messages[-1] if chat.messages else None
        if not (last and last.get("role") == "assistant" and last.get("tool_calls")):
            return
        blocks = getattr(msg, "thinking_blocks", None)
        if blocks:
            last["thinking_blocks"] = _normalize_blocks(blocks)
        msg_psf = _as_dict(getattr(msg, "provider_specific_fields", None))
        if msg_psf:
            last["provider_specific_fields"] = msg_psf
        raw_calls = {tc.id: tc for tc in (getattr(msg, "tool_calls", None) or []) if getattr(tc, "id", None)}
        for stored in last["tool_calls"]:
            psf = _as_dict(getattr(raw_calls.get(stored.get("id")), "provider_specific_fields", None))
            if psf:
                stored["provider_specific_fields"] = psf
    except Exception as e:  # history shape is internal to the library: never break the turn over it
        logger.debug(f"thought_signature: {e}")


async def ask_tools(chat: LlmChat, text: str | None):
    """One non-streaming turn; `text=None` continues after add_tool_result(). Returns ChatResponse (content, tool_calls)."""
    try:
        resp = await chat.send_message_with_tools(UserMessage(text=text) if text else None)
    except Exception as e:
        await _mark_failure(e)
        raise
    _keep_thought_signatures(chat, resp)
    await _mark_ok()
    await record_usage(getattr(chat, "_kind", "other"))
    if resp.finish_reason == "length":
        logger.warning(f"LLM {getattr(chat, '_kind', '?')}: respuesta cortada por el límite de tokens de salida")
    return resp


async def ask_text(chat: LlmChat, prompt: str) -> str:
    return await _ask(chat, prompt)

SYSTEM = (
    "Eres un gestor de riesgo cuantitativo senior especializado en futuros perpetuos USDT de criptomonedas. "
    "Evalúas señales generadas por un sistema algorítmico y decides si se ejecutan. Prioriza preservar capital: "
    "rechaza señales con indicadores contradictorios, entradas tardías (RSI extremo a favor), contra la tendencia mayor, "
    "con volatilidad anormal o funding rate extremo en contra. Funding: si la señal va EN CONTRA del funding y el nivel es ALTO baja la confianza "
    "de forma clara (el lado está sobrecargado y la posición paga por mantenerse); si es EXTREMO en contra, rechaza salvo confluencia excepcional. "
    "Un funding A FAVOR es un pequeño punto positivo, nunca suficiente por sí solo para aprobar. "
    "El nivel del funding ya está normalizado a 8h: en pares que liquidan cada 4h o 1h el valor por periodo parece menor pero se cobra más veces. "
    "Si se incluye contexto de noticias, úsalo como factor de riesgo: "
    "una noticia grave sobre el par o un evento macro inminente justifican rechazar o bajar la confianza; un sentimiento favorable no basta por sí solo para aprobar. "
    "Aprueba solo setups con confluencia clara. "
    "Responde EXCLUSIVAMENTE con un objeto JSON válido, sin texto adicional, con este formato: "
    '{"decision":"APROBAR" o "RECHAZAR","confidence":entero 0-100,"risk":"BAJO"|"MEDIO"|"ALTO","reasoning":"máximo 3 frases en español"}'
)


def _news_lines(n) -> str:
    if not n:
        return ""
    m = n["mercado"]
    out = [f"Noticias (hace {n['actualizado_hace_min']} min): sentimiento mercado {m['sentiment']:+d} · riesgo {m['risk']} · {m['summary']}"]
    if n.get("par"):
        out.append(f"Noticias del par: sentimiento {n['par']['sentiment']:+d} · riesgo {n['par']['risk']} · {n['par']['note']}")
    if n.get("alertas"):
        out.append("Alertas: " + " | ".join(n["alertas"]))
    if n.get("eventos_proximos"):
        out.append("Eventos macro próximos: " + " | ".join(n["eventos_proximos"]))
    if n.get("titulares_del_par"):
        out.append("Titulares del par: " + " | ".join(n["titulares_del_par"]))
    return "\n".join(out) + "\n"


def _layers_line(ctx) -> str:
    parts = []
    if ctx.get("horizon") == "long":
        parts.append("Operación de LARGO PLAZO (estrategia diaria/4h: SL y TP amplios, puede durar días o semanas)")
    rg = ctx.get("btc_regime")
    if rg and rg.get("state") in ("ALCISTA", "BAJISTA"):
        parts.append(f"Régimen BTC diario: {rg['state']} (SMA{rg['sma']} {rg['distance_pct']:+.1f}%)")
    if ctx.get("rotation_bias"):
        parts.append(f"Sesgo de rotación semanal del par: {ctx['rotation_bias']}")
    return (" | ".join(parts) + "\n") if parts else ""


def _build_prompt(symbol, timeframe, sig, ctx) -> str:
    return (
        f"SEÑAL: {sig['side'].upper()} {symbol} | Estrategia: {sig['strategy']} | TF: {timeframe}\n"
        f"Entrada: {sig['price']} | SL: {sig['sl']:.6g} | TP: {sig['tp']:.6g} | R:R {abs(sig['tp']-sig['price'])/max(abs(sig['price']-sig['sl']),1e-12):.2f}\n"
        f"Confirmaciones ({sig['score']}): {'; '.join(sig['reasons'])}\n"
        f"Indicadores: {json.dumps(sig['indicators'])}\n"
        f"Tendencia TF superior ({ctx.get('htf_tf')}): {ctx.get('htf_trend')}\n"
        + _layers_line(ctx) +
        f"Cambio 24h: {ctx.get('change_24h', 0):.2f}% | {funding.prompt_line(ctx.get('funding'))}\n"
        f"Últimos cierres (% cambio por vela, más reciente al final): {ctx.get('recent_changes')}\n"
        f"Cuenta: {ctx.get('open_positions')} posiciones abiertas, PnL diario {ctx.get('daily_pnl_pct', 0):.2f}%\n"
        + _news_lines(ctx.get("news")) +
        "¿Ejecutar esta operación?"
    )


async def evaluate_signal(symbol: str, timeframe: str, sig: dict, ctx: dict) -> dict:
    text = await _ask(_chat(SYSTEM, "signal"), _build_prompt(symbol, timeframe, sig, ctx))
    data = parse_json(text, "evaluación de señal")
    decision = str(data.get("decision", "RECHAZAR")).upper()
    return {
        "decision": "APROBADA" if decision.startswith("APROB") else "RECHAZADA",
        "confidence": int(max(0, min(100, int(data.get("confidence", 0))))),
        "risk": str(data.get("risk", "MEDIO")).upper(),
        "reasoning": str(data.get("reasoning", ""))[:600],
    }


DEFAULT_POLICY = {"caps": {"risk_per_trade": 1.5, "leverage": 5, "max_positions": 5}, "min_trades": 30, "min_item_trades": 20, "level": 0, "name": "Conservador"}
STRATEGY_KEYS_TXT = "trend_ema, mean_reversion, breakout_turtle, supertrend_momentum, ema_pullback, ema_cross (Cruce EMA 10/20, apagada por defecto: salida al cruce contrario)"
CRYPTO_MARKET = {
    "desc": "un bot cuantitativo de futuros perpetuos USDT en modo paper trading", "lev": lambda v: f"{v}x",
    "strategy_keys": STRATEGY_KEYS_TXT + ", multi_horizon (Tendencia Multi-Horizonte, diaria, largo plazo), double_divergence (Divergencia doble RSI+MACD, 4h, largo plazo)",
    "extra_rule": ("(7) Capas nuevas con evidencia académica a favor; mantenlas salvo prueba real en contra: btc_regime_filter (solo largos con BTC sobre su SMA diaria y "
                   "solo cortos debajo; btc_regime_sma 50 o 200) → desactívalo o cambia la SMA solo si rendimiento_real.shadow.by_kind REGIMEN tiene ≥ 30 sombras con pnl claramente "
                   "positivo (el filtro deja escapar ganancias); rotation_enabled (rotación semanal por momentum ponderado por volumen) → desactívala solo con ≥ 30 sombras ROTACION "
                   "con pnl positivo; entry_order_type ('maker' = orden límite post-only al 0,02 %, 'market' = 0,055 %) → vuelve a 'market' solo si entradas_maker.fill_rate < 40 % y "
                   "by_entry_type muestra que las entradas a mercado tras esperar (market_fallback) pierden claramente frente a las maker; max_long_positions (tope propio de las "
                   "operaciones de largo plazo, 1-6) → ajústalo solo con ≥ 10 operaciones de largo plazo cerradas (largo_plazo.by_horizon). Las estrategias de largo plazo se apagan "
                   "con disabled_strategies como las demás y requieren la misma evidencia real (≥ min_item_trades operaciones). "
                   "(8) Gestión de salidas (gestion_salidas): be_start_r (R al que el stop pasa a breakeven, 0,5-3), trail_start_r (R desde el que el stop sigue al precio, "
                   "0,5-3, ≥ be_start_r), trail_atr_mult (distancia del trailing en ATR, 0,5-3), partial_tp_enabled / partial_tp_r (R del cierre parcial, 0,5-3) / partial_tp_pct "
                   "(% cerrado, 25-75). Ajústalos SOLO con ≥ 20 operaciones con recorrido registrado (gestion_salidas.excursion.tracked) y según la evidencia: si near_miss_pct es alto "
                   "(muchas salidas por trailing/SL que alcanzaron ≥ 70 % del TP) acerca el cierre parcial (baja partial_tp_r) o aprieta el trailing (baja trail_atr_mult) o reduce el TP "
                   "vía symbol_params; si las salidas por TRAILING tienen avg_mfe_r alto pero pnl bajo el trailing va demasiado lejos; si avg_mae_r de las ganadoras es alto, el breakeven "
                   "llega demasiado pronto (sube be_start_r). Un cambio de ±0,25 R / ±0,25 ATR / ±10 % por revisión como máximo. "),
    "extra_keys": (", btc_regime_filter (bool), btc_regime_sma (50|200), rotation_enabled (bool), entry_order_type ('market'|'maker'), max_long_positions (1-6), "
                   "be_start_r, trail_start_r, trail_atr_mult, partial_tp_enabled (bool), partial_tp_r, partial_tp_pct"),
}
FX_MARKET = {
    "desc": "un bot cuantitativo de forex y metales (cuenta HFM, lotes de 0.01, spread como único coste, horario forex con sesiones Sídney/Tokio/Londres/Nueva York) en modo paper trading",
    "lev": lambda v: f"1:{v}",
    "extra_rule": ("(6) sessions_allowed es la lista de sesiones en las que el bot puede ABRIR posiciones (ids válidos: sydney, tokyo, london, newyork; "
                   "lista vacía = todas). Solo restringe sesiones con evidencia: session_stats del backtest con ≥ 15 operaciones por sesión descartada "
                   "o rendimiento_real; Londres y Nueva York concentran la liquidez y suelen ser las mejores. "
                   "(7) Calibra el filtro IA SOLO con las operaciones sombra (rendimiento_real.shadow): cambia ai_min_confidence únicamente con ≥ 30 sombras "
                   "RECHAZADA/BAJA_CONFIANZA y según by_confidence (sube el mínimo si los tramos bajos pierden, bájalo si los tramos justo por debajo del mínimo ganan de forma consistente); "
                   "ai_news_weight ('normal' o 'bajo') regula cuánto pesan las alertas generales de mercado en el veredicto por señal: ponlo en 'bajo' solo si ai_by_news_risk "
                   "muestra que las señales rechazadas con riesgo ALTO habrían ganado (pnl > 0) con ≥ 20 sombras, y devuélvelo a 'normal' si con ≥ 20 sombras pierden. "
                   "Sin esa muestra, no toques el filtro IA y explica en notes cuántas sombras faltan. "
                   "(8) Filtros FX (filtros_fx) ajustables con evidencia de sombras del motivo correspondiente (≥ 20 sombras: filter_value < 0 → relajar, > 0 → mantener/endurecer): "
                   "currency_exposure_limit (1-6), rollover_guard (bool), vol_filter_enabled (bool) / vol_min_pct (0-50) / vol_high_pct (60-100), seasonality_bias (bool) / "
                   "seasonality_block_pairs (lista de pares) / seasonality_size_factor (0,25-1), usd_regime_filter (bool) / usd_regime_sma (50|200), fomc_rule (bool), "
                   "max_long_positions (1-6; la estrategia fix_reversion se apaga/enciende con disabled_strategies como las demás). "
                   "(9) Gestión de salidas (filtros_fx.gestion_salidas): be_start_r, trail_start_r (≥ be_start_r), trail_atr_mult (0,5-3), partial_tp_enabled, partial_tp_r (0,5-3), "
                   "partial_tp_pct (25-75). Solo con ≥ 20 operaciones con recorrido (excursion.tracked): near_miss_pct alto → acercar el parcial o apretar el trailing; "
                   "TRAILING con mfe alto y pnl bajo → trailing demasiado lejos; mae alto en ganadoras → breakeven demasiado pronto. Pasos máximos ±0,25 R / ±0,25 ATR / ±10 % por revisión. "),
    "extra_keys": (", sessions_allowed (lista de ids), ai_news_weight ('normal'|'bajo'), currency_exposure_limit, rollover_guard, vol_filter_enabled, vol_min_pct, vol_high_pct, "
                   "seasonality_bias, seasonality_block_pairs, seasonality_size_factor, usd_regime_filter, usd_regime_sma, fomc_rule, max_long_positions, "
                   "be_start_r, trail_start_r, trail_atr_mult, partial_tp_enabled, partial_tp_r, partial_tp_pct"),
    "strategy_keys": STRATEGY_KEYS_TXT,
}


def advisor_system(policy: dict, market: dict = CRYPTO_MARKET) -> str:
    caps, lev = policy["caps"], market["lev"]
    MIN_TRADES_EVOLUTION = 5  # mirrors evolution.MIN_TRADES (kept local to avoid a circular import)
    return (
        f"Eres el director de inversiones de {market['desc']}. "
        "Recibes métricas reales y de backtest y debes DECIDIR cambios concretos de configuración para mejorar la expectativa "
        f"y proteger el capital. El usuario eligió el perfil de riesgo '{policy['name']}' (nivel {policy['level']} de 4): respeta ese nivel de actividad, "
        "no lo vuelvas más conservador ni más agresivo de lo que el usuario pidió; tu trabajo es podar lo que pierde y reforzar lo que gana dentro de ese perfil. "
        f"Reglas estrictas: (1) los cambios estructurales (quitar pares, apagar estrategias, bajar o subir el riesgo, máximo de posiciones, apalancamiento) "
        f"requieren evidencia REAL del bot: al menos {policy['min_trades']} operaciones cerradas en rendimiento_real y ≥ {policy['min_item_trades']} por par o "
        "estrategia afectada; el backtest por sí solo NO justifica esos cambios, solo recomendaciones en notes. (2) El backtest y el optimizador sí "
        "justifican aplicar symbol_params (SL/TP/ATR) cuando el óptimo robusto supera claramente a la línea base con ≥ 20 operaciones Y la validación "
        "walk_forward del par (si existe) es ROBUSTO o DUDOSO; si es SOBREAJUSTADO o NO_RENTABLE no apliques symbol_params en ese par. "
        "symbol_params es el diccionario COMPLETO que debe quedar activo (omite un par para devolverlo a los valores por defecto). "
        f"(3) Nunca subas el riesgo por operación por encima de {caps['risk_per_trade']}% ni el apalancamiento por encima de {lev(caps['leverage'])} ni el máximo de "
        f"posiciones por encima de {caps['max_positions']}; mantén al menos 2 pares activos y al menos 1 estrategia activa. "
        f"(4) Para apagar una estrategia usa disabled_strategies (lista completa de claves apagadas; claves válidas: {market.get('strategy_keys', STRATEGY_KEYS_TXT)}) y deja strategy en 'all'. "
        "(5) Si no hay evidencia suficiente, changes debe ser {} y explica en notes qué esperar antes de cambiar. "
        f"(6) Revisa evolucion (historial de cambios con su rendimiento posterior): si un cambio tiene veredicto PEOR con al menos {MIN_TRADES_EVOLUTION} operaciones "
        "después y el empeoramiento no se explica por el mercado, deshazlo devolviendo su id en revert y justifícalo en summary; nunca deshagas cambios PENDIENTE "
        "ni ya deshechos, y no deshagas un cambio solo para proponer lo contrario en changes. "
        + market["extra_rule"] +
        "Redacta summary y notes para el usuario final: frases completas y breves (máximo 2 por nota), en español claro y sin claves internas "
        "(di «SL/TP optimizados» en vez de symbol_params, «sesiones permitidas» en vez de sessions_allowed, «sobreajustado» en vez de SOBREAJUSTADO; "
        "nombra las estrategias como Tendencia EMA, Reversión a la media, Turtle, Supertrend, Pullback EMA21, Tendencia Multi-Horizonte y Divergencia doble en vez de sus claves). "
        "Responde EXCLUSIVAMENTE con un objeto JSON válido con este formato: "
        '{"summary":"2-4 frases en español explicando el diagnóstico","changes":{<claves opcionales: symbols (lista), disabled_strategies (lista), risk_per_trade, '
        f'max_positions, leverage, ai_min_confidence, trailing_enabled, htf_filter, symbol_params (dict símbolo→{{sl_atr,tp_atr,atr_period}}){market["extra_keys"]}>}},'
        '"revert":[<ids de cambios de evolucion a deshacer, normalmente vacío>],'
        '"notes":["recomendaciones adicionales en español que no son cambios de configuración"]}'
    )


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


async def advise(scope: str, context: dict, policy: dict = None, market: dict = CRYPTO_MARKET, kind: str = None) -> dict:
    prompt = f"ÁMBITO: {scope}\nCONTEXTO (JSON):\n{json.dumps(context, ensure_ascii=False, default=str)}\n\nDecide los cambios de configuración."
    text = await _ask(_chat(advisor_system(policy or DEFAULT_POLICY, market), kind or ("fx-advisor" if market is FX_MARKET else "advisor")), prompt)
    data = parse_json(text, "revisión IA")
    changes = data.get("changes") or {}
    revert = data.get("revert") or []
    return {
        "summary": _clip(data.get("summary", ""), 1200),
        "changes": changes if isinstance(changes, dict) else {},
        "revert": [str(x) for x in revert if isinstance(x, (str, int))][:3] if isinstance(revert, list) else [],
        "notes": [_clip(n, 420) for n in (data.get("notes") or [])][:6],
    }
