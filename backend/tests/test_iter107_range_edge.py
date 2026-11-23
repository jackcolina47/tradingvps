"""Range-edge path: a sideways pair reaches the AI only on a tested edge of its range; the middle of the range stays blocked."""
import funnel


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
        assert _m(rsi=61.9)["range_edge"] is None
        assert _demand_edge(rsi=38.1)["range_edge"] is None
        assert _m(rsi=62)["range_edge"] == "EXTREMO_OFERTA" and _demand_edge(rsi=38)["range_edge"] == "EXTREMO_DEMANDA"

    def test_no_room_to_opposite_zone_blocked(self):
        assert _m(demand={"low": 108.4, "high": 108.6, "touches": 2, "dist_atr": 1.79})["range_edge"] is None
        assert _m(demand={"low": 108.4, "high": 108.6, "touches": 2, "dist_atr": 1.8})["range_edge"] == "EXTREMO_OFERTA"

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

    def test_target_is_quarter_atr_before_opposite_zone(self):
        assert funnel.range_target(_m(), "short") == 107.65
        assert funnel.range_target(_demand_edge(), "long") == 109.95

    def test_row_text_carries_the_tag(self):
        m = {**_m(), "price": 110.0, "trend": "MIXTA", "ema20": 109.0, "ema50": 109.0, "ema200": 109.0, "dist_ema20_atr": 1.0, "macd_hist": 0.1, "macd_rising": False, "atr_pct": 0.5}
        assert "oportunidad_rango: EXTREMO_OFERTA" in funnel._row_txt("X", m)
        assert "oportunidad_rango" not in funnel._row_txt("X", {**m, "range_edge": None})

    def test_prompts_mention_range(self):
        assert "EXTREMO_OFERTA / EXTREMO_DEMANDA" in funnel.DEEP_SYSTEM and "0.25 ATR antes de la zona opuesta" in funnel.DEEP_SYSTEM
        assert "oportunidad_rango" in funnel.RANGE_FAST_SYSTEM
