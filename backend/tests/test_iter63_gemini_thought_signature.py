# Iter63: Gemini 3 thought_signature preservation in tool-calling chat (unit, no network) + chat API regression
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_advisor  # noqa: E402
from litellm.llms.vertex_ai.gemini.transformation import _gemini_convert_messages_with_history  # noqa: E402

BLOCKS = [{"type": "thinking", "thinking": json.dumps({"functionCall": {"name": "get_price", "args": {"symbol": "BTCUSDT"}}}),
           "signature": "SIG123"}]


def _assistant():
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_price", "arguments": '{"symbol": "BTCUSDT"}'}}]}


def _chat(last=None):
    return SimpleNamespace(messages=[{"role": "user", "content": "x"}, last if last is not None else _assistant()])


def _resp(blocks="__missing__"):
    msg = SimpleNamespace() if blocks == "__missing__" else SimpleNamespace(thinking_blocks=blocks)
    return SimpleNamespace(raw=SimpleNamespace(choices=[SimpleNamespace(message=msg)]))


# _keep_thought_signatures helper
class TestKeepThoughtSignatures:
    def test_copies_blocks(self):
        c = _chat()
        ai_advisor._keep_thought_signatures(c, _resp(BLOCKS))
        assert c.messages[-1]["thinking_blocks"] == BLOCKS

    def test_no_attr(self):
        c = _chat()
        ai_advisor._keep_thought_signatures(c, _resp())
        assert "thinking_blocks" not in c.messages[-1]

    def test_none_blocks(self):
        c = _chat()
        ai_advisor._keep_thought_signatures(c, _resp(None))
        assert "thinking_blocks" not in c.messages[-1]

    def test_last_without_tool_calls(self):
        c = _chat({"role": "assistant", "content": "hola"})
        ai_advisor._keep_thought_signatures(c, _resp(BLOCKS))
        assert c.messages[-1] == {"role": "assistant", "content": "hola"}

    def test_last_is_user(self):
        c = SimpleNamespace(messages=[{"role": "user", "content": "x"}])
        ai_advisor._keep_thought_signatures(c, _resp(BLOCKS))
        assert c.messages[-1] == {"role": "user", "content": "x"}

    def test_raw_missing(self):
        c = _chat()
        ai_advisor._keep_thought_signatures(c, SimpleNamespace())
        ai_advisor._keep_thought_signatures(c, SimpleNamespace(raw=None))
        ai_advisor._keep_thought_signatures(SimpleNamespace(messages=[]), _resp(BLOCKS))
        assert "thinking_blocks" not in c.messages[-1]


# litellm Gemini request shape
def _fc_parts(msgs):
    contents = _gemini_convert_messages_with_history(messages=msgs)
    model = [c for c in contents if c.get("role") == "model"]
    assert len(model) == 1
    return [p for p in model[0]["parts"] if "function_call" in p or "functionCall" in p]


class TestGeminiRequestShape:
    def test_with_blocks_has_signature(self):
        a = _assistant()
        a["thinking_blocks"] = BLOCKS
        parts = _fc_parts([{"role": "user", "content": "x"}, a,
                           {"role": "tool", "tool_call_id": "c1", "name": "get_price", "content": "{}"}])
        assert len(parts) == 1
        assert parts[0].get("thoughtSignature") == "SIG123"

    def test_without_blocks_no_signature(self):
        parts = _fc_parts([{"role": "user", "content": "x"}, _assistant(),
                           {"role": "tool", "tool_call_id": "c1", "name": "get_price", "content": "{}"}])
        assert len(parts) == 1
        assert "thoughtSignature" not in parts[0]

    def test_end_to_end_via_helper(self):
        c = SimpleNamespace(messages=[{"role": "user", "content": "x"}, _assistant()])
        ai_advisor._keep_thought_signatures(c, _resp(BLOCKS))
        c.messages.append({"role": "tool", "tool_call_id": "c1", "name": "get_price", "content": "{}"})
        parts = _fc_parts(c.messages)
        assert len(parts) == 1 and parts[0]["thoughtSignature"] == "SIG123"


# Chat API regression (live, Emergent key)
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return s


def _ask(client, text):
    r = client.post(f"{BASE_URL}/api/chat/message", json={"text": text, "panel": "crypto"})
    assert r.status_code == 200, r.text
    d = r.json()
    jid = d.get("job_id") or d.get("id") or (d.get("job") or {}).get("id")
    assert jid, d
    for _ in range(60):
        time.sleep(3)
        j = client.get(f"{BASE_URL}/api/chat/jobs/{jid}").json()
        if j.get("status") in ("done", "error", "failed"):
            return j
    pytest.fail("chat job timeout")


class TestChatRegression:
    def test_tool_turn_and_followup(self, client):
        j = _ask(client, "¿Cuál es el estado actual del bot cripto y cuántas posiciones abiertas tiene? Usa tus herramientas.")
        print("JOB1", json.dumps(j, ensure_ascii=False)[:1500])
        assert j.get("status") == "done", j
        a = j["result"]["assistant"]
        assert not a.get("error"), a
        assert a.get("tools"), a
        j2 = _ask(client, "Gracias, resume en una línea.")
        print("JOB2", json.dumps(j2, ensure_ascii=False)[:1500])
        assert j2.get("status") == "done", j2
        a2 = j2["result"]["assistant"]
        assert not a2.get("error"), a2
        assert a2.get("content"), a2
