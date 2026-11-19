"""Iter102 — the AI-funnel decisions panel must now show PASS / CONTRA_4H / BAJA_CONFIANZA verdicts:
 - stage2 PASS inserts a Signal(ai_decision='PASS', sl=0, tp=0, ai_confidence) and logs 'PASS del modelo potente · confianza N%'
 - 5-min cooldown lets a second PASS insert another Signal (every re-analysis is tracked)
 - stage2 BUY/SELL under threshold: 1st → BAJA_CONFIANZA + shadow; retry after cooldown on the same candle → new BAJA_CONFIANZA Signal
   with note 'Reanálisis: la operación sombra de esta vela ya está abierta' and NO 2nd shadow
 - stage2 side against m['bias'] (4H bias) → Signal(ai_decision='CONTRA_4H'), nothing opened
 - stage2 BUY confidence > threshold still records APROBADA and executes (regression)
 - All the above work on the FX engine (guard_kind='fx') too
 - API: inserting a PASS Signal into the per-bot collection makes it show up in the dashboard JSON (cleans up after itself)
"""
import os
import sys
import time
import pytest
import requests
import pandas as pd
import numpy as np
from bson import ObjectId

sys.path.insert(0, "/app/backend")


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


# ================ shared fake engine (mirrors iter101 but exposes mtf attrs) ================
import funnel as funnel_mod  # noqa: E402
from models import Settings, Signal  # noqa: E402


class _Coll:
    def __init__(self):
        self.docs = []
        self._auto = 0

    async def insert_one(self, d):
        self._auto += 1
        d = {**d, "_id": self._auto}
        self.docs.append(d)

        class _R:
            pass
        _R.inserted_id = self._auto
        return _R

    async def update_one(self, q, u):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(u.get("$set", {}))
        return None


class FakeDB:
    def __init__(self):
        self.signals = _Coll()
        self.pending_entries = _Coll()


class FakeMarket:
    def __init__(self):
        self.tickers = {"BTCUSDT": {"price": 100.0}, "ETHUSDT": {"price": 50.0},
                        "EURUSD": {"price": 1.1}, "XAUUSD": {"price": 2000.0}}


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
    usage_kind = "signal"

    def __init__(self, max_positions=3, kind="crypto", mtf=False):
        self.guard_kind = kind
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
        self._max_positions = max_positions
        self._open_positions = set()
        if mtf:
            self.funnel_mtf = ("4h", "1h")

    async def log(self, level, text):
        self.logs.append((level, text))

    async def funnel_frame(self, symbol, tf, limit=300):
        p = self.market.tickers.get(symbol, {}).get("price", 100.0)
        return _make_df(price=p), True

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
        self.opened.append(("open", symbol, signal_id))
        self._open_positions.add(symbol)
        return True, ""

    async def funnel_shadow(self, signal_id, symbol, sig, settings, kind, acc, confidence):
        self.shadows.append({"signal_id": signal_id, "symbol": symbol, "kind": kind, "confidence": confidence})


