# Iter65: Emergent key precedence (typed > saved > env) + diagnostics (key_hint/key_origin)
import os
import subprocess
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split()[0]
BASE_URL = BASE_URL.rstrip("/")
REAL_KEY = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/backend/.env") if l.startswith("EMERGENT_LLM_KEY=")][0]
FAKE_KEY = "sk-emergent-TEST0000000000ab12"
REAL_HINT = "••••" + REAL_KEY[-4:]


@pytest.fixture(scope="module")
def s():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    assert r.status_code == 200
    ses = requests.Session()
    ses.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    ses.put(f"{BASE_URL}/api/config", json={"clear_emergent_llm_key": True})
    yield ses
    ses.put(f"{BASE_URL}/api/config", json={"clear_emergent_llm_key": True, "ai_source": "emergent", "ai_provider": "anthropic", "ai_model": "claude-sonnet-5-5"})


def cfg(s):
    r = s.get(f"{BASE_URL}/api/config")
    assert r.status_code == 200
    return r.json()


def test_01_initial_env(s):
    a = cfg(s)["ai_active"]
    assert REAL_HINT == "••••f195"
    assert a["key_hint"] == REAL_HINT and a["key_origin"] == "env" and a["key_origin_label"] == "variable del servidor"


def test_02_saved_fake_precedence(s):
    r = s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": FAKE_KEY})
    assert r.status_code == 200, r.text
    c = cfg(s)
    assert c["emergent_configured"] is True
    a = c["ai_active"]
    assert a["key_hint"] == "••••ab12" and a["key_origin"] == "config" and a["key_origin_label"] == "guardada en Ajustes"
    time.sleep(1)
    log = subprocess.run("tail -n 300 /var/log/supervisor/backend.err.log", shell=True, capture_output=True, text=True).stdout
    assert "IA configurada" in log and "clave ••••ab12 (guardada en Ajustes)" in log


def test_03_ai_test_saved_fake_fails(s):
    r = s.post(f"{BASE_URL}/api/ai/test", json={}, timeout=90)
    assert r.status_code == 424, r.text
    d = r.json()["detail"]
    assert "clave ••••ab12 · guardada en Ajustes" in d
    assert "API key inválida" in d, d


def test_04_typed_overrides_for_test_only(s):
    r = s.post(f"{BASE_URL}/api/ai/test", json={"emergent_llm_key": REAL_KEY}, timeout=90)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["key_hint"] == REAL_HINT and d["key_origin_label"] == "escrita en el formulario"
    assert REAL_KEY not in r.text
    assert cfg(s)["ai_active"]["key_hint"] == "••••ab12"


def test_05_replace_with_real(s):
    r = s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": REAL_KEY})
    assert r.status_code == 200
    a = cfg(s)["ai_active"]
    assert a["key_hint"] == REAL_HINT and a["key_origin"] == "config"
    r = s.post(f"{BASE_URL}/api/ai/test", json={}, timeout=90)
    assert r.status_code == 200, r.text
    assert r.json()["key_origin_label"] == "guardada en Ajustes"


def test_06_fx_ai_test(s):
    r = s.post(f"{BASE_URL}/api/fx/ai/test", json={}, timeout=90)
    assert r.status_code == 200, r.text
    assert r.json()["key_hint"] == REAL_HINT
    g = s.get(f"{BASE_URL}/api/fx/config")
    if g.status_code == 200 and "ai_active" in g.json():
        assert g.json()["ai_active"]["key_hint"] == REAL_HINT


def test_07_clear_back_to_env(s):
    r = s.put(f"{BASE_URL}/api/config", json={"clear_emergent_llm_key": True})
    assert r.status_code == 200
    c = cfg(s)
    assert c["emergent_configured"] is False
    a = c["ai_active"]
    assert a["key_hint"] == REAL_HINT and a["key_origin"] == "env"
    r = s.post(f"{BASE_URL}/api/ai/test", json={}, timeout=90)
    assert r.status_code == 200, r.text
    assert r.json()["key_origin_label"] == "variable del servidor"


def test_08_invalid_prefix_rejected(s):
    r = s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": "abc123456789"})
    assert r.status_code in (400, 422)
    assert cfg(s)["ai_active"]["key_origin"] == "env"
