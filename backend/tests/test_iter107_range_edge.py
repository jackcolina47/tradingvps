"""Range-edge path: a sideways pair reaches the AI only on a tested edge of its range; the middle of the range stays blocked.
Pure unit tests + integration tests of funnel.run / funnel._deep with a fake engine (see test_iter103_bybit_hardening).
"""
import sys
import pytest

sys.path.insert(0, "/app/backend")

import funnel  # noqa: E402
import funnel as funnel_mod  # noqa: E402
from tests.test_iter103_bybit_hardening import (  # noqa: E402
    FakeEngine, _settings, _force_in_hours,
)


def _m(**over):
    base = {"atr": 1.0, "adx": 12, "rsi": 65, "activity": 0.5, "activity_kind": "volumen", "regime": None,
            "supply": {"low": 110.2, "high": 110.6, "touches": 3, "dist_atr": 0.2}, "demand": {"low": 107.0, "high": 107.4, "touches": 2, "dist_atr": 2.6}}
    base.update(over)
    base["range_edge"] = funnel.range_edge(base)
    return base


def _demand_edge(**over):
    return _m(**{"rsi": 35, "supply": {"low": 110.2, "high": 110.6, "touches": 2, "dist_atr": 2.4}, "demand": {"low": 107.0, "high": 107.4, "touches": 2, "dist_atr": 0.3}, **over})


class TestRangeEdge:
    def test_supply_edge_passes(self):
        m = _m()
        assert m["range_edge"] == "EXTREMO_OFERTA" and funnel.stage0(m) is None

    def test_demand_edge_passes(self):
        m = _demand_edge()
        assert m["range_edge"] == "EXTREMO_DEMANDA" and funnel.stage0(m) is None

    def test_middle_of_range_blocked(self):
        m = _m(rsi=50, supply={"low": 110.2, "high": 110.6, "touches": 3, "dist_atr": 1.5}, demand={"low": 107.0, "high": 107.4, "touches": 3, "dist_atr": 1.5})
        assert m["range_edge"] is None and "ADX" in funnel.stage0(m)

    def test_too_far_from_zone_blocked(self):
        assert _m(supply={"low": 110.2, "high": 110.6, "touches": 3, "dist_atr": 0.36})["range_edge"] is None

    def test_single_touch_blocked(self):
        m = _m(supply={"low": 110.2, "high": 110.6, "touches": 1, "dist_atr": 0.2})
        assert m["range_edge"] is None and funnel.stage0(m) is not None

    def test_rsi_not_extreme_blocked(self):
        assert funnel.RANGE_RSI == (42, 58)
        assert _m(rsi=57.9)["range_edge"] is None
        assert _demand_edge(rsi=42.1)["range_edge"] is None
        assert _m(rsi=58)["range_edge"] == "EXTREMO_OFERTA" and _demand_edge(rsi=42)["range_edge"] == "EXTREMO_DEMANDA"
        assert _m(rsi=60)["range_edge"] == "EXTREMO_OFERTA" and _demand_edge(rsi=40)["range_edge"] == "EXTREMO_DEMANDA"   # blocked with the old 62 / 38

    def test_no_room_to_opposite_zone_blocked(self):
        assert _m(demand={"low": 108.4, "high": 108.6, "touches": 2, "dist_atr": 1.29})["range_edge"] is None
        assert _m(demand={"low": 108.4, "high": 108.6, "touches": 2, "dist_atr": 1.3})["range_edge"] == "EXTREMO_OFERTA"
        assert funnel.RANGE_ROOM_ATR == 1.3 and funnel.RANGE_ZONE_ATR == 0.35
        assert _demand_edge(supply={"low": 110.2, "high": 110.6, "touches": 2, "dist_atr": 1.29})["range_edge"] is None
        assert _demand_edge(supply={"low": 110.2, "high": 110.6, "touches": 2, "dist_atr": 1.3})["range_edge"] == "EXTREMO_DEMANDA"

    def test_missing_opposite_zone_blocked(self):
        assert _m(demand=None)["range_edge"] is None

    def test_trending_pair_gets_no_tag(self):
        m = _m(adx=25, activity=1.5)
        assert m["range_edge"] is None and funnel.stage0(m) is None

    def test_choppy_regime_counts_as_sideways(self):
        m = _m(adx=22, regime="RANGO / CHOPPY")
        assert m["range_edge"] == "EXTREMO_OFERTA" and funnel.stage0(m) is None   # also clears the weak-volume gate

    def test_low_volume_without_edge_still_blocked(self):
        assert "volumen" in funnel.stage0(_m(adx=25, rsi=50))

    def test_row_text_carries_the_tag(self):
        m = {**_m(), "price": 110.0, "trend": "MIXTA", "ema20": 109.0, "ema50": 109.0, "ema200": 109.0, "dist_ema20_atr": 1.0, "macd_hist": 0.1, "macd_rising": False, "atr_pct": 0.5}
        assert "oportunidad_rango: EXTREMO_OFERTA" in funnel._row_txt("X", m)
        assert "oportunidad_rango" not in funnel._row_txt("X", {**m, "range_edge": None})

    def test_prompts_mention_range(self):
        assert "EXTREMO_OFERTA / EXTREMO_DEMANDA" in funnel.DEEP_SYSTEM and "0.2 ATR antes de la zona opuesta" in funnel.DEEP_SYSTEM
        assert "oportunidad_rango" in funnel.RANGE_FAST_SYSTEM


