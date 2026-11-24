# Iteration 45: long-horizon strategies (multi_horizon, double_divergence), BTC regime filter, weekly rotation, maker entries,
# horizon-aware position caps, API surface (meta, market/context, settings, performance, backtest).
# Run with: pytest tests/test_iter45_long_horizon_regime_maker.py -n 0 -q
import os
import sys
import math
import numpy as np
import pytest
import requests
from types import SimpleNamespace
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

import maker  # noqa: E402
from indicators import build_df  # noqa: E402
from strategies_slow import multi_horizon, double_divergence, scan_slow, SLOW_KEYS, SLOW_INFO, ensemble_votes  # noqa: E402
from strategies import evaluate  # noqa: E402
from engine import STRATEGY_INFO  # noqa: E402
from models import Settings, SettingsUpdate, CRYPTO_STRATEGY_KEYS  # noqa: E402
from risk import MAKER_FEE, FEE_RATE  # noqa: E402
from engine import TradingEngine as Engine  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PASSWORD = "BlackCat2233@"


def klines(closes, vol=1000.0, ts0=1_700_000_000_000, step=86_400_000, spread=0.01):
    out = []
    for k, c in enumerate(closes):
        o = closes[k - 1] if k else c
        hi, lo = max(o, c) * (1 + spread), min(o, c) * (1 - spread)
        out.append([ts0 + k * step, float(o), float(hi), float(lo), float(c), vol])
    return out


def trend_series(n=320, start=100.0, drift=0.004, seed=1):
    rng = np.random.default_rng(seed)
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.004)))
    return closes


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": PASSWORD})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


# ---------- registry ----------
class TestRegistry:
    def test_slow_keys_registered(self):
        assert SLOW_KEYS == ["multi_horizon", "double_divergence"]
        for k in SLOW_KEYS:
            assert k in STRATEGY_INFO and STRATEGY_INFO[k]["horizon"] == "long"
            assert k in CRYPTO_STRATEGY_KEYS
        assert SLOW_INFO["multi_horizon"]["tf"] == "1d" and SLOW_INFO["double_divergence"]["tf"] == "4h"

    def test_fast_evaluate_ignores_slow_keys(self):
        df = build_df(klines(trend_series(260)))
        assert evaluate(df, len(df) - 1, None, "multi_horizon", {}, "relaxed", []) is None

    def test_settings_defaults_active(self):
        s = Settings()
        assert s.entry_order_type == "maker"
        assert s.max_long_positions >= 1 and s.maker_wait_bars >= 1
        assert not set(SLOW_KEYS) & set(s.disabled_strategies)

    def test_settings_update_validates(self):
        assert SettingsUpdate(entry_order_type="market").entry_order_type == "market"
        with pytest.raises(Exception):
            Settings(entry_order_type="limit_xyz")
        with pytest.raises(Exception):
            SettingsUpdate(maker_wait_bars=0)


# ---------- multi-horizon ----------
class TestMultiHorizon:
    def test_uptrend_yields_long_once_votes_flip(self):
        base = [100.0] * 230
        closes = base + trend_series(90, start=100.0, drift=0.02, seed=3)
        df = build_df(klines(closes))
        sigs = scan_slow(df, "multi_horizon")
        assert sigs, "expected at least one multi-horizon signal in a strong uptrend"
        i, sig = next(iter(sigs.items()))
        assert sig["side"] == "long" and sig["strategy"] == "multi_horizon" and sig["sl"] < sig["price"] < sig["tp"]
        longs, _ = ensemble_votes(df)
        assert longs[i] >= 3 and longs[i - 1] < 3

    def test_downtrend_yields_short(self):
        closes = [100.0] * 230 + trend_series(90, start=100.0, drift=-0.02, seed=4)
        df = build_df(klines(closes))
        sigs = scan_slow(df, "multi_horizon")
        assert sigs and all(s["side"] == "short" for s in sigs.values())

    def test_no_lookahead(self):
        closes = [100.0] * 230 + trend_series(90, start=100.0, drift=0.02, seed=3)
        df = build_df(klines(closes))
        sigs = scan_slow(df, "multi_horizon")
        i = next(iter(sigs))
        truncated = build_df(klines(closes[: i + 1]))
        sig_t = multi_horizon(truncated, i)
        assert sig_t is not None and sig_t["side"] == sigs[i]["side"] and math.isclose(sig_t["price"], sigs[i]["price"])

    def test_flat_market_no_signal(self):
        rng = np.random.default_rng(7)
        closes = [100 + rng.normal(0, 0.3) for _ in range(320)]
        assert scan_slow(build_df(klines(closes)), "multi_horizon") == {}

    def test_too_short_returns_none(self):
        df = build_df(klines(trend_series(150)))
        assert multi_horizon(df, len(df) - 1) is None


