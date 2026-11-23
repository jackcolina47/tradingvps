"""AI funnel shared by the three bots. Every 60 s (inside operating hours, with free slots):
  stage 0 · metrics per pair (EMAs 20/50/200, RSI, MACD, ADX, ATR, volume, supply/demand zones) + deterministic filter, no AI
  stage 1 · cheap model picks 0–3 candidates from the whole table (one call)
  stage 2 · strong model analyses each candidate like a professional trader (+ learned rules) → BUY / SELL / PASS and a success confidence.
Confidence above the configured threshold → real entry; below → shadow trade (one per candle). A stage-1 candidate is sent again to stage 2 after the
5-minute cool-down passes (while the cheap model keeps picking it and slots remain). Position size always comes from the fixed-fractional risk sizing of each engine."""
import time
import json
import asyncio
from bson import ObjectId
import logging
from datetime import datetime, timezone, timedelta
import pandas as pd
from models import Signal, now_iso
from ai_advisor import _chat, _ask, _cfg, parse_json, label
from ai_funnel import fast_cfg
import ai_rules
import trade_guard

logger = logging.getLogger("funnel")
STRATEGY = "ai_funnel"
INFO = {STRATEGY: {"name": "Embudo IA", "rules": "Filtro técnico sin IA → modelo rápido elige hasta 3 candidatos → modelo potente decide BUY/SELL/PASS con un % de confianza. "
                                                 "Solo se ejecuta si la confianza supera «Confianza IA para abrir operación»; si no, se sigue como operación sombra.",
                   "basis": "Análisis discrecional de un trader profesional (tendencia, EMAs, momentum, volumen, oferta y demanda) con dimensionamiento por riesgo fijo."}}
TRIGGER_MIN, TRIGGER_DELAY_SEC = 15, 8   # the funnel fires at :00/:15/:30/:45 + 8 s, once the exchange has closed the candle
MIN_ADX, MIN_VOLUME = 18, 0.8
OVEREXT_RSI, OVEREXT_ATR = (32, 68), 2.0   # late entries: RSI beyond these and price further than 2 ATR from the EMA20 (rich engines)
SL_BUFFER_ATR = 0.25                        # safety margin added behind an AI stop (rich engines)
SWING_BARS, RANGE_BARS, RANGE_CROSSES = 20, 15, 3


def next_trigger(now: float = None) -> float:
    """Epoch of the next quarter-hour mark + safety delay."""
    now = time.time() if now is None else now
    step = TRIGGER_MIN * 60
    t = (now - TRIGGER_DELAY_SEC) // step * step + step + TRIGGER_DELAY_SEC
    return t if t > now else t + step
MAX_CANDIDATES = 3
CONFIDENCE_LEVELS = (10, 20, 30, 40, 50, 60, 70, 80, 90)
FAST_TIMEOUT_SEC, DEEP_TIMEOUT_SEC = 25, 75
SL_ATR_BAND = (0.5, 4.0)   # an AI stop must sit between 0.5 and 4 ATR from the entry
MIN_RR = 1.0
COUNTER_MIN_RR = 1.5   # a trade against the 4H trend needs this much room to the AI target (first EMA / zone of 4H-1H)
DEEP_COOLDOWN_SEC = 300    # guard against manual re-scans: a candidate goes back to the strong model on every 15-min trigger, never sooner than 5 min
REPEAT_MOVE_ATR = 0.15     # stage 1 is not repeated while no eligible pair moved this much (in ATR) since the last call on the same candle
MTF_TTL = {"4h": 300, "1h": 120}   # seconds a higher-timeframe frame is reused (multi-timeframe engines only)
MTF_FAST = {"provider": "gemini", "model": "gemini-3.5-flash"}   # cheap model of the multi-timeframe stage 1
BIAS_SIDE = {"ALCISTA": "long", "BAJISTA": "short"}
MARKET = {"crypto": "criptomonedas (futuros perpetuos USDT)", "fx": "forex y metales (CFD)"}


def coerce_confidence(v) -> int:
    try:
        v = int(round(float(v) / 10) * 10)
    except (TypeError, ValueError):
        return 60
    return min(90, max(10, v))


# ---------- stage 0 ----------
def zones(df: pd.DataFrame, i: int, atr: float, price: float, k: int = 3, lookback: int = 150) -> dict:
    """Nearest demand zone below and supply zone above the price: clusters of swing lows / highs (≥2 touches within 0.5 ATR weigh more)."""
    lo, hi = df["low"].values, df["high"].values
    start = max(k, i - lookback)
    lows = [float(lo[j]) for j in range(start, i - k + 1) if lo[j] <= lo[j - k:j + k + 1].min()]
    highs = [float(hi[j]) for j in range(start, i - k + 1) if hi[j] >= hi[j - k:j + k + 1].max()]

    def nearest(levels: list, below: bool):
        side = sorted((x for x in levels if (x < price if below else x > price)), key=lambda x: abs(price - x))
        if not side:
            return None
        base = side[0]
        touches = [x for x in levels if abs(x - base) <= 0.5 * atr]
        return {"low": round(min(touches), 8), "high": round(max(touches), 8), "touches": len(touches), "dist_atr": round(abs(price - base) / atr, 2)}
    return {"demand": nearest(lows, True), "supply": nearest(highs, False)} if atr > 0 else {"demand": None, "supply": None}


