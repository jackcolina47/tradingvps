"""Iter109 — lower MIN_VOLUME from 0.8 to 0.55 in funnel.py.

Checks:
  (A) Constants: MIN_VOLUME == 0.55, MIN_ADX == 18.
  (B) stage0 / trend_gate behaviour on the volume path (activity_kind='volumen', adx 25):
      0.54 → discarded with reason mentioning 'volumen' and '0.55';
      0.55, 0.6, 0.78 and 0.8 → None (pass).
      With activity_kind != 'volumen' (fx/rango), activity 0.3 passes (only ADX filter).
      ADX 17.9 still discards even with high volume (ADX takes precedence).
  (C) Range-edge path unchanged: sideways pair (adx 12) on a valid edge still passes stage0
      with activity 0.3; centre of range still blocked.
  (D) Structural turn constants intact: TURN_VOLUME 2.0, TURN_BARS 6, TURN_ADX 20.
  (E) Integration funnel.run with FakeEngine (stage1/stage2 patched, no real tokens):
      adx 25, volume 0.6× → reaches stage1 (not dropped by stage0);
      volume 0.5× → dropped in stage0, FILTER log mentions it.

Run isolated:   cd /app/backend && python -m pytest tests/test_iter109_min_volume.py -n 0 -q
"""
import sys
import pytest

sys.path.insert(0, "/app/backend")

import funnel as funnel_mod  # noqa: E402

# Reuse FakeEngine, _settings, _force_in_hours from the iter103 suite
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours  # noqa: E402


# ---------------- A. constants ----------------
class TestConstants:
    def test_min_volume_is_055(self):
        assert funnel_mod.MIN_VOLUME == 0.55

    def test_min_adx_is_18(self):
        assert funnel_mod.MIN_ADX == 18

    def test_structural_turn_constants_unchanged(self):
        assert funnel_mod.TURN_VOLUME == 2.0
        assert funnel_mod.TURN_BARS == 6
        assert funnel_mod.TURN_ADX == 20


# ---------------- B. stage0 / trend_gate ----------------
def _m(**over):
    base = {"atr": 1.0, "adx": 25.0, "activity": 1.0, "activity_kind": "volumen",
            "demand": {"dist_atr": 2.0}, "supply": {"dist_atr": 2.0}}
    base.update(over)
    return base


class TestStage0VolumeThreshold:
    def test_volume_054_discarded(self):
        r = funnel_mod.stage0(_m(activity=0.54))
        assert r is not None
        assert "volumen" in r
        assert "0.55" in r

    def test_trend_gate_054_discarded(self):
        r = funnel_mod.trend_gate(_m(activity=0.54))
        assert r is not None
        assert "volumen" in r and "0.55" in r

    @pytest.mark.parametrize("activity", [0.55, 0.6, 0.78, 0.8, 1.0, 2.0])
    def test_volume_at_or_above_055_passes(self, activity):
        assert funnel_mod.stage0(_m(activity=activity)) is None
        assert funnel_mod.trend_gate(_m(activity=activity)) is None

    def test_non_volume_kind_ignores_activity(self):
        # fx without broker-volume → activity_kind != 'volumen'; low activity must pass
        assert funnel_mod.stage0(_m(activity=0.3, activity_kind="rango")) is None
        assert funnel_mod.trend_gate(_m(activity=0.3, activity_kind="rango")) is None

    def test_low_adx_still_discards_even_with_high_volume(self):
        r = funnel_mod.stage0(_m(adx=17.9, activity=2.0))
        assert r is not None
        assert "ADX" in r

    def test_low_adx_discards_before_volume_check(self):
        # ADX fails first; message should talk about ADX, not about volume
        r = funnel_mod.trend_gate(_m(adx=17.9, activity=0.1))
        assert r is not None
        assert "ADX" in r