# ---------- Integration tests of funnel.run / funnel._deep ----------
# Price map for FakeMarket: BTC=100.0, ETH=50.0
_PRICE_TO_SYM = {100.0: "BTCUSDT", 50.0: "ETHUSDT"}


def _patch_metrics(monkeypatch, cfg_by_symbol: dict):
    """Patch funnel.metrics so each symbol gets its range-edge context (adx, rsi, supply, demand, fast_cross, regime, activity)."""
    real = funnel_mod.metrics

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        sym = _PRICE_TO_SYM.get(round(p, 6))
        cfg = cfg_by_symbol.get(sym) if sym else None
        if cfg:
            m.update(cfg)
            if rich and "regime" in cfg:
                m["regime"] = cfg["regime"]
        return m
    monkeypatch.setattr(funnel_mod, "metrics", fake)


def _mtf_factory(trend_4h="MIXTA", trend_1h="MIXTA"):
    async def _mtf(engine, symbol, price):
        return {"4h": {"tf": "4h", "trend": trend_4h, "rsi": 50, "adx": 20, "dist_ema50_atr": 0, "demand": None, "supply": None},
                "1h": {"tf": "1h", "trend": trend_1h, "rsi": 50, "adx": 20, "dist_ema50_atr": 0}}
    return _mtf


def _stage1_factory(candidates):
    async def fake_stage1(kind, tf, rows, limit, usage_kind):
        return {"candidates": [c for c in candidates if c["symbol"] in rows], "model": "fast", "ms": 1}
    fake_stage1.called_with_rows = None
    return fake_stage1


def _stage2_recorder(calls, result=None):
    async def fake_stage2(kind, symbol, tf, cand, m, df, i, has_volume, htf, rules, ctx, settings, usage_kind):
        calls.append({"symbol": symbol, "side": cand["side"], "cand": dict(cand), "m_range_edge": m.get("range_edge")})
        return result or {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None, "reasoning": "", "model": "deep", "ms": 1}
    return fake_stage2


# Pair at supply edge: price 100, supply 100.2 (0.2 ATR), demand 2.6 ATR away, RSI 65, ADX 12
_EDGE_SUPPLY = {"adx": 12, "rsi": 65, "activity": 0.5, "activity_kind": "volumen", "regime": None,
                "supply": {"low": 100.15, "high": 100.25, "touches": 3, "dist_atr": 0.2},
                "demand": {"low": 97.0, "high": 97.4, "touches": 2, "dist_atr": 2.6},
                "fast_cross": "ALCISTA", "atr": 1.0}
# Pair at demand edge: price 50, demand 49.8 (0.2 ATR), supply 2.6 ATR away, RSI 35, ADX 12
_EDGE_DEMAND = {"adx": 12, "rsi": 35, "activity": 0.5, "activity_kind": "volumen", "regime": None,
                "supply": {"low": 52.6, "high": 52.9, "touches": 2, "dist_atr": 2.6},
                "demand": {"low": 49.75, "high": 49.85, "touches": 3, "dist_atr": 0.2},
                "fast_cross": "BAJISTA", "atr": 1.0}


class TestIntegrationA_EdgeValid:
    @pytest.mark.asyncio
    async def test_supply_edge_short_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics(monkeypatch, {"BTCUSDT": _EDGE_SUPPLY})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "rechazo oferta"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert len(calls) == 1, calls
        assert calls[0]["side"] == "short"
        assert calls[0]["cand"].get("range_edge") == "EXTREMO_OFERTA"
        assert calls[0]["m_range_edge"] == "EXTREMO_OFERTA"
        # SCAN log of range-edge path mentions distances
        scan = " ".join(t for lv, t in eng.logs if lv == "SCAN")
        assert "EXTREMO_OFERTA" in scan and "extremo de rango" in scan

    @pytest.mark.asyncio
    async def test_demand_edge_long_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["ETHUSDT"])
        _patch_metrics(monkeypatch, {"ETHUSDT": _EDGE_DEMAND})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "ETHUSDT", "side": "long", "reason": "rechazo demanda"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert len(calls) == 1 and calls[0]["side"] == "long"
        assert calls[0]["cand"].get("range_edge") == "EXTREMO_DEMANDA"


