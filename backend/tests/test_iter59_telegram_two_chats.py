# Iteration 59: Telegram second chat ID (telegram_chat_id_2) - notify fan-out unit tests + config API
import os
import sys
import asyncio
import logging
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import notify  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip()
BASE_URL = BASE_URL.rstrip("/")


def run(c):
    return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


# ---------- notify unit tests (mocked HTTP) ----------
@pytest.fixture
def calls(monkeypatch):
    rec = {"msg": [], "doc": [], "fail": set()}

    async def fake_one(token, chat_id, text):
        rec["msg"].append(chat_id)
        if chat_id in rec["fail"]:
            raise RuntimeError(f"chat not found {chat_id}")
        return True

    async def fake_doc(token, chat_id, data, filename, caption):
        rec["doc"].append(chat_id)
        if chat_id in rec["fail"]:
            raise RuntimeError("doc fail")
        return True

    monkeypatch.setattr(notify, "_send_one", fake_one)
    monkeypatch.setattr(notify, "_send_document_one", fake_doc)
    saved = dict(notify._cfg)
    yield rec
    notify._cfg.clear()
    notify._cfg.update(saved)


class TestNotifyUnit:
    def test_configure_two(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        assert notify.chat_ids() == ["111", "222"]
        assert notify.is_on() is True

    def test_send_both(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        assert run(notify.send("x")) is True
        assert sorted(calls["msg"]) == ["111", "222"]

    def test_send_explicit_chat(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        run(notify.send("x", chat_id="999"))
        assert calls["msg"] == ["999"]

    def test_dedupe(self, calls):
        notify.configure(True, "tok", "111", {}, " 111 ")
        assert notify.chat_ids() == ["111"]
        run(notify.send("x"))
        assert calls["msg"] == ["111"]

    def test_partial_failure(self, calls, caplog):
        notify.configure(True, "tok", "111", {}, "222")
        calls["fail"].add("222")
        with caplog.at_level(logging.WARNING, logger="notify"):
            assert run(notify.send("x")) is True
        assert any("222" in r.message for r in caplog.records)

    def test_send_detailed(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        calls["fail"].add("222")
        res = run(notify.send_detailed("x"))
        d = {r["chat_id"]: r for r in res}
        assert d["111"]["ok"] is True and d["111"]["error"] is None
        assert d["222"]["ok"] is False and "chat not found" in d["222"]["error"]

    def test_all_fail_raises(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        calls["fail"].update({"111", "222"})
        with pytest.raises(RuntimeError):
            run(notify.send("x"))

    def test_no_chats(self, calls):
        notify.configure(True, "tok", "", {}, "")
        assert notify.is_on() is False
        with pytest.raises(RuntimeError, match="Telegram no configurado"):
            run(notify.send("x"))

    def test_send_document_fanout(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        assert run(notify.send_document(b"abc", "f.zip", "cap")) is True
        assert sorted(calls["doc"]) == ["111", "222"]

    def test_notify_kind_disabled(self, calls):
        notify.configure(True, "tok", "111", {"orders": False}, "222")
        assert run(notify.notify("x", "orders")) is False
        assert calls["msg"] == []
        assert run(notify.notify("x", "ai")) is True

    def test_notify_all_fail_returns_false(self, calls):
        notify.configure(True, "tok", "111", {}, "222")
        calls["fail"].update({"111", "222"})
        assert run(notify.notify("x", "orders")) is False


# ---------- API tests ----------
@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    r = sess.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    sess.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield sess
    sess.put(f"{BASE_URL}/api/config", json={"telegram_chat_id": "", "telegram_chat_id_2": "", "telegram_enabled": False})


class TestConfigApi:
    def test_persist_two(self, s):
        r = s.put(f"{BASE_URL}/api/config", json={"telegram_chat_id": "111", "telegram_chat_id_2": "222"})
        assert r.status_code == 200
        g = s.get(f"{BASE_URL}/api/config").json()
        assert g["telegram_chat_id"] == "111" and g["telegram_chat_id_2"] == "222"
        assert g["telegram_chats_count"] == 2
        assert g["telegram_ready"] is False

    def test_global_on_fx_and_pepper(self, s):
        for p in ("/api/fx/config", "/api/pepper/config"):
            g = s.get(f"{BASE_URL}{p}").json()
            assert g["telegram_chat_id_2"] == "222", p
            assert g["telegram_chats_count"] == 2, p

    def test_clear_second(self, s):
        r = s.put(f"{BASE_URL}/api/config", json={"telegram_chat_id_2": ""})
        assert r.status_code == 200
        g = s.get(f"{BASE_URL}/api/config").json()
        assert g["telegram_chat_id_2"] == "" and g["telegram_chats_count"] == 1

    def test_too_long_rejected(self, s):
        r = s.put(f"{BASE_URL}/api/config", json={"telegram_chat_id_2": "1" * 41})
        assert r.status_code == 422
        r = s.put(f"{BASE_URL}/api/fx/config", json={"telegram_chat_id_2": "1" * 41})
        assert r.status_code == 422

    def test_fx_put_second(self, s):
        r = s.put(f"{BASE_URL}/api/fx/config", json={"telegram_chat_id_2": "333"})
        assert r.status_code == 200
        assert s.get(f"{BASE_URL}/api/config").json()["telegram_chat_id_2"] == "333"

    @pytest.mark.parametrize("base", ["/api", "/api/fx", "/api/pepper"])
    def test_telegram_test_no_token(self, s, base):
        r = s.post(f"{BASE_URL}{base}/telegram/test")
        assert r.status_code == 424
        assert "Telegram no configurado" in r.json()["detail"]

    @pytest.mark.parametrize("base", ["/api", "/api/fx"])
    def test_chats_no_token(self, s, base):
        r = s.post(f"{BASE_URL}{base}/telegram/chats", json={"token": ""})
        assert r.status_code == 400
        assert "token del bot" in r.json()["detail"]

    def test_restore(self, s):
        r = s.put(f"{BASE_URL}/api/config", json={"telegram_chat_id": "", "telegram_chat_id_2": "", "telegram_enabled": False})
        assert r.status_code == 200
        g = s.get(f"{BASE_URL}/api/config").json()
        assert g["telegram_chat_id"] == "" and g["telegram_chat_id_2"] == "" and g["telegram_chats_count"] == 0
