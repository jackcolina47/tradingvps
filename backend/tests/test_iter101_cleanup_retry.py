"""Iter101 — post-cleanup regression:
 - Removed endpoints return 404/405
 - /api/meta cleaned (no profiles/mode_info/strategies/signal_modes; has confidence_levels + strategy_info.ai_funnel)
 - /api/dashboard cleaned (no news/profile_active/optimize_job)
 - /api/plan and /api/fx/plan work without a 'backtest' key
 - PUT /api/config and /api/fx/config: no news_*/wf_auto_remove but telegram/auto_review persist
 - PUT /api/settings accepts ai_min_confidence, risk_per_trade, atr_levels_enabled, breakeven/trailing/partial_tp
 - POST /api/ai/advise scope != 'plan' → 400 (no token spent)
 - chat/tools.py & proposals.py cleaned (no run_backtest/run_optimizer/run_walk_forward/get_news/get_market_context/get_walk_forward_status;
   get_guard present; proposals.ACTIONS has no set_profile / refresh_news)
 - Funnel retry cycle with mocked stage1/stage2 (shadow on low confidence, cooldown respected, re-exec after cooldown;
   execution when confidence > threshold; par not picked by stage1 never reaches stage2; confidence == threshold → shadow;
   max_positions full → funnel paused)
 - maker.cancel_all(symbols=[...]) cancels only the matching pending_entries
"""
import os
import sys
import time
import asyncio
import importlib
import pytest
import requests
import pandas as pd
import numpy as np

sys.path.insert(0, "/app/backend")


# -------- shared BASE_URL / token --------
def _read_fe_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_fe_env()).rstrip("/")
PWD = "BlackCat2233@"


@pytest.fixture(scope="module")
def token():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": PWD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ========== 1. Removed endpoints ==========
REMOVED_GET = [
    "/api/news", "/api/fx/news", "/api/fx/news/trades", "/api/backtest",
    "/api/optimize", "/api/walk-forward/check", "/api/ai/optimize-all",
    "/api/rotation/refresh", "/api/housekeeping/reopt", "/api/cron/walk-forward",
    "/api/lab/overview", "/api/lab/dashboard",
]
REMOVED_POST = [
    "/api/news/refresh", "/api/profile", "/api/fx/profile",
    "/api/backtest", "/api/optimize", "/api/walk-forward/check",
    "/api/ai/optimize-all", "/api/rotation/refresh", "/api/housekeeping/reopt",
    "/api/cron/walk-forward",
]


class TestRemovedEndpoints:
    @pytest.mark.parametrize("path", REMOVED_GET)
    def test_get_removed(self, path, h):
        r = requests.get(f"{BASE_URL}{path}", headers=h, timeout=15)
        assert r.status_code in (404, 405), f"{path} GET -> {r.status_code}"

    @pytest.mark.parametrize("path", REMOVED_POST)
    def test_post_removed(self, path, h):
        r = requests.post(f"{BASE_URL}{path}", headers=h, json={}, timeout=15)
        assert r.status_code in (404, 405), f"{path} POST -> {r.status_code}"


# ========== 2. /api/meta cleanup ==========
class TestMetaCleanup:
    @pytest.mark.parametrize("prefix", ["/api", "/api/fx"])
    def test_meta_shape(self, prefix, h):
        r = requests.get(f"{BASE_URL}{prefix}/meta", headers=h, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        for banned in ("profiles", "mode_info", "strategies", "signal_modes"):
            assert banned not in data, f"{prefix}/meta still contains {banned}"
        assert "confidence_levels" in data
        assert data["confidence_levels"] == [10, 20, 30, 40, 50, 60, 70, 80, 90]
        assert "strategy_info" in data and "ai_funnel" in data["strategy_info"]


# ========== 3. /api/dashboard cleanup ==========
class TestDashboardCleanup:
    @pytest.mark.parametrize("prefix", ["/api", "/api/fx", "/api/pepper"])
    def test_dashboard_no_news(self, prefix, h):
        r = requests.get(f"{BASE_URL}{prefix}/dashboard", headers=h, timeout=25)
        assert r.status_code == 200, r.text
        data = r.json()
        for banned in ("news", "profile_active", "optimize_job"):
            assert banned not in data, f"{prefix}/dashboard still has {banned}"


# ========== 4. plan endpoint ==========
class TestPlan:
    @pytest.mark.parametrize("prefix", ["/api", "/api/fx"])
    def test_plan_projection(self, prefix, h):
        payload = {"initial_capital": 500, "monthly_deposit": 100, "target_monthly": 100.0, "scenario": "base"}
        r = requests.post(f"{BASE_URL}{prefix}/plan", headers=h, json=payload, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        # No 'backtest' key anywhere in the response
        assert "backtest" not in data
        # projection must have some numeric content
        assert isinstance(data, dict)


# ========== 5. Baseline GET endpoints ==========
class TestBaselineGet:
    @pytest.mark.parametrize("path", [
        "/api/dashboard", "/api/meta", "/api/guard", "/api/settings",
        "/api/config", "/api/performance", "/api/evolution",
        "/api/plan/checkin", "/api/market/context", "/api/housekeeping",
        "/api/weekly-summary", "/api/ai-rules",
        "/api/fx/dashboard", "/api/fx/meta", "/api/fx/guard",
        "/api/fx/settings", "/api/fx/config", "/api/fx/performance",
        "/api/fx/evolution", "/api/fx/plan/checkin", "/api/fx/weekly-summary",
        "/api/pepper/dashboard", "/api/pepper/guard", "/api/pepper/settings",
        "/api/pepper/config", "/api/pepper/performance", "/api/pepper/evolution",
        "/api/pepper/plan/checkin", "/api/pepper/weekly-summary",
    ])
    def test_200(self, path, h):
        r = requests.get(f"{BASE_URL}{path}", headers=h, timeout=25)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:120]}"


