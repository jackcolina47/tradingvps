from typing import Optional
from indicators import snapshot

STRATEGY_INFO = {
    "trend_ema": {
        "name": "Tendencia Multi-Confirmación",
        "rules": "Cruce EMA9/EMA21 a favor de EMA200 + RSI 50-70 (long) / 30-50 (short) + MACD + ADX/Volumen. SL 1.5×ATR, TP 3×ATR (R:R 1:2).",
        "basis": "Trend following clásico (Dennis, Seykota): cortar pérdidas rápido y dejar correr ganancias con filtro de tendencia mayor.",
    },
    "mean_reversion": {
        "name": "Reversión a la Media",
        "rules": "Cierre fuera de Bandas de Bollinger(20,2) que regresa al interior + RSI extremo + ADX<25 (mercado lateral). TP en banda media, SL 1.5×ATR.",
        "basis": "Bollinger + RSI en rangos: alta tasa de acierto cuando no hay tendencia fuerte.",
    },
    "breakout_turtle": {
        "name": "Ruptura Turtle (Donchian 20)",
        "rules": "Ruptura del máximo/mínimo de 20 velas con volumen ≥1.5× y ADX>20 a favor de EMA50. SL 2×ATR (N), TP 4×ATR.",
        "basis": "Sistema de las Tortugas de Richard Dennis (1983), uno de los casos de éxito más documentados del trading sistemático.",
    },
    "supertrend_momentum": {
        "name": "Supertrend + Momentum",
        "rules": "Giro del Supertrend(10, 3×ATR) en las últimas 2 velas confirmado por precio sobre/bajo EMA50, RSI a favor (>50 / <50) y ADX con fuerza. SL 2×ATR, TP 4×ATR.",
        "basis": "Seguimiento de tendencia con stop dinámico de volatilidad (Olivier Seban); popular en cripto por filtrar el ruido lateral.",
    },
    "ema_pullback": {
        "name": "Pullback a EMA21",
        "rules": "Tendencia definida (EMA50 sobre/bajo EMA200 y precio a favor), retroceso que toca la EMA21 y vela de continuación que cierra de nuevo a favor con RSI sin sobreextensión. SL 1.5×ATR, TP 3×ATR.",
        "basis": "Compra de retrocesos en tendencia (Raschke, Minervini): entrar a favor de la tendencia con un stop más ajustado que en la ruptura.",
    },
    "ema_cross": {
        "name": "Cruce EMA 10/20",
        "rules": "La EMA10 cruza la EMA20 al cierre de la vela (al alza → largo, a la baja → corto) con el precio a favor de la EMA200 (tendencia mayor) y ADX>20 (sin laterales); en criterios estrictos las EMAs deben ir ordenadas 10/20/50. SL de protección 2×ATR, objetivo lejano 6×ATR (3R) y salida clásica: se cierra en cuanto las EMAs vuelven a cruzarse al revés, dejando correr la tendencia (el trailing del bot, si está activo, también puede cerrarla antes). Apagada por defecto.",
        "basis": "Cruce de medias rápido de los swing traders (9/21 ≈ 10/20). En backtests publicados ronda el 40-47 % de acierto con profit factor 1,6-3 en activos con tendencia; su punto débil son los rangos, por eso se exige la media de 200 y ADX.",
    },
}

