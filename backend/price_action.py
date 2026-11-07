"""Price-action facts computed from raw candles (dicts with open/high/low/close) so the AI can judge «chasing» entries: extended moves (several large
same-colour candles), nearby swing highs/lows acting as resistance/support, and distance of SL/TP to those levels. Pure functions, no I/O."""


def _pivots(c: list, k: int = 3):
    highs, lows = [], []
    for i in range(k, len(c) - k):
        win = c[i - k:i + k + 1]
        if c[i]["high"] >= max(x["high"] for x in win):
            highs.append((i, c[i]["high"]))
        if c[i]["low"] <= min(x["low"] for x in win):
            lows.append((i, c[i]["low"]))
    return highs, lows


def _atr(c: list, n: int = 14) -> float:
    trs = []
    for i in range(1, len(c)):
        h, l, pc = c[i]["high"], c[i]["low"], c[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    tail = trs[-n:] or [0.0]
    return sum(tail) / len(tail)


def facts(candles: list, side: str, entry: float, atr: float = 0.0, sl: float = None, tp: float = None, max_run: int = 3, swing_atr: float = 1.0) -> dict:
    """`candles` = closed candles BEFORE the entry (oldest first). Returns metrics + Spanish `summary` lines.
    `max_run` = consecutive large candles from which the move counts as extended; `swing_atr` = ATR distance under which an opposing swing counts as «too close»."""
    c = [x for x in candles if all(k in x for k in ("open", "high", "low", "close"))]
    if len(c) < 10:
        return {"summary": ["Sin suficientes velas previas para evaluar la acción del precio."]}
    atr = float(atr or 0) or _atr(c) or 1e-9
    long = side == "long"
    last = c[-5:]
    bodies = [(x["close"] - x["open"]) / atr for x in last]
    run = 0
    for b in reversed(bodies):
        if (b > 0) == long and abs(b) > 0.05:
            run += 1
        else:
            break
    run_size = round(sum(abs(b) for b in bodies[-run:]), 2) if run else 0.0
    big_run = run >= max(1, int(max_run)) and run_size >= 2.0
    move_pct = round((c[-1]["close"] - c[-run - 1]["close"]) / c[-run - 1]["close"] * 100, 3) if run else 0.0

    highs, lows = _pivots(c[-60:])
    ref = c[-1]["close"]
    above = [p for _, p in highs if p > ref]
    below = [p for _, p in lows if p < ref]
    swing_h = min(above) if above else None
    swing_l = max(below) if below else None
    out = {
        "atr": round(atr, 6), "consecutive_candles": run, "run_size_atr": run_size, "run_move_pct": move_pct, "extended_move": big_run,
        "swing_high": swing_h, "swing_low": swing_l,
        "swing_high_atr": round((swing_h - ref) / atr, 2) if swing_h else None,
        "swing_low_atr": round((ref - swing_l) / atr, 2) if swing_l else None,
        "range_20_pos_pct": None,
    }
    hi20 = max(x["high"] for x in c[-20:])
    lo20 = min(x["low"] for x in c[-20:])
    if hi20 > lo20:
        out["range_20_pos_pct"] = round((ref - lo20) / (hi20 - lo20) * 100, 1)
    if tp:
        tgt = swing_h if long else swing_l
        out["tp_beyond_swing"] = bool(tgt and ((long and tp > tgt) or (not long and tp < tgt)))
    lines = []
    dir_txt = "alcistas" if long else "bajistas"
    if run:
        lines.append(f"Entrada tras {run} vela(s) {dir_txt} seguidas que suman {run_size} ATR ({move_pct:+.2f} %)." + (" MOVIMIENTO EXTENDIDO: riesgo de perseguir el precio." if big_run else ""))
    else:
        lines.append("La última vela va en contra de la dirección de la operación (no se persigue el precio).")
    opp = out["swing_high_atr"] if long else out["swing_low_atr"]
    opp_lvl = swing_h if long else swing_l
    if opp is not None:
        near = opp <= float(swing_atr)
        lines.append(f"{'Resistencia' if long else 'Soporte'} (swing previo) en {opp_lvl:.6g}, a {opp} ATR" + (" — MUY CERCA: poco recorrido antes de un nivel que puede frenar el precio." if near else "."))
        out["near_opposing_swing"] = near
        out["opposing_swing"], out["opposing_swing_atr"] = opp_lvl, opp
        if tp and out.get("tp_beyond_swing"):
            lines.append("El TP queda MÁS ALLÁ de ese swing: para cobrar debería romperlo.")
    else:
        out["near_opposing_swing"] = False
        lines.append(f"Sin swing {'alto' if long else 'bajo'} previo cercano en las últimas 60 velas (camino libre).")
    if out["range_20_pos_pct"] is not None:
        lines.append(f"Precio en el {out['range_20_pos_pct']} % del rango de las últimas 20 velas" + (" (zona alta para un LONG)." if long and out["range_20_pos_pct"] > 80 else " (zona baja para un SHORT)." if not long and out["range_20_pos_pct"] < 20 else "."))
    out["summary"] = lines
    out["warning"] = bool(big_run or out.get("near_opposing_swing"))
    return out


def from_df(df, side: str, entry: float, atr: float = 0.0, sl: float = None, tp: float = None, upto: int = None, max_run: int = 3, swing_atr: float = 1.0) -> dict:
    """Helper for engines: `df` pandas OHLC frame; `upto` = index of the signal candle (exclusive)."""
    sub = df.iloc[-80:upto] if upto is not None else df.iloc[-80:]
    rows = [{"open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)} for r in sub.itertuples()]
    return facts(rows, side, entry, atr, sl, tp, max_run, swing_atr)


def hard_block(pa: dict) -> str:
    """Deterministic gate: reason text if the entry chases an extended move or sits against a nearby swing, else ''."""
    if not pa or not pa.get("warning"):
        return ""
    parts = []
    if pa.get("extended_move"):
        parts.append(f"{pa['consecutive_candles']} velas grandes seguidas ({pa['run_size_atr']} ATR)")
    if pa.get("near_opposing_swing"):
        parts.append(f"swing contrario a {pa['opposing_swing_atr']} ATR ({pa['opposing_swing']:.6g})")
    return "precio extendido: " + " y ".join(parts)


def prompt_lines(pa: dict) -> str:
    if not pa or not pa.get("summary"):
        return ""
    return "Acción del precio antes de la entrada: " + " ".join(pa["summary"]) + "\n"
