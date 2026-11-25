"""Iter122: Bybit Labs close an EMA-cross trade when a candle CLOSES on the wrong side of the EMA21 (a wick through it does not count); Bybit main is unchanged."""
import pandas as pd
import pytest

import lab as lab_mod
from backtest import simulate
from indicators import build_df
from strategies import closed_beyond_exit_ema, ema_test, CLOSE_EMA_REASON, EMA_TEST
from test_iter80_ema_test import run, _pos_doc

H = 3600_000


def _df(close, ema9=10.5, ema21=10.0, ts=10 * H):
    row = {"ts": ts, "low": 9.5, "high": 10.8, "close": close, "ema21": ema21, "ema27": ema21, "ema9": ema9, "ema10": ema9, "ema20": ema21}
    return pd.DataFrame([row, row])


def test_helper():
    assert closed_beyond_exit_ema("long", {"close": 9.9, "ema27": 10}) and not closed_beyond_exit_ema("long", {"close": 10.1, "ema27": 10})
    assert closed_beyond_exit_ema("short", {"close": 10.1, "ema27": 10}) and not closed_beyond_exit_ema("short", {"close": 9.9, "ema27": 10})
    assert not closed_beyond_exit_ema("long", {"close": 10, "ema27": 10})


@pytest.mark.parametrize("is_lab,expected", [(True, (1, 1, 0, CLOSE_EMA_REASON)), (False, (1, 1, 1, None))])
def test_lab_closes_on_candle_close_beyond_exit_ema(monkeypatch, is_lab, expected):
    async def body(e, db):
        e.close_beyond_exit_ema = lab_mod.LabEngine.close_beyond_exit_ema if is_lab else e.close_beyond_exit_ema
        await db.positions.insert_one({**_pos_doc(side="long"), "opened_at": "1970-01-01T10:01:00+00:00"})
        await e._exit_on_cross("UNIUSDT", _df(9.8, ts=9 * H), 1, "1h")   # closed below the EMA21 but before the entry
        a = await db.positions.count_documents({})
        await e._exit_on_cross("UNIUSDT", _df(10.2), 1, "1h")            # low 9.5 pierced the EMA21 (10.0) and the candle closed back above it
        b = await db.positions.count_documents({})
        await e._exit_on_cross("UNIUSDT", _df(9.8), 1, "1h")             # closed below the EMA21, EMA9 still above (no opposite cross)
        t = await db.trades.find_one({})
        return a, b, await db.positions.count_documents({}), (t or {}).get("reason")
    assert lab_mod.LabEngine.close_beyond_exit_ema is True
    assert run(body, monkeypatch, {}) == expected


def test_crypto_backtest_flag():
    rows, p = [], 100.0
    for i in range(500):
        p *= 1 + (0.004 if (i // 40) % 2 == 0 else -0.004)
        rows.append([i * H, p, p * 1.003, p * 0.997, p, 100])
    df = build_df(rows)
    sigs = {i: s for i in range(210, len(df) - 1) if (s := ema_test(df, i))}
    off, on = simulate(df, sigs, 1.0, 3, True, 1000.0), simulate(df, sigs, 1.0, 3, True, 1000.0, close_exit_ema=True)
    assert off["trades"] and CLOSE_EMA_REASON not in {t["reason"] for t in off["trades"]} and CLOSE_EMA_REASON in {t["reason"] for t in on["trades"]}
    assert all(t["strategy"] == EMA_TEST for t in on["trades"])


def test_exit_uses_ema27_not_ema21():
    row = {"close": 10.1, "ema21": 10.0, "ema27": 10.3}  # short: closed above the EMA21 but still below the EMA27 → stays open
    assert not closed_beyond_exit_ema("short", row) and closed_beyond_exit_ema("short", {**row, "close": 10.4})
    assert CLOSE_EMA_REASON == "CIERRE_EMA27"
