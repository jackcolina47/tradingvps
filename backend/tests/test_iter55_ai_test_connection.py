# AI test-connection (Emergent universal key renewed) regression tests
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


@pytest.fixture(scope="module")
def s():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"}, timeout=30)
    if r.status_code != 200:
        pytest.skip("login failed")
    ses = requests.Session()
    ses.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return ses


# Emergent default (Claude) on all three bases
@pytest.mark.parametrize("base", ["/api", "/api/fx", "/api/pepper"])
def test_ai_test_default(s, base):
    r = s.post(f"{BASE_URL}{base}/ai/test", json={}, timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and isinstance(d["reply"], str) and d["reply"]
    assert d.get("source") == "emergent"
    if base == "/api":
        assert d.get("provider") == "anthropic"
        print("default:", d.get("model"))


# Other providers through emergent key
@pytest.mark.parametrize("prov,model", [("openai", "gpt-5.5"), ("gemini", "gemini-3-flash-preview")])
def test_ai_test_other_providers(s, prov, model):
    r = s.post(f"{BASE_URL}/api/ai/test", json={"ai_provider": prov, "ai_model": model}, timeout=120)
    if r.status_code == 424:
        pytest.xfail(f"model unavailable: {r.text[:200]}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["provider"] == prov and d["reply"]
    assert d.get("saved") is False


# Health OK + config untouched
def test_config_health_and_unchanged(s):
    r = s.get(f"{BASE_URL}/api/config", timeout=30)
    assert r.status_code == 200
    c = r.json()
    assert c.get("ai_source") == "emergent"
    assert c.get("ai_provider") == "anthropic"
    act = c.get("ai_active") or {}
    print("ai_active:", {k: v for k, v in act.items() if "key" not in k})
    assert (act.get("health") or {}).get("ok") is True