def _rich(df: pd.DataFrame, i: int, atr: float, price: float) -> dict:
    """Extra context for the learned rules: fast EMAs, ADX slope, last swings and range regime."""
    close, adx, hi, lo = df["close"], df["adx"].values, df["high"].values, df["low"].values
    e9, e21 = float(close.ewm(span=9, adjust=False).mean().iloc[i]), float(close.ewm(span=21, adjust=False).mean().iloc[i])
    prev = (float(adx[i - 1]), float(adx[i - 2]))
    slope = "SUBIENDO" if adx[i] > max(prev) + 0.3 else "BAJANDO" if adx[i] < min(prev) - 0.3 else "PLANA"
    start = max(2, i - SWING_BARS + 1)
    highs = [j for j in range(start, i - 1) if hi[j] >= hi[j - 2:j + 3].max()]
    lows = [j for j in range(start, i - 1) if lo[j] <= lo[j - 2:j + 3].min()]
    sh = float(hi[highs[-1]]) if highs else float(hi[start:i + 1].max())
    sl = float(lo[lows[-1]]) if lows else float(lo[start:i + 1].min())
    above = (close.iloc[i - RANGE_BARS + 1:i + 1] > df["ema20"].iloc[i - RANGE_BARS + 1:i + 1]).values
    crosses = int((above[1:] != above[:-1]).sum())
    return {"rich": True, "ema9": round(e9, 8), "ema21": round(e21, 8), "fast_cross": "ALCISTA" if e9 > e21 else "BAJISTA", "adx_slope": slope,
            "swing_high": round(sh, 8), "swing_high_atr": round((sh - price) / atr, 2) if atr else 0,
            "swing_low": round(sl, 8), "swing_low_atr": round((price - sl) / atr, 2) if atr else 0,
            "ema20_crosses": crosses, "regime": "RANGO / CHOPPY" if crosses >= RANGE_CROSSES else None}


def metrics(df: pd.DataFrame, i: int, has_volume: bool, price: float, rich: bool = False) -> dict:
    """Everything the models see about a pair; `i` = last closed candle. Without real/tick volume the candle range replaces it."""
    row = df.iloc[i]
    atr = float(row["atr"])
    rng = (df["high"] - df["low"])
    activity = float(row["vol_ratio"]) if has_volume else float(rng.iloc[i] / max(rng.iloc[i - 20:i].mean(), 1e-12))
    e20, e50, e200 = float(row["ema20"]), float(row["ema50"]), float(row["ema200"])
    trend = "ALCISTA" if e20 > e50 > e200 else "BAJISTA" if e20 < e50 < e200 else "MIXTA"
    z = zones(df, i, atr, price)
    return {"price": price, "close": float(row["close"]), "ema20": round(e20, 8), "ema50": round(e50, 8), "ema200": round(e200, 8), "trend": trend,
            "rsi": round(float(row["rsi"]), 1), "macd_hist": round(float(row["macd_hist"]), 8), "macd_rising": bool(row["macd_hist"] > df["macd_hist"].iloc[i - 1]),
            "adx": round(float(row["adx"]), 1), "atr": round(atr, 8), "atr_pct": round(atr / price * 100, 3) if price else 0,
            "activity": round(activity, 2), "activity_kind": "volumen" if has_volume else "rango",
            "dist_ema20_atr": round((price - e20) / atr, 2) if atr else 0, "demand": z["demand"], "supply": z["supply"], "candle_ts": int(row["ts"]),
            **(_rich(df, i, atr, price) if rich else {})}


def stage0(m: dict) -> str | None:
    """Deterministic filter: a flat, quiet pair far from any zone is not worth an AI call. Returns the discard reason."""
    if not m["atr"] > 0:
        return "sin ATR válido"
    if m["adx"] < MIN_ADX:
        return f"sin tendencia (ADX {m['adx']} < {MIN_ADX})"
    if m["activity_kind"] == "volumen" and m["activity"] < MIN_VOLUME:   # forex on Yahoo data has no reliable volume: ADX only
        return f"volumen débil ({m['activity']}× < {MIN_VOLUME}×)"
    return None


def overextended(m: dict) -> str | None:
    """'suelo' / 'techo' when the price is stretched (RSI + distance to the EMA20). Rich engines only."""
    if not m.get("rich"):
        return None
    if m["rsi"] < OVEREXT_RSI[0] and m["dist_ema20_atr"] < -OVEREXT_ATR:
        return "suelo"
    if m["rsi"] > OVEREXT_RSI[1] and m["dist_ema20_atr"] > OVEREXT_ATR:
        return "techo"
    return None


FAST_CROSS_SIDE = {"long": "ALCISTA", "short": "BAJISTA"}   # EMA9/21 cross each side needs (rich engines), unless it is a rebound
OVEREXT_KEEP = {"suelo": ("ALCISTA", "long"), "techo": ("BAJISTA", "short")}   # 4H trend that keeps the pair alive for a rebound, and the only side allowed


def overext_discard(m: dict, trend_4h: str = None) -> str | None:
    """Stage-0 reason: a stretched pair is dropped without AI unless the 4H trend backs the rebound."""
    ext = overextended(m)
    if ext and trend_4h != OVEREXT_KEEP[ext][0]:
        return f"sobreextendido en {ext} (RSI/ATR) · RSI {m['rsi']}, precio a {m['dist_ema20_atr']:+} ATR de EMA20, 4H {trend_4h or 's/d'}"
    return None


