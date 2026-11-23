# Iter116: mean_reversion tuned for real 1H ranges (strict mode) + rule isolation + Bollinger values for the AI.
import pandas as pd
import pytest
import ai_rules
import strategies as st

S = st.MODES["strict"]


def _df(side="long", **kw):
    o = dict(adx=18.0, rsi_prev=33.0, vol_prev=1.2, green=True, mid=101.2, swing=None, wick=False)
    o.update(kw)
    flat = dict(open=99.7, high=99.85, low=99.65, close=99.7, bb_up=102.4, bb_mid=o["mid"], bb_low=99.6, rsi=45.0, adx=o["adx"], atr=0.4, vol_ratio=1.0,
                ema9=100.5, ema10=100.5, ema20=100.6, ema21=100.6, ema50=100.7, ema200=100.8, macd_hist=0.0)
    rows = [dict(flat) for _ in range(30)]
    if o["swing"]:
        rows[15]["high"] = o["swing"]
    rows.append({**flat, "open": 99.9, "high": 100.0, "low": 99.3, "close": 99.4, "rsi": o["rsi_prev"], "vol_ratio": o["vol_prev"]})          # pierces the lower band
    op, cl = (99.5, 99.9) if o["green"] else (100.0, 99.9)
    rows.append({**flat, "open": op, "high": 100.0, "low": 99.0 if o["wick"] else 99.45 if o["green"] else 99.85, "close": cl})                                             # re-enters
    df = pd.DataFrame(rows)
    if side == "short":  # mirror everything around 100.6
        for c in ("open", "high", "low", "close", "bb_up", "bb_mid", "bb_low"):
            df[c] = 201.2 - df[c]
        df["high"], df["low"] = df["low"].copy(), df["high"].copy()
        df["bb_up"], df["bb_low"] = df["bb_low"].copy(), df["bb_up"].copy()
        df["rsi"] = 100 - df["rsi"]
    df["ts"] = range(len(df))
    return df


@pytest.mark.parametrize("side", ["long", "short"])
def test_clean_range_reentry_signals(side):
    df = _df(side)
    sig = st.mean_reversion(df, len(df) - 1, None, None, S)
    d = 1 if side == "long" else -1
    assert sig["side"] == side and sig["tp"] == pytest.approx(df["bb_mid"].iloc[-1] - d * 0.2 * 0.4)
    assert (sig["tp"] - sig["price"]) * d / abs(sig["price"] - sig["sl"]) >= 1.3
    ind = sig["indicators"]
    assert ind["bb_upper"] == ind["bb_up"] and ind["bb_lower"] == ind["bb_low"] and ind["bb_mid"] is not None


@pytest.mark.parametrize("kw", [dict(adx=22.0), dict(rsi_prev=35.1), dict(vol_prev=2.01), dict(green=False), dict(mid=100.7), dict(swing=100.75)])
def test_strict_rejections(kw):
    df = _df(**kw)
    assert st.mean_reversion(df, len(df) - 1, None, None, S) is None


def test_edges_and_rejection_wick_and_swing_target():
    for kw in (dict(adx=21.9), dict(rsi_prev=35.0), dict(vol_prev=2.0), dict(green=False, wick=True)):
        df = _df(**kw)
        assert st.mean_reversion(df, len(df) - 1, None, None, S) is not None, kw
    df = _df(swing=101.0)   # swing below the mean → TP in front of the swing
    assert st.mean_reversion(df, len(df) - 1, None, None, S)["tp"] == pytest.approx(101.0 - 0.08)


def test_other_modes_keep_legacy_behaviour_and_atr_levels_do_not_override():
    df = _df(green=False, vol_prev=3.0, adx=24.0)
    sig = st.mean_reversion(df, len(df) - 1, None, None, st.MODES["relaxed"])
    assert sig and sig["tp"] == pytest.approx(101.2)
    df = _df()
    sig = st.evaluate(df, len(df) - 1, "mean_reversion", None, {"atr_levels": (2.0, 4.0)}, "strict")
    assert sig["tp"] == pytest.approx(101.2 - 0.08) and "atr_levels" not in sig


def test_mean_reversion_skips_pullback_and_momentum_rules():
    keep = [{"text": "No operar en viernes por la tarde"}, {"text": "En reversión a la media, evitar si el momentum es fuerte"}, {"text": "No comprar en Bollinger inferior con ADX alto"}]
    drop = [{"text": "No compres pullbacks si el RSI supera 65"}, {"text": "En estrategias de momentum exigir volumen"}, {"text": "Evitar rupturas sin retesteo"}, {"text": "Breakout solo con ADX>25"}]
    assert all(ai_rules.relevant(r, "mean_reversion") for r in keep) and not any(ai_rules.relevant(r, "mean_reversion") for r in drop)
    assert all(ai_rules.relevant(r, "ema_pullback") for r in drop) and all(ai_rules.relevant(r, "breakout_turtle") for r in keep + drop)