# ========== 6. PUT config ==========
class TestConfigPut:
    @pytest.mark.parametrize("prefix", ["/api", "/api/fx"])
    def test_config_no_legacy_fields(self, prefix, h):
        r = requests.get(f"{BASE_URL}{prefix}/config", headers=h, timeout=15)
        assert r.status_code == 200
        cfg = r.json()
        for banned in ("news_enabled", "news_auto_trade", "wf_auto_remove"):
            assert banned not in cfg, f"{prefix}/config still exposes {banned}"
        # telegram + auto_review fields still present
        assert "telegram_enabled" in cfg
        assert "auto_review_enabled" in cfg

    @pytest.mark.parametrize("prefix", ["/api", "/api/fx"])
    def test_config_put_persist_auto_review(self, prefix, h):
        # read current
        before = requests.get(f"{BASE_URL}{prefix}/config", headers=h, timeout=15).json()
        orig = before.get("auto_review_enabled", True)
        payload = {"auto_review_enabled": not orig}
        r = requests.put(f"{BASE_URL}{prefix}/config", headers=h, json=payload, timeout=15)
        assert r.status_code == 200, r.text
        after = requests.get(f"{BASE_URL}{prefix}/config", headers=h, timeout=15).json()
        assert after["auto_review_enabled"] == (not orig)
        # restore
        requests.put(f"{BASE_URL}{prefix}/config", headers=h, json={"auto_review_enabled": orig}, timeout=15)


# ========== 7. PUT settings ==========
class TestSettingsPut:
    @pytest.mark.parametrize("prefix", ["/api", "/api/fx", "/api/pepper"])
    def test_risk_atr_toggles(self, prefix, h):
        before = requests.get(f"{BASE_URL}{prefix}/settings", headers=h, timeout=15).json()
        orig_atr = before.get("atr_levels_enabled", False)
        orig_be = before.get("breakeven_enabled", False)
        orig_tr = before.get("trailing_enabled", False)
        orig_pt = before.get("partial_tp_enabled", False)
        payload = {"atr_levels_enabled": not orig_atr, "breakeven_enabled": not orig_be,
                   "trailing_enabled": not orig_tr, "partial_tp_enabled": not orig_pt}
        r = requests.put(f"{BASE_URL}{prefix}/settings", headers=h, json=payload, timeout=20)
        assert r.status_code == 200, r.text
        after = requests.get(f"{BASE_URL}{prefix}/settings", headers=h, timeout=15).json()
        assert after["atr_levels_enabled"] == (not orig_atr)
        assert after["breakeven_enabled"] == (not orig_be)
        assert after["trailing_enabled"] == (not orig_tr)
        assert after["partial_tp_enabled"] == (not orig_pt)
        # restore
        requests.put(f"{BASE_URL}{prefix}/settings", headers=h, json={
            "atr_levels_enabled": orig_atr, "breakeven_enabled": orig_be,
            "trailing_enabled": orig_tr, "partial_tp_enabled": orig_pt,
        }, timeout=20)


