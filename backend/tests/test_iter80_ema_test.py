# Iter80: «Test cruce EMA 10/20» (strategy ema_test, exit_mode 'cross', stop-and-reverse).
# Unit (strategies), engine integration on an ISOLATED Mongo db (dropped at end), position_review, backtest, and API settings/meta/backtest.
# Run with -n 0. Never touches the real bot db except settings (restored) via API.
import os
import sys
import uuid
import asyncio
import types
import pytest
import requests
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv("/app/frontend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
import strategies as st  # noqa: E402
import backtest  # noqa: E402
import engine as engine_mod  # noqa: E402
import position_review  # noqa: E402
import ai_advisor  # noqa: E402
import ai_funnel  # noqa: E402
from indicators import build_df  # noqa: E402
from models import Settings, Position  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
TEST_DB = f"test_iter80_ema_{uuid.uuid4().hex[:8]}"
DEFAULTS = ["ENAUSDT", "ONDOUSDT", "ARBUSDT", "UNIUSDT"]


# ---------- synthetic klines ----------
def _rows(direction: str, n: int = 300, base: float = 10.0):
    """Trend against `direction` for n-2 bars, then a strong move at bar n-2 (the closed candle) that makes EMA10 cross EMA20."""
    rows, t0, p = [], 1_700_000_000_000, base
    step = -0.01 if direction == "up" else 0.01
    for k in range(n):
        if k == n - 2:
            p = p * (1.25 if direction == "up" else 0.75)
        elif k < n - 2:
            p = p + step
        rows.append([t0 + k * 300_000, p, p * 1.002, p * 0.998, p, 1000.0])
    return rows


def _flat(n=300):
    return [[1_700_000_000_000 + k * 300_000, 100.0, 100.2, 99.8, 100.0 + (k % 2) * 0.01, 1000.0] for k in range(n)]


def _alternating_df(n=900):
    rows, t0, p = [], 1_700_000_000_000, 10.0
    import math
    for k in range(n):
        p = 10 + 2 * math.sin(k / 15.0)
        rows.append([t0 + k * 300_000, p, p * 1.003, p * 0.997, p, 1000.0])
    return build_df(rows)


# ---------- Unit: strategies ----------
class TestStrategyUnit:
    def test_long_on_up_cross(self):
        df = build_df(_rows("up"))
        i = len(df) - 2
        s = st.ema_test(df, i)
        assert s and s["side"] == "long" and s["strategy"] == "ema_test" and s["exit_mode"] == "cross"
        atr, price = float(df.iloc[i]["atr"]), float(df.iloc[i]["close"])
        assert s["sl"] == pytest.approx(price - 2 * atr, rel=1e-3) and s["tp"] == pytest.approx(price + 6 * atr, rel=1e-3)

    def test_short_on_down_cross(self):
        df = build_df(_rows("down"))
        s = st.ema_test(df, len(df) - 2)
        assert s and s["side"] == "short"
        atr, price = float(df.iloc[-2]["atr"]), float(df.iloc[-2]["close"])
        assert s["sl"] == pytest.approx(price + 2 * atr, rel=1e-3) and s["tp"] == pytest.approx(price - 6 * atr, rel=1e-3)

    def test_none_without_cross(self):
        df = build_df(_rows("up"))
        assert st.ema_test(df, len(df) - 5) is None

    def test_adx_filter(self):
        """Sideways filter: with params ema_test_adx_min above the candle's ADX the cross is ignored; below (or 0) it passes."""
        df = build_df(_rows("up")); i = len(df) - 2
        adx = float(df.iloc[i]["adx"])
        assert st.ema_test(df, i, params={"ema_test_adx_min": adx + 5}) is None
        sig = st.ema_test(df, i, params={"ema_test_adx_min": max(adx - 5, 1)})
        assert sig and sig["side"] == "long" and "ADX" in sig["reasons"][1]
        assert st.ema_test(df, i, params={"ema_test_adx_min": 0}) is not None

    def test_registry(self):
        assert "ema_test" not in st.active_strategies("all")
        assert st.active_strategies("ema_test") == ["ema_test"]
        assert st.exit_signal("ema_test", "long", 1.0, 2.0) is True
        assert st.exit_signal("ema_test", "short", 1.0, 2.0) is False
        assert "ema_test" in st.STRATEGY_INFO and "ema_test" in st.EXIT_ON_CROSS and "ema_test" in st.CROSS_ONLY


# ---------- Engine integration (isolated real Mongo db) ----------
class FakeMarket:
    def __init__(self, data):
        self.data, self.calls = data, []
        self.tickers = {s: {"price": float(r[-1][4]), "change_pct": 0} for s, r in data.items()}

    async def fetch_klines(self, symbol, tf, limit=300, cache_sec=8):
        self.calls.append(symbol)
        return self.data.get(symbol, _flat())


class FakeBroker:
    def __init__(self):
        self.opens = []

    async def open(self, symbol, side, qty, sl, tp, leverage):
        self.opens.append({"symbol": symbol, "sl": sl, "tp": tp})
        return {"order_id": "o1", "link_id": "l1", "qty": qty, "entry": 0.0, "fee": 0.0, "filled": True}

    async def wallet(self):
        return {"wallet_balance": 500, "equity": 500, "available": 500, "unrealised": 0}


async def _noop(*a, **k):
    return None


def _boom(*a, **k):
    raise AssertionError("AI must not be called for ema_test")


async def _aboom(*a, **k):
    raise AssertionError("AI must not be called for ema_test")


def run(coro_fn, monkeypatch, data, daily_paused=False, live=False):
    """Fresh motor client + engine per test (motor binds to the loop)."""
    monkeypatch.setattr(engine_mod, "tg", _noop)
    monkeypatch.setattr(ai_funnel, "quick_filter", _aboom, raising=False)
    monkeypatch.setattr(engine_mod, "evaluate_signal", _aboom)

    async def main():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[TEST_DB]
        for c in ("positions", "trades", "signals", "account", "pending_entries", "logs"):
            await db[c].delete_many({})
        e = engine_mod.TradingEngine(db)
        e.market = FakeMarket(data)
        e.running, e.live, e.logs = True, live, []
        e.broker = FakeBroker() if live else None
        e.bybit = {"down_since": None, "error": None}
        e.news = None

        async def log(level, msg):
            e.logs.append((level, msg))
        e.log = log
        e.normal_signals = []

        async def hs(symbol, sig, *a, **k):
            e.normal_signals.append((symbol, sig["strategy"]))
        e._handle_signal = hs

        async def slow(*a, **k):
            return 0
        e._scan_slow = slow
        await e._new_account(500.0)
        if daily_paused:
            await db.account.update_one({"_id": "main"}, {"$set": {"daily_paused": True}})
        try:
            return await coro_fn(e, db)
        finally:
            client.close()
    return asyncio.run(main())


def _settings(**kw):
    base = dict(symbols=["BTCUSDT"], ema_test_enabled=True, ema_test_symbols=["UNIUSDT"], htf_filter=False, timeframe="5m")
    base.update(kw)
    return Settings(**base)


def _pos_doc(symbol="UNIUSDT", side="long", strategy="ema_test", exit_mode="cross", price=10.0):
    p = Position(symbol=symbol, side=side, strategy=strategy, entry_price=price, qty=1.0, initial_qty=1.0, leverage=3, margin=3.33,
                 sl=price * 0.9 if side == "long" else price * 1.1, tp=price * 1.3 if side == "long" else price * 0.7,
                 initial_sl=price * 0.9 if side == "long" else price * 1.1, atr=0.2, fees=0.0, mark_price=price, exit_mode=exit_mode)
    return p.to_mongo()


class TestEngineIntegration:
    def test_a_open_long_on_up_cross_no_ai(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings())
            assert "UNIUSDT" in e.market.calls and "BTCUSDT" in e.market.calls
            sig = await db.signals.find_one({"symbol": "UNIUSDT"})
            assert sig and sig["strategy"] == "ema_test" and sig["ai_decision"] == "SIN_IA" and sig["key"].startswith("UNIUSDT:ema_test:long:")
            pos = await db.positions.find_one({"symbol": "UNIUSDT"})
            assert pos and pos["exit_mode"] == "cross" and pos["side"] == "long" and pos["strategy"] == "ema_test"
            assert all(s != "UNIUSDT" for s, _ in e.normal_signals)  # test pair not in settings.symbols: no normal strategies
            # dedupe: second scan, no new signal
            await e.scan_once(_settings())
            assert await db.signals.count_documents({"symbol": "UNIUSDT"}) == 1
        run(body, monkeypatch, {"UNIUSDT": _rows("up")})

    def test_b_stop_and_reverse(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(side="long"))
            await e.scan_once(_settings())
            tr = await db.trades.find_one({"symbol": "UNIUSDT"})
            assert tr and tr["reason"] == "CRUCE" and tr["exit_mode"] == "cross" and tr["tags"] == ["Cruce EMAs Especial"]
            pos = await db.positions.find({"symbol": "UNIUSDT"}).to_list(5)
            assert len(pos) == 1 and pos[0]["side"] == "short" and pos[0]["exit_mode"] == "cross"
        run(body, monkeypatch, {"UNIUSDT": _rows("down")})

    def test_c1_toggle_off_pair_in_symbols_still_closes(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(side="long"))
            await e.scan_once(_settings(ema_test_enabled=False, symbols=["BTCUSDT", "UNIUSDT"]))
            assert await db.trades.count_documents({"symbol": "UNIUSDT", "reason": "CRUCE"}) == 1
            assert await db.positions.count_documents({"symbol": "UNIUSDT"}) == 0
            assert await db.signals.count_documents({"symbol": "UNIUSDT", "strategy": "ema_test"}) == 0
        run(body, monkeypatch, {"UNIUSDT": _rows("down")})

    def test_c2_toggle_off_pair_not_in_symbols_still_closes(self, monkeypatch):
        """Fixed: pairs with an open test position keep being scanned with the toggle off, so the opposite cross still closes them (no reversal)."""
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(side="long"))
            await e.scan_once(_settings(ema_test_enabled=False))
            return ("UNIUSDT" in e.market.calls, await db.positions.count_documents({"symbol": "UNIUSDT"}), await db.signals.count_documents({"strategy": "ema_test"}))
        fetched, still_open, new_signals = run(body, monkeypatch, {"UNIUSDT": _rows("down")})
        assert fetched is True and still_open == 0 and new_signals == 0

    def test_d_daily_paused_blocks(self, monkeypatch):
        async def body(e, db):
            await e.scan_once(_settings())
            sig = await db.signals.find_one({"symbol": "UNIUSDT"})
            assert sig["ai_decision"] == "BLOQUEADA" and "pérdida diaria" in sig["note"]
            assert await db.positions.count_documents({}) == 0
        run(body, monkeypatch, {"UNIUSDT": _rows("up")}, daily_paused=True)

    def test_e_slot_ignores_cross(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(symbol="UNIUSDT"))
            docs = await e._open_and_pending()
            return e._slot_block(docs, _settings(max_positions=1), "short"), docs
        block, docs = run(body, monkeypatch, {})
        assert block is None, f"cross position consumed a slot: {block} · docs projection={docs}"

    def test_e2_normal_open_with_cross_open(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(symbol="UNIUSDT"))
            sig = {"strategy": "trend_ema", "side": "long", "price": 100.0, "sl": 98.0, "tp": 106.0, "atr": 1.0}
            e.market.tickers["SOLUSDT"] = {"price": 100.0}
            await e.open_position("SOLUSDT", sig, _settings(max_positions=1), None)
            return await db.positions.count_documents({"symbol": "SOLUSDT"})
        assert run(body, monkeypatch, {}) == 1

    def test_f_paper_tick_no_exit(self, monkeypatch):
        async def body(e, db):
            res = await db.positions.insert_one(_pos_doc(side="long", price=10.0))
            d = await db.positions.find_one({"_id": res.inserted_id})
            p = Position.from_mongo(d)

            async def nope(*a, **k):
                raise AssertionError("cross position closed by SL/TP")
            e.close_position = nope
            from risk import DEFAULT_RULES
            await e._manage_paper_position(p, 5.0, dict(DEFAULT_RULES))  # far below virtual SL 9.0
            d2 = await db.positions.find_one({"_id": res.inserted_id})
            assert d2["mark_price"] == 5.0 and d2["unrealized_pnl"] == pytest.approx(-5.0) and d2["sl"] == d["sl"]
            await e._manage_paper_position(p, 20.0, dict(DEFAULT_RULES))  # beyond virtual TP 13
            d3 = await db.positions.find_one({"_id": res.inserted_id})
            assert d3 and d3["mark_price"] == 20.0 and not d3.get("partial_done") and not d3.get("breakeven")
        run(body, monkeypatch, {})

    def test_g_record_trade_tags(self, monkeypatch):
        async def body(e, db):
            p = Position.from_mongo({**_pos_doc(), "_id": __import__("bson").ObjectId()})
            t = await e._record_trade(p, 11.0, 1.0, 0.01, "CRUCE")
            pn = Position.from_mongo({**_pos_doc(strategy="trend_ema", exit_mode=None), "_id": __import__("bson").ObjectId()})
            t2 = await e._record_trade(pn, 11.0, 1.0, 0.01, "TP")
            return t, t2
        t, t2 = run(body, monkeypatch, {})
        assert t.tags == ["Cruce EMAs Especial"] and t.exit_mode == "cross"
        assert t2.tags == [] and t2.exit_mode is None

    def test_h_real_path_no_sl_tp(self, monkeypatch):
        async def body(e, db):
            async def size(*a, **k):
                return {"qty": 1.0}
            e.live_size = size
            e.market.tickers.update({"UNIUSDT": {"price": 10.0}, "SOLUSDT": {"price": 100.0}})
            s = _settings()
            await e.open_position("UNIUSDT", {"strategy": "ema_test", "side": "long", "price": 10.0, "sl": 9.6, "tp": 11.2, "atr": 0.2}, s, None, exit_mode="cross")
            await e.open_position("SOLUSDT", {"strategy": "trend_ema", "side": "long", "price": 100.0, "sl": 98.0, "tp": 106.0, "atr": 1.0}, s, None)
            return e.broker.opens, await db.positions.find_one({"symbol": "UNIUSDT"})
        opens, pos = run(body, monkeypatch, {}, live=True)
        assert opens[0]["symbol"] == "UNIUSDT" and opens[0]["sl"] is None and opens[0]["tp"] is None
        assert opens[1]["symbol"] == "SOLUSDT" and isinstance(opens[1]["sl"], float) and isinstance(opens[1]["tp"], float)
        assert pos["exit_mode"] == "cross" and pos["live"] is True


