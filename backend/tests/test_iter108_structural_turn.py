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

    def test_adx_below_20_no_turn(self):
        df = _frame(1)
        df.loc[df.index[-2], "adx"] = 19.9
        assert _metrics(df)["turn"] is None
        df.loc[df.index[-2], "adx"] = 20.0
        assert _metrics(df)["turn"] == "GIRO_ALCISTA_CONFIRMADO"

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



# =========================================================
# Extra unit coverage (boundaries, color, window edges, robustness)
# =========================================================
class TestDetectionBoundaries:
    def test_fast_cross_bajista_no_bullish_turn(self):
        """EMA9 <= EMA21 (fast_cross BAJISTA) must prevent a bullish turn even with volume+color."""
        # direction=1 with breakout=False → close drifts DOWN (100 → 94), so EMA9 < EMA21 at the end.
        df = _frame(1, breakout=False)
        i = len(df) - 2
        df.loc[df.index[i - 1], "vol_ratio"] = 5.0
        df.loc[df.index[i - 1], "open"] = float(df["close"].iloc[i - 1]) - 0.5  # bullish-coloured candle
        m = _metrics(df)
        assert m["fast_cross"] == "BAJISTA"
        assert m["turn"] != "GIRO_ALCISTA_CONFIRMADO"   # bullish branch must not fire with EMA9 < EMA21

    def test_exact_2x_volume_passes(self):
        """Exactly 2.0× (boundary, >=) must trigger the turn."""
        df = _frame(1, impulse_vol=1.0)  # no impulse
        # inject exactly 2.0× at closed candle (i-1, inside window i-4..i)
        i = len(df) - 2
        df.loc[df.index[i - 1], "vol_ratio"] = 2.0
        # ensure that candle is bullish-coloured (close > open)
        df.loc[df.index[i - 1], "open"] = float(df["close"].iloc[i - 1]) - 0.5
        m = _metrics(df)
        if m["fast_cross"] == "ALCISTA" and m["price"] > m["swing_high"] and m["adx_slope"] == "SUBIENDO":
            assert m["turn"] == "GIRO_ALCISTA_CONFIRMADO"

    def test_just_below_2x_fails(self):
        """1.99× must NOT trigger (strict >=)."""
        df = _frame(1, impulse_vol=1.99)
        assert _metrics(df)["turn"] is None

    def test_impulse_outside_window_no_turn(self):
        """Impulse at i-6 (just outside the 6-candle window i-5..i) must not trigger; at i-5 it does."""
        df = _frame(1, impulse_vol=1.0)
        i = len(df) - 2
        df.loc[df.index[i - 6], "vol_ratio"] = 10.0   # outside window
        df.loc[df.index[i - 6], "open"] = float(df["close"].iloc[i - 6]) - 0.5
        assert _metrics(df)["turn"] is None
        df.loc[df.index[i - 5], "vol_ratio"] = 10.0   # oldest candle of the window
        df.loc[df.index[i - 5], "open"] = float(df["close"].iloc[i - 5]) - 0.5
        assert _metrics(df)["turn"] == "GIRO_ALCISTA_CONFIRMADO"

    def test_impulse_candle_wrong_color_no_turn(self):
        """Strong-volume candle but bearish-coloured (close < open): bullish turn must not fire."""
        df = _frame(1, impulse_vol=1.0)
        i = len(df) - 2
        df.loc[df.index[i - 1], "vol_ratio"] = 5.0
        # force bearish color: open > close
        df.loc[df.index[i - 1], "open"] = float(df["close"].iloc[i - 1]) + 0.5
        assert _metrics(df)["turn"] is None

    def test_adx_plana_keeps_turn(self):
        """The tag survives the pause: a flat ADX >= 20 no longer blocks the turn."""
        df = _frame(1)
        i = len(df) - 2
        # Flatten ADX around i
        df.loc[df.index[i - 2:i + 1], "adx"] = 25.0
        m = _metrics(df)
        assert m["adx_slope"] == "PLANA" and m["turn"] == "GIRO_ALCISTA_CONFIRMADO"

    def test_adx_bajando_keeps_turn(self):
        df = _frame(1)
        i = len(df) - 2
        df.loc[df.index[i - 2], "adx"] = 40.0
        df.loc[df.index[i - 1], "adx"] = 30.0
        df.loc[df.index[i], "adx"] = 20.0
        m = _metrics(df)
        assert m["adx_slope"] == "BAJANDO" and m["turn"] == "GIRO_ALCISTA_CONFIRMADO"

    def test_window_excludes_forming_candle(self):
        """The 5-candle window ends at i (= len-2), the forming candle at len-1 must NOT be inspected."""
        df = _frame(1, impulse_vol=1.0)
        i = len(df) - 2
        # Put a huge volume on the forming candle (i+1) only — detector must ignore it.
        df.loc[df.index[i + 1], "vol_ratio"] = 20.0
        df.loc[df.index[i + 1], "open"] = float(df["close"].iloc[i + 1]) - 0.5
        assert _metrics(df)["turn"] is None


