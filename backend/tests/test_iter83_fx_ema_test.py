# Iter83: «Test cruce EMA 10/20» ported to the FX bots (HotForex / Pepperstone): settings, scan → direct open (no AI), paper safety SL,
# close on the opposite cross + reversal, no slot usage, backtest and API. Engine tests run on an ISOLATED Mongo db (dropped at end). Run with -n 0.
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

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
import strategies as st  # noqa: E402
from fx.engine import FxEngine  # noqa: E402
from fx import engine_signals  # noqa: E402
from fx.models import FxSettings  # noqa: E402
from fx.backtest import run_backtest  # noqa: E402
from fx.risk import trade_rules  # noqa: E402
from fx.broker import HFM, PEPPERSTONE as PEPPER  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
TEST_DB = f"test_iter83_fx_ema_{uuid.uuid4().hex[:8]}"
PW = "BlackCat2233@"


def _rows(direction: str, n: int = 300, base: float = 1.1000):
    rows, t0, p = [], 1_700_000_000_000, base
    step = -0.0001 if direction == "up" else 0.0001
    for k in range(n):
        if k == n - 2:
            p = p * (1.03 if direction == "up" else 0.97)
        elif k < n - 2:
            p = p + step
        rows.append([t0 + k * 300_000, p, p * 1.0005, p * 0.9995, p, 0.0])
    return rows


def _flat(n=300, base=1.1):
    return [[1_700_000_000_000 + k * 300_000, base, base * 1.0003, base * 0.9997, base + (k % 2) * 0.00001, 0.0] for k in range(n)]


class FakeMarket:
    def __init__(self, data):
        self.data, self.calls = data, []
        self.tickers = {s: {"price": float(r[-1][4]), "change_pct": 0} for s, r in data.items()}

    async def fetch_klines(self, symbol, tf, limit=300, cache_sec=8):
        self.calls.append(symbol)
        return self.data.get(symbol, _flat())

    async def fetch_tickers(self, symbols, cache_sec=5):
        return self.tickers


async def _noop(*a, **k):
    return None


async def _aboom(*a, **k):
    raise AssertionError("AI must not be called for ema_test")


def run(coro_fn, monkeypatch, data, profile=HFM, daily_paused=False):
    monkeypatch.setattr(engine_signals, "market_open", lambda *a, **k: True)
    monkeypatch.setattr(engine_signals, "closing_soon", lambda *a, **k: False)
    monkeypatch.setattr(engine_signals, "evaluate_signal", _aboom)
    monkeypatch.setattr(engine_signals.ai_funnel, "quick_filter", _aboom, raising=False)

    async def main():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[TEST_DB]
        e = FxEngine(db, profile, FakeMarket(data))
        for c in ("positions", "trades", "signals", "account", "pending_orders", "logs", "shadow_positions"):
            await e.db[c].delete_many({})
        e.running, e.logs, e.news = True, [], None

        async def log(level, msg):
            e.logs.append((level, msg))
        e.log, e.tg = log, _noop
        e.normal_signals = []

        async def hs(symbol, sig, *a, **k):
            e.normal_signals.append((symbol, sig["strategy"]))
        e._handle_signal = hs
        await e._new_account(50000.0)
        if daily_paused:
            await e.db.account.update_one({"_id": "main"}, {"$set": {"daily_paused": True}})
        try:
            return await coro_fn(e, e.db)
        finally:
            client.close()
    return asyncio.run(main())


def _settings(**kw):
    base = dict(symbols=["USDJPY"], ema_test_enabled=True, ema_test_symbols=["EURUSD"], htf_filter=False, timeframe="5m", max_positions=1, sessions_allowed=["tokyo"])
    base.update(kw)
    return FxSettings(**base)


def teardown_module(module):
    async def drop():
        c = AsyncIOMotorClient(MONGO_URL)
        await c.drop_database(TEST_DB)
        c.close()
    asyncio.run(drop())


# ---------- settings model ----------
class TestSettings:
    def test_defaults_and_validation(self):
        s = FxSettings()
        assert s.ema_test_enabled is False and s.ema_test_symbols == ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"] and s.ema_test_adx_filter is False and s.ema_test_adx_min == 20.0
        s = FxSettings(ema_test_symbols=["XAUUSD", "NOPE", "XAUUSD", "EURUSD"])
        assert s.ema_test_symbols == ["XAUUSD", "EURUSD"]
        assert "ema_test" not in FxSettings.model_fields["strategy"].annotation.__name__  # strategy keys untouched


