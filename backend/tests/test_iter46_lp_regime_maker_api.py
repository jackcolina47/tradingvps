# Iteration 46 (QA iter39): API coverage for long-horizon strategies, BTC regime, weekly rotation, maker entries.
# Run with: python -m pytest tests/test_iter46_lp_regime_maker_api.py -n 0 -q
import os
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PASSWORD = "BlackCat2233@"
KEYS = ["multi_horizon", "double_divergence"]
SETTING_KEYS = ["entry_order_type", "max_long_positions", "maker_wait_bars", "disabled_strategies"]


@pytest.fixture(scope="module")
def s():
    ses = requests.Session()
    r = ses.post(f"{BASE_URL}/api/auth/login", json={"password": PASSWORD}, timeout=30)
    assert r.status_code == 200
    ses.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return ses


@pytest.fixture(scope="module")
def original(s):
    orig = s.get(f"{BASE_URL}/api/settings", timeout=30).json()
    snap = {k: orig[k] for k in SETTING_KEYS}
    yield snap
    r = s.put(f"{BASE_URL}/api/settings", json=snap, timeout=60)
    assert r.status_code == 200, r.text
    now = s.get(f"{BASE_URL}/api/settings", timeout=30).json()
    for k in SETTING_KEYS:
        assert now[k] == snap[k], k


# --- meta
def test_meta(s):
    m = s.get(f"{BASE_URL}/api/meta", timeout=30).json()
    assert m["long_horizon_keys"] == KEYS
    strats = m["strategies"]
    keys = [x if isinstance(x, str) else x.get("key") for x in strats]
    assert len(keys) == 8 and "all" in keys and all(k in keys for k in KEYS)
    for k in KEYS:
        info = m["strategy_info"][k]
        assert info["name"] and info.get("rules") and info["horizon"] == "long"


def test_regime_and_rotation_removed(s):
    assert s.get(f"{BASE_URL}/api/market/context", timeout=30).status_code == 404
    assert s.post(f"{BASE_URL}/api/rotation/refresh", timeout=30).status_code in (404, 405)
    g = s.get(f"{BASE_URL}/api/settings", timeout=30).json()
    assert not {"btc_regime_filter", "btc_regime_sma", "rotation_enabled", "rotation_lookback_days"} & set(g)


def test_cancel_pending_empty(s):
    r = s.delete(f"{BASE_URL}/api/pending-entries", timeout=30)
    assert r.status_code == 200 and isinstance(r.json()["cancelled"], int)


# --- settings validation
@pytest.mark.parametrize("payload", [{"entry_order_type": "limit"}, {"max_long_positions": 0},
                                     {"max_long_positions": 7}, {"maker_wait_bars": 4}, {"maker_wait_bars": 0}])
def test_settings_invalid(s, original, payload):
    r = s.put(f"{BASE_URL}/api/settings", json=payload, timeout=30)
    assert r.status_code == 422, (payload, r.status_code, r.text)


def test_settings_valid_roundtrip(s, original):
    p = {"entry_order_type": "market", "max_long_positions": 3, "maker_wait_bars": 3}
    r = s.put(f"{BASE_URL}/api/settings", json=p, timeout=60)
    assert r.status_code == 200, r.text
    g = s.get(f"{BASE_URL}/api/settings", timeout=30).json()
    for k, v in p.items():
        assert g[k] == v, k


def test_disable_strategies(s, original):
    meta = s.get(f"{BASE_URL}/api/meta", timeout=30).json()
    allk = [x if isinstance(x, str) else x.get("key") for x in meta["strategies"]]
    allk = [k for k in allk if k != "all"]
    assert len(allk) == 7
    r = s.put(f"{BASE_URL}/api/settings", json={"disabled_strategies": allk}, timeout=30)
    assert r.status_code == 422
    short = [k for k in allk if k not in KEYS]
    r = s.put(f"{BASE_URL}/api/settings", json={"disabled_strategies": short}, timeout=30)
    assert r.status_code == 200, r.text
    assert sorted(s.get(f"{BASE_URL}/api/settings", timeout=30).json()["disabled_strategies"]) == sorted(short)


# --- backtest
@pytest.mark.parametrize("strategy,tf", [("multi_horizon", "1d"), ("double_divergence", "4h")])
def test_backtest_slow(s, strategy, tf):
    r = s.post(f"{BASE_URL}/api/backtest", json={"symbol": "BTCUSDT", "strategy": strategy, "timeframe": "15m",
                                                 "risk_per_trade": 1, "leverage": 3}, timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["timeframe"] == tf and d["total_trades"] >= 0
    print(strategy, d["total_trades"], d.get("win_rate"), d.get("total_return_pct"))


def test_backtest_fast(s):
    r = s.post(f"{BASE_URL}/api/backtest", json={"symbol": "ETHUSDT", "strategy": "trend_ema", "timeframe": "1h",
                                                 "risk_per_trade": 1, "leverage": 3}, timeout=120)
    assert r.status_code == 200, r.text
    assert r.json()["timeframe"] == "1h" and r.json()["total_trades"] >= 0


# --- performance / weekly
def test_performance(s):
    d = s.get(f"{BASE_URL}/api/performance", timeout=60).json()
    assert "by_horizon" in d and "by_entry_type" in d
    for k in ("maker_fills", "market_fallbacks", "fill_rate", "fee_saved", "pending"):
        assert k in d["maker"], k


def test_weekly(s):
    d = s.get(f"{BASE_URL}/api/weekly-summary", timeout=90).json()
    m = d["market"]
    assert {"entry_order_type", "pending_entries", "max_long_positions"} == set(m)
    text = d.get("text") or d.get("message") or ""
    assert "<b>Entradas</b>:" in text and "rotación" not in text, text[:500]


def test_fx_weekly(s):
    r = s.get(f"{BASE_URL}/api/fx/weekly-summary", timeout=90)
    assert r.status_code == 200
    assert r.json().get("market") is None


def test_fx_regression(s):
    assert s.get(f"{BASE_URL}/api/fx/settings", timeout=30).status_code == 200
    m = s.get(f"{BASE_URL}/api/fx/meta", timeout=30)
    assert m.status_code == 200
    assert "multi_horizon" not in str(m.json().get("strategies"))
