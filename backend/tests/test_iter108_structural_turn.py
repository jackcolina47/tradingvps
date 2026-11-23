"""Early structural turn of the entry timeframe (GIRO_ALCISTA / GIRO_BAJISTA_CONFIRMADO), detected whatever 4H / 1H still say."""
import numpy as np
import pandas as pd
import pytest
import funnel
from indicators import add_indicators
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def _frame(direction=1, impulse_vol=8.0, breakout=True, n=320):
    """Slow drift against `direction`, then an impulse candle with volume that breaks the last swing, then two small candles in favour."""
    rng = np.random.default_rng(7)
    close = 100 - direction * np.linspace(0, 6, n) + 0.25 * np.sin(np.arange(n) / 3) + rng.normal(0, 0.03, n)
    vol = np.full(n, 100.0)
    if breakout:
        k = n - 5
        close[k:] = close[k - 1] + direction * np.array([1.0, 3.5, 4.2, 4.6, 4.8])
        vol[k + 1] = 100.0 * impulse_vol
    op = np.r_[close[0], close[:-1]]
    df = pd.DataFrame({"ts": np.arange(n) * 900_000, "open": op, "high": np.maximum(op, close) + 0.05, "low": np.minimum(op, close) - 0.05, "close": close, "volume": vol})
    return add_indicators(df)


def _metrics(df, rich=True, has_volume=True):
    return funnel.metrics(df, len(df) - 2, has_volume, float(df["close"].iloc[-1]), rich)


class TestDetection:
    def test_bullish_turn_while_slow_emas_are_bearish(self):
        m = _metrics(_frame(1))
        assert m["trend"] != "ALCISTA"   # EMA20/50/200 of the entry timeframe have not turned yet
        assert m["fast_cross"] == "ALCISTA" and m["adx_slope"] == "SUBIENDO" and m["price"] > m["swing_high"]
        assert m["turn"] == "GIRO_ALCISTA_CONFIRMADO"

    def test_bearish_turn_is_symmetric(self):
        m = _metrics(_frame(-1))
        assert m["fast_cross"] == "BAJISTA" and m["price"] < m["swing_low"] and m["turn"] == "GIRO_BAJISTA_CONFIRMADO"

    def test_no_volume_impulse_no_turn(self):
        assert _metrics(_frame(1, impulse_vol=1.5))["turn"] is None

    def test_no_breakout_no_turn(self):
        assert _metrics(_frame(1, breakout=False))["turn"] is None

    def test_impulse_older_than_five_candles_no_turn(self):
        df = _frame(1)
        df.loc[df.index[-4], "vol_ratio"] = 1.0
        df.loc[df.index[-9], "vol_ratio"] = 9.0
        assert _metrics(df)["turn"] is None

    def test_adx_not_rising_no_turn(self):
        df = _frame(1)
        df.loc[df.index[-2], "adx"] = float(df["adx"].iloc[-3])
        m = _metrics(df)
        assert m["adx_slope"] != "SUBIENDO" and m["turn"] is None

    def test_price_back_below_swing_no_turn(self):
        df = _frame(1)
        m = funnel.metrics(df, len(df) - 2, True, float(df["close"].iloc[-8]), True)
        assert m["turn"] is None

    def test_plain_engine_has_no_field(self):
        assert "turn" not in _metrics(_frame(1), rich=False)

    def test_without_real_volume_uses_candle_range(self):
        assert _metrics(_frame(1), has_volume=False)["turn"] == "GIRO_ALCISTA_CONFIRMADO"


class TestText:
    def test_row_shows_the_field(self):
        m = {**_metrics(_frame(1)), "tf": "15m"}
        assert "giro_estructura_15m: GIRO_ALCISTA_CONFIRMADO" in funnel._row_txt("X", m)
        assert "giro_estructura_15m: NINGUNO" in funnel._row_txt("X", {**m, "turn": None})

    def test_prompts(self):
        assert "deja de proponer SHORT" in funnel.TURN_FAST_SYSTEM and "GIRO_BAJISTA_CONFIRMADO" in funnel.TURN_FAST_SYSTEM
        assert "NO compres en la cresta de una vela expansiva (> 2.5 ATR)" in funnel.DEEP_SYSTEM


async def _mtf_bearish(engine, symbol, price):
    return {"4h": {"tf": "4h", "trend": "BAJISTA", "rsi": 40, "adx": 25, "dist_ema50_atr": -0.5, "demand": None, "supply": None},
            "1h": {"tf": "1h", "trend": "BAJISTA", "rsi": 45, "adx": 20, "dist_ema50_atr": -0.3}}


@pytest.mark.asyncio
async def test_long_turn_against_bearish_4h_reaches_strong_model(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng = FakeEngine(mtf=True, funnel_rich=True)
    real, seen = funnel.metrics, {}

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        m.update(adx=25, activity=2.0, rsi=60, dist_ema20_atr=1.0, fast_cross="ALCISTA", turn="GIRO_ALCISTA_CONFIRMADO", regime=None)
        return m
    monkeypatch.setattr(funnel, "metrics", fake)
    monkeypatch.setattr(funnel, "_mtf", _mtf_bearish)

    async def fake_stage1(kind, tf, rows, limit, usage_kind):
        seen["rows"] = "\n".join(funnel._row_txt(s, m) for s, m in rows.items())
        return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "retroceso tras ruptura"}], "model": "fast", "ms": 1}

    async def fake_stage2(kind, symbol, tf, cand, m, *a, **kw):
        seen["cand"], seen["turn"] = dict(cand), m.get("turn")
        return {"decision": "PASS", "confidence": 30, "sl": None, "tp": None, "rule": None, "reasoning": "x", "model": "deep", "ms": 1}
    monkeypatch.setattr(funnel, "stage1", fake_stage1)
    monkeypatch.setattr(funnel, "stage2", fake_stage2)
    await funnel.run(eng, _settings())
    assert "GIRO_ALCISTA_CONFIRMADO" in seen["rows"]
    assert seen["cand"]["side"] == "long" and seen["cand"]["counter_trend"] is True and seen["turn"] == "GIRO_ALCISTA_CONFIRMADO"