# ---------- engine: open on cross (immune to AI, sessions and slots) ----------
class TestOpenOnCross:
    @pytest.mark.parametrize("profile", [HFM])  # Pepperstone is EMA-only since iter101: sessions and «Máx. posiciones» apply there (tests/test_iter101)
    def test_long_opens_without_ai(self, monkeypatch, profile):
        async def body(e, db):
            await e.scan_once(_settings())
            return await db.positions.find_one({"symbol": "EURUSD"}), await db.signals.find_one({"symbol": "EURUSD"}), e.normal_signals
        pos, sig, normal = run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)}, profile)
        assert pos and pos["side"] == "long" and pos["strategy"] == "ema_test" and pos["exit_mode"] == "cross" and pos["qty"] > 0
        assert pos["sl"] < pos["entry_price"] and abs(pos["entry_price"] - pos["sl"]) == pytest.approx(3 * pos["atr"], rel=1e-3)
        assert sig["ai_decision"] == "SIN_IA" and sig["executed"] is True and sig["strategy"] == "ema_test"
        assert not [n for n in normal if n[0] == "EURUSD"]  # EURUSD is a test-only pair → no regular strategies evaluated

    def test_short_on_down_cross(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings())
            return await db.positions.find_one({"symbol": "EURUSD"})
        pos = run(body, monkeypatch, {"EURUSD": _rows("down"), "USDJPY": _flat(base=150.0)})
        assert pos and pos["side"] == "short" and pos["sl"] > pos["entry_price"]

    def test_ignores_max_positions_slot(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one({"symbol": "GBPUSD", "side": "long", "strategy": "trend_ema", "horizon": "short", "unrealized_pnl": 0.0, "margin": 1.0,
                                           "entry_price": 1.3, "qty": 0.01, "sl": 1.29, "tp": 1.32, "initial_sl": 1.29, "atr": 0.001, "fees": 0.0, "mark_price": 1.3,
                                           "leverage": 100, "opened_at": "2026-01-01T00:00:00+00:00", "live": False, "mfe_r": 0.0, "mae_r": 0.0})
            await e.scan_once(_settings(max_positions=1))
            return await db.positions.count_documents({"symbol": "EURUSD"})
        assert run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)}) == 1

    def test_toggle_off_no_entry(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings(ema_test_enabled=False))
            return await db.positions.count_documents({}), e.market.calls
        n, calls = run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)})
        assert n == 0 and "EURUSD" not in calls

    def test_daily_pause_blocks(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings())
            return await db.positions.count_documents({}), await db.signals.find_one({"symbol": "EURUSD"})
        n, sig = run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)}, daily_paused=True)
        assert n == 0 and sig["ai_decision"] == "BLOQUEADA" and "pérdida diaria" in sig["note"]

    def test_normal_strategies_still_pass_through_ai_path(self, monkeypatch):
        """A regular pair with a cross still goes through _handle_signal (AI path), never the test shortcut."""
        async def body(e, db):
            await e.scan_once(_settings(ema_test_symbols=["GBPUSD"], symbols=["EURUSD"], strategy="ema_cross", disabled_strategies=[]))
            return await db.positions.count_documents({"symbol": "EURUSD"}), e.normal_signals
        n, normal = run(body, monkeypatch, {"EURUSD": _rows("up"), "GBPUSD": _flat(base=1.3)})
        assert n == 0 and normal == [("EURUSD", "ema_cross")]


# ---------- engine: close on opposite cross + reversal; safety SL in paper ----------
class TestExitAndReverse:
    def test_opposite_cross_closes_and_reverses(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings())
            first = await db.positions.find_one({"symbol": "EURUSD"})
            e.market.data["EURUSD"] = _rows("down")
            e.market.tickers["EURUSD"]["price"] = float(e.market.data["EURUSD"][-1][4])
            await e.scan_once(_settings())
            return first, await db.positions.find_one({"symbol": "EURUSD"}), await db.trades.find_one({"symbol": "EURUSD"})
        first, second, trade = run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)})
        assert first["side"] == "long" and second and second["side"] == "short" and second["_id"] != first["_id"]
        assert trade["reason"] == "CRUCE" and trade["exit_mode"] == "cross" and st.EMA_TEST_TAG in trade["tags"]

    def test_toggle_off_still_closes_open_test_position(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings())
            e.market.data["EURUSD"] = _rows("down")
            await e.scan_once(_settings(ema_test_enabled=False))
            return await db.positions.count_documents({"symbol": "EURUSD"}), await db.trades.count_documents({"symbol": "EURUSD"})
        assert run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)}) == (0, 1)

    def test_paper_safety_sl(self, monkeypatch):
        from models import Position
        from datetime import datetime, timezone

        async def body(e, db):
            await e.scan_once(_settings())
            doc = await db.positions.find_one({"symbol": "EURUSD"})
            p = Position.from_mongo(doc)
            rules = trade_rules(_settings())
            now = datetime.now(timezone.utc)
            await e._manage_paper_position(p, p.sl + 0.0002, rules, now)          # above SL → still open, no trailing/partial
            still = await db.positions.find_one({"_id": doc["_id"]})
            await e._manage_paper_position(p, p.sl - 0.0001, rules, now, rollover=True)  # rollover spike → SL ignored
            still2 = await db.positions.find_one({"_id": doc["_id"]})
            await e._manage_paper_position(p, p.sl - 0.0001, rules, now)          # hit → closed
            return still, still2, await db.positions.find_one({"_id": doc["_id"]}), await db.trades.find_one({"symbol": "EURUSD"})
        still, still2, gone, trade = run(body, monkeypatch, {"EURUSD": _rows("up"), "USDJPY": _flat(base=150.0)})
        assert still and still["sl"] == still["initial_sl"] and not still.get("partial_done") and still2
        assert gone is None and trade["reason"] == "SL" and st.EMA_TEST_TAG in trade["tags"]


