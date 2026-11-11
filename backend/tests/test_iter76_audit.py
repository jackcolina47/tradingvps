"""Iteration 76 audit: health sweep, auth, backtests, performance, isolation, LLM concurrency, _serialized unit test, state integrity.
Run serially: pytest tests/test_iter76_audit.py -n 0
"""
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PW = open("/app/memory/test_credentials.md").read().split("`")[1]
LOG = "/var/log/supervisor/backend.err.log"
SKIP_SLOW = {"/api/deploy/package", "/api/backup"}  # downloads, checked separately only for status
KLINES = {"/api/klines", "/api/fx/klines", "/api/pepper/klines"}


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": PW}, timeout=30)
    assert r.status_code == 200
    t = r.json()["token"]
    assert isinstance(t, str) and t
    return t


@pytest.fixture(scope="module")
def H(token):
    return {"Authorization": f"Bearer {token}"}


def _get_paths():
    d = json.load(open("/tmp/openapi.json")) if os.path.exists("/tmp/openapi.json") else requests.get("http://localhost:8001/openapi.json").json()
    return [p for p, ops in d["paths"].items() if "get" in ops and "{" not in p]


# --- Auth ---
class TestAuth:
    def test_wrong_password(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "TEST_wrong"}, timeout=30)
        assert r.status_code in (400, 401, 403)

    def test_protected_401(self):
        for p in ("/api/settings", "/api/fx/settings", "/api/pepper/settings", "/api/trades"):
            r = requests.get(BASE_URL + p, timeout=30)
            assert r.status_code == 401, (p, r.status_code)

    def test_root_public_meta_protected(self):
        # /api/ is the only public GET; /api/meta and /api/health are not public (health does not exist)
        assert requests.get(f"{BASE_URL}/api/", timeout=30).status_code == 200
        assert requests.get(f"{BASE_URL}/api/meta", timeout=30).status_code == 401


# --- GET health sweep ---
class TestHealthSweep:
    def test_all_gets(self, H):
        bad, slow = [], []
        for p in _get_paths():
            t0 = time.time()
            try:
                r = requests.get(BASE_URL + p, headers=H, timeout=120, stream=p in SKIP_SLOW)
                code = r.status_code
                r.close()
            except Exception as e:
                code = f"EXC {e}"
            dt = time.time() - t0
            if dt > 15:
                slow.append((p, round(dt, 1)))
            exp = 422 if p in KLINES else 200
            if code != exp:
                bad.append((p, code))
        print("SLOW:", slow)
        print("BAD:", bad)
        assert not bad, bad


# --- Backtests ---
class TestBacktest:
    def test_crypto_backtest(self, H):
        r = requests.post(f"{BASE_URL}/api/backtest", headers=H, json={"symbol": "SOLUSDT", "timeframe": "1h"}, timeout=180)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        print(json.dumps(d)[:600])
        assert isinstance(d, dict) and d

    def test_fx_backtest(self, H):
        r = requests.post(f"{BASE_URL}/api/fx/backtest", headers=H, json={"symbol": "EURUSD", "timeframe": "1h"}, timeout=180)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        print(json.dumps(d)[:600])
        assert isinstance(d, dict) and d


# --- Performance & isolation ---
class TestPerformance:
    @pytest.mark.parametrize("pre", ["/api", "/api/fx", "/api/pepper"])
    def test_performance_daily(self, H, pre):
        r = requests.get(f"{BASE_URL}{pre}/performance", headers=H, timeout=60)
        assert r.status_code == 200
        d = r.json()
        print(pre, list(d.keys()))
        assert any("daily" in k for k in d.keys()), list(d.keys())

    @pytest.mark.parametrize("pre", ["/api", "/api/fx", "/api/pepper"])
    def test_pairs_30d(self, H, pre):
        r = requests.get(f"{BASE_URL}{pre}/performance/pairs?days=30", headers=H, timeout=60)
        assert r.status_code == 200, r.text[:200]
        print(pre, json.dumps(r.json())[:300])

    def test_trades_isolated(self, H):
        counts = {}
        for pre in ("/api", "/api/fx", "/api/pepper"):
            d = requests.get(f"{BASE_URL}{pre}/trades", headers=H, timeout=60).json()
            items = d if isinstance(d, list) else d.get("trades", d.get("items", []))
            counts[pre] = len(items)
        print("trade counts", counts)
        assert counts == {"/api": 0, "/api/fx": 2, "/api/pepper": 0}, counts


