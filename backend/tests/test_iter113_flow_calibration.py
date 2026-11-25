"""Iter113 — flow calibration: SL_ATR_BAND floor 0.85 ATR (final stop ≥ 1.10 ATR, 2:1 target ≥ 2.20 ATR), range edge with RSI 42 / 58 and 1.3 ATR
of room (exact 1:1, no partial close) and the continuous forex session 03:00–15:00 NY."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
import funnel
import trade_guard as tg

NY = ZoneInfo("America/New_York")


def _s(fixed=False):
    return type("S", (), {"atr_levels_enabled": fixed, "atr_sl_mult": 2.0, "atr_tp_mult": 4.0})()


def ny(day, h, m=0):
    return datetime(2026, 11, day, h, m, tzinfo=NY).astimezone(timezone.utc)   # Nov 2026: 16 Mon … 20 Fri, 21 Sat, 22 Sun


class TestStopFloor:
    def test_constants(self):
        assert funnel.SL_ATR_BAND == (0.85, 4.0) and funnel.SL_BUFFER_ATR == 0.25 and funnel.TREND_RR == 2.0 and funnel.RANGE_RR == 1.0

    @pytest.mark.parametrize("side,ai_sl,final", [("long", 99.15, 98.9), ("short", 100.85, 101.1)])
    def test_minimum_stop_is_110_atr_and_target_220(self, side, ai_sl, final):
        sl, src = funnel.stop_level(side, 100.0, 1.0, _s(), ai_sl, None, funnel.SL_BUFFER_ATR)
        assert src == "ia" and round(sl, 6) == final
        dist = abs(100.0 - sl)
        assert round(dist, 6) == 1.10 and round(funnel.TREND_RR * dist, 6) == 2.20 and round(funnel.RANGE_RR * dist, 6) == 1.10

    @pytest.mark.parametrize("side,ai_sl", [("long", 99.16), ("long", 99.5), ("short", 100.84), ("long", 95.9)])
    def test_out_of_band_falls_back_to_atr_stop(self, side, ai_sl):
        sl, src = funnel.stop_level(side, 100.0, 1.0, _s(), ai_sl, None, 0.25)
        assert src == "atr" and abs(100.0 - sl) == 2.0

    def test_stop_between_085_and_11_now_accepted(self):
        assert funnel.stop_level("long", 100.0, 1.0, _s(), 99.0, None, 0.25) == (98.75, "ia")   # 1.0 ATR: rejected with the 1.1 floor

    def test_prompt_asks_for_085_to_4_atr(self):
        import inspect
        src = inspect.getsource(funnel.stage2)
        assert "SL_ATR_BAND[0]:g" in src and "SL_ATR_BAND[1]:g" in src and f"{funnel.SL_ATR_BAND[0]:g} y {funnel.SL_ATR_BAND[1]:g}" == "0.85 y 4"


def _m(rsi, near, far, edge="supply", adx=12, regime=None):
    zone, other = {"low": 100.2, "high": 100.4, "touches": 2, "dist_atr": near}, {"low": 98.0, "high": 98.2, "touches": 2, "dist_atr": far}
    sup, dem = (zone, other) if edge == "supply" else (other, zone)
    return {"atr": 1.0, "adx": adx, "rsi": rsi, "regime": regime, "supply": sup, "demand": dem}


class TestRangeEdge:
    def test_constants(self):
        assert funnel.RANGE_RSI == (42, 58) and funnel.RANGE_ROOM_ATR == 1.3 and funnel.RANGE_ZONE_ATR == 0.35 and funnel.RANGE_TOUCHES == 2 and funnel.MIN_ADX == 18

    @pytest.mark.parametrize("rsi,edge,tag", [(58, "supply", "EXTREMO_OFERTA"), (57.9, "supply", None), (42, "demand", "EXTREMO_DEMANDA"), (42.1, "demand", None),
                                              (61, "supply", "EXTREMO_OFERTA"), (39, "demand", "EXTREMO_DEMANDA"), (50, "supply", None), (50, "demand", None)])
    def test_rsi_thresholds(self, rsi, edge, tag):
        assert funnel.range_edge(_m(rsi, 0.2, 2.0, edge)) == tag

    @pytest.mark.parametrize("far,ok", [(1.3, True), (1.29, False), (1.5, True), (1.79, True)])
    def test_room_to_opposite_zone(self, far, ok):
        assert (funnel.range_edge(_m(60, 0.2, far)) == "EXTREMO_OFERTA") is ok
        assert (funnel.range_edge(_m(40, 0.2, far, "demand")) == "EXTREMO_DEMANDA") is ok

    def test_still_needs_price_on_the_zone_two_touches_and_sideways(self):
        assert funnel.range_edge(_m(60, 0.36, 2.0)) is None
        assert funnel.range_edge(_m(60, 0.35, 2.0)) == "EXTREMO_OFERTA"
        m = _m(60, 0.2, 2.0)
        m["supply"]["touches"] = 1
        assert funnel.range_edge(m) is None
        assert funnel.range_edge(_m(60, 0.2, 2.0, adx=18)) is None and funnel.range_edge(_m(60, 0.2, 2.0, adx=17.9)) == "EXTREMO_OFERTA"
        assert funnel.range_edge(_m(60, 0.2, 2.0, adx=22, regime="RANGO / CHOPPY")) == "EXTREMO_OFERTA"

    def test_minimum_room_fits_the_exact_one_to_one(self):
        """Tightest accepted setup: stop 1.10 ATR → 1:1 target 1.10 ATR + 0.2 ATR margin = 1.30 ATR of room."""
        m = {"atr": 1.0, "tf": "15m", "supply": None, "demand": {"low": 98.2, "high": 98.69, "touches": 2, "dist_atr": 1.31}}
        assert funnel.rr_block(m, "short", 100.0, 1.10, funnel.RANGE_RR, True) is None
        assert "1:1" in funnel.rr_block(m, "short", 100.0, 1.12, funnel.RANGE_RR, True)

    def test_range_trades_keep_exact_ratio_without_partial(self):
        for f in ("engine.py", "fx/engine_positions.py"):
            assert open(f"/app/backend/{f}").read().count('rules["partial_tp_enabled"] and p.rr_target != 1.0 and not p.partial_done') == 2


class TestForexSession:
    def test_windows(self):
        assert tg.WINDOWS["fx"] == [("Londres + Nueva York", 180, 900), ("Sídney + Tokio", 1140, 1440)]
        assert tg.WINDOWS["crypto"] == [("24 horas", 0, 1440)]

    @pytest.mark.parametrize("h,m,ok", [(0, 0, False), (2, 59, False), (3, 0, True), (4, 0, True), (6, 30, True), (8, 0, True), (12, 0, True), (14, 59, True),
                                        (15, 0, False), (17, 0, False), (18, 59, False), (19, 0, True), (23, 59, True)])
    def test_in_hours(self, h, m, ok):
        assert tg.in_hours("fx", ny(18, h, m)) is ok
        assert tg.in_hours("crypto", ny(18, h, m)) is True

    def test_status_and_countdown(self):
        st = tg.hours_status("fx", ny(18, 3))
        assert st["in_hours"] and st["window"] == "Londres + Nueva York" and st["seconds_left"] == 12 * 3600
        assert [(w["start"], w["end"]) for w in st["windows"]] == [("03:00", "15:00"), ("19:00", "00:00")]
        st = tg.hours_status("fx", ny(18, 15))
        assert not st["in_hours"] and st["next_window"] == "Sídney + Tokio" and st["seconds_left"] == 4 * 3600
        st = tg.hours_status("fx", ny(18, 0, 30))
        assert not st["in_hours"] and st["next_window"] == "Londres + Nueva York" and st["seconds_left"] == 150 * 60

    def test_entry_block_reason(self):
        assert tg.entry_block("fx", "EURUSD", ny(18, 16))[1] == "HORARIO" and tg.entry_block("fx", "EURUSD", ny(18, 1))[1] == "HORARIO"

    def test_friday_rule_unchanged(self):
        assert (tg.FRIDAY_BLOCK_MIN, tg.FRIDAY_FLAT_MIN, tg.FRIDAY_END_MIN) == (720, 930, 1020)
        assert not tg.friday(ny(20, 11, 59))["block"] and tg.friday(ny(20, 12))["block"] and tg.friday(ny(20, 15, 30))["flat"]
        assert not tg.friday(ny(19, 13))["block"]

    def test_weekend_closed(self):
        assert not tg.in_hours("fx", ny(21, 10)) and not tg.in_hours("fx", ny(22, 10)) and tg.in_hours("fx", ny(22, 19))