def levels(side: str, price: float, atr: float, settings, ai_sl=None, ai_tp=None, digits: int = None, buffer_atr: float = 0.0) -> tuple:
    """(sl, tp, source). Fixed toggle ON → sl_mult×ATR / tp_mult×ATR. OFF → the AI's levels when sane, otherwise the same ATR multiples.
    `buffer_atr` moves an accepted AI stop that many ATR further away (the position size is computed afterwards from the final stop)."""
    d = 1 if side == "long" else -1
    sl, tp, source = price - d * settings.atr_sl_mult * atr, price + d * settings.atr_tp_mult * atr, "atr"
    if not settings.atr_levels_enabled and ai_sl and ai_tp:
        risk, reward = (price - ai_sl) * d, (ai_tp - price) * d
        if SL_ATR_BAND[0] * atr <= risk <= SL_ATR_BAND[1] * atr and reward >= MIN_RR * (risk + buffer_atr * atr):
            sl, tp, source = float(ai_sl) - d * buffer_atr * atr, float(ai_tp), "ia"
    if digits is not None:
        sl, tp = round(sl, digits), round(tp, digits)
    return sl, tp, source


# ---------- stage 1 ----------
FAST_SYSTEM = (
    "Eres el primer filtro de un bot de trading de {market}. Recibes una tabla con el estado técnico de varios pares en la temporalidad {tf} "
    "(tendencia por EMAs 20/50/200, RSI, histograma MACD, ADX, ATR, actividad = volumen o rango frente a su media, y las zonas de demanda/oferta más cercanas "
    "con su distancia en ATR). Elige como máximo {n} pares en los que AHORA MISMO pueda haber una entrada de calidad (tendencia clara con retroceso a EMA o zona, "
    "ruptura con actividad, rechazo en oferta/demanda). Lo normal es que no haya ninguno: no fuerces candidatos. Otro modelo hará después el análisis completo. "
    'Responde EXCLUSIVAMENTE con JSON válido: {{"candidates":[{{"symbol":"PAR","side":"long"|"short","reason":"máx. 15 palabras en español"}}]}}'
)


MTF_SYSTEM = (
    " ANÁLISIS MULTITEMPORAL: cada par trae además su estructura en 4H y en 1H. Evalúa primero 4H (estructura y dirección de fondo), después 1H "
    "(confirmación: tendencia, momentum y si el precio está en una zona útil) y usa la tabla de {tf} solo como gatillo de entrada. "
    "Prefiere pares donde 4H y 1H apoyen la misma dirección o donde 1H esté retrocediendo hacia una zona a favor de 4H. "
    "También puedes proponer el lado contrario a 4H como rebote técnico de contratendencia, pero solo si {tf} muestra un giro claro: RSI en sobreventa/sobrecompra extrema, "
    "rechazo o absorción en una demanda/oferta relevante, o cruce de las EMAs 20/50."
)


def _mtf_txt(m: dict) -> str:
    out = []
    for tf, h in (m.get("mtf") or {}).items():
        z = f" · demanda {_zone_txt(h['demand'])} · oferta {_zone_txt(h['supply'])}" if "demand" in h else ""
        out.append(f"{tf.upper()}: tendencia {h['trend']} · RSI {h['rsi']} · ADX {h['adx']} · precio a {h['dist_ema50_atr']:+} ATR de su EMA50{z}")
    return " || ".join(out) + (f" || lado a favor de 4H: {m['bias'].upper() + ' (el contrario solo como rebote de contratendencia)' if m.get('bias') else 'LONG o SHORT'}" if out else "")


def _zone_txt(z) -> str:
    return f"{z['low']:.6g}-{z['high']:.6g} ({z['touches']} toques, a {z['dist_atr']} ATR)" if z else "ninguna"


def _row_txt(symbol: str, m: dict) -> str:
    return (f"{symbol}: precio {m['price']:.6g} | tendencia {m['trend']} (EMA20 {m['ema20']:.6g}, EMA50 {m['ema50']:.6g}, EMA200 {m['ema200']:.6g}; precio a {m['dist_ema20_atr']:+} ATR de EMA20) | "
            f"RSI {m['rsi']} | MACD hist {m['macd_hist']:.4g} {'subiendo' if m['macd_rising'] else 'bajando'} | ADX {m['adx']} | ATR {m['atr_pct']}% | "
            f"{m['activity_kind']} {m['activity']}× | demanda {_zone_txt(m['demand'])} | oferta {_zone_txt(m['supply'])}"
            + (f"\n   EMA9 {m['ema9']:.6g}, EMA21 {m['ema21']:.6g} · cruce_emas_rapidas: {m['fast_cross']} ({'EMA9 > EMA21' if m['fast_cross'] == 'ALCISTA' else 'EMA9 < EMA21'}) | "
               f"pendiente_adx: {m['adx_slope']} | último swing high ({SWING_BARS} velas) {m['swing_high']:.6g} (a {m['swing_high_atr']:+} ATR) · "
               f"último swing low {m['swing_low']:.6g} (a {m['swing_low_atr']:+} ATR)"
               + (f" | regimen_{m.get('tf', 'tf')}: {m['regime']} ({m['ema20_crosses']} cruces de EMA20 en {RANGE_BARS} velas): exige extremos claros de oferta/demanda" if m["regime"] else "")
               if m.get("rich") else "")
            + (f"\n   {_mtf_txt(m)}" if m.get("mtf") else ""))