# ---------- position_review ----------
class TestPositionReview:
    def _run(self, monkeypatch, action):
        async def review(*a, **k):
            return {"action": action, "confidence": 90, "reasoning": "test", "r_now": 0.5}
        monkeypatch.setattr(ai_advisor, "review_position", review)
        monkeypatch.setattr(position_review, "tg", _noop)

        async def body(e, db):
            await db.positions.insert_one(_pos_doc())
            e.closed = []

            async def close(pid, *a, **k):
                e.closed.append(pid)
            e.close_position = close
            out = await position_review.review_open_positions(e, _settings(ai_position_review_mode="auto"), "USDT", "Bybit")
            return out, e.closed
        return run(body, monkeypatch, {"UNIUSDT": _flat(), "BTCUSDT": _flat()})

    def test_breakeven_not_applicable(self, monkeypatch):
        out, closed = self._run(monkeypatch, "BREAKEVEN")
        assert "no aplicable" in out[0]["acted"] and closed == []

    def test_cerrar_closes(self, monkeypatch):
        out, closed = self._run(monkeypatch, "CERRAR")
        assert out[0]["acted"] == "cerrada" and len(closed) == 1


# ---------- backtest ----------
class TestBacktestUnit:
    def test_only_cross_exits(self):
        df = _alternating_df()
        r = backtest.run_backtest(df, "ema_test", 1, 3, True, 10000.0, "UNIUSDT")
        reasons = {t["reason"] for t in r["trades"]}
        assert r["total_trades"] > 3 and reasons == {"CRUCE"}, reasons
        sides = [t["side"] for t in r["trades"]]
        assert all(a != b for a, b in zip(sides, sides[1:]))  # stop-and-reverse alternates


