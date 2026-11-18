from datetime import datetime
from zoneinfo import ZoneInfo
from risk import position_size
from fx import risk as fxr
from fx.market import friday_stage

NY = ZoneInfo("America/New_York")
T = {"EURUSD": {"price": 1.1}}


def test_crypto_exact_or_nothing():
    qty, _ = position_size(1000, 1000, 1.0, 100.0, 98.0, 5, lot=(0.001, 0.001))
    assert abs(qty * 2 - 10) < 0.01
    assert position_size(1000, 1000, 1.0, 100.0, 99.99, 5, lot=(0.001, 0.001)) == (0.0, 0.0)   # needs 100k notional > margin


def test_fx_exact_or_nothing():
    lots, _, real = fxr.position_size(10000, 10000, 1.0, 1.1, 1.098, 100, "EURUSD", T)
    assert lots == 0.5 and real == 1.0
    assert fxr.position_size(100, 100, 1.0, 1.1, 1.09, 100, "EURUSD", T, 10.0)[0] == 0.0      # below min lot: no fallback
    assert fxr.position_size(10000, 100, 1.0, 1.1, 1.0999, 30, "EURUSD", T)[0] == 0.0          # no margin


def test_friday_stage():
    f = lambda d, h, m=0: friday_stage(datetime(2026, 6, d, h, m, tzinfo=NY))  # noqa: E731
    assert (f(5, 11, 59), f(5, 12), f(5, 15, 29), f(5, 15, 30), f(5, 16, 59), f(4, 13), f(8, 13)) == ("", "block", "block", "flat", "flat", "", "")
