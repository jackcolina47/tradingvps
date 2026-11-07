# Iter72: Learned AI rules - unit (prompt injection, active filter) + API CRUD/isolation
import os
import sys
import uuid
import asyncio
import pytest
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv("/app/frontend/.env")

import ai_rules  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")

SIG = {"side": "long", "strategy": "trend", "price": 1.3155, "sl": 1.3100, "tp": 1.3260, "score": 3,
       "reasons": ["EMA cross"], "indicators": {"rsi": 55}}


# ---------- Unit: prompt_lines / _build_prompt ----------
class TestPromptUnit:
    def test_prompt_lines_empty(self):
        assert ai_rules.prompt_lines([]) == ""
        assert ai_rules.prompt_lines(None) == ""

    def test_prompt_lines_symbol(self):
        out = ai_rules.prompt_lines([{"text": "X", "symbol": "GBPUSD"}])
        assert "REGLAS APRENDIDAS" in out and "(solo GBPUSD)" in out and "- X" in out

    def test_prompt_lines_global_no_solo(self):
        out = ai_rules.prompt_lines([{"text": "Regla global", "symbol": ""}])
        assert "solo" not in out.split("\n", 1)[1]

    def test_ai_advisor_build_prompt(self):
        import ai_advisor
        p = ai_advisor._build_prompt("BTCUSDT", "15m", SIG, {"learned_rules": [{"text": "No comprar tras 3 velas grandes", "symbol": ""}]})
        assert "No comprar tras 3 velas grandes" in p and "REGLAS APRENDIDAS" in p
        p2 = ai_advisor._build_prompt("BTCUSDT", "15m", SIG, {})
        assert "REGLAS APRENDIDAS" not in p2

    def test_fx_ai_build_prompt(self):
        from fx import ai as fxai
        p = fxai._build_prompt("GBPUSD", "15m", SIG, {"learned_rules": [{"text": "No comprar tras 3 velas grandes", "symbol": ""}]})
        assert "No comprar tras 3 velas grandes" in p and "REGLAS APRENDIDAS" in p


# ---------- Unit: active() against a real Mongo test collection ----------
class TestActiveUnit:
    def test_active_filters(self):
        from motor.motor_asyncio import AsyncIOMotorClient
        from fx.engine_core import PrefixedDB

        async def run():
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            prefix = f"test_iter72_{uuid.uuid4().hex[:8]}_"
            db = PrefixedDB(client[os.environ["DB_NAME"]], prefix)
            try:
                g = await ai_rules.add(db, ai_rules.RuleIn(text="Regla global de prueba"))
                gb = await ai_rules.add(db, ai_rules.RuleIn(text="Regla solo gbp prueba", symbol="gbpusd"))
                eu = await ai_rules.add(db, ai_rules.RuleIn(text="Regla solo eur prueba", symbol="EURUSD"))
                dis = await ai_rules.add(db, ai_rules.RuleIn(text="Regla deshabilitada prueba"))
                await ai_rules.patch(db, dis["id"], ai_rules.RulePatch(enabled=False))
                ids = {r["id"] for r in await ai_rules.active(db, "GBPUSD")}
                assert g["id"] in ids and gb["id"] in ids
                assert eu["id"] not in ids and dis["id"] not in ids
                all_ids = {r["id"] for r in await ai_rules.active(db)}
                assert eu["id"] in all_ids and dis["id"] not in all_ids
            finally:
                await client[os.environ["DB_NAME"]].drop_collection(f"{prefix}ai_rules")
                client.close()

        asyncio.run(run())


# ---------- API: CRUD + isolation ----------
@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


class TestRulesApi:
    def test_crud_and_isolation(self, client):
        fx = f"{BASE_URL}/api/fx/ai-rules"
        text = f"TEST_Regla de prueba número uno {uuid.uuid4().hex[:6]}"
        r = client.post(fx, json={"text": text, "symbol": "eurusd"})
        assert r.status_code in (200, 201), r.text
        rule = r.json()
        rid = rule["id"]
        try:
            assert rule["symbol"] == "EURUSD" and rule["enabled"] is True and rule["text"] == text
            assert client.post(fx, json={"text": text}).status_code == 409
            assert client.post(fx, json={"text": "abc"}).status_code == 422
            g = client.get(fx).json()
            assert g["max"] == 30 and any(x["id"] == rid for x in g["rules"])
            p = client.patch(f"{fx}/{rid}", json={"enabled": False})
            assert p.status_code == 200 and p.json()["enabled"] is False
            p = client.patch(f"{fx}/{rid}", json={"text": "Regla editada de prueba"})
            assert p.status_code == 200 and p.json()["text"] == "Regla editada de prueba"
            g = client.get(fx).json()
            got = [x for x in g["rules"] if x["id"] == rid][0]
            assert got["text"] == "Regla editada de prueba" and got["enabled"] is False
            # isolation
            for other in ("/api/ai-rules", "/api/pepper/ai-rules"):
                o = client.get(f"{BASE_URL}{other}")
                assert o.status_code == 200
                assert not any(x["id"] == rid for x in o.json()["rules"])
            assert client.patch(f"{fx}/badid", json={"enabled": True}).status_code == 404
        finally:
            d = client.delete(f"{fx}/{rid}")
        assert d.status_code == 200 and d.json() == {"ok": True}
        assert client.delete(f"{fx}/{rid}").status_code == 404

    def test_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/fx/ai-rules")
        assert r.status_code in (401, 403)
