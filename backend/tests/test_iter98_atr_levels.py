from strategies import apply_atr_levels, atr_params
from models import Settings
from fx.models import FxSettings
from risk import position_size

S = {"side": "long", "price": 100.0, "sl": 99.0, "tp": 103.0, "atr": 1.5, "reasons": ["x"], "tp_mode": "fixed"}


def test_apply():
    o = apply_atr_levels(S, (2.0, 4.0))
    assert (o["sl"], o["tp"], o["tp_mode"]) == (97.0, 106.0, "atr") and S["sl"] == 99.0
    o = apply_atr_levels({**S, "side": "short"}, (2.0, 4.0))
    assert (o["sl"], o["tp"]) == (103.0, 94.0)
    assert apply_atr_levels({**S, "atr": 0}, (2, 4))["sl"] == 99.0
    assert apply_atr_levels({**S, "atr": float("nan")}, (2, 4))["sl"] == 99.0
    assert apply_atr_levels({**S, "exit_mode": "cross"}, (2, 4))["sl"] == 99.0
    assert apply_atr_levels(S, None) is S


def test_params_defaults_backward_compatible():
    assert atr_params(Settings()) == {} and atr_params(FxSettings()) == {}
    assert atr_params(Settings(atr_levels_enabled=True)) == {"atr_levels": (2.0, 4.0)}


def test_sizing_uses_sl_distance_and_min_notional():
    qty, _ = position_size(1000, 1000, 1.0, 100.0, 97.0, 5, lot=(0.001, 0.001))
    assert abs(qty * 3.0 - 10.0) < 0.01
    assert position_size(1000, 1000, 1.0, 100.0, 100.0, 5) == (0.0, 0.0)
    assert position_size(1000, 1000, 1.0, 100.0, 97.0, 5, lot=(0.001, 0.001), min_notional=500) == (0.0, 0.0)
