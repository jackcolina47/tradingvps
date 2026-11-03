# Live Gemini 3 chat: thought_signature regression across the three panels (real key, slow)
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")


@pytest.fixture(scope="module")
def s():
    ses = requests.Session()
    r = ses.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    ses.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return ses


def _ask(s, text, panel, timeout=180):
    for _ in range(30):  # wait if a previous job is still running
        r = s.post(f"{BASE_URL}/api/chat/message", json={"text": text, "panel": panel})
        if r.status_code != 409:
            break
        time.sleep(5)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    end = time.time() + timeout
    while time.time() < end:
        j = s.get(f"{BASE_URL}/api/chat/jobs/{jid}").json()
        if j.get("status") not in ("running", "pending", "queued"):
            return j
        time.sleep(3)
    pytest.fail("job timeout")


# Precondition: Gemini 3 Flash active and healthy
def test_precondition_gemini(s):
    a = s.get(f"{BASE_URL}/api/config").json()["ai_active"]
    assert a["source"] == "gemini" and a["model"] == "gemini-3-flash-preview" and a["health"]["ok"] is True


# Tool-using turn via API (crypto panel)
def test_btc_price_tool_turn(s):
    j = _ask(s, "Dame el precio actual de BTCUSDT usando tus herramientas", "crypto")
    assert j["status"] == "done", j
    a = j["result"]["assistant"]
    assert a["error"] is False, a
    assert a["content"].strip()
    assert "thought_signature" not in a["content"]
    print("tools:", a.get("tools"))
