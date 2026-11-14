# Iteration 84: API tests for EMA 10/20 test strategy ported to HotForex (/api/fx) and Pepperstone (/api/pepper)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PREFIXES = ["/api/fx", "/api/pepper"]
FIELDS = ["ema_test_enabled", "ema_test_symbols", "ema_test_adx_filter", "ema_test_adx_min"]


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    r = sess.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    sess.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return sess


@pytest.fixture(scope="module", autouse=True)
def restore(s):
    orig = {p: {k: s.get(f"{BASE_URL}{p}/settings").json()[k] for k in FIELDS} for p in PREFIXES}
    orig_bybit = {k: s.get(f"{BASE_URL}/api/settings").json().get(k) for k in FIELDS}
    yield
    for p in PREFIXES:
        r = s.put(f"{BASE_URL}{p}/settings", json=orig[p])
        assert r.status_code == 200
        cur = s.get(f"{BASE_URL}{p}/settings").json()
        assert {k: cur[k] for k in FIELDS} == orig[p]
    cur = {k: s.get(f"{BASE_URL}/api/settings").json().get(k) for k in FIELDS}
    assert cur == orig_bybit, "bybit settings changed by fx tests"


# Settings GET
@pytest.mark.parametrize("p", PREFIXES)
def test_settings_have_fields(s, p):
    d = s.get(f"{BASE_URL}{p}/settings").json()
    for k in FIELDS:
        assert k in d
    assert isinstance(d["ema_test_enabled"], bool)
    assert isinstance(d["ema_test_symbols"], list)
    assert 5 <= d["ema_test_adx_min"] <= 60


# Settings PUT + sanitization + independence
@pytest.mark.parametrize("p,other", [("/api/fx", "/api/pepper"), ("/api/pepper", "/api/fx")])
def test_put_and_independence(s, p, other):
    before_other = {k: s.get(f"{BASE_URL}{other}/settings").json()[k] for k in FIELDS}
    payload = {"ema_test_enabled": True, "ema_test_symbols": ["EURUSD", "FOOBAR", "EURUSD", "XAUUSD"],
               "ema_test_adx_filter": True, "ema_test_adx_min": 33}
    r = s.put(f"{BASE_URL}{p}/settings", json=payload)
    assert r.status_code == 200, r.text
    d = s.get(f"{BASE_URL}{p}/settings").json()
    assert d["ema_test_enabled"] is True
    assert d["ema_test_symbols"] == ["EURUSD", "XAUUSD"]
    assert d["ema_test_adx_filter"] is True
    assert d["ema_test_adx_min"] == 33
    after_other = {k: s.get(f"{BASE_URL}{other}/settings").json()[k] for k in FIELDS}
    assert after_other == before_other


@pytest.mark.parametrize("p", PREFIXES)
@pytest.mark.parametrize("v", [4, 61])
def test_adx_min_bounds(s, p, v):
    r = s.put(f"{BASE_URL}{p}/settings", json={"ema_test_adx_min": v})
    assert r.status_code == 422


@pytest.mark.parametrize("p", PREFIXES)
def test_meta(s, p):
    d = s.get(f"{BASE_URL}{p}/meta").json()
    strategies = d["strategies"]
    keys = strategies if isinstance(strategies, list) else list(strategies.keys())
    flat = [k if isinstance(k, str) else k.get("key") for k in keys]
    assert "ema_test" in flat
    assert "ema_test" not in d["strategy_keys"]


@pytest.mark.parametrize("p", PREFIXES)
def test_backtest_ema_test(s, p):
    # ensure sessions_allowed set does not filter the test
    r = s.post(f"{BASE_URL}{p}/backtest", json={"symbol": "EURUSD", "strategy": "ema_test", "timeframe": "15m"}, timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    res = d.get("result", d)
    trades = res.get("trades", [])
    print(p, "trades", len(trades), "skipped_session", res.get("skipped_session"))
    assert res.get("skipped_session", 0) == 0
    for t in trades:
        assert t.get("reason") in ("CRUCE", "SL", "FIN_SEMANA", "FIN"), t
        assert not t.get("partial"), t


def test_backtest_bad_strategy(s):
    r = s.post(f"{BASE_URL}/api/pepper/backtest", json={"symbol": "EURUSD", "strategy": "nope", "timeframe": "15m"})
    assert r.status_code in (400, 422)


def test_bybit_regression(s):
    d = s.get(f"{BASE_URL}/api/settings").json()
    for k in FIELDS:
        assert k in d