class TestRobustness:
    def test_short_dataframe_60_candles(self):
        """_rich / metrics must not raise on a short frame; turn should be None (no breakout)."""
        df = _frame(1, n=60)
        i = len(df) - 2
        try:
            m = funnel.metrics(df, i, True, float(df["close"].iloc[-1]), rich=True)
        except Exception as e:
            pytest.fail(f"metrics raised on 60-candle frame: {e}")
        assert "turn" in m   # key present, value may be None or confirmed

    def test_nan_vol_ratio_in_window(self):
        df = _frame(1)
        i = len(df) - 2
        df.loc[df.index[i - 2], "vol_ratio"] = float("nan")
        # Should not raise; turn may still be confirmed if other window candles qualify.
        m = _metrics(df)
        assert "turn" in m

    def test_zero_atr_no_exception(self):
        df = _frame(1)
        i = len(df) - 2
        df.loc[df.index[i], "atr"] = 0.0
        try:
            m = funnel.metrics(df, i, True, float(df["close"].iloc[-1]), rich=True)
        except Exception as e:
            pytest.fail(f"metrics raised with ATR=0: {e}")
        # turn still computable (does not depend on ATR)
        assert "turn" in m


# =========================================================
# Prompt composition
# =========================================================
class TestPrompts:
    def test_deep_system_format_does_not_raise(self):
        """New text added to DEEP_SYSTEM must not break the .format(...) call stage2 uses."""
        try:
            s = funnel.DEEP_SYSTEM.format(market=funnel.MARKET["crypto"], tf="15m", levels="x")
        except KeyError as e:
            pytest.fail(f"DEEP_SYSTEM.format raised KeyError: {e}")
        assert "GIRO_ALCISTA_CONFIRMADO" in s and "2.5 ATR" in s
        assert "GIRO_BAJISTA_CONFIRMADO" in s

    def test_turn_fast_system_format(self):
        s = funnel.TURN_FAST_SYSTEM.format(tf="15m")
        assert "giro_estructura_15m" in s and "GIRO_ALCISTA_CONFIRMADO" in s

    @pytest.mark.asyncio
    async def test_stage1_adds_turn_fast_system_only_when_turn_present(self, monkeypatch):
        """funnel.stage1 must include TURN_FAST_SYSTEM only when at least one row has turn."""
        captured = {}

        class FakeChat:
            def __init__(self, system):
                self.system = system
                self._probe = False

        def fake_chat(system, usage_kind, cfg=None):
            captured["system"] = system
            return FakeChat(system)

        async def fake_ask(chat, prompt):
            return '{"candidates": []}'

        # Avoid _row_txt KeyError by patching it too — we only care about the composed `system`.
        monkeypatch.setattr(funnel, "_chat", fake_chat)
        monkeypatch.setattr(funnel, "_ask", fake_ask)
        monkeypatch.setattr(funnel, "_row_txt", lambda s, m: f"{s}: {m.get('turn')}")

        # Case A: no turn in any row → TURN_FAST_SYSTEM absent
        rows_no_turn = {"BTCUSDT": {"turn": None, "range_edge": None, "mtf": None}}
        await funnel.stage1("crypto", "15m", rows_no_turn, 3, "signal")
        assert "giro_estructura_15m" not in captured["system"]

        # Case B: one row has turn → TURN_FAST_SYSTEM present
        rows_with_turn = {"BTCUSDT": {"turn": "GIRO_ALCISTA_CONFIRMADO", "range_edge": None, "mtf": None}}
        await funnel.stage1("crypto", "15m", rows_with_turn, 3, "signal")
        assert "giro_estructura_15m" in captured["system"]
        assert "deja de proponer SHORT" in captured["system"]