# --- LLM concurrency fix (real calls) ---
class TestLlmConcurrency:
    def test_three_parallel_ai_tests(self, H):
        before = open(LOG, errors="ignore").read().count("concurrent_request_limit")
        paths = ["/api/ai/test", "/api/fx/ai/test", "/api/pepper/ai/test"]

        def call(p):
            t0 = time.time()
            r = requests.post(BASE_URL + p, headers=H, timeout=180)
            return p, r.status_code, round(time.time() - t0, 1), r.text[:200]

        with ThreadPoolExecutor(3) as ex:
            res = list(ex.map(call, paths))
        print(res)
        time.sleep(2)
        after = open(LOG, errors="ignore").read().count("concurrent_request_limit")
        print("429 count before/after", before, after)
        for p, code, dt, txt in res:
            assert code == 200, (p, code, txt)
            assert '"ok":true' in txt.replace(" ", ""), txt
        assert after == before, f"new concurrent_request_limit errors: {after - before}"


# --- Unit test of ai_advisor._serialized ---
class TestSerializedUnit:
    def _mod(self):
        sys.path.insert(0, "/app/backend")
        import ai_advisor
        return ai_advisor

    def test_retry_then_success(self, monkeypatch):
        m = self._mod()
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)
        monkeypatch.setattr(m.asyncio, "sleep", fake_sleep)
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("Error code: 429 - {'error': 'concurrent_request_limit'}")
            return "VALUE"
        m._gate = asyncio.Semaphore(1)
        assert asyncio.run(m._serialized(fn)) == "VALUE"
        assert calls["n"] == 3
        assert sleeps == [2, 5]

    def test_non_429_reraised(self, monkeypatch):
        m = self._mod()
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)
        monkeypatch.setattr(m.asyncio, "sleep", fake_sleep)
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            raise ValueError("boom")
        m._gate = asyncio.Semaphore(1)
        with pytest.raises(ValueError):
            asyncio.run(m._serialized(fn))
        assert calls["n"] == 1 and sleeps == []

    def test_exhausted_retries_raise(self, monkeypatch):
        m = self._mod()

        async def fake_sleep(s):
            pass
        monkeypatch.setattr(m.asyncio, "sleep", fake_sleep)
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            raise RuntimeError("CONCURRENCY_REQUEST_LIMIT")
        m._gate = asyncio.Semaphore(1)
        with pytest.raises(RuntimeError):
            asyncio.run(m._serialized(fn))
        assert calls["n"] == 4


# --- State integrity ---
class TestStateIntegrity:
    def test_bybit_state(self, H):
        assert requests.get(f"{BASE_URL}/api/mode", headers=H, timeout=30).json()["mode"] == "paper"
        s = requests.get(f"{BASE_URL}/api/settings", headers=H, timeout=30).json()
        exp = {f"{c}USDT" for c in "BTC ETH SOL XRP AVAX LINK ZEC ENA ONDO ARB UNI".split()}
        assert set(s["symbols"]) == exp and len(s["symbols"]) == 11
        for sym in ("ZECUSDT", "HYPEUSDT"):
            p = s["symbol_params"][sym]
            assert (p["sl_atr"], p["tp_atr"], p["atr_period"]) == (1.5, 6.0, 20.0)
        assert s["reopt_symbols"] == ["ZECUSDT", "HYPEUSDT"]
        assert s["bot_running"] is False, "Bybit bot is running"

    @pytest.mark.parametrize("pre", ["/api/fx", "/api/pepper"])
    def test_fx_state(self, H, pre):
        s = requests.get(f"{BASE_URL}{pre}/settings", headers=H, timeout=30).json()
        assert s.get("price_action_block") is True
        m = requests.get(f"{BASE_URL}{pre}/mode", headers=H, timeout=30).json()
        assert m["mode"] == "paper"
        running = s.get("bot_running", s.get("running"))
        assert running is False, f"{pre} running={running}"