def _settings(ai_min_confidence=60, max_positions=3):
    return Settings(ai_min_confidence=ai_min_confidence, entry_order_type="market",
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

    async def _hit(db, rid):
        return None
    monkeypatch.setattr(ai_rules, "active", _active)
    monkeypatch.setattr(ai_rules, "hit", _hit)


# ====================== 1. PASS records a Signal ======================
class TestPassRecorded:
    @pytest.mark.asyncio
    async def test_pass_creates_signal_and_log(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "retroceso a EMA20"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            return {"decision": "PASS", "confidence": 35, "sl": None, "tp": None,
                    "rule": None, "reasoning": "sin momentum", "model": "deep", "ms": 42}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        n = await funnel_mod.run(eng, s)
        assert n == 0  # PASS does not count
        # Exactly one Signal document stored
        assert len(eng.db.signals.docs) == 1
        d = eng.db.signals.docs[0]
        assert d["ai_decision"] == "PASS"
        assert d["ai_confidence"] == 35
        assert d["ai_min_confidence"] == 60
        assert d["ai_stage"] == 2
        assert d["sl"] == 0 and d["tp"] == 0
        assert d["side"] == "long"
        assert d["executed"] is False
        assert d["ai_reasoning"] == "sin momentum"
        # No position, no shadow
        assert eng.opened == []
        assert eng.shadows == []
        # Log contains confidence
        eval_logs = [t for lv, t in eng.logs if lv == "AI_EVAL"]
        assert any("PASS del modelo potente" in t and "confianza 35%" in t for t in eval_logs), eval_logs


# ====================== 2. Cooldown + second PASS ======================
class TestPassCooldown:
    @pytest.mark.asyncio
    async def test_second_pass_after_cooldown(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "r"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            return {"decision": "PASS", "confidence": 30, "sl": None, "tp": None,
                    "rule": None, "reasoning": "x", "model": "deep", "ms": 5}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert len(eng.db.signals.docs) == 1
        # Immediate re-run: no new Signal (cooldown)
        await funnel_mod.run(eng, s)
        assert len(eng.db.signals.docs) == 1
        # Expire cooldown
        eng.funnel_deep_at["BTCUSDT"] = time.time() - (funnel_mod.DEEP_COOLDOWN_SEC + 1)
        await funnel_mod.run(eng, s)
        assert len(eng.db.signals.docs) == 2
        assert all(d["ai_decision"] == "PASS" for d in eng.db.signals.docs)


# ====================== 3. BAJA_CONFIANZA retry ======================
class TestLowConfidenceRetry:
    @pytest.mark.asyncio
    async def test_retry_records_signal_without_second_shadow(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "r"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            return {"decision": "BUY", "confidence": 45, "sl": None, "tp": None,
                    "rule": None, "reasoning": "flojo", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        # 1st: real Signal (approved=False, so BAJA_CONFIANZA path with shadow)
        assert len(eng.db.signals.docs) == 1
        first = eng.db.signals.docs[0]
        assert first["ai_decision"] == "BAJA_CONFIANZA"
        assert first["sl"] != 0 and first["tp"] != 0  # has real SL/TP
        assert len(eng.shadows) == 1

        # Expire cooldown and re-run on the same candle
        eng.funnel_deep_at["BTCUSDT"] = time.time() - (funnel_mod.DEEP_COOLDOWN_SEC + 1)
        await funnel_mod.run(eng, s)
        assert len(eng.db.signals.docs) == 2
        second = eng.db.signals.docs[1]
        assert second["ai_decision"] == "BAJA_CONFIANZA"
        assert second["sl"] == 0 and second["tp"] == 0
        assert "Reanálisis" in second["note"]
        assert len(eng.shadows) == 1, "no second shadow should be opened"


# ====================== 4. CONTRA_4H ======================
class TestContra4H:
    @pytest.mark.asyncio
    async def test_against_4h_bias_records_contra_4h(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True)
        s = _settings(ai_min_confidence=60)

        async def fake_mtf(engine, symbol, price):
            # 4H bullish, 1H bullish → bias = long. stage2 returns SELL → CONTRA_4H.
            return {
                "4h": {"tf": "4h", "trend": "ALCISTA", "rsi": 60, "adx": 25, "dist_ema50_atr": 0.5,
                       "demand": None, "supply": None},
                "1h": {"tf": "1h", "trend": "ALCISTA", "rsi": 55, "adx": 20, "dist_ema50_atr": 0.3},
            }
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            # stage1 is called AFTER bias assignment; it must return long (matching bias) else it's filtered pre-stage-2
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "r"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            # strong model decides the opposite direction
            return {"decision": "SELL", "confidence": 62, "sl": None, "tp": None,
                    "rule": None, "reasoning": "contra tendencia", "model": "deep", "ms": 11}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert len(eng.db.signals.docs) == 1
        d = eng.db.signals.docs[0]
        assert d["ai_decision"] == "CONTRA_4H"
        assert d["ai_confidence"] == 62
        assert d["sl"] == 0 and d["tp"] == 0
        assert d["side"] == "short"
        assert eng.opened == []
        assert eng.shadows == []

    @pytest.mark.asyncio
    async def test_counter_trend_with_ai_target_opens(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True)
        s = _settings(ai_min_confidence=60)

        async def fake_mtf(engine, symbol, price):
            return {"4h": {"tf": "4h", "trend": "ALCISTA", "rsi": 60, "adx": 25, "dist_ema50_atr": 0.5, "demand": None, "supply": None},
                    "1h": {"tf": "1h", "trend": "ALCISTA", "rsi": 55, "adx": 20, "dist_ema50_atr": 0.3}}
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)
        seen = {}

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            seen["price"], seen["atr"] = rows["BTCUSDT"]["price"], rows["BTCUSDT"]["atr"]
            return {"candidates": [{"symbol": "BTCUSDT", "side": "short", "reason": "rebote"}], "model": "fast", "ms": 10}

        async def fake_stage2(kind, symbol, tf, cand, m, *a, **kw):
            seen["counter"] = cand.get("counter_trend")
            return {"decision": "SELL", "confidence": 75, "sl": None, "tp": m["price"] - 3.2 * m["atr"],
                    "rule": None, "reasoning": "rebote", "model": "deep", "ms": 11}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert seen["counter"] is True
        d = eng.db.signals.docs[0]
        assert d["ai_decision"] == "APROBADA" and d["context"]["funnel"]["counter_trend"] is True
        assert abs(d["sl"] - (seen["price"] + 2 * seen["atr"])) < 1e-6
        assert abs(d["tp"] - (seen["price"] - 3.2 * seen["atr"])) < 1e-6
        assert len(eng.opened) == 1


# ====================== 5. APROBADA regression ======================
class TestApprovedExecutes:
    @pytest.mark.asyncio
    async def test_high_confidence_approves_and_opens(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine()
        s = _settings(ai_min_confidence=60)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "r"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            return {"decision": "BUY", "confidence": 78, "sl": None, "tp": None,
                    "rule": None, "reasoning": "clara", "model": "deep", "ms": 10}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        n = await funnel_mod.run(eng, s)
        assert n == 1
        assert len(eng.db.signals.docs) == 1
        d = eng.db.signals.docs[0]
        assert d["ai_decision"] == "APROBADA"
        assert d["ai_confidence"] == 78
        assert d["sl"] > 0 and d["tp"] > 0
        assert eng.opened and eng.opened[-1][1] == "BTCUSDT"


# ====================== 6. FX engine kind ======================
class TestFxFunnel:
    @pytest.mark.asyncio
    async def test_fx_pass_records_signal(self, monkeypatch):
        """guard_kind='fx' routes through the same funnel; PASS must still be recorded."""
        await _force_in_hours(monkeypatch)
        # FX-style engine with no mtf for simplicity (no bias check)
        eng = FakeEngine(kind="fx")
        # use crypto Settings but override symbols to crypto so the Settings validator accepts (the funnel reads settings.symbols,
        # but stage1 is mocked anyway and symbols are only used for the eligible loop). Use BTCUSDT/ETHUSDT in both engines.
        s = _settings(ai_min_confidence=50)

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            assert kind == "fx"  # confirm the engine kind is propagated
            return {"candidates": [{"symbol": "BTCUSDT", "side": "short", "reason": "ruptura"}],
                    "model": "fast", "ms": 10}

        async def fake_stage2(*a, **kw):
            return {"decision": "PASS", "confidence": 40, "sl": None, "tp": None,
                    "rule": None, "reasoning": "sin volumen", "model": "deep", "ms": 7}

        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert len(eng.db.signals.docs) == 1
        d = eng.db.signals.docs[0]
        assert d["ai_decision"] == "PASS"
        assert d["ai_confidence"] == 40
        assert d["ai_min_confidence"] == 50
        assert d["side"] == "short"
        assert d["sl"] == 0 and d["tp"] == 0


# ====================== 7. API: dashboards expose seeded decisions ======================
def _pass_signal_doc(symbol="BTCUSDT"):
    return Signal(
        symbol=symbol, side="long", strategy="ai_funnel", timeframe="15m", price=100.0, sl=0, tp=0, score=2,
        reasons=["TEST_iter102"], indicators={"close": 100.0, "ema20": 100.0, "ema50": 100.0, "ema200": 100.0,
                                              "rsi": 55.0, "macd_hist": 0.1, "adx": 25.0, "atr": 1.0, "activity": 2.0},
        ai_decision="PASS", ai_confidence=35, ai_min_confidence=60, ai_stage=2, ai_model="deep", ai_ms=42,
        ai_reasoning="TEST_iter102 pass seeded", note="TEST_iter102", key=f"TEST_iter102:{int(time.time())}",
        context={"funnel": {"verdict": "PASS"}},
    ).to_mongo()


@pytest.fixture
def mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    url, name = os.environ.get("MONGO_URL"), os.environ.get("DB_NAME")
    if not (url and name):
        pytest.skip("MONGO_URL / DB_NAME missing")
    client = AsyncIOMotorClient(url)
    import asyncio
    try:
        client.get_io_loop = asyncio.get_running_loop
    except Exception:
        pass
    return client[name]


class TestDashboardsShowSeededDecisions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("collection,prefix", [
        ("signals", "/api"),
        ("fx_signals", "/api/fx"),
        ("pep_signals", "/api/pepper"),
    ])
    async def test_seeded_pass_appears_in_dashboard(self, collection, prefix, mongo_db, h):
        doc = _pass_signal_doc()
        inserted = await mongo_db[collection].insert_one(doc)
        oid = inserted.inserted_id
        try:
            r = requests.get(f"{BASE_URL}{prefix}/dashboard", headers=h, timeout=25)
            assert r.status_code == 200, r.text
            sigs = r.json().get("signals", [])
            match = [s for s in sigs if s.get("note") == "TEST_iter102" or str(s.get("id")) == str(oid)]
            assert match, f"seeded PASS Signal not found in {prefix}/dashboard (got {len(sigs)} signals)"
            d = match[0]
            assert d["ai_decision"] == "PASS"
            assert d["ai_confidence"] == 35
            assert d["sl"] == 0 and d["tp"] == 0
            # performance still 200 while the seeded doc is there
            r2 = requests.get(f"{BASE_URL}{prefix}/performance", headers=h, timeout=25)
            assert r2.status_code == 200
        finally:
            await mongo_db[collection].delete_one({"_id": ObjectId(oid) if not isinstance(oid, ObjectId) else oid})