MODES = {
    "strict": dict(trend_rsi=(50, 70), trend_macd=True, trend_ema200=True, trend_min_score=5, adx_conf=20, vol_conf=1.0,
                   mr_adx=25, mr_rsi=35, turtle_vol=1.5, turtle_adx=20, st_adx=20, pb_adx=20, pb_rsi=(40, 65), xc_adx=20, xc_ema200=True, xc_stack=True),
    "relaxed": dict(trend_rsi=(45, 75), trend_macd=True, trend_ema200=True, trend_min_score=4, adx_conf=15, vol_conf=0.8,
                    mr_adx=30, mr_rsi=40, turtle_vol=1.2, turtle_adx=15, st_adx=15, pb_adx=15, pb_rsi=(35, 70), xc_adx=15, xc_ema200=True, xc_stack=False),
    "minimal": dict(trend_rsi=(40, 80), trend_macd=False, trend_ema200=False, trend_min_score=4, adx_conf=10, vol_conf=0.6,
                    mr_adx=35, mr_rsi=45, turtle_vol=1.0, turtle_adx=10, st_adx=10, pb_adx=10, pb_rsi=(30, 75), xc_adx=0, xc_ema200=False, xc_stack=False),
}
MODE_INFO = {
    "strict": {"name": "Estrictos", "desc": "Umbrales originales: todas las confirmaciones deben coincidir"},
    "relaxed": {"name": "Relajados", "desc": "RSI y ADX más amplios, volumen menos exigente: ~2× más señales"},
    "minimal": {"name": "Mínimos", "desc": "Solo la condición principal de cada estrategia: máxima frecuencia, mínima calidad"},
}


def _signal(side, row, sl, tp, strategy, reasons, score, key_ts, tp_mode="atr"):
    return {
        "side": side, "strategy": strategy, "price": float(row["close"]), "sl": float(sl), "tp": float(tp),
        "atr": float(row["atr"]), "score": score, "reasons": reasons, "key_ts": int(key_ts), "tp_mode": tp_mode,
        "indicators": snapshot(row),
    }


def _k(params, key, default):
    return float((params or {}).get(key) or default)


def _rsi_ok(rsi, band, long):
    lo, hi = band
    return lo < rsi < hi if long else (100 - hi) < rsi < (100 - lo)


def _against_htf(htf, long):
    return htf is not None and htf == (-1 if long else 1)


def _with_htf(htf, long):
    return htf is not None and htf == (1 if long else -1)


def _atr_levels(r, long, params, sl_def, tp_def):
    atr = r["atr"]
    sl_k, tp_k = _k(params, "sl_atr", sl_def), _k(params, "tp_atr", tp_def)
    sl = r["close"] - sl_k * atr if long else r["close"] + sl_k * atr
    tp = r["close"] + tp_k * atr if long else r["close"] - tp_k * atr
    return sl, tp


