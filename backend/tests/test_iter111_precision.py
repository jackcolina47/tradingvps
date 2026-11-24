"""Iter111 — orderly pullback through the EMA9/21 filter (EMA50 lock), free path by obstacle timeframe (75 % 4H / 50 % 15m),
brake-candle rule in DEEP_SYSTEM and SL_ATR_BAND floor at 1.1 ATR."""
import pytest
import funnel
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def _s(fixed=False):
    return type("S", (), {"atr_levels_enabled": fixed, "atr_sl_mult": 2.0, "atr_tp_mult": 4.0})()


# ---------- (4) SL_ATR_BAND ----------
class TestStopBand:
    def test_band(self):
        assert funnel.SL_ATR_BAND == (1.1, 4.0) and funnel.SL_BUFFER_ATR == 0.25

    @pytest.mark.parametrize("ai_sl,expected", [(98.85, (98.6, "ia")), (98.95, (98.0, "atr")), (99.5, (98.0, "atr")), (96.0, (95.75, "ia")), (95.9, (98.0, "atr"))])
    def test_stop_level_floor_and_ceiling(self, ai_sl, expected):
        sl, src = funnel.stop_level("long", 100.0, 1.0, _s(), ai_sl, None, 0.25)
        assert (round(sl, 6), src) == expected

    def test_short_side(self):
        assert funnel.stop_level("short", 100.0, 1.0, _s(), 101.0, None, 0.25) == (102.0, "atr")
        sl, src = funnel.stop_level("short", 100.0, 1.0, _s(), 101.15, None, 0.25)
        assert round(sl, 6) == 101.4 and src == "ia"

    def test_levels_uses_same_band(self):
        assert funnel.levels("long", 100.0, 1.0, _s(), 99.0, 103.0)[2] == "atr"
        assert funnel.levels("long", 100.0, 1.0, _s(), 98.85, 103.0)[2] == "ia"

    def test_prompt_states_band(self):
        import inspect
        assert "SL_ATR_BAND[0]:g" in inspect.getsource(funnel.stage2)


# ---------- (2) rr_block by obstacle timeframe ----------
def _m(supply=None, demand=None, supply_4h=None, demand_4h=None):
    return {"atr": 1.0, "tf": "15m", "supply": supply, "demand": demand, "mtf": {"4h": {"supply": supply_4h, "demand": demand_4h}}}


def _z(low, high):
    return {"low": low, "high": high, "touches": 2, "dist_atr": 0}


class TestRoomByTimeframe:
    """Entry 100, stop distance 1.25 → 2:1 target at 102.5 · 15m needs 1.25 free (50 %) · 4H needs 1.875 free (75 %)."""

    def test_constants(self):
        assert (funnel.RR_FREE_4H, funnel.RR_FREE_ENTRY) == (0.75, 0.50) and not hasattr(funnel, "RR_FREE_PCT")

    def test_15m_micro_zone_at_half_way_passes(self):
        assert funnel.rr_block(_m(supply=_z(101.25, 101.6)), "long", 100.0, 1.25, 2.0, False) is None
        assert funnel.rr_block(_m(demand=_z(98.4, 98.75)), "short", 100.0, 1.25, 2.0, False) is None

    def test_15m_micro_zone_before_half_way_blocks(self):
        why = funnel.rr_block(_m(supply=_z(101.24, 101.6)), "long", 100.0, 1.25, 2.0, False)
        assert "15m" in why and "50%" in why and "oferta" in why
        assert "demanda de 15m" in funnel.rr_block(_m(demand=_z(98.4, 98.76)), "short", 100.0, 1.25, 2.0, False)

    def test_4h_zone_at_75pct_passes(self):
        assert funnel.rr_block(_m(supply_4h=_z(101.875, 103.0)), "long", 100.0, 1.25, 2.0, False) is None
        assert funnel.rr_block(_m(demand_4h=_z(97.0, 98.125)), "short", 100.0, 1.25, 2.0, False) is None

    def test_4h_zone_before_75pct_blocks(self):
        why = funnel.rr_block(_m(supply_4h=_z(101.87, 103.0)), "long", 100.0, 1.25, 2.0, False)
        assert "4h" in why and "75%" in why
        assert "demanda de 4h" in funnel.rr_block(_m(demand_4h=_z(97.0, 98.13)), "short", 100.0, 1.25, 2.0, False)

    def test_same_distance_passes_on_15m_but_blocks_on_4h(self):
        z = _z(101.5, 102.0)   # 1.5 free: ≥ 50 % but < 75 %
        assert funnel.rr_block(_m(supply=z), "long", 100.0, 1.25, 2.0, False) is None
        assert "4h" in funnel.rr_block(_m(supply_4h=z), "long", 100.0, 1.25, 2.0, False)

    def test_old_85pct_case_now_free(self):
        assert funnel.rr_block(_m(supply=_z(102.0, 102.4)), "long", 100.0, 1.25, 2.0, False) is None

    def test_range_rule_untouched(self):
        assert "1:1" in funnel.rr_block(_m(demand=_z(98.0, 98.6)), "short", 100.0, 1.25, 1.0, True)
        assert funnel.rr_block(_m(demand=_z(98.0, 98.55)), "short", 100.0, 1.25, 1.0, True) is None