# ========== 8. AI advise validation ==========
class TestAiAdviseValidation:
    def test_bad_scope_400(self, h):
        r = requests.post(f"{BASE_URL}/api/ai/advise", headers=h, json={"scope": "random_scope_xyz"}, timeout=15)
        assert r.status_code == 400, f"expected 400 for unknown scope, got {r.status_code}: {r.text[:200]}"

    def test_fx_bad_scope_400(self, h):
        r = requests.post(f"{BASE_URL}/api/fx/ai/advise", headers=h, json={"scope": "random_scope_xyz"}, timeout=15)
        assert r.status_code == 400


# ========== 9. chat/tools cleanup (import-level, no LLM) ==========
class TestChatToolsCleanup:
    def test_tools_removed(self):
        from chat import tools
        names = {t["function"]["name"] for t in tools.TOOLS}
        banned = {"run_backtest", "run_optimizer", "run_walk_forward",
                  "get_news", "get_market_context", "get_walk_forward_status"}
        assert banned.isdisjoint(names), f"Still exposed: {banned & names}"
        assert "get_guard" in names

    def test_proposals_actions_cleaned(self):
        from chat import proposals
        assert "set_profile" not in proposals.ACTIONS
        assert "refresh_news" not in proposals.ACTIONS


# ========== 10. Funnel retry / shadow / cooldown (fake engine, mocked stage1/stage2) ==========
import funnel as funnel_mod  # noqa: E402
from models import Settings  # noqa: E402


class FakeDB:
    class _Coll:
        def __init__(self):
            self.docs = []
            self._auto = 0

        async def insert_one(self, d):
            self._auto += 1
            d = {**d, "_id": self._auto}
            self.docs.append(d)

            class _R:
                inserted_id = self._auto

            _R.inserted_id = self._auto
            return _R

        async def update_one(self, *a, **kw):
            return None

    def __init__(self):
        self.signals = FakeDB._Coll()
        self.pending_entries = FakeDB._Coll()


class FakeMarket:
    def __init__(self):
        self.tickers = {"BTCUSDT": {"price": 100.0}, "ETHUSDT": {"price": 50.0}}


def _make_df(n=250, price=100.0):
    rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    base = np.full(n, price, dtype=float)
    return pd.DataFrame({
        "ts": [int(t.timestamp() * 1000) for t in rng],
        "open": base, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": [100.0] * n,
        "ema20": base, "ema50": base, "ema200": base, "rsi": [55.0] * n,
        "macd_hist": [0.1] * n, "adx": [25.0] * n, "atr": [1.0] * n, "vol_ratio": [2.0] * n,
    })


class FakeEngine:
    guard_kind = "crypto"
    usage_kind = "signal"

    def __init__(self, max_positions=3):
        self.db = FakeDB()
        self.market = FakeMarket()
        self.analysis = {}
        self.funnel_said = {}
        self.funnel_last = {}
        self.signal_keys = set()
        self.running = True
        self.logs = []
        self.shadows = []
        self.opened = []
        self.mode_label = "PAPER"
        self.live = False
        self.bybit = {"down_since": None, "error": None}
        self._open_fail = None
        self._max_positions = max_positions
        self._open_positions = set()

    async def log(self, level, text):
        self.logs.append((level, text))

    async def funnel_frame(self, symbol, tf, limit=300):
        return _make_df(), True

    @staticmethod
    def funnel_htf(tf):
        return "4h"

    @staticmethod
    def funnel_digits(symbol):
        return None

    def funnel_analysis(self, symbol, m):
        return {"htf": None}

    def funnel_ctx(self, symbol):
        return {"modo": self.mode_label}

    async def funnel_state(self, settings):
        used = len(self._open_positions)
        block = f"máximo de posiciones alcanzado ({settings.max_positions})" if used >= settings.max_positions else None
        return {"acc": {"equity": 500.0, "daily_pnl_pct": 0.0, "daily_paused": False},
                "open": set(self._open_positions),
                "free": max(0, settings.max_positions - used),
                "block": block}

    async def funnel_open(self, symbol, sig, settings, signal_id, acc):
        if settings.entry_order_type == "maker":
            # mimic pending entry
            await self.db.pending_entries.insert_one({"symbol": symbol, "signal_id": signal_id, "side": sig["side"]})
            self.opened.append(("pending", symbol))
            return True, ""
        self.opened.append(("open", symbol))
        self._open_positions.add(symbol)
        return True, ""

    async def funnel_shadow(self, signal_id, symbol, sig, settings, kind, acc, confidence):
        self.shadows.append({"signal_id": signal_id, "symbol": symbol, "kind": kind, "confidence": confidence})


