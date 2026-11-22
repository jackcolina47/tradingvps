from indicators import build_df
from strategies import ema_test, ema_test_params
from models import Settings
from fx.models import FxSettings

C = {"ema_test_confirm": {"adx": 24, "dist_atr": 0.5, "bars": 20}}


def mk(e10, e20, adx, atr=1.0):
    df = build_df([[i * 1000, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(len(e10))])
    df["ema9"], df["ema21"], df["adx"], df["atr"] = [float(x) for x in e10], [float(x) for x in e20], [float(x) for x in adx], atr
    return df


def sigs(df, p=C):
    return {i: s["side"] for i in range(1, len(df)) if (s := ema_test(df, i, params=p))}


def test_enter_on_cross_candle():
    df = mk([99, 99, 100.2, 100.3], [100] * 4, [10, 10, 30, 30])
    assert sigs(df) == {2: "long"}


def test_delayed_adx_and_only_first():
    df = mk([101, 101, 99.8, 99.8, 99.7, 99.7], [100] * 6, [10, 10, 10, 20, 25, 30])
    assert sigs(df) == {4: "short"}


def test_discard_when_emas_far():
    df = mk([99, 99, 100.2, 100.4, 100.9, 100.3], [100] * 6, [10, 10, 10, 20, 25, 30])
    assert sigs(df) == {}


def test_expired_window():
    df = mk([99, 99] + [100.2] * 6, [100] * 8, [10] * 7 + [30])
    assert sigs(df, {"ema_test_confirm": {"adx": 24, "dist_atr": 0.5, "bars": 3}}) == {}
    assert sigs(df, {"ema_test_confirm": {"adx": 24, "dist_atr": 0.5, "bars": 5}}) == {7: "long"}


def test_params_and_legacy():
    assert ema_test_params(Settings()) == {}
    assert ema_test_params(FxSettings(ema_test_adx_filter=True)) == {"ema_test_adx_min": 20.0}
    assert ema_test_params(Settings(ema_test_confirm_enabled=True, ema_test_adx_filter=True)) == C
    df = mk([99, 99, 100.2, 100.3], [100] * 4, [10, 10, 10, 10])
    assert sigs(df, {}) == {2: "long"}
