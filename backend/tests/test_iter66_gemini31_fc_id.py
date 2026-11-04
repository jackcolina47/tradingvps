# Iter66: Gemini 3.1 Pro functionCall `id` normalization (unit, no network) + chat API live check
import json
import os
import sys
import time

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_advisor  # noqa: E402
from litellm.llms.vertex_ai.gemini.transformation import _gemini_convert_messages_with_history  # noqa: E402

RAW = [{"type": "thinking", "thinking": json.dumps({"functionCall": {"name": "get_price", "args": {"symbol": "BTCUSDT"}, "id": "call_126"}}),
        "signature": "SIG"}]


def _msgs(blocks):
    return [{"role": "user", "content": "precio"},
            {"role": "assistant", "content": None, "thinking_blocks": blocks,
             "tool_calls": [{"id": "call_d186", "type": "function",
                             "function": {"name": "get_price", "arguments": '{"symbol": "BTCUSDT"}'}}]},
            {"role": "tool", "tool_call_id": "call_d186", "name": "get_price", "content": "{}"}]


def _model_parts(blocks):
    contents = _gemini_convert_messages_with_history(messages=_msgs(blocks))
    model = [c for c in contents if c.get("role") == "model"]
    assert len(model) == 1
    return model[0]["parts"]


def _sig(p):
    return p.get("thoughtSignature") or p.get("thought_signature")


# _normalize_blocks
class TestNormalizeBlocks:
    def test_strips_id_keeps_signature(self):
        out = ai_advisor._normalize_blocks(RAW)
        part = json.loads(out[0]["thinking"])
        assert "id" not in part["functionCall"]
        assert part["functionCall"] == {"name": "get_price", "args": {"symbol": "BTCUSDT"}}
        assert out[0]["signature"] == "SIG" and out[0]["type"] == "thinking"
        assert "call_126" in RAW[0]["thinking"]  # input not mutated

    def test_no_function_call_unchanged(self):
        b = [{"type": "thinking", "thinking": json.dumps({"text": "hola"}), "signature": "S"}]
        assert ai_advisor._normalize_blocks(b) == b

    def test_invalid_json_unchanged(self):
        b = [{"type": "thinking", "thinking": "not json {", "signature": "S"}, {"type": "thinking", "thinking": None}]
        assert ai_advisor._normalize_blocks(b) == b

    def test_fc_without_id_unchanged(self):
        b = [{"type": "thinking", "thinking": json.dumps({"functionCall": {"name": "x", "args": {}}}), "signature": "S"}]
        assert ai_advisor._normalize_blocks(b) == b


# litellm request shape demonstrates bug and fix
class TestLitellmShape:
    def test_normalized_single_signed_part(self):
        parts = _model_parts(ai_advisor._normalize_blocks(RAW))
        assert len(parts) == 1, parts
        assert _sig(parts[0]) == "SIG"

    def test_raw_id_produces_unsigned_duplicate(self):
        parts = _model_parts(RAW)
        assert len(parts) == 2, parts
        assert sum(1 for p in parts if not _sig(p)) == 1


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"}, timeout=30)
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return s


# live API check (Gemini 3.1 Pro)
class TestLiveChat:
    def test_config_gemini31(self, client):
        a = client.get(f"{BASE_URL}/api/config", timeout=30).json().get("ai_active", {})
        assert a.get("source") == "gemini" and a.get("model") == "gemini-3.1-pro-preview", a

    def test_fx_logs_tool_chat(self, client):
        r = client.post(f"{BASE_URL}/api/chat/message", timeout=30,
                        json={"text": "Lee los últimos 5 logs del bot fx con tus herramientas y resume en una línea", "panel": "fx"})
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        job = {}
        for _ in range(90):
            time.sleep(3)
            job = client.get(f"{BASE_URL}/api/chat/jobs/{jid}", timeout=30).json()
            if job.get("status") in ("done", "error", "failed"):
                break
        assert job.get("status") == "done", job
        a = job["result"]["assistant"]
        print("TOOLS:", a.get("tools") or a.get("tool_calls"), "CONTENT:", a.get("content", "")[:300])
        assert a.get("error") is False, a
        assert a.get("content", "").strip()
        assert "thought_signature" not in a["content"] and "No pude responder" not in a["content"]