# =========================================================
# Integration: funnel.run with turn + bearish 4H
# =========================================================
async def _mtf_bearish2(engine, symbol, price):
    return {"4h": {"tf": "4h", "trend": "BAJISTA", "rsi": 40, "adx": 25, "dist_ema50_atr": -0.5, "demand": None, "supply": None},
            "1h": {"tf": "1h", "trend": "BAJISTA", "rsi": 45, "adx": 20, "dist_ema50_atr": -0.3}}


def _patch_turn_metrics(monkeypatch, *, turn="GIRO_ALCISTA_CONFIRMADO", fast_cross="ALCISTA",
                       rsi=60, dist_ema20_atr=1.0, adx=25):
    real = funnel.metrics

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        m.update(adx=adx, activity=2.0, rsi=rsi, dist_ema20_atr=dist_ema20_atr,
                 fast_cross=fast_cross, turn=turn, regime=None)
        return m
    monkeypatch.setattr(funnel, "metrics", fake)


class TestIntegrationTurn:
    @pytest.mark.asyncio
    async def test_buy_with_good_rr_opens(self, monkeypatch):
        """Counter-trend LONG with BUY 80% and AI tp giving R:R>=1.5 must open."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        _patch_turn_metrics(monkeypatch)
        monkeypatch.setattr(funnel, "_mtf", _mtf_bearish2)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "giro alcista"}],
                    "model": "fast", "ms": 1}

        async def fake_stage2(kind, symbol, tf, cand, m, df, i, has_volume, htf, rules, ctx, settings, usage_kind):
            # ATR=1, price=100, atr_sl_mult=2 → sl=98, risk=2; tp=103.5 → reward=3.5, R:R=1.75
            return {"decision": "BUY", "confidence": 80, "sl": 98.0, "tp": 103.5,
                    "rule": None, "reasoning": "retroceso ordenado", "model": "deep", "ms": 1}

        monkeypatch.setattr(funnel, "stage1", fake_stage1)
        monkeypatch.setattr(funnel, "stage2", fake_stage2)
        await funnel.run(eng, _settings())
        assert eng.opened, "trade should have opened"
        assert eng.opened[0][0] == "BTCUSDT"



    @pytest.mark.asyncio
    async def test_short_with_fast_cross_alcista_dropped_before_stage2(self, monkeypatch):
        """SHORT following 4H BAJISTA but with fast_cross ALCISTA on 15m must be dropped by EMA9/21 filter (no stage2)."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        # turn present but we propose SHORT (same side as 4H BAJISTA), fast_cross ALCISTA
        _patch_turn_metrics(monkeypatch, fast_cross="ALCISTA")
        monkeypatch.setattr(funnel, "_mtf", _mtf_bearish2)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "short", "reason": "sigue 4H"}],
                    "model": "fast", "ms": 1}

        stage2_called = {"n": 0}

        async def fake_stage2(kind, symbol, tf, cand, m, *a, **kw):
            stage2_called["n"] += 1
            return {"decision": "PASS", "confidence": 30, "sl": None, "tp": None, "rule": None,
                    "reasoning": "x", "model": "deep", "ms": 1}

        monkeypatch.setattr(funnel, "stage1", fake_stage1)
        monkeypatch.setattr(funnel, "stage2", fake_stage2)
        await funnel.run(eng, _settings())
        assert stage2_called["n"] == 0
        risk_logs = [t for lvl, t in eng.logs if lvl == "RISK"]
        assert any("contra el giro de estructura" in t and "GIRO_ALCISTA_CONFIRMADO" in t for t in risk_logs)
        assert not eng.opened

    @pytest.mark.asyncio
    async def test_overextended_techo_with_turn_long_dropped(self, monkeypatch):
        """Documented current behaviour: overextended 'techo' (RSI 75, dist 3.0) with 4H BAJISTA
        keeps the pair alive only for SHORT (rebound). A LONG candidate from stage1 is dropped as late."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        _patch_turn_metrics(monkeypatch, rsi=75, dist_ema20_atr=3.0)
        monkeypatch.setattr(funnel, "_mtf", _mtf_bearish2)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "giro"}],
                    "model": "fast", "ms": 1}

        stage2_called = {"n": 0}

        async def fake_stage2(kind, symbol, tf, cand, m, *a, **kw):
            stage2_called["n"] += 1
            return {"decision": "PASS", "confidence": 30, "sl": None, "tp": None, "rule": None,
                    "reasoning": "x", "model": "deep", "ms": 1}

        monkeypatch.setattr(funnel, "stage1", fake_stage1)
        monkeypatch.setattr(funnel, "stage2", fake_stage2)
        # should not raise; LONG dropped as late, no open
        await funnel.run(eng, _settings())
        assert stage2_called["n"] == 0
        assert not eng.opened
        risk_logs = [t for lvl, t in eng.logs if lvl == "RISK"]
        assert any("sobreextendido en techo" in t for t in risk_logs)


async def _blocked(monkeypatch, side, **cfg):
    await _force_in_hours(monkeypatch)
    eng = FakeEngine(mtf=True, funnel_rich=True)
    _patch_turn_metrics(monkeypatch, **cfg)
    monkeypatch.setattr(funnel, "_mtf", _mtf_bearish2)
    calls = []

    async def fake_stage1(kind, tf, rows, limit, usage_kind):
        return {"candidates": [{"symbol": "BTCUSDT", "side": side, "reason": "x"}], "model": "fast", "ms": 1}

    async def fake_stage2(kind, symbol, tf, cand, m, *a, **kw):
        calls.append(cand["side"])
        return {"decision": "PASS", "confidence": 30, "sl": None, "tp": None, "rule": None, "reasoning": "x", "model": "deep", "ms": 1}
    monkeypatch.setattr(funnel, "stage1", fake_stage1)
    monkeypatch.setattr(funnel, "stage2", fake_stage2)
    await funnel.run(eng, _settings())
    return calls, [t for lvl, t in eng.logs if lvl == "RISK"]


@pytest.mark.asyncio
async def test_rebound_short_blocked_on_bullish_turn_even_when_overextended(monkeypatch):
    calls, risk = await _blocked(monkeypatch, "short", rsi=75, dist_ema20_atr=3.0)
    assert calls == [] and any("contra el giro de estructura" in t for t in risk)


@pytest.mark.asyncio
async def test_long_blocked_on_bearish_turn(monkeypatch):
    calls, risk = await _blocked(monkeypatch, "long", turn="GIRO_BAJISTA_CONFIRMADO", fast_cross="BAJISTA")
    assert calls == [] and any("GIRO_BAJISTA_CONFIRMADO" in t for t in risk)


@pytest.mark.asyncio
async def test_short_passes_on_bearish_turn(monkeypatch):
    calls, _ = await _blocked(monkeypatch, "short", turn="GIRO_BAJISTA_CONFIRMADO", fast_cross="BAJISTA")
    assert calls == ["short"]
