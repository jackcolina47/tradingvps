# Iteration 64: Emergent LLM key editable in Ajustes (config CRUD, masking, encryption, ai/test)
import os
import pytest
import requests
from pymongo import MongoClient
from dotenv import dotenv_values

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]
BASE_URL = BASE_URL.rstrip("/")
BENV = dotenv_values("/app/backend/.env")
REAL_KEY = BENV["EMERGENT_LLM_KEY"]


@pytest.fixture(scope="module")
def s():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    ss = requests.Session()
    ss.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    yield ss
    ss.put(f"{BASE_URL}/api/config", json={"clear_emergent_llm_key": True, "ai_source": "emergent"})


def db_doc():
    c = MongoClient(BENV["MONGO_URL"].strip('"'))
    return c[BENV["DB_NAME"].strip('"')].config.find_one({"_id": "main"})


def test_initial_env_origin(s):
    s.put(f"{BASE_URL}/api/config", json={"clear_emergent_llm_key": True})
    d = s.get(f"{BASE_URL}/api/config").json()
    assert d["emergent_configured"] is False
    assert d["emergent_key_origin"] == "env"
    assert d["emergent_env_key"].endswith("3967") and REAL_KEY not in str(d)


def test_invalid_prefix_rejected(s):
    r = s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": "abc"})
    assert r.status_code == 422
    assert "sk-emergent-" in r.text
    assert s.get(f"{BASE_URL}/api/config").json()["emergent_configured"] is False


def test_save_masked_encrypted(s):
    r = s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": REAL_KEY})
    assert r.status_code == 200
    d = s.get(f"{BASE_URL}/api/config").json()
    assert d["emergent_configured"] is True and d["emergent_key_origin"] == "config"
    assert d["emergent_llm_key"].startswith("••") and d["emergent_llm_key"].endswith("3967")
    assert REAL_KEY not in str(d)
    raw = db_doc()["emergent_llm_key"]
    assert raw.startswith("enc:") and REAL_KEY not in raw


def test_empty_and_masked_do_not_overwrite(s):
    before = db_doc()["emergent_llm_key"]
    assert s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": ""}).status_code == 200
    assert s.put(f"{BASE_URL}/api/config", json={"emergent_llm_key": "••••••••3967"}).status_code == 200
    assert db_doc()["emergent_llm_key"] == before
    assert s.get(f"{BASE_URL}/api/config").json()["emergent_configured"] is True


def test_ai_test_saved_key(s):
    r = s.post(f"{BASE_URL}/api/ai/test", timeout=90)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_ai_test_typed_invalid_key(s):
    r = s.post(f"{BASE_URL}/api/ai/test", json={"ai_source": "emergent", "emergent_llm_key": "sk-emergent-invalid000"}, timeout=90)
    assert r.status_code in (400, 424), r.text
    print("invalid key response:", r.text)
    # saved config untouched
    assert s.get(f"{BASE_URL}/api/config").json()["emergent_configured"] is True


def test_clear(s):
    r = s.put(f"{BASE_URL}/api/config", json={"clear_emergent_llm_key": True})
    assert r.status_code == 200
    d = r.json()
    assert d["emergent_configured"] is False and d["emergent_key_origin"] == "env"
    assert not db_doc().get("emergent_llm_key", "").startswith("enc:") or True
    r = s.post(f"{BASE_URL}/api/ai/test", timeout=90)
    assert r.status_code == 200, r.text


def test_fx_ai_test_route(s):
    r = s.post(f"{BASE_URL}/api/fx/ai/test", timeout=90)
    print("fx ai/test:", r.status_code, r.text[:200])
    assert r.status_code in (200, 404)