async def stage1(kind: str, tf: str, rows: dict, limit: int, usage_kind: str) -> dict:
    mtf = any(m.get("mtf") for m in rows.values())
    system = FAST_SYSTEM.format(market=MARKET[kind], tf=tf, n=limit) + (MTF_SYSTEM.format(tf=tf) if mtf else "")
    prompt = "\n".join(_row_txt(s, m) for s, m in rows.items()) + f"\n\n¿Qué pares (máx. {limit}) merecen el análisis completo?"
    t0 = time.monotonic()

    async def ask(cfg):
        chat = _chat(system, f"{usage_kind}-fast", cfg)
        chat._probe = False
        return parse_json(await asyncio.wait_for(_ask(chat, prompt), FAST_TIMEOUT_SEC), "embudo etapa 1")
    cfg = {**_cfg, **MTF_FAST} if mtf else fast_cfg()
    try:
        data = await ask(cfg)
    except Exception as e:
        if not mtf:
            raise
        logger.warning(f"[STAGE 1] {cfg['model']} falló ({str(e)[:120]}) → modelo rápido de respaldo")
        cfg = fast_cfg()
        data = await ask(cfg)
    out = []
    for c in data.get("candidates") or []:
        sym, side = str((c or {}).get("symbol", "")).upper(), str((c or {}).get("side", "")).lower()
        if sym in rows and side in ("long", "short") and sym not in [x["symbol"] for x in out]:
            out.append({"symbol": sym, "side": side, "reason": str(c.get("reason") or "")[:160]})
    res = {"candidates": out[:limit], "model": label(cfg["model"]), "ms": int((time.monotonic() - t0) * 1000)}
    logger.info(f"[STAGE 1] {kind} {tf} · {cfg['model']} · {res['ms']} ms · {len(rows)} pares → {[c['symbol'] for c in res['candidates']]}")
    return res


# ---------- stage 2 ----------
DEEP_SYSTEM = (
    "Eres un trader profesional de {market} con 20 años de experiencia operando intradía. Analizas UN par en la temporalidad {tf} y decides si abrir una "
    "operación AHORA. Valora como lo harías con tu propio dinero: estructura y tendencia (EMAs 20/50/200 y temporalidad superior), momentum (RSI, MACD, ADX), "
    "volumen/actividad, zonas de oferta y demanda, dónde quedaría el stop y cuánto recorrido libre hay hasta el objetivo, y si la entrada llega tarde. "
    "Actúa como un trader intradía profesional y decisivo. Si la estructura en la temporalidad operativa ({tf}) muestra alineación técnica clara, el stop loss queda "
    "técnicamente protegido y existe un espacio libre con un ratio riesgo/beneficio real >= 1.5 hacia el siguiente obstáculo relevante, prioriza emitir BUY o SELL. "
    "Usa PASS únicamente si existe un conflicto severo o riesgo de invalidación inmediata. "
    "Si la operación va en contra de la tendencia mayor de 4H (rebote técnico), el Take Profit debe fijarse obligatoriamente antes de la primera EMA relevante "
    "(EMA 20 o EMA 50 de 4H/1H) o bloque de oferta/demanda previo, asegurando una toma de beneficios rápida: en ese caso devuelve SIEMPRE `tp` como precio, "
    "con al menos 1.5 veces la distancia del stop. "
    "`confidence` es tu estimación honesta (0-100) de la probabilidad de que la operación alcance el objetivo antes que el stop; no la infles. "
    "{levels} "
    "Si hay REGLAS APRENDIDAS, tienen prioridad: si la operación incumple alguna, responde PASS e indica su número en `rule`. "
    'Responde EXCLUSIVAMENTE con JSON válido, sin texto adicional: {{"decision":"BUY"|"SELL"|"PASS","confidence":entero 0-100,"sl":número o null,"tp":número o null,'
    '"rule":número de la regla incumplida (0 si ninguna),"reasoning":"máx. 60 palabras en español con los datos concretos que deciden"}}'
)


def _candles_txt(df: pd.DataFrame, i: int, n: int, has_volume: bool) -> str:
    out = []
    for _, r in df.iloc[max(0, i - n + 1):i + 1].iterrows():
        t = datetime.fromtimestamp(r["ts"] / 1000, timezone.utc).strftime("%d %H:%M")
        out.append(f"{t} O{r['open']:.6g} H{r['high']:.6g} L{r['low']:.6g} C{r['close']:.6g}" + (f" V{r['volume']:.4g}" if has_volume else ""))
    return "\n".join(out)


