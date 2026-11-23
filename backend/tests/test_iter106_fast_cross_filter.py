"""Iter106 — funnel.run drops stage1 candidates that go against the EMA9/21 cross of the entry timeframe,
without spending tokens in stage2. Exceptions: counter_trend (against 4H bias) and over-extended rebound.
Reuses FakeEngine / fake_metrics / _force_in_hours from test_iter103_bybit_hardening.
"""
import sys
import pytest

sys.path.insert(0, "/app/backend")

import funnel as funnel_mod  # noqa: E402
from tests.test_iter103_bybit_hardening import (  # noqa: E402
    FakeEngine, _settings, _force_in_hours, _patch_metrics_stretched,
)


def _patch_metrics_cross(monkeypatch, cross_by_symbol: dict):
    """Patch funnel.metrics so each symbol gets its fast_cross set as instructed (when the engine is rich)."""
    real = funnel_mod.metrics

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        # pick the symbol via price (FakeMarket: BTC=100, ETH=50)
        sym = {100.0: "BTCUSDT", 50.0: "ETHUSDT"}.get(round(p, 6))
        if rich and sym and sym in cross_by_symbol:
            m["fast_cross"] = cross_by_symbol[sym]
        return m
    monkeypatch.setattr(funnel_mod, "metrics", fake)


async def _mtf_factory(trend_4h="MIXTA", trend_1h="MIXTA"):
    async def _mtf(engine, symbol, price):
        return {"4h": {"tf": "4h", "trend": trend_4h, "rsi": 50, "adx": 20, "dist_ema50_atr": 0, "demand": None, "supply": None},
                "1h": {"tf": "1h", "trend": trend_1h, "rsi": 50, "adx": 20, "dist_ema50_atr": 0}}
    return _mtf


def _stage1_factory(candidates):
    async def fake_stage1(kind, tf, rows, limit, usage_kind):
        # Only return candidates whose symbol is in the eligible rows
        return {"candidates": [c for c in candidates if c["symbol"] in rows],
                "model": "fast", "ms": 1}
    return fake_stage1


def _stage2_recorder(calls):
    async def fake_stage2(kind, symbol, tf, cand, *a, **kw):
        calls.append({"symbol": symbol, "side": cand["side"],
                      "counter_trend": bool(cand.get("counter_trend"))})
        return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None,
                "rule": None, "reasoning": "", "model": "deep", "ms": 1}
    return fake_stage2


# ---------- (1) Constants ----------
class TestConstants:
    def test_fast_cross_side_table(self):
        assert funnel_mod.FAST_CROSS_SIDE == {"long": "ALCISTA", "short": "BAJISTA"}

    def test_real_engines_are_rich(self):
        import engine as eng_mod
        assert getattr(eng_mod.TradingEngine, "funnel_rich", False) is True
        from fx.engine_signals import SignalsMixin
        assert getattr(SignalsMixin, "funnel_rich", False) is True

    def test_fx_bots_are_rich(self):
        import deps
        for engine, _store in deps.FX_BOTS:
            assert getattr(engine, "funnel_rich", False) is True, engine