# ---------- (1) orderly pullback lock ----------
def _pb(side="long", **kw):
    """Valid pullback: 4H + 1H with the side, close beyond EMA50, price 0.2 ATR from the EMA20, last swing intact."""
    up = side == "long"
    trend = "ALCISTA" if up else "BAJISTA"
    m = {"atr": 1.0, "price": 100.0, "close": 100.0, "ema20": 100.2 if up else 99.8, "ema50": 99.4 if up else 100.6,
         "swing_high": 101.5, "swing_low": 98.5, "turn": None, "fast_cross": "BAJISTA" if up else "ALCISTA",
         "mtf": {"4h": {"trend": trend}, "1h": {"trend": trend}}}
    m.update(kw)
    return m


class TestPullbackLock:
    @pytest.mark.parametrize("side", ["long", "short"])
    def test_valid_pullback_passes(self, side):
        assert funnel.pullback_block(_pb(side), side) is None

    @pytest.mark.parametrize("mtf", [{"4h": {"trend": "ALCISTA"}, "1h": {"trend": "MIXTA"}}, {"4h": {"trend": "MIXTA"}, "1h": {"trend": "ALCISTA"}},
                                     {"4h": {"trend": "ALCISTA"}, "1h": {"trend": "BAJISTA"}}, {}, None])
    def test_needs_4h_and_1h(self, mtf):
        assert "4H y 1H" in funnel.pullback_block(_pb("long", mtf=mtf), "long")

    def test_turn_against_blocks(self):
        assert "giro" in funnel.pullback_block(_pb("long", turn="GIRO_BAJISTA_CONFIRMADO"), "long")
        assert "giro" in funnel.pullback_block(_pb("short", turn="GIRO_ALCISTA_CONFIRMADO"), "short")
        assert funnel.pullback_block(_pb("long", turn="GIRO_ALCISTA_CONFIRMADO"), "long") is None

    def test_close_beyond_ema50_blocks(self):
        assert "EMA50" in funnel.pullback_block(_pb("long", close=99.3), "long")
        assert "EMA50" in funnel.pullback_block(_pb("long", close=99.4), "long")       # on the EMA50 is not above it
        assert "EMA50" in funnel.pullback_block(_pb("short", close=100.7), "short")

    def test_must_be_testing_ema20_or_ema50(self):
        assert "testeando" in funnel.pullback_block(_pb("long", ema20=101.0, ema50=99.0), "long")       # 1.0 ATR from both
        assert funnel.pullback_block(_pb("long", ema20=100.5, ema50=99.0), "long") is None               # exactly 0.5 ATR from EMA20
        assert funnel.pullback_block(_pb("long", ema20=101.5, ema50=99.6), "long") is None               # testing the EMA50 instead
        assert "testeando" in funnel.pullback_block(_pb("short", ema20=99.0, ema50=101.0), "short")

    def test_broken_swing_blocks(self):
        assert "swing low" in funnel.pullback_block(_pb("long", swing_low=100.1), "long")
        assert "swing high" in funnel.pullback_block(_pb("short", swing_high=99.9), "short")
        assert funnel.pullback_block(_pb("long", swing_low=100.0), "long") is None


def _patch(monkeypatch, over):
    real = funnel.metrics

    def fake(df, i, has_volume, p, rich=False):
        m = real(df, i, has_volume, p, rich)
        if rich:
            m.update(over)
        return m
    monkeypatch.setattr(funnel, "metrics", fake)