async def stage2(kind: str, symbol: str, tf: str, cand: dict, m: dict, df, i: int, has_volume: bool, htf: dict, rules: list, ctx: dict, settings, usage_kind: str) -> dict:
    fixed = settings.atr_levels_enabled
    lv = (f"El stop y el objetivo son FIJOS: SL a {settings.atr_sl_mult:g}×ATR y TP a {settings.atr_tp_mult:g}×ATR de la entrada; deja sl y tp en null y juzga la operación con esos niveles (excepción: en contratendencia contra 4H el stop sigue fijo pero el `tp` lo fijas tú)."
          if fixed else "Propón `sl` y `tp` como precios: el stop detrás de la estructura (entre 0,5 y 4 ATR de la entrada) y el objetivo antes del siguiente obstáculo, con R:R ≥ 1.")
    chat = _chat(DEEP_SYSTEM.format(market=MARKET[kind], tf=tf, levels=lv), usage_kind)
    rule_lines = "\n".join(f"{n}. {r['text']}" + (f" (solo {r['symbol']})" if r.get("symbol") else "") for n, r in enumerate(rules, 1))
    prompt = (
        f"PAR: {symbol} | TF {tf} | hora Nueva York {datetime.now(trade_guard.NY).strftime('%A %H:%M')}\n"
        f"Candidato del filtro rápido: {cand['side'].upper()} · {cand['reason']}" + (" · CONTRATENDENCIA (contra 4H): rebote técnico con TP corto" if cand.get("counter_trend") else "") + "\n"
        f"ESTADO ({tf}): {_row_txt(symbol, {**m, 'mtf': None})}\n"
        + (f"ANÁLISIS MULTITEMPORAL (4H = estructura, 1H = confirmación, {tf} = entrada): {_mtf_txt(m)}\n" if m.get("mtf") else
           f"TEMPORALIDAD SUPERIOR ({htf['tf']}): tendencia {htf['trend']} · RSI {htf['rsi']} · ADX {htf['adx']} · precio a {htf['dist_ema50_atr']:+} ATR de su EMA50\n") +
        
        f"CONTEXTO: {json.dumps(ctx, ensure_ascii=False)}\n"
        f"ÚLTIMAS VELAS CERRADAS ({tf}, UTC{', V = ' + ('volumen' if kind == 'crypto' else 'volumen de ticks del bróker') if has_volume else ', sin volumen fiable: usa el rango'}):\n"
        f"{_candles_txt(df, i, 40, has_volume)}\n"
        + (f"REGLAS APRENDIDAS:\n{rule_lines}\n" if rules else "")
        + "¿Abres operación ahora?"
    )
    t0 = time.monotonic()
    data = parse_json(await asyncio.wait_for(_ask(chat, prompt), DEEP_TIMEOUT_SEC), "embudo etapa 2")
    decision = str(data.get("decision", "PASS")).upper()
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None  # noqa: E731
    try:
        conf, rule_n = int(max(0, min(100, int(data.get("confidence") or 0)))), int(data.get("rule") or 0)
    except (TypeError, ValueError):
        conf, rule_n = 0, 0
    res = {"decision": decision if decision in ("BUY", "SELL") else "PASS", "confidence": conf, "sl": num(data.get("sl")), "tp": num(data.get("tp")),
           "rule": rules[rule_n - 1] if 1 <= rule_n <= len(rules) else None, "reasoning": str(data.get("reasoning") or "")[:500],
           "model": label(_cfg["model"]), "ms": int((time.monotonic() - t0) * 1000)}
    logger.info(f"[STAGE 2 {res['decision']}] {symbol} · {_cfg['model']} · {res['ms']} ms · {conf}%")
    return res


# ---------- orchestration ----------
def _key(symbol: str, candle_ts: int) -> str:
    return f"{symbol}:{STRATEGY}:{candle_ts}"


def _note(engine, symbol: str, stage: int, state: str, text: str, confidence: int = None):
    engine.analysis.setdefault(symbol, {})["funnel"] = {"stage": stage, "state": state, "text": text[:200], "confidence": confidence, "at": now_iso()}


async def _say(engine, key: str, level: str, text: str):
    """Logs a funnel status line only when it changes (the cycle runs every minute)."""
    if engine.funnel_said.get(key) != text:
        engine.funnel_said[key] = text
        await engine.log(level, text)


def _htf(df: pd.DataFrame, tf: str) -> dict:
    r = df.iloc[-2]
    e20, e50, e200, atr = float(r["ema20"]), float(r["ema50"]), float(r["ema200"]), float(r["atr"])
    return {"tf": tf, "trend": "ALCISTA" if e20 > e50 > e200 else "BAJISTA" if e20 < e50 < e200 else "MIXTA", "rsi": round(float(r["rsi"]), 1),
            "adx": round(float(r["adx"]), 1), "dist_ema50_atr": round((float(r["close"]) - e50) / atr, 2) if atr else 0}


async def _mtf(engine, symbol: str, price: float) -> dict:
    """4H structure + 1H confirmation of a pair (frames cached a few minutes: they change slowly)."""
    cache, out = engine.__dict__.setdefault("funnel_mtf_cache", {}), {}
    for tf in engine.funnel_mtf:
        hit = cache.get((symbol, tf))
        if not hit or time.time() - hit[0] > MTF_TTL[tf]:
            df, _ = await engine.funnel_frame(symbol, tf, 250)
            hit = cache[(symbol, tf)] = (time.time(), df)
        df = hit[1]
        out[tf] = _htf(df, tf)
        if tf == "4h":
            out[tf].update(zones(df, len(df) - 2, float(df["atr"].iloc[-2]), price))
    return out


def _upcoming(kind: str, symbol: str, hours: int = 4) -> list:
    now = datetime.now(timezone.utc)
    return [f"{e['country']} {e['title']} en {int((datetime.fromisoformat(e['time']) - now).total_seconds() / 60)} min" for e in trade_guard.calendar.events
            if now < datetime.fromisoformat(e["time"]) <= now + timedelta(hours=hours) and trade_guard.affects(kind, symbol, e["country"])][:3]