# ---------- API ----------
@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    pw = "BlackCat2233@"
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": pw})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    yield s
    s.put(f"{BASE_URL}/api/settings", json={"ema_test_enabled": False, "ema_test_symbols": DEFAULTS})


class TestApi:
    def test_meta(self, api):
        d = api.get(f"{BASE_URL}/api/meta").json()
        assert "ema_test" in d["strategies"] and "ema_test" not in d["strategy_keys"]

    def test_settings_defaults_and_update(self, api):
        d = api.get(f"{BASE_URL}/api/settings").json()
        assert d["ema_test_enabled"] is False and d["ema_test_symbols"] == DEFAULTS
        r = api.put(f"{BASE_URL}/api/settings", json={"ema_test_symbols": ["ENAUSDT", "FOOUSDT"]})
        assert r.status_code == 200
        assert api.get(f"{BASE_URL}/api/settings").json()["ema_test_symbols"] == ["ENAUSDT"]
        api.put(f"{BASE_URL}/api/settings", json={"ema_test_enabled": True})
        assert api.get(f"{BASE_URL}/api/settings").json()["ema_test_enabled"] is True
        api.put(f"{BASE_URL}/api/settings", json={"ema_test_enabled": False, "ema_test_symbols": DEFAULTS})
        d = api.get(f"{BASE_URL}/api/settings").json()
        assert d["ema_test_enabled"] is False and d["ema_test_symbols"] == DEFAULTS

    def test_fx_settings_no_ema(self, api):
        d = api.get(f"{BASE_URL}/api/fx/settings").json()
        assert not [k for k in d if k.startswith("ema_test")]

    @pytest.mark.parametrize("tf", ["5m", "1h"])
    def test_backtest_api(self, api, tf):
        r = api.post(f"{BASE_URL}/api/backtest", json={"symbol": "UNIUSDT", "strategy": "ema_test", "timeframe": tf}, timeout=120)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        trades = d.get("trades") or []
        assert trades, d.keys()
        assert {t["reason"] for t in trades} == {"CRUCE"}


def teardown_module(module):
    async def drop():
        c = AsyncIOMotorClient(MONGO_URL)
        await c.drop_database(TEST_DB)
        c.close()
    asyncio.run(drop())
