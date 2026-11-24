"""Exact reward:risk by regime: 2:1 in trend / breakout / counter-trend, 1:1 without partial close in range trades. Sizing stays on risk_per_trade."""
import pytest
import funnel
import risk
from models import Position
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def _s(fixed=False):
    return type("S", (), {"atr_levels_enabled": fixed, "atr_sl_mult": 2.0, "atr_tp_mult": 4.0})()


class TestStopLevel:
    def test_ai_stop_gets_buffer(self):
        assert funnel.stop_level("long", 100.0, 1.0, _s(), 99.0, None, 0.25) == (98.75, "ia")
        assert funnel.stop_level("short", 100.0, 1.0, _s(), 101.0, None, 0.25) == (101.25, "ia")

    def test_fixed_or_insane_stop_uses_atr(self):
        assert funnel.stop_level("long", 100.0, 1.0, _s(True), 99.0, None, 0.25) == (98.0, "atr")
        assert funnel.stop_level("long", 100.0, 1.0, _s(), 99.8, None, 0.25) == (98.0, "atr")   # closer than 0.5 ATR
        assert funnel.stop_level("long", 100.0, 1.0, _s(), None, None, 0.25) == (98.0, "atr")


def _m(supply=None, demand=None, supply_4h=None, demand_4h=None):
    return {"atr": 1.0, "tf": "15m", "supply": supply, "demand": demand, "mtf": {"4h": {"supply": supply_4h, "demand": demand_4h}}}


def _z(low, high):
    return {"low": low, "high": high, "touches": 2, "dist_atr": 0}


class TestRoom:
    """Entry 100, stop distance 1.25 → 2:1 target at 102.5 (85 % = 2.125) · 1:1 target at 101.25."""

    def test_trend_free_path(self):
        assert funnel.rr_block(_m(), "long", 100.0, 1.25, 2.0, False) is None
        assert funnel.rr_block(_m(supply=_z(102.2, 102.6)), "long", 100.0, 1.25, 2.0, False) is None   # 2.2 ≥ 2.125

    def test_trend_blocked_by_entry_tf_zone(self):
        why = funnel.rr_block(_m(supply=_z(102.0, 102.4)), "long", 100.0, 1.25, 2.0, False)
        assert "2:1" in why and "15m" in why

    def test_trend_blocked_by_4h_zone(self):
        assert "4h" in funnel.rr_block(_m(supply_4h=_z(101.5, 103.0)), "long", 100.0, 1.25, 2.0, False)

    def test_short_uses_demand(self):
        assert "demanda" in funnel.rr_block(_m(demand=_z(98.6, 99.0)), "short", 100.0, 1.25, 2.0, False)
        assert funnel.rr_block(_m(demand=_z(97.0, 97.8)), "short", 100.0, 1.25, 2.0, False) is None
        assert funnel.rr_block(_m(supply=_z(100.2, 100.4)), "short", 100.0, 1.25, 2.0, False) is None   # supply is not in a short's way

    def test_zone_already_behind_price_is_no_obstacle(self):
        assert funnel.rr_block(_m(supply=_z(99.0, 99.5)), "long", 100.0, 1.25, 2.0, False) is None

    def test_price_inside_zone_blocks(self):
        assert funnel.rr_block(_m(supply=_z(99.8, 100.5)), "long", 100.0, 1.25, 2.0, False)

    def test_range_needs_target_plus_margin(self):
        assert funnel.rr_block(_m(demand=_z(98.0, 98.55)), "short", 100.0, 1.25, 1.0, True) is None        # 1.45 − 0.2 = 1.25
        assert "1:1" in funnel.rr_block(_m(demand=_z(98.0, 98.6)), "short", 100.0, 1.25, 1.0, True)    # 1.40 − 0.2 < 1.25
        assert funnel.rr_block(_m(), "short", 100.0, 1.25, 1.0, True) is None                              # CHOPPY regime without an opposite zone


def _sig(eng):
    assert len(eng.opened) == 1
    d = eng.db.signals.docs[0]
    return {"price": d["price"], "sl": d["sl"], "tp": d["tp"], "atr": d["indicators"]["atr"], "rr_target": d["context"]["funnel"]["rr_target"]}


def _run_factory(monkeypatch, *, stage2, range_edge=None, regime=None, trend_4h="MIXTA", fixed=False):
    eng = FakeEngine(mtf=True, funnel_rich=True)
    real = funnel.metrics

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        m.update(adx=25, activity=2.0, rsi=50, dist_ema20_atr=0.5, fast_cross="ALCISTA", turn=None, regime=regime,
                 supply={"low": 110.0, "high": 110.5, "touches": 3, "dist_atr": 10}, demand={"low": 89.5, "high": 90.0, "touches": 3, "dist_atr": 10})
        return m
    monkeypatch.setattr(funnel, "metrics", fake)
    monkeypatch.setattr(funnel, "range_edge", lambda m: range_edge)

    async def mtf(engine, symbol, price):
        return {"4h": {"tf": "4h", "trend": trend_4h, "rsi": 50, "adx": 25, "dist_ema50_atr": 0.5, "demand": None, "supply": None},
                "1h": {"tf": "1h", "trend": trend_4h, "rsi": 50, "adx": 20, "dist_ema50_atr": 0.3}}
    monkeypatch.setattr(funnel, "_mtf", mtf)

    async def s1(kind, tf, rows, limit, usage_kind):
        return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}], "model": "fast", "ms": 1}

    async def s2(kind, symbol, tf, cand, m, *a, **kw):
        return {"decision": "BUY", "confidence": 80, "rule": None, "reasoning": "ok", "model": "deep", "ms": 1, **stage2(m)}
    monkeypatch.setattr(funnel, "stage1", s1)
    monkeypatch.setattr(funnel, "stage2", s2)
    return eng, _settings(atr_levels_enabled=fixed)