# ---------------- C. range-edge path unchanged ----------------
class TestRangeEdgeUnchanged:
    def _rm(self, **over):
        # Sideways pair (adx 12) at a valid supply edge, with demand far away
        base = {
            "atr": 1.0, "adx": 12.0, "rsi": 65.0,
            "activity": 0.3, "activity_kind": "volumen",
            "supply": {"dist_atr": 0.2, "touches": 2, "low": 101.0, "high": 101.5},
            "demand": {"dist_atr": 2.5, "touches": 2, "low": 95.0, "high": 95.5},
        }
        base.update(over)
        return base

    def test_valid_edge_passes_stage0_with_low_volume(self):
        m = self._rm()
        m["range_edge"] = funnel_mod.range_edge(m)
        assert m["range_edge"] == "EXTREMO_OFERTA"
        # Even though volume (0.3) is below 0.55, range_edge path bypasses trend_gate
        assert funnel_mod.stage0(m) is None

    def test_centre_of_range_still_blocked(self):
        # No edge: both zones far away → range_edge=None, and low ADX → stage0 discards
        m = self._rm(supply={"dist_atr": 1.5, "touches": 2, "low": 101.0, "high": 101.5},
                     demand={"dist_atr": 1.5, "touches": 2, "low": 95.0, "high": 95.5})
        m["range_edge"] = funnel_mod.range_edge(m)
        assert m["range_edge"] is None
        r = funnel_mod.stage0(m)
        assert r is not None  # discarded (low ADX)
        assert "ADX" in r


# ---------------- E. integration via FakeEngine ----------------
def _patch_activity(monkeypatch, target_price, activity):
    real_metrics = funnel_mod.metrics

    def fake_metrics(df, i, has_volume, p, rich=False):
        m = real_metrics(df, i, has_volume, p, rich)
        if abs(p - target_price) < 1e-9:
            m["activity"] = activity
            m["activity_kind"] = "volumen"
        return m
    monkeypatch.setattr(funnel_mod, "metrics", fake_metrics)


class TestFunnelRunIntegration:
    @pytest.mark.asyncio
    async def test_volume_060_reaches_stage1(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=False, funnel_rich=False)
        s = _settings()
        # BTCUSDT price=100.0, ETHUSDT price=50.0 (from FakeMarket)
        _patch_activity(monkeypatch, 100.0, 0.6)   # just above the new threshold
        _patch_activity_eth = funnel_mod.metrics  # keep ref (noqa)

        seen = {"stage1_rows": None, "stage2_calls": 0}

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            seen["stage1_rows"] = set(rows.keys())
            return {"candidates": [], "model": "fast", "ms": 1}

        async def fake_stage2(*a, **kw):
            seen["stage2_calls"] += 1
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None,
                    "rule": None, "reasoning": "", "model": "deep", "ms": 1}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)

        # With activity 0.6 on BTCUSDT it must reach stage1 (previously discarded with 0.8)
        assert seen["stage1_rows"] is not None
        assert "BTCUSDT" in seen["stage1_rows"]
        # funnel log must NOT report BTCUSDT as discarded in stage 0
        filter_logs = [t for lvl, t in eng.logs if lvl == "FILTER"]
        joined = " | ".join(filter_logs)
        assert "BTCUSDT (volumen" not in joined

    @pytest.mark.asyncio
    async def test_volume_050_dropped_in_stage0(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=False, funnel_rich=False)
        s = _settings()
        _patch_activity(monkeypatch, 100.0, 0.5)   # below the new threshold

        seen = {"stage1_rows": None, "stage2_calls": 0}

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            seen["stage1_rows"] = set(rows.keys())
            return {"candidates": [], "model": "fast", "ms": 1}

        async def fake_stage2(*a, **kw):
            seen["stage2_calls"] += 1
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None,
                    "rule": None, "reasoning": "", "model": "deep", "ms": 1}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)

        # BTCUSDT dropped before stage1
        assert seen["stage1_rows"] is not None
        assert "BTCUSDT" not in seen["stage1_rows"]
        # funnel marks it DESCARTADO at stage 0 with a 'volumen' reason
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"
        assert "volumen" in eng.analysis["BTCUSDT"]["funnel"]["text"]
        # and the aggregated stage-0 FILTER log mentions BTCUSDT + volumen
        filter_logs = [t for lvl, t in eng.logs if lvl == "FILTER"]
        joined = " | ".join(filter_logs)
        assert "BTCUSDT" in joined and "volumen" in joined
        assert seen["stage2_calls"] == 0