async def run(engine, settings) -> int:
    """One funnel cycle. Returns the number of BUY/SELL decisions of stage 2."""
    kind, tf = engine.guard_kind, settings.timeframe
    rows, frames = {}, {}
    for symbol in settings.symbols:
        try:
            df, has_volume = await engine.funnel_frame(symbol, tf)
            i = len(df) - 2
            price = engine.market.tickers.get(symbol, {}).get("price") or float(df["close"].iloc[-1])
            m = metrics(df, i, has_volume, price, getattr(engine, "funnel_rich", False))
            m["tf"] = tf
            rows[symbol], frames[symbol] = m, (df, i, has_volume)
            prev = engine.analysis.get(symbol, {})
            engine.analysis[symbol] = {**engine.funnel_analysis(symbol, m), "trend": {"MIXTA": "NEUTRAL"}.get(m["trend"], m["trend"]), "rsi": m["rsi"], "macd_hist": m["macd_hist"],
                                       "adx": m["adx"], "activity": m["activity"], "activity_kind": m["activity_kind"], "demand": m["demand"], "supply": m["supply"],
                                       "signal": None, "signal_strategy": None, "funnel": prev.get("funnel"), "updated_at": now_iso()}
        except Exception as e:
            logger.exception(f"funnel metrics {symbol}")
            await _say(engine, f"err:{symbol}", "ERROR", f"{symbol}: sin datos para el embudo · {str(e)[:120]}")
    if not engine.running:
        return 0
    hours = trade_guard.hours_status(kind)
    if not hours["in_hours"]:
        await _say(engine, "gate", "SCAN", f"Fuera del horario operativo · próxima ventana «{hours['next_window']}» a las "
                                           f"{datetime.fromisoformat(hours['next_change']).astimezone(trade_guard.NY).strftime('%H:%M')} NY · sin análisis de IA" if hours["next_change"] else "Fuera del horario operativo")
        return 0
    state = await engine.funnel_state(settings)
    if state["block"]:
        await _say(engine, "gate", "SCAN", f"Embudo en pausa · {state['block']}")
        return 0
    await _say(engine, "gate", "SCAN", f"Embudo IA activo · ventana «{hours['window']}» · {state['free']} hueco(s) libre(s)")

    eligible = {}
    for symbol, m in rows.items():
        if symbol in state["open"]:
            continue
        ev = trade_guard.calendar.blocking(kind, symbol)
        if ev:
            _note(engine, symbol, 0, "NOTICIAS", f"Bloqueado por evento rojo: {trade_guard.event_label(ev)}")
            await _say(engine, f"news:{symbol}", "RISK", f"{symbol}: sin entradas por evento rojo ({trade_guard.event_label(ev)}) · ±{trade_guard.BEFORE_MIN} min")
            continue
        why = stage0(m)
        if why:
            _note(engine, symbol, 0, "DESCARTADO", why)
            continue
        if getattr(engine, "funnel_mtf", None):
            try:
                m["mtf"] = await _mtf(engine, symbol, m["price"])
            except Exception as e:
                logger.exception(f"funnel mtf {symbol}")
                _note(engine, symbol, 0, "DESCARTADO", f"Sin datos de 4H/1H · {str(e)[:100]}")
                continue
            m["bias"] = BIAS_SIDE.get(m["mtf"]["4h"]["trend"])
            engine.analysis[symbol]["htf"] = {"MIXTA": "NEUTRAL"}.get(m["mtf"]["4h"]["trend"], m["mtf"]["4h"]["trend"])
            engine.analysis[symbol]["mtf"] = {tf: h["trend"] for tf, h in m["mtf"].items()}
        why = overext_discard(m, (m.get("mtf") or {}).get("4h", {}).get("trend"))
        if why:
            _note(engine, symbol, 0, "DESCARTADO", why)
            continue
        eligible[symbol] = m
    if not eligible:
        return 0
    fp = {s: (m["candle_ts"], m["price"]) for s, m in eligible.items()}
    last = engine.funnel_last
    limit = min(MAX_CANDIDATES, state["free"])
    s1 = getattr(engine, "funnel_s1", None)
    if not (s1 and set(fp) == set(last) and all(fp[s][0] == last[s][0] and abs(fp[s][1] - last[s][1]) < REPEAT_MOVE_ATR * eligible[s]["atr"] for s in fp)):
        engine.funnel_last = fp   # something moved since the last stage-1 call (otherwise its candidates are reused: no tokens spent)
        try:
            s1 = engine.funnel_s1 = await stage1(kind, tf, eligible, limit, engine.usage_kind)
        except Exception as e:
            logger.exception("funnel stage 1")
            await engine.log("ERROR", f"Embudo · filtro rápido sin respuesta: {str(e)[:140]}")
            return 0
        for c in list(s1["candidates"]):
            ext = overextended(eligible[c["symbol"]])
            if ext and c["side"] != OVEREXT_KEEP[ext][1]:   # stretched pair kept alive for the rebound only: the late side is dropped
                s1["candidates"].remove(c)
                _note(engine, c["symbol"], 1, "DESCARTADO", f"{c['side'].upper()} tardío: sobreextendido en {ext} (RSI/ATR)")
                await engine.log("RISK", f"{c['symbol']}: candidato {c['side'].upper()} descartado sin IA potente · sobreextendido en {ext} (RSI/ATR)")
                continue
            if eligible[c["symbol"]].get("bias") not in (None, c["side"]):
                c["counter_trend"] = True   # stage 2 must set the target before the first 4H/1H obstacle
                await engine.log("SCAN", f"{c['symbol']}: candidato {c['side'].upper()} de contratendencia (4H {eligible[c['symbol']]['mtf']['4h']['trend']}) · el modelo potente exigirá TP corto")
            cross = eligible[c["symbol"]].get("fast_cross")
            if cross and cross != FAST_CROSS_SIDE[c["side"]] and not c.get("counter_trend") and not ext:
                s1["candidates"].remove(c)   # against the EMA9/21 cross of the entry timeframe: the strong model would reject it anyway
                why = f"{c['side'].upper()} contra el cruce EMA9/21 de {tf} ({cross}: EMA9 {'>' if cross == 'ALCISTA' else '<'} EMA21)"
                _note(engine, c["symbol"], 1, "DESCARTADO", why)
                await engine.log("RISK", f"{c['symbol']}: candidato {why} · descartado sin IA potente")
        picked = {c["symbol"] for c in s1["candidates"]}
        for symbol in eligible:
            if symbol not in picked:
                _note(engine, symbol, 1, "DESCARTADO", f"El filtro rápido no ve entrada ({s1['model']})")
        await engine.log("SCAN", f"Embudo {'4H/1H → ' if getattr(engine, 'funnel_mtf', None) else ''}{tf} · {len(rows)} pares → {len(eligible)} tras filtro técnico → {len(picked)} candidato(s) del modelo rápido"
                                 + (f": {', '.join(c['symbol'] + ' ' + c['side'].upper() for c in s1['candidates'])}" if picked else "") + f" · {s1['model']} · {s1['ms']} ms")
    found = 0
    deep_at = engine.__dict__.setdefault("funnel_deep_at", {})
    for cand in s1["candidates"][:limit]:
        if cand["symbol"] not in eligible or time.time() - deep_at.get(cand["symbol"], 0) < DEEP_COOLDOWN_SEC:
            continue
        deep_at[cand["symbol"]] = time.time()
        try:
            found += await _deep(engine, settings, cand, eligible[cand["symbol"]], frames[cand["symbol"]], s1)
        except Exception as e:
            logger.exception(f"funnel stage 2 {cand['symbol']}")
            await engine.log("ERROR", f"{cand['symbol']}: análisis del modelo potente fallido · {str(e)[:140]}")
    return found


