"""Claude prompts for the HotForex bot: signal validation (forex/metals context) and macro news-event trading assessment."""
import json
import logging
from ai_advisor import _chat, _ask, _news_lines, parse_json
import price_action
import ai_rules
from fx.symbols import SPECS, pips

logger = logging.getLogger("fx.ai")

SYSTEM = (
    "Eres un gestor de riesgo cuantitativo senior especializado en forex y metales (oro, plata) con apalancamiento tipo CFD/MetaTrader. "
    "Evalúas señales generadas por un sistema algorítmico y decides si se ejecutan. Prioriza preservar capital: rechaza señales con indicadores "
    "contradictorios, entradas tardías (RSI extremo a favor), contra la tendencia mayor, con stop demasiado estrecho frente al spread, "
    "o cuando un dato macro de alto impacto de alguna de las dos divisas del par se publica en los próximos minutos. Ten en cuenta la sesión activa: "
    "la liquidez y los rangos son mayores en Londres y Nueva York; en la sesión asiática los pares europeos y el oro tienden a rangos estrechos. "
    "Si se incluye contexto de noticias (sesgo por divisa, riesgo de mercado, alertas), úsalo como factor de riesgo: un sesgo claro en contra de la señal "
    "justifica bajar la confianza o rechazar; un sesgo a favor es un punto positivo pero nunca suficiente por sí solo. "
    "Aprueba solo setups con confluencia clara. "
    "Responde EXCLUSIVAMENTE con un objeto JSON válido, sin texto adicional, con este formato: "
    '{"decision":"APROBAR" o "RECHAZAR","confidence":entero 0-100,"risk":"BAJO"|"MEDIO"|"ALTO","reasoning":"máximo 3 frases en español"}'
)

NEWS_SYSTEM = (
    "Eres un trader profesional de noticias macro en forex y metales. Recibes un evento del calendario económico que acaba de publicarse "
    "(título, país/divisa, previsión, dato anterior) y titulares publicados en los minutos posteriores. Tu tarea: (1) extraer el DATO REAL publicado si "
    "aparece en algún titular (no lo inventes: si no aparece, actual debe ser null y trades debe ser []); (2) valorar la sorpresa frente a la previsión "
    "desde el punto de vista de la divisa del evento (POSITIVA = fortalece a la divisa, NEGATIVA = la debilita, EN_LINEA = sin sorpresa relevante); "
    "(3) proponer como máximo 2 operaciones entre los pares candidatos solo si la sorpresa es clara y el evento mueve de verdad el mercado "
    "(decisiones de tipos de interés, NFP, IPC, PIB, ventas minoristas, PMI muy desviado, comunicados de bancos centrales). Un dato en línea, una sorpresa "
    "mínima o un evento de segunda fila deben dar trades [] o confianza baja (<60). Recuerda la dirección: si la divisa del evento es la BASE del par "
    "(p.ej. EUR en EURUSD) una sorpresa positiva implica long; si es la COTIZADA (p.ej. USD en EURUSD, o USD en XAUUSD) una sorpresa positiva implica short. "
    "La confianza debe ser honesta: solo sorpresas grandes y sin matices en eventos de primer nivel merecen 75 o más. "
    "Responde EXCLUSIVAMENTE con un objeto JSON válido: "
    '{"actual":"texto o null","forecast":"texto","previous":"texto","surprise":"POSITIVA"|"NEGATIVA"|"EN_LINEA"|"DESCONOCIDA",'
    '"summary":"1-2 frases en español","trades":[{"symbol":"PAR","side":"long"|"short","confidence":entero 0-100,"reasoning":"1 frase en español"}]}'
)


def _parse(text: str) -> dict:
    return parse_json(text, "evaluación de señal FX")


def _weight_line(weight) -> str:
    """'bajo' = the shadow results showed the filter rejecting profitable signals on days of market-wide alerts (set by the AI review or the user)."""
    if weight != "bajo":
        return ""
    return ("Peso de las noticias: BAJO (calibrado con los resultados sombra). Las alertas generales de mercado que no afecten específicamente a las divisas "
            "de este par solo deben restar confianza, no justificar por sí solas un rechazo; rechaza si el riesgo es específico del par, hay un sesgo claro "
            "en contra en sus divisas o un evento de alto impacto inminente.\n")