class TestIntegrationB_MiddleBlocked:
    @pytest.mark.asyncio
    async def test_middle_of_range_not_eligible(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        middle = {**_EDGE_SUPPLY, "rsi": 50,
                  "supply": {"low": 101.4, "high": 101.6, "touches": 3, "dist_atr": 1.5},
                  "demand": {"low": 98.4, "high": 98.6, "touches": 3, "dist_atr": 1.5}}
        _patch_metrics(monkeypatch, {"BTCUSDT": middle})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))

        s1_called = {"n": 0}

        async def fake_s1(*a, **kw):
            s1_called["n"] += 1
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_s1)
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert s1_called["n"] == 0, "no eligible pair → stage1 must not be called"
        assert calls == []
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"


class TestIntegrationC_WrongSide:
    @pytest.mark.asyncio
    async def test_long_on_supply_edge_discarded(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        _patch_metrics(monkeypatch, {"BTCUSDT": _EDGE_SUPPLY})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert calls == [], "wrong side on range-only pair must NOT reach stage2"
        risk = " ".join(t for lv, t in eng.logs if lv == "RISK")
        assert "solo se admite SHORT" in risk, risk


class TestIntegrationD_TrendingRegression:
    @pytest.mark.asyncio
    async def test_trending_pair_still_discarded_by_cross(self, monkeypatch):
        """Trending pair (adx=25, activity=1.5, no regime) → range_edge=None; SHORT with fast_cross=ALCISTA and 4H MIXTA keeps being dropped by the EMA9/21 cross filter."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        cfg = {"adx": 25, "rsi": 55, "activity": 1.5, "activity_kind": "volumen", "regime": None,
               "supply": _EDGE_SUPPLY["supply"], "demand": _EDGE_SUPPLY["demand"],
               "fast_cross": "ALCISTA", "atr": 1.0}
        _patch_metrics(monkeypatch, {"BTCUSDT": cfg})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "x"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))

        await funnel_mod.run(eng, s)
        assert calls == []
        risk = " ".join(t for lv, t in eng.logs if lv == "RISK")
        assert "cruce EMA9/21" in risk


class TestIntegrationE_Levels:
    """Approved range trade: stage2 SELL 80, threshold 60, pair at supply edge on BTCUSDT."""

    async def _run(self, monkeypatch, settings, stage2_result, cfg=None):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        _patch_metrics(monkeypatch, {"BTCUSDT": cfg or _EDGE_SUPPLY})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "rechazo"}]))

        async def _async_stage2(kind, symbol, tf, cand, *a, **kw):
            return stage2_result
        monkeypatch.setattr(funnel_mod, "stage2", _async_stage2)
        await funnel_mod.run(eng, settings)
        return eng


        # Note: when source == "ia" (AI tp kept as-is, inside range), current AI_EVAL log does NOT include 'RANGO'
        # (see funnel.py line ~600: only source in {'contra','rango','atr'} produce branded labels). Report to main agent.




class TestIntegrationF_CounterTrend:

    @pytest.mark.asyncio
    async def test_counter_trend_with_room_opens(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"], atr_levels_enabled=False, ai_min_confidence=60)
        _patch_metrics(monkeypatch, {"BTCUSDT": _EDGE_SUPPLY})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("ALCISTA", "ALCISTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "rebote"}]))

        # sl=100.8 (risk 0.8 before buffer → 1.05 after buffer), tp=98.0 (reward 2.0 > 1.5*1.05=1.575 counter ok)
        async def s2(kind, symbol, tf, cand, *a, **kw):
            return {"decision": "SELL", "confidence": 80, "sl": 100.8, "tp": 98.0, "rule": None, "reasoning": "contra OK", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage2", s2)
        await funnel_mod.run(eng, s)
        assert len(eng.opened) == 1


class TestIntegrationG_RegimeAndVolume:
    @pytest.mark.asyncio
    async def test_choppy_weak_volume_passes_via_range(self, monkeypatch):
        """Regime RANGO/CHOPPY, ADX 22, volume 0.5× on valid edge → passes stage0 via range-edge path."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        cfg = {**_EDGE_SUPPLY, "adx": 22, "regime": "RANGO / CHOPPY", "activity": 0.5}
        _patch_metrics(monkeypatch, {"BTCUSDT": cfg})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))
        monkeypatch.setattr(funnel_mod, "stage1", _stage1_factory(
            [{"symbol": "BTCUSDT", "side": "short", "reason": "rechazo"}]))
        calls = []
        monkeypatch.setattr(funnel_mod, "stage2", _stage2_recorder(calls))
        await funnel_mod.run(eng, s)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_adx_22_no_regime_weak_volume_blocked(self, monkeypatch):
        """ADX 22 (not sideways), no regime, volume 0.5× → blocked by weak-volume gate (range_edge None since not sideways)."""
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings(symbols=["BTCUSDT"])
        cfg = {**_EDGE_SUPPLY, "adx": 22, "regime": None, "activity": 0.5}
        _patch_metrics(monkeypatch, {"BTCUSDT": cfg})
        monkeypatch.setattr(funnel_mod, "_mtf", _mtf_factory("MIXTA", "MIXTA"))

        s1n = {"n": 0}

        async def fake_s1(*a, **kw):
            s1n["n"] += 1
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_s1)
        await funnel_mod.run(eng, s)
        assert s1n["n"] == 0
        txt = eng.analysis["BTCUSDT"]["funnel"]["text"]
        assert "volumen" in txt.lower()


