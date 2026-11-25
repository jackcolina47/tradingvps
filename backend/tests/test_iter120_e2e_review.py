"""E2E review test for iter120 MT bridge - runs against preview backend."""
import os, time, requests, pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://repo-workspace-14--8ursakdp.preview.emergentagent.com").rstrip("/")
PASSWORD = "BlackCat2233@"
BRIDGE_URL = "http://127.0.0.1:8765"
BRIDGE_TOKEN = "preview-fake-bridge-token-0123456789"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"password": PASSWORD}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# ---------------- FX / HotForex ----------------

def test_fx_config_no_metaapi(auth):
    r = auth.get(f"{BASE}/api/fx/config", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    body = str(data).lower()
    assert "metaapi" not in body, f"metaapi leaked: {body[:300]}"
    # token must not leak clear (only masked)
    assert BRIDGE_TOKEN not in body
    assert "bridge_effective" in data
    assert data["bridge_effective"].get("account_id") == "hfm_v1" or data.get("bridge_default_account") == "hfm_v1"
    assert "hfm_configured" in data


def test_fx_put_config_and_test(auth):
    r = auth.put(f"{BASE}/api/fx/config", json={
        "bridge_url": BRIDGE_URL,
        "bridge_token": BRIDGE_TOKEN,
        "bridge_account_id": "hfm_v1",
    }, timeout=15)
    assert r.status_code == 200, r.text
    r2 = auth.post(f"{BASE}/api/fx/hfm/test", json={}, timeout=30)
    assert r2.status_code == 200, r2.text
    d = r2.json()
    assert d.get("token_valid") is True, d
    assert d.get("account", {}).get("state") == "DEPLOYED", d
    assert d.get("account", {}).get("connection") == "CONNECTED", d
    assert "balance" in d.get("info", {}) or "equity" in d.get("info", {})
    suf = d.get("suffix")
    assert (isinstance(suf, dict) and suf.get("suffix") == "b") or suf == "b", d
    syms = d.get("symbols", {})
    assert not syms.get("missing") and not syms.get("disabled") and not syms.get("errors"), syms


def test_fx_mode_can_go_live(auth):
    r = auth.get(f"{BASE}/api/fx/mode", timeout=10)
    assert r.status_code == 200, r.text
    assert r.json().get("can_go_live") is True, r.json()


def test_fx_validations_bad_url_and_account(auth):
    r = auth.put(f"{BASE}/api/fx/config", json={"bridge_url": "ftp://x"}, timeout=10)
    assert 400 <= r.status_code < 500, r.text
    r2 = auth.put(f"{BASE}/api/fx/config", json={"bridge_account_id": "hfm v1!"}, timeout=10)
    assert 400 <= r2.status_code < 500, r2.text


def test_fx_bad_token_returns_424(auth):
    r = auth.post(f"{BASE}/api/fx/hfm/test", json={"bridge_token": "token-malo-0123456789-0123456789"}, timeout=20)
    assert r.status_code == 424, (r.status_code, r.text)
    body = r.text.lower()
    assert "401" in body or "rechaz" in body or "token" in body


def test_fx_account_notfound_hint(auth):
    r = auth.post(f"{BASE}/api/fx/hfm/test", json={"bridge_account_id": "no_existe"}, timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    hint = str(d.get("hint", "")) + str(d)
    assert "no_existe" in hint or "definid" in hint.lower() or "disponibl" in hint.lower()


def test_fx_bridge_unreachable(auth):
    # Switch to unreachable
    r = auth.put(f"{BASE}/api/fx/config", json={"bridge_url": "http://127.0.0.1:9"}, timeout=10)
    assert r.status_code == 200
    t0 = time.time()
    r2 = auth.post(f"{BASE}/api/fx/hfm/test", json={}, timeout=30)
    elapsed = time.time() - t0
    assert elapsed < 25, f"took {elapsed}s"
    assert r2.status_code == 424, (r2.status_code, r2.text)
    # restore
    r3 = auth.put(f"{BASE}/api/fx/config", json={"bridge_url": BRIDGE_URL}, timeout=10)
    assert r3.status_code == 200
    r4 = auth.post(f"{BASE}/api/fx/hfm/test", json={}, timeout=30)
    assert r4.status_code == 200, r4.text


def test_fx_reconnect_symbols_detect_klines(auth):
    r = auth.post(f"{BASE}/api/fx/hfm/reconnect", json={}, timeout=20)
    assert r.status_code == 200, r.text
    assert r.json().get("ok") in (True, None) or "account" in r.json()
    r2 = auth.post(f"{BASE}/api/fx/hfm/symbols", json={}, timeout=20)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    found = False
    pairs = body.get("symbols") or body.get("pairs") or body
    if isinstance(pairs, dict):
        for v in pairs.values():
            if isinstance(v, dict) and str(v.get("broker_symbol", "")).endswith("b"):
                found = True; break
    elif isinstance(pairs, list):
        for p in pairs:
            if isinstance(p, dict) and str(p.get("broker_symbol", "")).endswith("b"):
                found = True; break
    assert found, body
    r3 = auth.post(f"{BASE}/api/fx/hfm/detect-suffix", json={}, timeout=20)
    assert r3.status_code == 200, r3.text
    assert r3.json().get("suffix") == "b"
    r4 = auth.get(f"{BASE}/api/fx/klines", params={"symbol": "EURUSD", "tf": "1h", "limit": 50}, timeout=20)
    assert r4.status_code == 200, r4.text
    assert r4.json().get("source") == "hfm"


def test_fx_old_endpoints_gone(auth):
    for path in ["/api/fx/hfm/link", "/api/fx/hfm/recreate", "/api/fx/hfm/redeploy"]:
        r = auth.post(f"{BASE}{path}", json={}, timeout=10)
        assert r.status_code in (404, 405), f"{path} -> {r.status_code}"


# ---------------- Pepperstone ----------------

def test_pepper_flow(auth):
    r = auth.put(f"{BASE}/api/pepper/config", json={
        "bridge_url": BRIDGE_URL,
        "bridge_token": BRIDGE_TOKEN,
        "bridge_account_id": "",
    }, timeout=15)
    assert r.status_code == 200, r.text
    r2 = auth.post(f"{BASE}/api/pepper/hfm/test", json={}, timeout=30)
    assert r2.status_code == 200, r2.text
    d = r2.json()
    assert d.get("account", {}).get("connection") == "CONNECTED", d
    r3 = auth.get(f"{BASE}/api/pepper/mode", timeout=10)
    assert r3.status_code == 200


# ---------------- Regression ----------------

def test_dashboards(auth):
    for p in ["/api/dashboard", "/api/fx/dashboard", "/api/pepper/dashboard"]:
        r = auth.get(f"{BASE}{p}", timeout=15)
        assert r.status_code == 200, f"{p} -> {r.status_code}: {r.text[:200]}"