def _build_prompt(symbol: str, timeframe: str, sig: dict, ctx: dict) -> str:
    spec = SPECS[symbol]
    stop_pips = pips(symbol, abs(sig["price"] - sig["sl"]))
    spread_pips = pips(symbol, spec["spread"])
    sess = ", ".join(s["name"] for s in ctx.get("sessions", []) if s["open"]) or "ninguna (mercado cerrado o entre sesiones)"
    return (
        f"SEÑAL: {sig['side'].upper()} {symbol} ({spec['name']}) | Estrategia: {sig['strategy']} | TF: {timeframe}\n"
        f"Entrada: {sig['price']} | SL: {sig['sl']:.{spec['digits']}f} | TP: {sig['tp']:.{spec['digits']}f} | "
        f"R:R {abs(sig['tp'] - sig['price']) / max(abs(sig['price'] - sig['sl']), 1e-12):.2f} | stop {stop_pips:.1f} pips · spread {spread_pips:.1f} pips\n"
        f"Confirmaciones ({sig['score']}): {'; '.join(sig['reasons'])}\n"
        f"Indicadores: {json.dumps(sig['indicators'])}\n"
        f"Tendencia TF superior ({ctx.get('htf_tf')}): {ctx.get('htf_trend')}\n"
        f"Cambio 24h: {ctx.get('change_24h', 0):.2f}% | Sesiones activas: {sess}\n"
        f"Últimos cierres (% cambio por vela, más reciente al final): {ctx.get('recent_changes')}\n"
        + price_action.prompt_lines(ctx.get("price_action"))
        + ai_rules.prompt_lines(ctx.get("learned_rules")) +
        f"Cuenta: {ctx.get('open_positions')} posiciones abiertas, PnL diario {ctx.get('daily_pnl_pct', 0):.2f}%\n"
        + _news_lines(ctx.get("news")) + _weight_line(ctx.get("news_weight")) +
        "¿Ejecutar esta operación?"
    )


async def evaluate_signal(symbol: str, timeframe: str, sig: dict, ctx: dict, kind: str = "fx") -> dict:
    data = _parse(await _ask(_chat(SYSTEM, f"{kind}-signal"), _build_prompt(symbol, timeframe, sig, ctx)))
    decision = str(data.get("decision", "RECHAZAR")).upper()
    return {
        "decision": "APROBADA" if decision.startswith("APROB") else "RECHAZADA",
        "confidence": int(max(0, min(100, int(data.get("confidence", 0))))),
        "risk": str(data.get("risk", "MEDIO")).upper(),
        "reasoning": str(data.get("reasoning", ""))[:600],
    }


def _news_trade_prompt(event: dict, headlines: list, candidates: list, context: dict | None) -> str:
    lines = [f"- [{h['source']} · hace {h.get('age_min', '?')} min] {h['title']}" + (f" — {h['summary'][:160]}" if h.get("summary") else "") for h in headlines[:25]]
    ctx = ""
    if context:
        ctx = f"\nSESGO ACTUAL POR DIVISA (análisis previo de noticias): {json.dumps(context, ensure_ascii=False)}\n"
    return (
        f"EVENTO: {event['title']} · divisa {event['country']} ({event.get('country_name', '')}) · publicado {event['time'][:16]} UTC · impacto {event['impact']}\n"
        f"Previsión: {event.get('forecast') or 'n/d'} · Anterior: {event.get('previous') or 'n/d'}\n"
        f"PARES CANDIDATOS: {', '.join(candidates)}\n"
        f"{ctx}\nTITULARES POSTERIORES A LA PUBLICACIÓN:\n" + ("\n".join(lines) if lines else "- (ninguno todavía)") +
        "\n\nDevuelve solo el JSON. Dentro de los textos no uses comillas dobles ni saltos de línea."
    )


async def assess_news_event(event: dict, headlines: list, candidates: list, context: dict | None = None, kind: str = "fx") -> dict:
    chat = _chat(NEWS_SYSTEM, f"{kind}-news-trade")
    text = await _ask(chat, _news_trade_prompt(event, headlines, candidates, context))
    try:
        data = _parse(text)
    except ValueError:
        data = _parse(await _ask(chat, "Tu respuesta anterior no era JSON válido. Repite la respuesta como un único objeto JSON válido, sin texto adicional."))
    trades = []
    for t in data.get("trades") or []:
        if not isinstance(t, dict) or str(t.get("symbol", "")).upper() not in candidates:
            continue
        side = str(t.get("side", "")).lower()
        if side not in ("long", "short"):
            continue
        trades.append({"symbol": str(t["symbol"]).upper(), "side": side, "confidence": int(max(0, min(100, int(t.get("confidence", 0) or 0)))),
                       "reasoning": str(t.get("reasoning", ""))[:300]})
    actual = data.get("actual")
    surprise = str(data.get("surprise", "DESCONOCIDA")).upper()
    return {
        "actual": None if actual in (None, "", "null", "None") else str(actual)[:40],
        "forecast": str(data.get("forecast") or event.get("forecast") or "")[:40],
        "previous": str(data.get("previous") or event.get("previous") or "")[:40],
        "surprise": surprise if surprise in ("POSITIVA", "NEGATIVA", "EN_LINEA", "DESCONOCIDA") else "DESCONOCIDA",
        "summary": str(data.get("summary", ""))[:400],
        "trades": trades[:4],
    }