# ---------- double divergence ----------
class TestDoubleDivergence:
    def _bullish_div_df(self):
        rng = np.random.default_rng(11)
        closes = list(200 + np.cumsum(rng.normal(0.15, 0.4, 260)))
        base = closes[-1]
        leg1 = [base - 2 * k for k in range(1, 16)]
        bounce = [leg1[-1] + 1.4 * k for k in range(1, 9)]
        leg2 = [bounce[-1] - 0.9 * k for k in range(1, 15)]
        rebound = [leg2[-1] + 1.2 * k for k in range(1, 6)]
        closes = closes + leg1 + bounce + leg2 + rebound
        return build_df(klines(closes, step=4 * 3600_000, spread=0.002))

    def test_signal_structure_when_present(self):
        df = self._bullish_div_df()
        sigs = scan_slow(df, "double_divergence")
        for i, s in sigs.items():
            assert s["strategy"] == "double_divergence"
            assert (s["side"] == "long" and s["sl"] < s["price"] < s["tp"]) or (s["side"] == "short" and s["tp"] < s["price"] < s["sl"])
            assert math.isclose(abs(s["tp"] - s["price"]), 2 * abs(s["price"] - s["sl"]), rel_tol=1e-6)
            assert any("Divergencia" in r for r in s["reasons"])

    def test_requires_history(self):
        df = build_df(klines(trend_series(150), step=4 * 3600_000))
        assert double_divergence(df, len(df) - 1) is None

    def test_no_lookahead(self):
        df = self._bullish_div_df()
        sigs = scan_slow(df, "double_divergence")
        if not sigs:
            pytest.skip("synthetic series produced no divergence")
        i = next(iter(sigs))
        raw = klines(list(df["close"].values), step=4 * 3600_000, spread=0.002)
        sig_t = double_divergence(build_df(raw[: i + 1]), i)
        assert sig_t is not None and sig_t["side"] == sigs[i]["side"]


# ---------- maker ----------
class TestMaker:
    def test_limit_price_and_fill(self):
        assert maker.limit_price("long", 100.0) < 100.0 < maker.limit_price("short", 100.0)
        lim = maker.limit_price("long", 100.0)
        assert maker.paper_filled("long", lim, lim - 0.001) and not maker.paper_filled("long", lim, lim)
        lim_s = maker.limit_price("short", 100.0)
        assert maker.paper_filled("short", lim_s, lim_s + 0.001) and not maker.paper_filled("short", lim_s, 100.0)

    def test_wait_seconds(self):
        assert maker.wait_seconds("1h", 2) == 7200 and maker.wait_seconds("1d", 5) == 86400

    def test_fee_constants(self):
        assert MAKER_FEE < FEE_RATE and math.isclose(MAKER_FEE, 0.0002)


# ---------- horizon-aware caps ----------
class TestSlotBlock:
    def test_long_not_counted_in_short_cap(self):
        eng = Engine.__new__(Engine)
        s = Settings(max_positions=2, max_long_positions=1)
        open_docs = [{"symbol": "BTCUSDT", "horizon": "long"}, {"symbol": "ETHUSDT", "horizon": "short"}]
        assert eng._slot_block(open_docs, s, "short") is None
        assert "largo plazo" in eng._slot_block(open_docs, s, "long")
        open_docs.append({"symbol": "SOLUSDT"})
        assert "corto plazo" in eng._slot_block(open_docs, s, "short")


# ---------- API ----------
class TestApi:
    def test_meta(self, client):
        m = client.get(f"{BASE_URL}/api/meta").json()
        assert m["long_horizon_keys"] == ["multi_horizon", "double_divergence"]
        assert set(SLOW_KEYS) <= set(m["strategy_keys"]) and "multi_horizon" in m["strategy_info"]

    def test_settings_roundtrip(self, client):
        cur = client.get(f"{BASE_URL}/api/settings").json()
        try:
            r = client.put(f"{BASE_URL}/api/settings", json={"entry_order_type": "market", "max_long_positions": 3})
            assert r.status_code == 200
            s = r.json()
            assert s["entry_order_type"] == "market" and s["max_long_positions"] == 3
            assert client.put(f"{BASE_URL}/api/settings", json={"entry_order_type": "bogus"}).status_code == 422
        finally:
            client.put(f"{BASE_URL}/api/settings", json={k: cur[k] for k in ("entry_order_type", "max_long_positions")})

    def test_performance_keys(self, client):
        d = client.get(f"{BASE_URL}/api/performance").json()
        assert "by_horizon" in d and "by_entry_type" in d and "maker" in d
        assert {"maker_fills", "market_fallbacks", "fill_rate", "fee_saved", "pending"} <= set(d["maker"])

    @pytest.mark.parametrize("strategy,tf", [("multi_horizon", "1d"), ("double_divergence", "4h")])
    def test_backtest_slow(self, client, strategy, tf):
        r = client.post(f"{BASE_URL}/api/backtest", json={"symbol": "BTCUSDT", "strategy": strategy, "timeframe": tf, "risk_per_trade": 1, "leverage": 3}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "total_trades" in d and d["total_trades"] >= 0 and d["timeframe"] == tf

    def test_pending_cancel_endpoint(self, client):
        r = client.delete(f"{BASE_URL}/api/pending-entries")
        assert r.status_code == 200 and "cancelled" in r.json()