async def _record(engine, cand: dict, m: dict, res: dict, s1: dict, tf: str, threshold: int, decision: str, key: str, note: str = ""):
    """Stores a strong-model verdict that opens nothing (PASS, against 4H, repeated low confidence) so the decisions panel shows it with its confidence."""
    side = {"BUY": "long", "SELL": "short"}.get(res["decision"], cand["side"])
    await engine.db.signals.insert_one(Signal(
        symbol=cand["symbol"], side=side, strategy=STRATEGY, timeframe=tf, price=m["price"], sl=0, tp=0, score=2, reasons=[f"Filtro rápido: {cand['reason']}"],
        indicators={k: m[k] for k in ("close", "ema20", "ema50", "ema200", "rsi", "macd_hist", "adx", "atr", "activity")},
        ai_decision=decision, ai_confidence=res["confidence"], ai_min_confidence=threshold, ai_stage=2, ai_model=res["model"], ai_ms=res["ms"],
        ai_reasoning=res["reasoning"], note=note, key=key,
        context={"funnel": {"stage1": {"reason": cand["reason"], "model": s1["model"], "ms": s1["ms"]}, "verdict": res["decision"],
                            "mtf": {t: h["trend"] for t, h in (m.get("mtf") or {}).items()} or None}}).to_mongo())


