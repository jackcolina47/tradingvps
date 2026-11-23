# Iter111: 1H optimizations (Bybit + HotForex): rule relevance by strategy, stricter ema_pullback / supertrend, TP trimmed before swings, late-entry / spread guards, richer price action.
import pandas as pd
import pytest
import ai_rules
import price_action as pa
import strategies as st


def test_trend_signals_skip_mean_reversion_rules():
    mr = {"text": "No operar reversión a la media con ADX alto"}
    bb = {"text": "Evitar compras en la banda superior de BOLLINGER"}
    ok = {"text": "No comprar tras 3 velas grandes seguidas"}
    for s in ("ema_pullback", "supertrend_momentum", "trend_ema"):
        assert not ai_rules.relevant(mr, s) and not ai_rules.relevant(bb, s) and ai_rules.relevant(ok, s)
    for s in ("mean_reversion", "breakout_turtle", None):
        assert ai_rules.relevant(mr, s) and ai_rules.relevant(bb, s)


def test_active_filters_per_bot_bank():
    import asyncio

    class Cur:
        def __init__(s, docs): s.docs = docs
        def sort(s, *a): return s
        def __aiter__(s): return s._g()
        async def _g(s):
            for d in s.docs:
                yield d

    class Db:
        def __init__(s, docs): s.ai_rules = s; s.docs = docs
        def find(s, q): return Cur(s.docs)
    bybit = Db([{"_id": 1, "text": "Regla de Bollinger del banco Bybit"}, {"_id": 2, "text": "Regla general Bybit"}])
    hfm = Db([{"_id": 3, "text": "Regla general HotForex"}])
    run = lambda db, s: [r["text"] for r in asyncio.run(ai_rules.active(db, "X", "long", s))]  # noqa: E731
    assert run(bybit, "ema_pullback") == ["Regla general Bybit"] and len(run(bybit, "mean_reversion")) == 2
    assert run(hfm, "ema_pullback") == ["Regla general HotForex"]


def _pb_df(adx=22.0, ema9=101.2, ema21=101.0):
    base = dict(open=101.1, high=101.6, low=101.05, close=101.5, ema9=ema9, ema10=101.1, ema20=101.0, ema21=ema21, ema50=100.0, ema200=99.0, rsi=55.0, adx=adx, atr=1.0,
                vol_ratio=1.0, macd_hist=0.1, bb_up=103.0, bb_mid=101.0, bb_low=99.0, st=100.0, st_dir=1, ts=2)
    prev = {**base, "low": 100.9, "ts": 1, "ema21": ema21 - 0.05}   # EMA21 rising (iter117: structure needs its slope)
    return pd.DataFrame([prev, prev, base])


@pytest.mark.parametrize("mode", ["strict", "relaxed", "minimal"])
def test_ema_pullback_needs_fast_emas_aligned_and_adx_20(mode):
    m = st.MODES[mode]
    assert st.ema_pullback(_pb_df(), 2, None, None, m)["side"] == "long"
    assert st.ema_pullback(_pb_df(adx=20.0), 2, None, None, m) is not None
    assert st.ema_pullback(_pb_df(adx=19.9), 2, None, None, m) is None
    assert st.ema_pullback(_pb_df(ema9=100.9, ema21=101.0), 2, None, None, m) is None


@pytest.mark.parametrize("adx,ok", [(19.9, False), (20.0, True)])
def test_supertrend_adx_floor_in_minimal_mode(adx, ok):
    row = dict(open=100, high=102, low=99.5, close=101.5, ema9=101, ema10=101, ema20=100.5, ema21=100.5, ema50=100.0, ema200=99.0, rsi=60.0, adx=adx, atr=1.0, vol_ratio=1.0,
               macd_hist=0.1, bb_up=103.0, bb_mid=101.0, bb_low=99.0, st=99.0, st_dir=1, ts=3)
    df = pd.DataFrame([{**row, "st_dir": -1, "ts": 1}, {**row, "st_dir": -1, "ts": 2}, row])
    assert (st.supertrend_momentum(df, 2, None, None, st.MODES["minimal"]) is not None) is ok


def test_trim_tp_before_swing_only_if_rr_holds():
    sig = {"side": "long", "price": 100.0, "sl": 98.0, "tp": 106.0, "atr": 1.0}
    assert pa.trim_tp({"tp_beyond_swing": True, "swing_high": 104.0}, sig) == pytest.approx(103.8)   # R:R 1.9
    assert pa.trim_tp({"tp_beyond_swing": True, "swing_high": 103.0}, sig) is None                    # R:R 1.4 → TP unchanged
    assert pa.trim_tp({"tp_beyond_swing": False, "swing_high": 104.0}, sig) is None
    short = {"side": "short", "price": 100.0, "sl": 102.0, "tp": 94.0, "atr": 1.0}
    assert pa.trim_tp({"tp_beyond_swing": True, "swing_low": 96.0}, short) == pytest.approx(96.2)
    assert pa.trim_tp({"tp_beyond_swing": True, "swing_high": 104.0}, {**sig, "exit_mode": "cross"}) is None


def test_refine_trims_and_enriches():
    rows = [dict(open=99.5, high=99.9, low=99.2, close=99.6, ema9=100.1, ema21=100.0)] * 30
    rows += [dict(open=100.2, high=104.0, low=100.0, close=100.4, ema9=100.2, ema21=100.0)]       # swing high 104
    rows += [dict(open=99.5, high=99.9, low=99.2, close=99.6, ema9=100.2, ema21=100.0)] * 10
    rows += [dict(open=99.8, high=100.5, low=99.5, close=100.0, ema9=100.3, ema21=100.0), dict(open=100.0, high=100.1, low=99.9, close=100.05, ema9=100.3, ema21=100.0)]
    df = pd.DataFrame(rows)
    sig = {"side": "long", "price": 100.0, "sl": 98.0, "tp": 106.0, "atr": 1.0, "reasons": ["x"]}
    out = pa.refine(df, sig, pa.from_df(df, "long", 100.0, 1.0, 98.0, 106.0, upto=len(df) - 1), "4h", 1, -1)
    assert sig["tp"] == pytest.approx(103.8) and out["tp_trimmed_from"] == 106.0 and not out["tp_beyond_swing"] and "TP recortado" in sig["reasons"][-1]
    assert out["upper_wick_pct"] == 50.0 and out["lower_wick_pct"] == 30.0 and out["ema9_21_gap_atr"] == 0.3
    assert out["htf_trend"] == "ALCISTA" and out["d1_trend"] == "BAJISTA" and "1D: BAJISTA" in out["summary"][-1] and "Tendencia 4h: ALCISTA" in out["summary"][-1]


def test_late_entry_and_spread_guards():
    assert pa.late_entry("long", 100.0, 100.41, 1.0) and not pa.late_entry("long", 100.0, 100.4, 1.0)
    assert pa.late_entry("short", 100.0, 99.5, 1.0) and not pa.late_entry("short", 100.0, 100.9, 1.0)      # crypto: only a move in favour counts
    assert pa.late_entry("short", 100.0, 100.9, 1.0, both_ways=True) and not pa.late_entry("long", 100.0, None, 1.0)
    assert pa.wide_spread(0.16, 1.0) and not pa.wide_spread(0.15, 1.0) and not pa.wide_spread(None, 1.0)