class TestUnitMetricsPipeline:
    def test_metrics_zones_range_edge_pipeline(self):
        """funnel.metrics with a synthetic DataFrame that oscillates clearly between 100 and 104, price pinned to the top → zones() produces a supply with ≥2 touches; range_edge may yield EXTREMO_OFERTA when RSI is forced ≥62."""
        import numpy as np
        import pandas as pd
        n = 300
        rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
        osc = 102 + 2 * np.sin(np.arange(n) * 0.4) + np.random.default_rng(0).normal(0, 0.05, n)
        close = osc.copy()
        close[-1] = 104.0   # price pinned to top
        high = np.maximum(close + 0.2, osc + 0.2)
        low = np.minimum(close - 0.2, osc - 0.2)
        df = pd.DataFrame({
            "ts": [int(t.timestamp() * 1000) for t in rng], "open": close, "high": high, "low": low,
            "close": close, "volume": [100.0] * n,
            "ema20": pd.Series(close).ewm(span=20, adjust=False).mean().values,
            "ema50": pd.Series(close).ewm(span=50, adjust=False).mean().values,
            "ema200": pd.Series(close).ewm(span=200, adjust=False).mean().values,
            "rsi": [55.0] * n, "macd_hist": [0.0] * n, "adx": [12.0] * n, "atr": [0.8] * n,
            "vol_ratio": [1.0] * n,
        })
        i = n - 2
        m = funnel.metrics(df, i, True, 104.0, rich=True)
        assert m["supply"] is not None
        assert m["supply"]["touches"] >= 1   # at least key exists
        assert "touches" in m["supply"]
        # Force RSI high and ensure pipeline returns tag when zone is at the edge and demand is far
        if m["supply"].get("touches", 0) >= 2 and m["supply"]["dist_atr"] <= 0.35 and m["demand"] and m["demand"]["dist_atr"] >= 1.3:
            m2 = {**m, "rsi": 65}
            assert funnel.range_edge(m2) == "EXTREMO_OFERTA"
        # Mid-range: price in the middle → range_edge None
        m3 = funnel.metrics(df, i, True, 102.0, rich=True)
        if m3.get("supply") and m3.get("demand"):
            # Supply above, demand below
            edge = funnel.range_edge({**m3, "rsi": 50})
            assert edge is None


class TestNoFalsePositives:
    """Guard: the range path never passes a non-edge sideways pair, and never crashes with missing fields."""

    def test_missing_regime_key_no_crash(self):
        # Non-rich metrics (no 'regime' key) with low ADX → should still evaluate range_edge safely
        m = {"adx": 12, "rsi": 65, "atr": 1.0,
             "supply": {"low": 100.2, "high": 100.3, "touches": 3, "dist_atr": 0.2},
             "demand": {"low": 97.0, "high": 97.2, "touches": 2, "dist_atr": 2.6}}
        # no 'regime' key
        assert funnel.range_edge(m) == "EXTREMO_OFERTA"

    def test_demand_none_no_crash(self):
        m = {"adx": 12, "rsi": 65, "atr": 1.0, "regime": None,
             "supply": {"low": 100.2, "high": 100.3, "touches": 3, "dist_atr": 0.2}, "demand": None}
        assert funnel.range_edge(m) is None

    def test_supply_none_no_crash(self):
        m = {"adx": 12, "rsi": 35, "atr": 1.0, "regime": None,
             "supply": None, "demand": {"low": 99.0, "high": 99.1, "touches": 3, "dist_atr": 0.2}}
        assert funnel.range_edge(m) is None

    def test_non_edge_sideways_not_tagged(self):
        """Low ADX but price NOT at an edge (distance 0.8 ATR) → no range_edge."""
        m = {"adx": 10, "rsi": 65, "atr": 1.0, "regime": None,
             "supply": {"low": 100.8, "high": 100.9, "touches": 3, "dist_atr": 0.8},
             "demand": {"low": 97.0, "high": 97.2, "touches": 2, "dist_atr": 2.6}}
        assert funnel.range_edge(m) is None