async def _deep(engine, settings, cand: dict, m: dict, frame: tuple, s1: dict) -> int:
    kind, tf, symbol = engine.guard_kind, settings.timeframe, cand["symbol"]
    df, i, has_volume = frame
    state = await engine.funnel_state(settings)
    if state["block"] or state["free"] <= 0 or symbol in state["open"]:
        return 0
    candle_key = _key(symbol, m["candle_ts"])
    key = f"{candle_key}:{int(time.time())}"
    htf_tf = engine.funnel_htf(tf)
    htf = m["mtf"]["1h"] if m.get("mtf") else _htf((await engine.funnel_frame(symbol, htf_tf, 250))[0], htf_tf)
    rules = await ai_rules.active(engine.db, symbol)
    ctx = {**engine.funnel_ctx(symbol), "posiciones_abiertas": len(state["open"]), "max_posiciones": settings.max_positions,
           "pnl_dia_pct": state["acc"]["daily_pnl_pct"], "eventos_rojos_proximos": _upcoming(kind, symbol)}
    res = await stage2(kind, symbol, tf, cand, m, df, i, has_volume, htf, rules, ctx, settings, engine.usage_kind)
    tail = f" · {res['model']} · {res['ms']} ms"
    threshold = settings.ai_min_confidence
    if res["decision"] == "PASS":
        await _record(engine, cand, m, res, s1, tf, threshold, "PASS", key, f"Regla aprendida: {res['rule']['text'][:120]}" if res["rule"] else "")
        _note(engine, symbol, 2, "PASS", res["reasoning"], res["confidence"])
        if res["rule"]:
            await ai_rules.hit(engine.db, res["rule"]["id"])
        await engine.log("AI_EVAL", f"{symbol}: PASS del modelo potente · confianza {res['confidence']}%" + (f" · regla «{res['rule']['text'][:80]}»" if res["rule"] else "") + f" · {res['reasoning'][:220]}{tail}")
        return 0
    side = "long" if res["decision"] == "BUY" else "short"
    price = engine.market.tickers.get(symbol, {}).get("price") or m["price"]
    sl, tp, source = levels(side, price, m["atr"], settings, res["sl"], res["tp"], engine.funnel_digits(symbol), SL_BUFFER_ATR if m.get("rich") else 0.0)
    counter = m.get("bias") not in (None, side)
    if counter:   # against the 4H trend: the stop stays, the target is the AI's (before the first 4H/1H obstacle), never further than the normal one
        d = 1 if side == "long" else -1
        risk, reward = (price - sl) * d, ((res["tp"] or price) - price) * d
        if reward < COUNTER_MIN_RR * risk:
            why = f"{res['decision']} de contratendencia sin recorrido: " + (f"R:R {reward / risk:.2f} < {COUNTER_MIN_RR} hasta el objetivo de la IA" if res["tp"] else "la IA no fijó objetivo")
            _note(engine, symbol, 2, "PASS", why, res["confidence"])
            await _record(engine, cand, m, res, s1, tf, threshold, "CONTRA_4H", key, f"{why} (4H {m['mtf']['4h']['trend']})")
            await engine.log("RISK", f"{symbol}: {why} · confianza {res['confidence']}% · 4H {m['mtf']['4h']['trend']}{tail}")
            return 0
        if reward < abs(tp - price):
            dg = engine.funnel_digits(symbol)
            tp, source = float(res["tp"]) if dg is None else round(float(res["tp"]), dg), "contra"
    approved = res["confidence"] > threshold
    if not approved and candle_key in engine.signal_keys:   # this candle already has its shadow trade: just report the new verdict
        _note(engine, symbol, 2, "SOMBRA", f"Confianza {res['confidence']}% ≤ {threshold}% · {res['reasoning']}", res["confidence"])
        await _record(engine, cand, m, res, s1, tf, threshold, "BAJA_CONFIANZA", key, "Reanálisis: la operación sombra de esta vela ya está abierta")
        await engine.log("AI_EVAL", f"{symbol} {res['decision']} · confianza {res['confidence']}% (umbral {threshold}%) → sigue sin ejecutarse · nuevo análisis al cierre de la próxima vela de 15 min · {res['reasoning'][:160]}{tail}")
        return 0
    sig = {"side": side, "price": price, "sl": sl, "tp": tp, "strategy": STRATEGY, "atr": m["atr"], "score": 2, "key_ts": m["candle_ts"],
           "reasons": [f"Filtro rápido: {cand['reason']}", f"Modelo potente ({res['confidence']}%): {res['reasoning']}"]
                      + ([f"Contratendencia (4H {m['mtf']['4h']['trend']}): objetivo corto fijado por la IA"] if counter else []),
           "indicators": {k: m[k] for k in ("close", "ema20", "ema50", "ema200", "rsi", "macd_hist", "adx", "atr", "activity")}}
    if source == "atr":
        sig["atr_levels"] = (settings.atr_sl_mult, settings.atr_tp_mult)
    signal = Signal(symbol=symbol, side=side, strategy=STRATEGY, timeframe=tf, price=price, sl=sl, tp=tp, score=2, reasons=sig["reasons"], indicators=sig["indicators"],
                    ai_decision="APROBADA" if approved else "BAJA_CONFIANZA", ai_confidence=res["confidence"], ai_min_confidence=threshold, ai_stage=2,
                    ai_model=res["model"], ai_ms=res["ms"], ai_adjusted_sl=res["sl"], ai_adjusted_tp=res["tp"], ai_levels_applied=source == "ia",
                    ai_reasoning=res["reasoning"], key=key,
                    context={**ctx, "funnel": {"stage1": {"reason": cand["reason"], "model": s1["model"], "ms": s1["ms"]}, "levels": source, "counter_trend": counter, "trend": m["trend"],
                                               "htf_tf": htf_tf, "mtf": {tf: h["trend"] for tf, h in (m.get("mtf") or {}).items()} or None, "activity_kind": m["activity_kind"], "demand": m["demand"], "supply": m["supply"]}})
    sid = str((await engine.db.signals.insert_one(signal.to_mongo())).inserted_id)
    head = f"{symbol} {res['decision']} · confianza {res['confidence']}% (umbral {threshold}%)"
    if not approved:
        _note(engine, symbol, 2, "SOMBRA", f"Confianza {res['confidence']}% ≤ {threshold}% → operación sombra · {res['reasoning']}", res["confidence"])
        await engine.log("AI_EVAL", f"{head} → RECHAZADA, se sigue como sombra · {res['reasoning'][:200]}{tail}")
        engine.signal_keys.add(candle_key)
        await engine.funnel_shadow(sid, symbol, sig, settings, "BAJA_CONFIANZA", state["acc"], res["confidence"])
        return 1
    await engine.log("AI_EVAL", f"{head} → APROBADA · {'CONTRATENDENCIA · SL fijo y TP corto de la IA' if source == 'contra' else 'SL/TP fijos por ATR' if source == 'atr' else 'SL/TP de la IA'} · {res['reasoning'][:200]}{tail}")
    late = getattr(engine, "funnel_late_atr", None)
    if late:   # market entry: never chase a move that already left
        t = engine.market.tickers.get(symbol, {})
        d = 1 if side == "long" else -1
        px = (t.get("price") or price) + d * (t.get("spread") or 0) / 2   # ask for a buy, bid for a sell
        ran = (px - m["close"]) * d / m["atr"] if m["atr"] else 0
        if ran > late:
            why = f"entrada tardía: el precio ya corrió {ran:.2f} ATR a favor desde el cierre de la vela señal (máx. {late})"
            await engine.db.signals.update_one({"_id": ObjectId(sid)}, {"$set": {"ai_decision": "TARDIA", "note": f"No ejecutada · {why}"}})
            _note(engine, symbol, 2, "PASS", why, res["confidence"])
            await engine.log("RISK", f"{symbol}: {res['decision']} aprobada pero descartada · {why} ({m['close']:.6g} → {px:.6g})")
            seen = engine.__dict__.setdefault("funnel_late_keys", set())
            if candle_key not in seen:   # one shadow per candle: what the late fill would have done
                seen.add(candle_key)
                await engine.funnel_shadow(sid, symbol, sig, settings, "TARDIA", state["acc"], res["confidence"])
                return 1
            return 0
    ok, reason = await engine.funnel_open(symbol, sig, settings, sid, state["acc"])
    if ok:
        _note(engine, symbol, 2, "EJECUTADA", res["reasoning"], res["confidence"])
    else:
        _note(engine, symbol, 2, "NO_EJECUTADA", reason, res["confidence"])
        await engine.db.signals.update_one({"key": key}, {"$set": {"note": f"No ejecutada: {reason}"}})
        await engine.funnel_shadow(sid, symbol, sig, settings, "NO_EJECUTADA", state["acc"], res["confidence"])
    return 1