# ---------- backtest ----------
class TestBacktest:
    def test_cross_only_exits(self):
        import math
        rows, t0 = [], 1_700_000_000_000
        for k in range(900):
            p = 1.1 + 0.01 * math.sin(k / 15.0)
            rows.append([t0 + k * 300_000, p, p * 1.0005, p * 0.9995, p, 0.0])
        r = run_backtest(rows, "ema_test", 1.0, 100, trade_rules(FxSettings()), 500.0, "EURUSD", "relaxed", [], {}, 4.0, True, ("tokyo",), None)
        assert r["total_trades"] > 5 and r["skipped_session"] == 0
        reasons = {t["reason"] for t in r["trades"]}
        assert reasons <= {"CRUCE", "CIERRE_EMA21", "SL", "FIN_SEMANA", "FIN"} and "CIERRE_EMA21" in reasons and not any(t["partial"] for t in r["trades"])  # Iter124: close beyond the EMA21

    def test_all_excludes_test(self):
        rows = _flat(600)
        r = run_backtest(rows, "all", 1.0, 100, trade_rules(FxSettings()), 500.0, "EURUSD", "relaxed", [], {}, 4.0, True)
        assert "ema_test" not in r["strategy_stats"]


# ---------- API ----------
@pytest.fixture(scope="module")
def auth():
    tok = requests.post(f"{BASE_URL}/api/auth/login", json={"password": PW}, timeout=15).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


class TestApi:
    @pytest.mark.parametrize("prefix", ["fx", "pepper"])
    def test_meta_settings_roundtrip(self, auth, prefix):
        meta = requests.get(f"{BASE_URL}/api/{prefix}/meta", headers=auth, timeout=15).json()
        assert "ema_test" in meta["strategies"] and "ema_test" not in meta["strategy_keys"]
        before = requests.get(f"{BASE_URL}/api/{prefix}/settings", headers=auth, timeout=15).json()
        keep = {k: before[k] for k in ("ema_test_enabled", "ema_test_symbols", "ema_test_adx_filter", "ema_test_adx_min")}
        try:
            r = requests.put(f"{BASE_URL}/api/{prefix}/settings", headers=auth, json={"ema_test_enabled": True, "ema_test_symbols": ["XAUUSD", "BAD", "GBPUSD"],
                                                                                        "ema_test_adx_filter": True, "ema_test_adx_min": 25}, timeout=15)
            assert r.status_code == 200
            d = r.json()
            assert d["ema_test_enabled"] is True and d["ema_test_symbols"] == ["XAUUSD", "GBPUSD"] and d["ema_test_adx_filter"] is True and d["ema_test_adx_min"] == 25
            assert requests.put(f"{BASE_URL}/api/{prefix}/settings", headers=auth, json={"ema_test_adx_min": 2}, timeout=15).status_code == 422
        finally:
            requests.put(f"{BASE_URL}/api/{prefix}/settings", headers=auth, json=keep, timeout=15)

    def test_backtest_ema_test(self, auth):
        r = requests.post(f"{BASE_URL}/api/pepper/backtest", headers=auth, json={"symbol": "EURUSD", "strategy": "ema_test", "timeframe": "15m"}, timeout=120)
        assert r.status_code == 200
        d = r.json()
        assert d["strategy"] == "ema_test" and "total_trades" in d and "total_pips" in d
        assert all(t["reason"] in ("CRUCE", "SL", "FIN_SEMANA", "FIN") for t in d["trades"])
