# Iter117: fast turns (no EMA50/EMA200 wait in ema_pullback / trend_ema) + «impulso rápido» trigger in supertrend_momentum.
import pandas as pd
import pytest
import strategies as st

S = st.MODES["strict"]
BASE = dict(open=101.1, high=101.6, low=101.05, close=101.5, ema9=101.2, ema10=101.1, ema20=101.0, ema21=101.0, ema50=100.0, ema200=103.0, rsi=58.0, adx=24.0, atr=1.0,
            vol_ratio=1.0, macd_hist=0.1, bb_up=103.0, bb_mid=101.0, bb_low=99.0, st=100.0, st_dir=1)


def _df(rows):
    df = pd.DataFrame(rows)
    df["ts"] = range(len(df))
    return df


def test_pullback_long_with_ema50_still_below_ema200():
    prev = {**BASE, "low": 100.9, "ema21": 100.95}
    assert st.ema_pullback(_df([prev, prev, BASE]), 2, None, None, S)["side"] == "long"            # EMA50 100 < EMA200 103: allowed now
    assert st.ema_pullback(_df([{**prev, "ema21": 101.0}, {**prev, "ema21": 101.05}, BASE]), 2, None, None, S) is None   # EMA21 falling
    assert st.ema_pullback(_df([prev, prev, {**BASE, "adx": 19.9}]), 2, None, None, S) is None       # ADX floor kept
    assert st.ema_pullback(_df([prev, prev, {**BASE, "ema50": 101.6}]), 2, None, None, S) is None    # close below EMA50


def test_trend_ema_cross_no_longer_needs_ema200_in_strict():
    before = {**BASE, "ema9": 100.9, "ema21": 100.95}
    df = _df([before, before, BASE])
    assert st.trend_ema(df, 2, None, None, S)["side"] == "long"                                      # close 101.5 < EMA200 103
    assert st.trend_ema(_df([before, before, {**BASE, "ema50": 101.6}]), 2, None, None, S) is None


def _impulse_rows(**kw):
    flat = {**BASE, "open": 100.0, "high": 100.3, "low": 99.7, "close": 100.0, "ema50": 100.2, "st_dir": -1, "adx": 23.0}
    return [dict(flat) for _ in range(14)] + [{**flat, "open": 100.0, "high": 101.7, "low": 99.9, "close": 101.6, "vol_ratio": 2.0, **kw}]


def test_impulse_fires_without_supertrend_flip_with_fixed_levels():
    df = _df(_impulse_rows())
    sig = st.supertrend_momentum(df, len(df) - 1, None, None, S)
    assert sig["side"] == "long" and sig["trigger"] == "impulse" and sig["sl"] == pytest.approx(101.6 - 1.8) and sig["tp"] == pytest.approx(101.6 + 3.2)
    out = st.evaluate(df, len(df) - 1, "supertrend_momentum", None, {"atr_levels": (2.0, 4.0)}, "strict")
    assert out["tp"] == pytest.approx(104.8)                                                          # dynamic ATR levels do not override the impulse levels


@pytest.mark.parametrize("kw", [dict(vol_ratio=1.79), dict(high=101.3, close=101.25), dict(adx=21.9), dict(close=100.25, high=101.7), dict(ema50=101.7)])
def test_impulse_rejections(kw):
    df = _df(_impulse_rows(**kw))
    assert st.supertrend_momentum(df, len(df) - 1, None, None, S) is None


def test_impulse_short():
    rows = _impulse_rows(open=100.0, high=100.1, low=98.3, close=98.4)
    for r in rows:
        r["ema50"] = 99.8
    df = _df(rows)
    assert st.supertrend_momentum(df, len(df) - 1, None, None, S)["side"] == "short"