# ---------- (2) MIXTA 4H (bias=None) — discard matrix ----------
class TestFastCrossDiscardMixta:
    @pytest.mark.asyncio
    async def test_short_against_alcista_cross_discarded(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "ALCISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "ruptura"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert calls == [], "stage2 must NOT be called"
        risk = [t for lv, t in eng.logs if lv == "RISK"]
        assert any("cruce EMA9/21" in t and "descartado sin IA potente" in t for t in risk), risk
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"
        assert eng.analysis["BTCUSDT"]["funnel"]["stage"] == 1

    @pytest.mark.asyncio
    async def test_long_against_bajista_cross_discarded(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "BAJISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert calls == []
        assert any("cruce EMA9/21" in t for lv, t in eng.logs if lv == "RISK")
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"

    @pytest.mark.asyncio
    async def test_short_with_bajista_cross_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "BAJISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert [(c["symbol"], c["side"]) for c in calls] == [("BTCUSDT", "short")]

    @pytest.mark.asyncio
    async def test_long_with_alcista_cross_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "ALCISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert [(c["symbol"], c["side"]) for c in calls] == [("BTCUSDT", "long")]


# ---------- (3) Counter-trend exception ----------
class TestCounterTrendException:
    @pytest.mark.asyncio
    async def test_short_counter_alcista_passes_with_alcista_cross(self, monkeypatch):
        """4H ALCISTA → bias=long; stage1 proposes SHORT → counter_trend=True; despite fast_cross=ALCISTA, stage2 IS called."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "ALCISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("ALCISTA", "ALCISTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "rebote"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert len(calls) == 1 and calls[0]["side"] == "short"
        assert calls[0]["counter_trend"] is True

    @pytest.mark.asyncio
    async def test_long_counter_bajista_passes_with_bajista_cross(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "BAJISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("BAJISTA", "BAJISTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "long", "reason": "rebote"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert len(calls) == 1 and calls[0]["side"] == "long"
        assert calls[0]["counter_trend"] is True


# ---------- (4) With 4H but against the cross (no overext) → discarded ----------
class TestWith4HAgainstCross:
    @pytest.mark.asyncio
    async def test_long_with_4h_alcista_but_bajista_cross_discarded(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "BAJISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("ALCISTA", "ALCISTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert calls == []
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"


# ---------- (5) Over-extended rebound exception ----------
class TestOverextRebound:
    @pytest.mark.asyncio
    async def test_suelo_long_with_bajista_cross_passes(self, monkeypatch):
        """Pair in 'suelo' (RSI 28, dist -2.5) + 4H ALCISTA + LONG candidate + fast_cross=BAJISTA → stage2 IS called."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_stretched(monkeypatch, 100.0, "suelo")
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "BAJISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("ALCISTA", "ALCISTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "long", "reason": "rebote suelo"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert len(calls) == 1 and calls[0]["side"] == "long"

    @pytest.mark.asyncio
    async def test_suelo_short_still_dropped_by_overext(self, monkeypatch):
        """In 'suelo' with 4H ALCISTA, SHORT is dropped by over-extended rule, not by the new cross rule (behaviour preserved)."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics_stretched(monkeypatch, 100.0, "suelo")
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "ALCISTA"})
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("ALCISTA", "ALCISTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert calls == []
        risk = " ".join(t for lv, t in eng.logs if lv == "RISK")
        assert "sobreextendido" in risk
        assert "cruce EMA9/21" not in risk


# ---------- (6) Non-rich engines not affected ----------
class TestNonRichEngineNotAffected:
    @pytest.mark.asyncio
    async def test_no_fast_cross_in_metrics_no_discard(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(kind="fx", mtf=False, funnel_rich=False)
        s = _settings(symbols=["BTCUSDT"])
        # Do NOT patch fast_cross: non-rich metrics don't have the key
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert [(c["symbol"], c["side"]) for c in calls] == [("BTCUSDT", "short")]


# ---------- (7) Multiple candidates: only the one against the cross is removed ----------
class TestMultipleCandidates:
    @pytest.mark.asyncio
    async def test_three_candidates_one_dropped(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        # Add a 3rd symbol to FakeMarket & settings
        eng.market.tickers["SOLUSDT"] = {"price": 25.0}
        s = _settings(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        # BTC: SHORT vs ALCISTA → must be dropped
        # ETH: LONG vs ALCISTA → keep
        # SOL: SHORT vs BAJISTA → keep
        _patch_metrics_cross(monkeypatch, {"BTCUSDT": "ALCISTA", "ETHUSDT": "ALCISTA"})
        # SOLUSDT not in dict; patch separately
        real = funnel_mod.metrics

        def fake(df, i, has_volume, p, rich=False):
            m = real(df, i, has_volume, p, rich)
            sym = {100.0: "BTCUSDT", 50.0: "ETHUSDT", 25.0: "SOLUSDT"}.get(round(p, 6))
            if rich and sym:
                m["fast_cross"] = {"BTCUSDT": "ALCISTA", "ETHUSDT": "ALCISTA", "SOLUSDT": "BAJISTA"}[sym]
            return m
        monkeypatch.setattr(funnel_mod, "metrics", fake)
        monkeypatch.setattr(funnel_mod, "_mtf", await _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory([
            {"symbol": "BTCUSDT", "side": "short", "reason": "a"},
            {"symbol": "ETHUSDT", "side": "long", "reason": "b"},
            {"symbol": "SOLUSDT", "side": "short", "reason": "c"},
        ]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        got = sorted((c["symbol"], c["side"]) for c in calls)
        assert got == [("ETHUSDT", "long"), ("SOLUSDT", "short")]
        # SCAN summary log counts 2 candidates
        scan = [t for lv, t in eng.logs if lv == "SCAN" and "candidato" in t]
        assert any("2 candidato" in t for t in scan), scan