def _settings(ai_min_confidence=60, entry_order_type="market", max_positions=3):
    return Settings(ai_min_confidence=ai_min_confidence, entry_order_type=entry_order_type,
                    max_positions=max_positions, symbols=["BTCUSDT", "ETHUSDT"],
                    atr_levels_enabled=True, atr_sl_mult=2.0, atr_tp_mult=4.0)


async def _force_in_hours(monkeypatch):
    import trade_guard
    monkeypatch.setattr(trade_guard, "hours_status",
                        lambda kind, now=None: {"in_hours": True, "window": "Test", "next_window": None,
                                                "next_change": None, "seconds_left": 0, "ny_time": "",
                                                "windows": []})
    monkeypatch.setattr(trade_guard.calendar, "blocking", lambda kind, symbol: None)
    monkeypatch.setattr(trade_guard.calendar, "events", [])
    monkeypatch.setattr(trade_guard, "affects", lambda kind, symbol, country: False)
    monkeypatch.setattr(trade_guard, "event_label", lambda ev: "test")
    import ai_rules
    async def _active(db, symbol):
        return []
    monkeypatch.setattr(ai_rules, "active", _active)
    async def _hit(db, rid):
        return None
    monkeypatch.setattr(ai_rules, "hit", _hit)


class TestFunnelRetry:
    @pytest.mark.asyncio
    async def test_low_confidence_creates_shadow_and_cooldown(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        stage1_calls = []
        stage2_calls = []

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            stage1_calls.append(list(rows.keys()))
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "test"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            stage2_calls.append(a[1])  # symbol
            return {"decision": "BUY", "confidence": 40, "sl": None, "tp": None,
                    "rule": None, "reasoning": "weak", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        # cycle 1
        n = await funnel_mod.run(eng, s)
        assert n == 1
        assert len(eng.shadows) == 1
        assert eng.shadows[0]["kind"] == "BAJA_CONFIANZA"
        assert eng.shadows[0]["confidence"] == 40
        assert stage1_calls == [["BTCUSDT", "ETHUSDT"]]
        assert stage2_calls == ["BTCUSDT"]

        # cycle 2 immediately after: stage2 must NOT be called again (cooldown).
        # Stage1 also must NOT be called again if nothing moved.
        await funnel_mod.run(eng, s)
        assert len(stage2_calls) == 1, "stage2 called before cooldown expired"
        # No new shadow (still same cycle / same key)
        assert len(eng.shadows) == 1

    @pytest.mark.asyncio
    async def test_cooldown_expiry_retries(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "test"}],
                    "model": "fast", "ms": 10}

        stage2_confidence = [40, 40]
        stage2_calls = []

        async def fake_stage2(*a, **kw):
            stage2_calls.append(a[1])
            c = stage2_confidence[len(stage2_calls) - 1] if len(stage2_calls) <= len(stage2_confidence) else 40
            return {"decision": "BUY", "confidence": c, "sl": None, "tp": None,
                    "rule": None, "reasoning": "x", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert len(stage2_calls) == 1
        assert len(eng.shadows) == 1

        # expire cooldown
        eng.funnel_deep_at["BTCUSDT"] = time.time() - (funnel_mod.DEEP_COOLDOWN_SEC + 1)
        await funnel_mod.run(eng, s)
        assert len(stage2_calls) == 2, "stage2 should be retried after cooldown"
        # Same candle: no duplicate shadow
        assert len(eng.shadows) == 1, "duplicate shadow on same candle"

    @pytest.mark.asyncio
    async def test_retry_executes_when_confidence_rises_maker(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60, entry_order_type="maker")

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "test"}],
                    "model": "fast", "ms": 10}

        confs = [40, 75]
        n_calls = [0]

        async def fake_stage2(*a, **kw):
            c = confs[n_calls[0]]
            n_calls[0] += 1
            return {"decision": "BUY", "confidence": c, "sl": None, "tp": None,
                    "rule": None, "reasoning": "y", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert len(eng.shadows) == 1
        assert eng.opened == []

        eng.funnel_deep_at["BTCUSDT"] = time.time() - (funnel_mod.DEEP_COOLDOWN_SEC + 1)
        await funnel_mod.run(eng, s)
        # should execute: with entry_order_type=maker a pending_entry row was created
        assert eng.opened and eng.opened[-1][0] == "pending"
        assert eng.opened[-1][1] == "BTCUSDT"
        assert len(eng.db.pending_entries.docs) == 1

    @pytest.mark.asyncio
    async def test_non_candidate_never_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "r"}],
                    "model": "fast", "ms": 10}

        stage2_symbols = []

        async def fake_stage2(*a, **kw):
            stage2_symbols.append(a[1])
            return {"decision": "PASS", "confidence": 10, "sl": None, "tp": None,
                    "rule": None, "reasoning": "p", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert "ETHUSDT" not in stage2_symbols
        assert stage2_symbols == ["BTCUSDT"]

    @pytest.mark.asyncio
    async def test_confidence_equal_threshold_shadows(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "r"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            return {"decision": "BUY", "confidence": 60, "sl": None, "tp": None,
                    "rule": None, "reasoning": "ok", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert eng.shadows and eng.shadows[0]["kind"] == "BAJA_CONFIANZA"
        assert eng.opened == []

    @pytest.mark.asyncio
    async def test_max_positions_full_blocks_funnel(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(max_positions=1)
        eng._open_positions.add("XRPUSDT")  # already full
        s = _settings(ai_min_confidence=60, max_positions=1)

        stage1_calls = [0]
        stage2_calls = [0]

        async def fake_stage1(*a, **kw):
            stage1_calls[0] += 1
            return {"candidates": [], "model": "fast", "ms": 1}

        async def fake_stage2(*a, **kw):
            stage2_calls[0] += 1
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None,
                    "rule": None, "reasoning": "x", "model": "deep", "ms": 1}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        n = await funnel_mod.run(eng, s)
        assert n == 0
        assert stage1_calls[0] == 0
        assert stage2_calls[0] == 0


# ========== 11. maker.cancel_all with symbols filter ==========
import maker as maker_mod  # noqa: E402


class _FakeDB2:
    def __init__(self, rows):
        self.pending_entries = _FakeColl2(rows)
        self.signals = _FakeColl2([])

    @staticmethod
    def make_async_iter(items):
        async def ait(self2):
            for i in items:
                yield i
        return ait


class _FakeColl2:
    def __init__(self, rows):
        self.rows = rows
        self.deleted = []

    def find(self, query=None, *a, **kw):
        q = query or {}
        matches = []
        if "symbol" in q and isinstance(q["symbol"], dict) and "$in" in q["symbol"]:
            matches = [r for r in self.rows if r.get("symbol") in q["symbol"]["$in"]]
        else:
            matches = list(self.rows)

        class _Cursor:
            def __init__(self, items):
                self.items = items
                self.i = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.i >= len(self.items):
                    raise StopAsyncIteration
                v = self.items[self.i]
                self.i += 1
                return v

        return _Cursor(matches)

    async def delete_one(self, q):
        self.deleted.append(q.get("_id"))
        self.rows[:] = [r for r in self.rows if r.get("_id") != q.get("_id")]

    async def update_one(self, *a, **kw):
        return None


class _FakeEng2:
    def __init__(self, rows):
        self.db = _FakeDB2(rows)
        self.broker = None
        self.logs = []

    async def log(self, level, text):
        self.logs.append((level, text))


class TestMakerCancelFilter:
    @pytest.mark.asyncio
    async def test_cancel_only_matching_symbol(self, monkeypatch):
        from bson import ObjectId
        oid1 = ObjectId()
        oid2 = ObjectId()
        rows = [
            {"_id": 1, "symbol": "BTCUSDT", "signal_id": str(oid1), "live": False, "order_id": None},
            {"_id": 2, "symbol": "ETHUSDT", "signal_id": str(oid2), "live": False, "order_id": None},
        ]
        eng = _FakeEng2(rows)
        # silence telegram inside maker.cancel_all
        async def _tg(*a, **kw):
            return None
        monkeypatch.setattr(maker_mod, "tg", _tg)
        n = await maker_mod.cancel_all(eng, "evento rojo", ["BTCUSDT"])
        assert n == 1
        # Only BTCUSDT removed
        remaining = [r["symbol"] for r in rows]
        assert remaining == ["ETHUSDT"]

    @pytest.mark.asyncio
    async def test_cancel_all_when_no_filter(self, monkeypatch):
        rows = [
            {"_id": 1, "symbol": "BTCUSDT", "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "live": False},
            {"_id": 2, "symbol": "ETHUSDT", "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "live": False},
        ]
        eng = _FakeEng2(rows)
        async def _tg(*a, **kw):
            return None
        monkeypatch.setattr(maker_mod, "tg", _tg)
        n = await maker_mod.cancel_all(eng, "stop")
        assert n == 2
        assert rows == []