def trend_ema(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    r = df.iloc[i]
    cross = None
    for j in range(i, i - 3, -1):
        a, b = df.iloc[j], df.iloc[j - 1]
        if a["ema9"] > a["ema21"] and b["ema9"] <= b["ema21"]:
            cross = ("long", int(a["ts"]))
            break
        if a["ema9"] < a["ema21"] and b["ema9"] >= b["ema21"]:
            cross = ("short", int(a["ts"]))
            break
    if not cross:
        return None
    side, key_ts = cross
    long = side == "long"
    aligned = r["ema9"] > r["ema21"] if long else r["ema9"] < r["ema21"]
    ref = r["ema200"] if m["trend_ema200"] else r["ema50"]
    trend_ok = r["close"] > ref if long else r["close"] < ref
    rsi_ok = _rsi_ok(r["rsi"], m["trend_rsi"], long)
    macd_ok = (r["macd_hist"] > 0 if long else r["macd_hist"] < 0) if m["trend_macd"] else True
    if not (aligned and trend_ok and rsi_ok and macd_ok) or _against_htf(htf, long):
        return None
    reasons = [
        f"Cruce {'alcista' if long else 'bajista'} EMA9/EMA21",
        f"Precio {'sobre' if long else 'bajo'} EMA{200 if m['trend_ema200'] else 50}",
        f"RSI {r['rsi']:.1f} en zona de momentum",
    ]
    if m["trend_macd"]:
        reasons.append(f"MACD histograma {'positivo' if long else 'negativo'}")
    score = 4
    if r["adx"] > m["adx_conf"]:
        score += 1
        reasons.append(f"ADX {r['adx']:.1f} (tendencia con fuerza)")
    if r["vol_ratio"] >= m["vol_conf"]:
        score += 1
        reasons.append(f"Volumen {r['vol_ratio']:.2f}× el promedio")
    if _with_htf(htf, long):
        score += 1
        reasons.append("Temporalidad superior a favor")
    if score < m["trend_min_score"]:
        return None
    sl, tp = _atr_levels(r, long, params, 1.5, 3.0)
    return _signal(side, r, sl, tp, "trend_ema", reasons, score, key_ts)


def mean_reversion(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    r, p = df.iloc[i], df.iloc[i - 1]
    if r["adx"] >= m["mr_adx"]:
        return None
    atr = r["atr"]
    sl_k = _k(params, "sl_atr", 1.5)
    if p["close"] < p["bb_low"] and r["close"] > r["bb_low"] and p["rsi"] < m["mr_rsi"]:
        side, sl, tp = "long", r["close"] - sl_k * atr, r["bb_mid"]
    elif p["close"] > p["bb_up"] and r["close"] < r["bb_up"] and p["rsi"] > 100 - m["mr_rsi"]:
        side, sl, tp = "short", r["close"] + sl_k * atr, r["bb_mid"]
    else:
        return None
    if abs(tp - r["close"]) < abs(r["close"] - sl):
        return None
    long = side == "long"
    reasons = [
        f"Reingreso a Bollinger desde banda {'inferior' if long else 'superior'}",
        f"RSI previo {p['rsi']:.1f} en {'sobreventa' if long else 'sobrecompra'}",
        f"ADX {r['adx']:.1f}: mercado lateral",
        "Objetivo en la media de 20 periodos",
    ]
    score = 4 + (1 if r["vol_ratio"] >= m["vol_conf"] else 0)
    return _signal(side, r, sl, tp, "mean_reversion", reasons, score, r["ts"], tp_mode="fixed")


def breakout_turtle(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    r, p = df.iloc[i], df.iloc[i - 1]
    if r["vol_ratio"] < m["turtle_vol"] or r["adx"] <= m["turtle_adx"]:
        return None
    if r["close"] > r["don_high"] and p["close"] <= p["don_high"] and r["close"] > r["ema50"]:
        side = "long"
    elif r["close"] < r["don_low"] and p["close"] >= p["don_low"] and r["close"] < r["ema50"]:
        side = "short"
    else:
        return None
    long = side == "long"
    if _against_htf(htf, long):
        return None
    sl, tp = _atr_levels(r, long, params, 2.0, 4.0)
    reasons = [
        f"Ruptura del {'máximo' if long else 'mínimo'} de 20 velas (Donchian)",
        f"Volumen {r['vol_ratio']:.2f}× el promedio",
        f"ADX {r['adx']:.1f}",
        f"Precio {'sobre' if long else 'bajo'} EMA50",
    ]
    score = 5 + (1 if _with_htf(htf, long) else 0)
    return _signal(side, r, sl, tp, "breakout_turtle", reasons, score, r["ts"])


def supertrend_momentum(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    r = df.iloc[i]
    flip = None
    for j in (i, i - 1):
        a, b = df.iloc[j], df.iloc[j - 1]
        if a["st_dir"] != 0 and b["st_dir"] != 0 and a["st_dir"] != b["st_dir"]:
            flip = ("long" if a["st_dir"] == 1 else "short", int(a["ts"]))
            break
    if not flip:
        return None
    side, key_ts = flip
    long = side == "long"
    still = r["st_dir"] == (1 if long else -1)
    ema_ok = r["close"] > r["ema50"] if long else r["close"] < r["ema50"]
    rsi_ok = r["rsi"] > 50 if long else r["rsi"] < 50
    if not (still and ema_ok and rsi_ok and r["adx"] > m["st_adx"]) or _against_htf(htf, long):
        return None
    reasons = [
        f"Supertrend girado a {'alcista' if long else 'bajista'} en {r['st']:.4g}",
        f"Precio {'sobre' if long else 'bajo'} EMA50",
        f"RSI {r['rsi']:.1f} {'>' if long else '<'} 50 (momentum a favor)",
        f"ADX {r['adx']:.1f}",
    ]
    score = 4 + (1 if r["adx"] > 25 else 0) + (1 if r["vol_ratio"] >= m["vol_conf"] else 0) + (1 if _with_htf(htf, long) else 0)
    sl, tp = _atr_levels(r, long, params, 2.0, 4.0)
    return _signal(side, r, sl, tp, "supertrend_momentum", reasons, score, key_ts)


def ema_pullback(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    r, p = df.iloc[i], df.iloc[i - 1]
    if r["adx"] <= m["pb_adx"]:
        return None
    up = r["ema50"] > r["ema200"] and r["close"] > r["ema50"]
    down = r["ema50"] < r["ema200"] and r["close"] < r["ema50"]
    if up and p["low"] <= p["ema21"] and r["close"] > r["ema21"] and r["close"] > r["open"] and _rsi_ok(r["rsi"], m["pb_rsi"], True):
        side = "long"
    elif down and p["high"] >= p["ema21"] and r["close"] < r["ema21"] and r["close"] < r["open"] and _rsi_ok(r["rsi"], m["pb_rsi"], False):
        side = "short"
    else:
        return None
    long = side == "long"
    if _against_htf(htf, long):
        return None
    reasons = [
        f"Tendencia {'alcista' if long else 'bajista'}: EMA50 {'sobre' if long else 'bajo'} EMA200",
        f"Retroceso tocó la EMA21 y la vela cierra de nuevo {'por encima' if long else 'por debajo'}",
        f"RSI {r['rsi']:.1f} sin sobreextensión",
        f"ADX {r['adx']:.1f}",
    ]
    score = 4 + (1 if r["adx"] > 25 else 0) + (1 if r["vol_ratio"] >= m["vol_conf"] else 0) + (1 if _with_htf(htf, long) else 0)
    sl, tp = _atr_levels(r, long, params, 1.5, 3.0)
    return _signal(side, r, sl, tp, "ema_pullback", reasons, score, r["ts"])


def ema_cross(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    r = df.iloc[i]
    cross = None
    for j in (i, i - 1):  # the cross on this closed candle or the previous one (the bot may scan a little late)
        a, b = df.iloc[j], df.iloc[j - 1]
        if a["ema10"] > a["ema20"] and b["ema10"] <= b["ema20"]:
            cross = ("long", int(a["ts"]))
            break
        if a["ema10"] < a["ema20"] and b["ema10"] >= b["ema20"]:
            cross = ("short", int(a["ts"]))
            break
    if not cross:
        return None
    side, key_ts = cross
    long = side == "long"
    aligned = r["ema10"] > r["ema20"] if long else r["ema10"] < r["ema20"]
    ref_n = 200 if m["xc_ema200"] else 50
    trend_ok = r["close"] > r[f"ema{ref_n}"] if long else r["close"] < r[f"ema{ref_n}"]
    stack_ok = (r["ema20"] > r["ema50"] if long else r["ema20"] < r["ema50"]) if m["xc_stack"] else True
    if not (aligned and trend_ok and stack_ok and r["adx"] > m["xc_adx"]) or _against_htf(htf, long):
        return None
    reasons = [
        f"EMA10 cruza {'al alza' if long else 'a la baja'} la EMA20 al cierre de la vela",
        f"Precio {'sobre' if long else 'bajo'} EMA{ref_n} (tendencia mayor a favor)",
    ]
    if m["xc_adx"]:
        reasons.append(f"ADX {r['adx']:.1f} > {m['xc_adx']}: mercado con tendencia, no lateral")
    if m["xc_stack"]:
        reasons.append("EMAs ordenadas 10/20/50")
    score = 4
    if (r["rsi"] > 50) == long:
        score += 1
        reasons.append(f"RSI {r['rsi']:.1f} a favor del momentum")
    if r["vol_ratio"] >= m["vol_conf"]:
        score += 1
        reasons.append(f"Volumen {r['vol_ratio']:.2f}× el promedio")
    if _with_htf(htf, long):
        score += 1
        reasons.append("Temporalidad superior a favor")
    reasons.append("Salida: cierre al cruce contrario de las EMAs (SL 2×ATR de protección)")
    sl, tp = _atr_levels(r, long, params, 2.0, 6.0)
    return _signal(side, r, sl, tp, "ema_cross", reasons, score, key_ts)


EMA_TEST = "ema_test"
EMA_TEST_TAG = "Cruce EMAs Especial"
STRATEGY_INFO[EMA_TEST] = {
    "name": "Test cruce EMA 10/20",
    "rules": "EXPERIMENTAL · Cruce puro de EMA10/EMA20 al cierre de la vela: al alza abre largo, a la baja abre corto. Sin SL ni TP: la posición se cierra "
             "(y se invierte) en el siguiente cruce contrario. Sin filtros, sin IA de aprobación. Solo en los pares del test.",
    "basis": "Sistema stop-and-reverse clásico de medias móviles para medir la rentabilidad bruta del cruce antes de añadirle filtros.",
}


def ema_test(df, i, htf=None, params=None, m=None) -> Optional[dict]:
    """Raw EMA10/EMA20 cross on the closed candle `i`. The SL/TP returned are VIRTUAL (2×ATR / 6×ATR): used only for position sizing and R metrics, never executed."""
    a, b = df.iloc[i], df.iloc[i - 1]
    if a["ema10"] > a["ema20"] and b["ema10"] <= b["ema20"]:
        side = "long"
    elif a["ema10"] < a["ema20"] and b["ema10"] >= b["ema20"]:
        side = "short"
    else:
        return None
    long = side == "long"
    sl, tp = _atr_levels(a, long, None, 2.0, 6.0)
    reasons = [f"EMA10 cruza {'al alza' if long else 'a la baja'} la EMA20 al cierre de la vela",
               "Test experimental: sin filtros, sin SL/TP · salida y giro en el siguiente cruce contrario"]
    return {**_signal(side, a, sl, tp, EMA_TEST, reasons, 3, int(a["ts"])), "exit_mode": "cross"}


STRATEGIES = {"trend_ema": trend_ema, "mean_reversion": mean_reversion, "breakout_turtle": breakout_turtle,
              "supertrend_momentum": supertrend_momentum, "ema_pullback": ema_pullback, "ema_cross": ema_cross, EMA_TEST: ema_test}
TEST_ONLY = {EMA_TEST}  # never part of «all»: only via its own toggle
EXIT_ON_CROSS = {"ema_cross", EMA_TEST}
CROSS_ONLY = {EMA_TEST}  # no SL/TP at all: the opposite cross is the only exit
EXIT_REASON = "CRUCE"


def exit_signal(strategy: str, side: str, ema10: float, ema20: float) -> bool:
    """Strategy-specific exit at candle close: ema_cross leaves as soon as the fast EMA is back on the wrong side of the slow one."""
    if strategy not in EXIT_ON_CROSS:
        return False
    return ema10 < ema20 if side == "long" else ema10 > ema20


def active_strategies(strategy: str, disabled=()) -> list:
    if strategy != "all":
        return [strategy] if strategy in STRATEGIES else []
    return [k for k in STRATEGIES if k not in set(disabled or ()) and k not in TEST_ONLY]


def evaluate(df, i, strategy: str, htf=None, params=None, mode: str = "strict", disabled=()) -> Optional[dict]:
    m = MODES.get(mode, MODES["strict"])
    for k in active_strategies(strategy, disabled):
        sig = STRATEGIES[k](df, i, htf, params, m)
        if sig:
            return sig
    return None