async def _run(monkeypatch, side, over, trends=None):
    await _force_in_hours(monkeypatch)
    eng = FakeEngine(mtf=True, funnel_rich=True)
    base = {k: v for k, v in _pb(side).items() if k not in ("mtf", "atr", "price")}
    _patch(monkeypatch, {**base, **over})
    t4, t1 = trends or (("ALCISTA",) * 2 if side == "long" else ("BAJISTA",) * 2)

    async def mtf(engine, symbol, price):
        return {"4h": {"tf": "4h", "trend": t4, "rsi": 50, "adx": 25, "dist_ema50_atr": 0, "demand": None, "supply": None},
                "1h": {"tf": "1h", "trend": t1, "rsi": 50, "adx": 20, "dist_ema50_atr": 0}}
    monkeypatch.setattr(funnel, "_mtf", mtf)

    async def s1(kind, tf, rows, limit, usage_kind):
        return {"candidates": [{"symbol": "BTCUSDT", "side": side, "reason": "retroceso"}], "model": "fast", "ms": 1}
    calls = []

    async def s2(kind, symbol, tf, cand, *a, **kw):
        calls.append(dict(cand))
        return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None, "reasoning": "", "model": "deep", "ms": 1}
    monkeypatch.setattr(funnel, "stage1", s1)
    monkeypatch.setattr(funnel, "stage2", s2)
    await funnel.run(eng, _settings(symbols=["BTCUSDT"]))
    return eng, calls


class TestPullbackInRun:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("side", ["long", "short"])
    async def test_orderly_pullback_reaches_strong_model(self, monkeypatch, side):
        eng, calls = await _run(monkeypatch, side, {})
        assert len(calls) == 1 and calls[0]["side"] == side and calls[0]["pullback"] is True and not calls[0].get("counter_trend")
        assert any("retroceso ordenado" in t for lv, t in eng.logs if lv == "SCAN")
        assert not any("cruce EMA9/21" in t for lv, t in eng.logs if lv == "RISK")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("over,reason", [({"close": 99.0}, "EMA50"), ({"ema20": 101.2, "ema50": 98.8}, "testeando"), ({"swing_low": 100.5}, "swing low")])
    async def test_lock_discards_without_strong_model(self, monkeypatch, over, reason):
        eng, calls = await _run(monkeypatch, "long", over)
        assert calls == []
        risk = [t for lv, t in eng.logs if lv == "RISK"]
        assert any("cruce EMA9/21" in t and reason in t and "descartado sin IA potente" in t for t in risk), risk
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"

    @pytest.mark.asyncio
    async def test_1h_not_aligned_discards(self, monkeypatch):
        eng, calls = await _run(monkeypatch, "long", {}, trends=("ALCISTA", "MIXTA"))
        assert calls == [] and any("4H y 1H" in t for lv, t in eng.logs if lv == "RISK")

    @pytest.mark.asyncio
    async def test_turn_against_never_reaches_strong_model(self, monkeypatch):
        eng, calls = await _run(monkeypatch, "short", {"turn": "GIRO_ALCISTA_CONFIRMADO"})
        assert calls == [] and any("giro de estructura" in t for lv, t in eng.logs if lv == "RISK")

    @pytest.mark.asyncio
    async def test_aligned_cross_is_not_tagged_pullback(self, monkeypatch):
        eng, calls = await _run(monkeypatch, "long", {"fast_cross": "ALCISTA"})
        assert len(calls) == 1 and "pullback" not in calls[0]


# ---------- (3) brake candle in DEEP_SYSTEM ----------
class TestBrakeCandleRule:
    def test_rule_text(self):
        t = funnel.DEEP_SYSTEM
        for needle in ("VELA DE FRENO OBLIGATORIA", "ÚLTIMA vela cerrada de {tf}", "PROHIBIDO aprobar una compra (BUY)", "mecha inferior de absorción",
                       "PROHIBIDO aprobar una venta (SELL)", "mecha superior de rechazo", "desaceleración o rechazo"):
            assert needle in t, needle

    def test_prompt_still_formats(self):
        out = funnel.DEEP_SYSTEM.format(market="x", tf="15m", levels="L")
        assert "ÚLTIMA vela cerrada de 15m" in out and "{" in out   # only the JSON braces remain