@pytest.mark.asyncio
async def test_trend_trade_is_exactly_two_to_one(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run_factory(monkeypatch, stage2=lambda m: {"sl": m["price"] - 1.0 * m["atr"], "tp": m["price"] + 9 * m["atr"]})
    await funnel.run(eng, s)
    sig = _sig(eng)
    risk_d, reward = sig["price"] - sig["sl"], sig["tp"] - sig["price"]
    assert abs(risk_d - 1.25 * sig["atr"]) < 1e-6          # AI stop + 0.25 ATR buffer
    assert abs(reward - 2.0 * risk_d) < 1e-6 and sig["rr_target"] == 2.0   # the AI's own tp (9 ATR) is ignored


@pytest.mark.asyncio
async def test_counter_trend_is_two_to_one_too(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run_factory(monkeypatch, trend_4h="BAJISTA", stage2=lambda m: {"sl": m["price"] - 1.0 * m["atr"], "tp": None})
    await funnel.run(eng, s)
    sig = _sig(eng)
    assert abs((sig["tp"] - sig["price"]) - 2.0 * (sig["price"] - sig["sl"])) < 1e-6
    assert eng.db.signals.docs[0]["context"]["funnel"]["counter_trend"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("edge,regime", [("EXTREMO_DEMANDA", None), (None, "RANGO / CHOPPY")])
async def test_range_trade_is_exactly_one_to_one(monkeypatch, edge, regime):
    await _force_in_hours(monkeypatch)
    eng, s = _run_factory(monkeypatch, range_edge=edge, regime=regime, stage2=lambda m: {"sl": m["price"] - 1.0 * m["atr"], "tp": None})
    await funnel.run(eng, s)
    sig = _sig(eng)
    assert abs((sig["tp"] - sig["price"]) - 1.0 * (sig["price"] - sig["sl"])) < 1e-6 and sig["rr_target"] == 1.0
    f = eng.db.signals.docs[0]["context"]["funnel"]
    assert f["range_trade"] is True and f["rr_target"] == 1.0


@pytest.mark.asyncio
async def test_fixed_atr_stop_keeps_exact_ratio(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run_factory(monkeypatch, fixed=True, stage2=lambda m: {"sl": None, "tp": None})
    await funnel.run(eng, s)
    sig = _sig(eng)
    assert abs((sig["price"] - sig["sl"]) - 2.0 * sig["atr"]) < 1e-6 and abs((sig["tp"] - sig["price"]) - 4.0 * sig["atr"]) < 1e-6


@pytest.mark.asyncio
async def test_obstacle_before_85pct_is_a_pass(monkeypatch):
    await _force_in_hours(monkeypatch)
    eng, s = _run_factory(monkeypatch, stage2=lambda m: {"sl": m["price"] - 1.0 * m["atr"], "tp": None})
    real = funnel.metrics

    def near_supply(*a, **k):
        m = real(*a, **k)
        m["supply"] = {"low": m["price"] + 1.5 * m["atr"], "high": m["price"] + 2.0 * m["atr"], "touches": 3, "dist_atr": 1.5}
        return m
    monkeypatch.setattr(funnel, "metrics", near_supply)
    await funnel.run(eng, s)
    assert eng.opened == []
    doc = eng.db.signals.docs[0]
    assert doc["ai_decision"] == "PASS" and "falta de recorrido para 2:1" in doc["note"]


def test_sizing_still_risks_one_percent_with_final_stop():
    sl, _ = funnel.stop_level("long", 65000.0, 400.0, _s(), 64400.0, None, 0.25)
    qty, _ = risk.position_size(100000, 100000, 1.0, 65000.0, sl, 10, "BTCUSDT")
    loss = ((65000.0 - sl) + risk.stop_cost(65000.0, sl)) * qty
    assert 0.95 <= loss / 100000 * 100 <= 1.0


def test_position_carries_rr_target_and_partial_guard_is_in_both_engines():
    assert "rr_target" in Position.model_fields
    for f in ("engine.py", "fx/engine_positions.py"):
        src = open(f"/app/backend/{f}").read()
        assert src.count('rules["partial_tp_enabled"] and p.rr_target != 1.0 and not p.partial_done') == 2
        assert src.count('rr_target=sig.get("rr_target")') == 2
