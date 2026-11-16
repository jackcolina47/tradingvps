# Iter89 API: Bybit Lab endpoints + Bybit settings (rules_auto_learn) + ai-rules API shape.
import os, pytest, requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE:
    # fallback to frontend .env
    import pathlib
    for line in pathlib.Path("/app/frontend/.env").read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE = line.split("=", 1)[1].strip().rstrip("/")
PWD = "BlackCat2233@"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"password": PWD}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_lab_requires_auth():
    r = requests.get(f"{BASE}/api/lab/dashboard", timeout=10)
    assert r.status_code in (401, 403)


def test_lab_dashboard(h):
    r = requests.get(f"{BASE}/api/lab/dashboard", headers=h, timeout=15)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"]["mode"] == "PAPER"
    assert d["status"].get("paper_only") is True
    assert isinstance(d.get("watchlist"), list) and len(d["watchlist"]) >= 1


def test_lab_meta(h):
    r = requests.get(f"{BASE}/api/lab/meta", headers=h, timeout=10)
    assert r.status_code == 200
    assert r.json().get("strategies") == ["ema_test"]


def test_lab_overview_settings_perf(h):
    for path in ("/api/lab/overview", "/api/lab/settings", "/api/lab/performance"):
        r = requests.get(f"{BASE}{path}", headers=h, timeout=15)
        assert r.status_code == 200, (path, r.text)


def test_lab_bot_start_scan_stop(h):
    assert requests.post(f"{BASE}/api/lab/bot/start", headers=h, timeout=15).status_code == 200
    assert requests.post(f"{BASE}/api/lab/bot/scan", headers=h, timeout=20).status_code == 200
    assert requests.post(f"{BASE}/api/lab/bot/stop", headers=h, timeout=15).status_code == 200


def test_lab_backtest(h):
    r = requests.post(f"{BASE}/api/lab/backtest", headers=h,
                      json={"symbol": "BTCUSDT", "timeframe": "15m"}, timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "trades" in d or "equity" in d or "result" in d or "summary" in d


def test_lab_isolation_from_bybit_settings(h):
    main = requests.get(f"{BASE}/api/settings", headers=h, timeout=10).json()
    lab = requests.get(f"{BASE}/api/lab/settings", headers=h, timeout=10).json()
    assert main.get("rules_auto_learn") is not None or "rules_auto_learn" in main
    # change lab timeframe and ensure main is unchanged
    main_tf = main.get("timeframe")
    new_tf = "15m" if lab.get("timeframe") != "15m" else "5m"
    put = requests.put(f"{BASE}/api/lab/settings", headers=h, json={"timeframe": new_tf}, timeout=10)
    assert put.status_code == 200
    main2 = requests.get(f"{BASE}/api/settings", headers=h, timeout=10).json()
    assert main2.get("timeframe") == main_tf


def test_no_lab_mode_endpoint(h):
    # must NOT exist a way to switch lab to real
    r = requests.put(f"{BASE}/api/lab/mode", headers=h, json={"mode": "REAL"}, timeout=10)
    assert r.status_code in (404, 405, 422)


def test_settings_rules_auto_learn_roundtrip(h):
    cur = requests.get(f"{BASE}/api/settings", headers=h, timeout=10).json()
    orig = cur.get("rules_auto_learn", True)
    try:
        r = requests.put(f"{BASE}/api/settings", headers=h, json={"rules_auto_learn": False}, timeout=10)
        assert r.status_code == 200
        assert requests.get(f"{BASE}/api/settings", headers=h, timeout=10).json()["rules_auto_learn"] is False
    finally:
        requests.put(f"{BASE}/api/settings", headers=h, json={"rules_auto_learn": bool(orig) if orig is not None else True}, timeout=10)


def test_ai_rules_shape(h):
    # Create a rule to ensure we can check shape
    create = requests.post(f"{BASE}/api/ai-rules", headers=h, json={"text": "TEST_ regla iter89 shape"}, timeout=10)
    assert create.status_code in (200, 201), create.text
    rid = create.json().get("id") or create.json().get("rule", {}).get("id")
    try:
        r = requests.get(f"{BASE}/api/ai-rules", headers=h, timeout=10)
        assert r.status_code == 200
        body = r.json()
        rules = body["rules"] if isinstance(body, dict) else body
        assert isinstance(rules, list) and len(rules) >= 1
        rule = next(x for x in rules if x.get("id") == rid)
        for k in ("hits", "misses", "auto", "blocked_closed", "blocked_wins", "blocked_pnl"):
            assert k in rule, f"missing {k} in rule {rule.get('id')}"
    finally:
        if rid:
            requests.delete(f"{BASE}/api/ai-rules/{rid}", headers=h, timeout=10)
