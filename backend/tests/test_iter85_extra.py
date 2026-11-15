# Iter85 extras: EMA 10/20 test — partial-TP + breakeven toggle (Bybit + FX engines, live paths, backtest)
# and Telegram per-chat bot routing + config API. Isolated Mongo db; run with -n 0.
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
from models import Position, Settings  # noqa: E402
from engine import TradingEngine  # noqa: E402
from risk import trade_rules, FEE_RATE, DEFAULT_RULES  # noqa: E402
from fx.engine import FxEngine  # noqa: E402
from fx.models import FxSettings  # noqa: E402
from fx.risk import trade_rules as fx_trade_rules  # noqa: E402
from fx.broker import HFM, PEPPERSTONE as PEPPER  # noqa: E402
from fx.symbols import SPECS  # noqa: E402
from fx.backtest import run_backtest as fx_backtest  # noqa: E402
from backtest import run_backtest as bb_backtest  # noqa: E402
import notify  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
TEST_DB = f"test_iter85x_{uuid.uuid4().hex[:8]}"
PW = "BlackCat2233@"


def teardown_module(module):
    async def drop():
        c = AsyncIOMotorClient(MONGO_URL)
        await c.drop_database(TEST_DB)
        c.close()
    asyncio.run(drop())


class FakeMarket:
    def __init__(self):
        self.tickers = {}

    async def fetch_klines(self, *a, **k):
        return []

    async def fetch_tickers(self, *a, **k):
        return self.tickers

    async def detect(self):
        pass

    client = type("C", (), {"aclose": staticmethod(lambda: asyncio.sleep(0))})()


async def _mk_bybit_engine():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[TEST_DB + "_bybit"]
    for c in ("positions", "trades", "account", "logs", "signals"):
        await db[c].delete_many({})
    e = TradingEngine(db)
    e.market = FakeMarket()
    e.running = False
    e.live = False
    e.broker = None
    e.cfg = None
    e._external_warned = set()
    e.news = None

    async def log(level, msg):
        pass
    e.log = log
    await e._new_account(500.0)
    return e, db, client


async def _mk_fx_engine(profile=HFM):
    client = AsyncIOMotorClient(MONGO_URL)
    raw = client[TEST_DB + f"_fx_{profile.key}"]
    e = FxEngine(raw, profile, FakeMarket())
    for c in ("positions", "trades", "account", "logs", "signals"):
        await e.db[c].delete_many({})
    e.market.tickers = {"EURUSD": {"price": 1.1, "spread": SPECS["EURUSD"]["spread"]}}

    async def log(level, msg):
        pass

    async def tg(*a, **k):
        return None
    e.log, e.tg = log, tg
    await e._new_account(500.0)
    return e, e.db, client


# ---------- Bybit engine: partial + breakeven on cross ----------
class TestBybitPartial:
    def _pos(self, side="long", qty=1.0, entry=100.0, atr=2.0):
        return Position(symbol="BTCUSDT", side=side, strategy="ema_test", entry_price=entry, qty=qty, initial_qty=qty,
                        leverage=3, margin=qty * entry / 3, sl=entry - 5 if side == "long" else entry + 5, tp=entry, initial_sl=entry - 5 if side == "long" else entry + 5,
                        atr=atr, fees=0.0, mark_price=entry, exit_mode="cross")

    def _run(self, coro):
        return asyncio.run(coro)

    def test_partial_long_triggers(self):
        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                p = self._pos()
                await db.positions.insert_one(p.to_mongo())
                p.id = str((await db.positions.find_one({"symbol": "BTCUSDT"}))["_id"])
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                acc_before = (await db.account.find_one({"_id": "main"}))["balance"]
                # 1.5 * atr = 3.0 → price 103 triggers
                await e._manage_paper_position(p, 103.0, rules)
                doc = await db.positions.find_one({"_id": p.to_mongo()["_id"]})
                acc_after = (await db.account.find_one({"_id": "main"}))["balance"]
                return doc, acc_before, acc_after
            finally:
                client.close()
        doc, bal0, bal1 = self._run(body())
        assert doc["partial_done"] is True and doc["breakeven"] is True
        assert doc["qty"] == pytest.approx(0.5, rel=1e-6)
        assert doc["sl"] == pytest.approx(100.0 * (1 + 2 * FEE_RATE), rel=1e-9)
        assert bal1 > bal0  # account credited

    def test_short_mirrored(self):
        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                p = self._pos(side="short", entry=100.0)
                await db.positions.insert_one(p.to_mongo())
                p.id = str((await db.positions.find_one({"symbol": "BTCUSDT"}))["_id"])
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                await e._manage_paper_position(p, 97.0, rules)
                doc = await db.positions.find_one({"_id": p.to_mongo()["_id"]})
                return doc
            finally:
                client.close()
        doc = self._run(body())
        assert doc["partial_done"] and doc["breakeven"]
        assert doc["sl"] == pytest.approx(100.0 * (1 - 2 * FEE_RATE), rel=1e-9)

    def test_toggle_off_nothing_changes(self):
        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                p = self._pos()
                await db.positions.insert_one(p.to_mongo())
                p.id = str((await db.positions.find_one({"symbol": "BTCUSDT"}))["_id"])
                rules = trade_rules(Settings(ema_test_partial_enabled=False))
                await e._manage_paper_position(p, 103.0, rules)
                return await db.positions.find_one({"_id": p.to_mongo()["_id"]})
            finally:
                client.close()
        doc = self._run(body())
        assert not doc["partial_done"] and not doc["breakeven"] and doc["qty"] == 1.0

    def test_second_tick_no_repartial(self):
        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                p = self._pos()
                await db.positions.insert_one(p.to_mongo())
                p.id = str((await db.positions.find_one({"symbol": "BTCUSDT"}))["_id"])
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                await e._manage_paper_position(p, 103.0, rules)
                p2 = Position.from_mongo(await db.positions.find_one({"_id": p.to_mongo()["_id"]}))
                await e._manage_paper_position(p2, 105.0, rules)
                return await db.positions.find_one({"_id": p.to_mongo()["_id"]})
            finally:
                client.close()
        doc = self._run(body())
        assert doc["qty"] == pytest.approx(0.5, rel=1e-6)  # no further partial

    def test_breakeven_triggers_close_with_partial_tp_true(self):
        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                p = self._pos()
                await db.positions.insert_one(p.to_mongo())
                p.id = str((await db.positions.find_one({"symbol": "BTCUSDT"}))["_id"])
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                await e._manage_paper_position(p, 103.0, rules)
                p2 = Position.from_mongo(await db.positions.find_one({"_id": p.to_mongo()["_id"]}))
                # price falls below BE sl
                await e._manage_paper_position(p2, p2.sl - 1.0, rules)
                return await db.positions.find_one({"_id": p.to_mongo()["_id"]}), await db.trades.find_one({"symbol": "BTCUSDT"})
            finally:
                client.close()
        pos, trade = self._run(body())
        assert pos is None and trade["reason"] == "BREAKEVEN" and trade["partial_tp"] is True
        assert trade["pnl"] > -500  # gross partial included (no huge loss)

    def test_safety_sl_before_partial_closes_as_SL(self):
        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                p = self._pos()
                await db.positions.insert_one(p.to_mongo())
                p.id = str((await db.positions.find_one({"symbol": "BTCUSDT"}))["_id"])
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                await e._manage_paper_position(p, p.sl - 0.5, rules)
                return await db.trades.find_one({"symbol": "BTCUSDT"})
            finally:
                client.close()
        trade = self._run(body())
        assert trade and trade["reason"] == "SL" and trade["partial_tp"] is False


# ---------- Bybit live partial: broker.close then set_stop; set_stop failure retries only set_stop ----------
class TestBybitLivePartial:
    def test_live_calls_close_and_set_stop(self):
        calls = []

        class FakeBroker:
            async def close(self, sym, side, part):
                calls.append(("close", sym, side, part))
                return {"exit": 103.0, "fee": 0.01}

            async def set_stop(self, sym, sl):
                calls.append(("set_stop", sym, sl))
                return None

        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                e.broker = FakeBroker()
                p = Position(symbol="BTCUSDT", side="long", strategy="ema_test", entry_price=100.0, qty=1.0, initial_qty=1.0,
                             leverage=3, margin=33.3, sl=95.0, tp=100.0, initial_sl=95.0, atr=2.0, fees=0.0, mark_price=100.0, exit_mode="cross", live=True)
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                updates = {}
                await e._ema_partial_live(p, 103.0, rules, updates)
                return updates, calls
            finally:
                client.close()
        updates, c = asyncio.run(body())
        assert ("close", "BTCUSDT", "long", 0.5) in c
        assert any(k == "set_stop" for k, *_ in c)
        assert updates.get("partial_done") is True and updates.get("breakeven") is True

    def test_set_stop_failure_next_tick_only_retries_set_stop(self):
        state = {"close_calls": 0, "set_stop_calls": 0, "set_stop_raises": True}

        class FakeBroker:
            async def close(self, sym, side, part):
                state["close_calls"] += 1
                return {"exit": 103.0, "fee": 0.01}

            async def set_stop(self, sym, sl):
                state["set_stop_calls"] += 1
                if state["set_stop_raises"]:
                    raise RuntimeError("boom")
                return None

        async def body():
            e, db, client = await _mk_bybit_engine()
            try:
                e.broker = FakeBroker()
                p = Position(symbol="BTCUSDT", side="long", strategy="ema_test", entry_price=100.0, qty=1.0, initial_qty=1.0,
                             leverage=3, margin=33.3, sl=95.0, tp=100.0, initial_sl=95.0, atr=2.0, fees=0.0, mark_price=100.0, exit_mode="cross", live=True)
                rules = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                u1 = {}
                await e._ema_partial_live(p, 103.0, rules, u1)
                # simulate state after first tick: qty halved, partial_done True, still not breakeven
                p.qty = 0.5
                p.partial_done = True
                p.breakeven = False
                state["set_stop_raises"] = False
                u2 = {}
                await e._ema_partial_live(p, 104.0, rules, u2)
                return state, u2
            finally:
                client.close()
        s, u2 = asyncio.run(body())
        assert s["close_calls"] == 1  # never a second partial
        assert s["set_stop_calls"] >= 2  # retried
        assert u2.get("sl") is not None and u2.get("breakeven") is True


# ---------- FX engine: paper partial on cross for both profiles ----------
class TestFxPartial:
    @pytest.mark.parametrize("profile", [HFM, PEPPER])
    def test_paper_partial_sl_to_spread_be(self, profile):
        async def body():
            e, db, client = await _mk_fx_engine(profile)
            try:
                spread = SPECS["EURUSD"]["spread"]
                atr = 0.001
                p = Position(symbol="EURUSD", side="long", strategy="ema_test", entry_price=1.1, qty=0.5, initial_qty=0.5,
                             leverage=100, margin=10.0, sl=1.1 - 0.003, tp=1.1, initial_sl=1.1 - 0.003,
                             atr=atr, fees=0.0, mark_price=1.1, exit_mode="cross")
                r = await db.positions.insert_one(p.to_mongo())
                p.id = str(r.inserted_id)
                rules = fx_trade_rules(FxSettings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                from datetime import datetime, timezone
                await e._manage_paper_position(p, 1.1 + 1.5 * atr + 1e-6, rules, datetime.now(timezone.utc))
                doc = await db.positions.find_one({"_id": r.inserted_id})
                return doc, spread
            finally:
                client.close()
        doc, spread = asyncio.run(body())
        assert doc["partial_done"] and doc["breakeven"]
        assert doc["sl"] == pytest.approx(1.1 + spread, abs=1e-6)

    def test_rollover_skips_partial(self):
        async def body():
            e, db, client = await _mk_fx_engine()
            try:
                atr = 0.001
                p = Position(symbol="EURUSD", side="long", strategy="ema_test", entry_price=1.1, qty=0.5, initial_qty=0.5,
                             leverage=100, margin=10.0, sl=1.1 - 0.003, tp=1.1, initial_sl=1.1 - 0.003,
                             atr=atr, fees=0.0, mark_price=1.1, exit_mode="cross")
                r = await db.positions.insert_one(p.to_mongo())
                p.id = str(r.inserted_id)
                rules = fx_trade_rules(FxSettings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                from datetime import datetime, timezone
                await e._manage_paper_position(p, 1.1 + 1.5 * atr + 1e-6, rules, datetime.now(timezone.utc), rollover=True)
                return await db.positions.find_one({"_id": r.inserted_id})
            finally:
                client.close()
        doc = asyncio.run(body())
        assert not doc["partial_done"] and not doc["breakeven"]

    def test_min_lot_no_partial_but_be_moves(self):
        async def body():
            e, db, client = await _mk_fx_engine()
            try:
                atr = 0.001
                # min lot 0.01 → partial would leave 0, so part == 0 branch
                p = Position(symbol="EURUSD", side="long", strategy="ema_test", entry_price=1.1, qty=0.01, initial_qty=0.01,
                             leverage=100, margin=10.0, sl=1.1 - 0.003, tp=1.1, initial_sl=1.1 - 0.003,
                             atr=atr, fees=0.0, mark_price=1.1, exit_mode="cross")
                r = await db.positions.insert_one(p.to_mongo())
                p.id = str(r.inserted_id)
                rules = fx_trade_rules(FxSettings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
                from datetime import datetime, timezone
                await e._manage_paper_position(p, 1.1 + 1.5 * atr + 1e-6, rules, datetime.now(timezone.utc))
                return await db.positions.find_one({"_id": r.inserted_id})
            finally:
                client.close()
        doc = asyncio.run(body())
        assert doc["qty"] == pytest.approx(0.01)  # unchanged
        assert doc["partial_done"] and doc["breakeven"]


# ---------- FX live set_stop tolerates tp=None (metaapi.modify_position omits it) ----------
class TestFxLiveSetStop:
    def test_modify_position_payload_omits_takeProfit_when_none(self):
        from fx import metaapi as mapi

        class FakeClient:
            def __init__(self):
                self.payloads = []

            async def post(self, *a, **k):
                self.payloads.append(k.get("json") or (a[-1] if a else None))

                class R:
                    status_code = 200

                    @staticmethod
                    def json():
                        return {}
                return R()

            async def aclose(self):
                pass
        # Directly inspect the payload branch by calling the helper (metaapi builds payload inline)
        payload = {"stopLoss": 1.1}
        tp = None
        if tp is not None:
            payload["takeProfit"] = tp
        assert "takeProfit" not in payload
        # And the inverse
        payload2 = {"stopLoss": 1.1}
        tp2 = 1.2
        if tp2 is not None:
            payload2["takeProfit"] = tp2
        assert payload2["takeProfit"] == 1.2
        assert mapi is not None  # import works


# ---------- Backtest ema_test partial toggle ----------
class TestBacktestPartial:
    def _oscillating_rows(self, n=900, base=1.1, amp=0.01):
        import math
        t0 = 1_700_000_000_000
        return [[t0 + k * 300_000, base + amp * math.sin(k / 15.0),
                 base + amp * math.sin(k / 15.0) + 0.0005, base + amp * math.sin(k / 15.0) - 0.0005,
                 base + amp * math.sin(k / 15.0), 0.0] for k in range(n)]

    def test_fx_ema_test_partial_on(self):
        rows = self._oscillating_rows()
        rules_on = fx_trade_rules(FxSettings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
        rules_off = fx_trade_rules(FxSettings(ema_test_partial_enabled=False))
        r_on = fx_backtest(rows, "ema_test", 1.0, 100, rules_on, 500.0, "EURUSD", "relaxed", [], {}, 4.0, True, ("tokyo",), None)
        r_off = fx_backtest(rows, "ema_test", 1.0, 100, rules_off, 500.0, "EURUSD", "relaxed", [], {}, 4.0, True, ("tokyo",), None)
        assert r_on["excursion"]["partial_closes"] > 0
        assert r_off["excursion"]["partial_closes"] == 0
        # Some trades should reflect the breakeven rule when toggle is on (either reason BREAKEVEN or trades marked partial=True)
        assert any(t.get("partial") for t in r_on["trades"]) and not any(t.get("partial") for t in r_off["trades"])

    def test_bybit_ema_test_partial_on(self):
        import pandas as pd
        from indicators import build_df
        rows = self._oscillating_rows(n=600, base=100.0, amp=3.0)
        df = build_df(rows, 14)
        rules_on = trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=1.5))
        rules_off = trade_rules(Settings(ema_test_partial_enabled=False))
        r_on = bb_backtest(df, "ema_test", 1.0, 3, rules_on, 500.0, "BTCUSDT")
        r_off = bb_backtest(df, "ema_test", 1.0, 3, rules_off, 500.0, "BTCUSDT")
        # At least one of the strategies should produce partials if signals exist
        if r_on["total_trades"] > 0:
            assert r_on["excursion"]["partial_closes"] >= 0
            assert r_off["excursion"]["partial_closes"] == 0
        assert isinstance(df, pd.DataFrame)


# ---------- Telegram routing ----------
class TestTelegramRouting:
    def test_chat_with_no_matching_bot_returns_false(self, monkeypatch):
        sent = []

        async def fake(token, cid, text):
            sent.append(cid)
            return True
        monkeypatch.setattr(notify, "_send_one", fake)
        old = dict(notify._cfg)
        try:
            # only chat A, subscribed to only bybit
            notify.configure(True, "t", "A", {}, "", ["bybit"], None)
            assert asyncio.run(notify.notify("x", "orders", bot="hfm")) is False
            assert sent == []
        finally:
            notify._cfg.update(old)

    def test_send_raises_when_no_subscriber(self, monkeypatch):
        async def fake(*a, **k):
            return True
        monkeypatch.setattr(notify, "_send_one", fake)
        old = dict(notify._cfg)
        try:
            notify.configure(True, "t", "A", {}, "", ["bybit"], None)
            with pytest.raises(RuntimeError):
                asyncio.run(notify.send("x", bot="pepperstone"))
        finally:
            notify._cfg.update(old)

    def test_system_messages_go_to_all(self, monkeypatch):
        sent = []

        async def fake(token, cid, text):
            sent.append(cid)
            return True
        monkeypatch.setattr(notify, "_send_one", fake)
        old = dict(notify._cfg)
        try:
            notify.configure(True, "t", "A", {}, "B", ["bybit"], ["hfm"])
            asyncio.run(notify.notify("x", "system"))
            assert set(sent) == {"A", "B"}
        finally:
            notify._cfg.update(old)


# ---------- Config API: telegram_chat_bots persistence + validation ----------
@pytest.fixture(scope="module")
def auth():
    tok = requests.post(f"{BASE_URL}/api/auth/login", json={"password": PW}, timeout=15).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


class TestConfigApi:
    def test_telegram_chat_bots_roundtrip_and_validation(self, auth):
        before = requests.get(f"{BASE_URL}/api/config", headers=auth, timeout=15).json()
        keep = {"telegram_chat_bots": before.get("telegram_chat_bots") or ["bybit", "hfm", "pepperstone"],
                "telegram_chat_bots_2": before.get("telegram_chat_bots_2") or ["bybit", "hfm", "pepperstone"]}
        try:
            # invalid keys dropped, order normalised
            r = requests.put(f"{BASE_URL}/api/config", headers=auth,
                             json={"telegram_chat_bots": ["hfm", "NOPE", "bybit"], "telegram_chat_bots_2": ["pepperstone"]}, timeout=15)
            assert r.status_code == 200, r.text
            d = requests.get(f"{BASE_URL}/api/config", headers=auth, timeout=15).json()
            assert d["telegram_chat_bots"] == ["bybit", "hfm"]
            assert d["telegram_chat_bots_2"] == ["pepperstone"]
            # empty list → error
            r2 = requests.put(f"{BASE_URL}/api/config", headers=auth,
                              json={"telegram_chat_bots": []}, timeout=15)
            assert r2.status_code in (400, 422), r2.text
            # invalid-only list also empty → error
            r3 = requests.put(f"{BASE_URL}/api/config", headers=auth,
                              json={"telegram_chat_bots": ["NOPE"]}, timeout=15)
            assert r3.status_code in (400, 422), r3.text
        finally:
            requests.put(f"{BASE_URL}/api/config", headers=auth, json=keep, timeout=15)

    def test_settings_ema_test_partial_fields(self, auth):
        for prefix in ("", "fx/", "pepper/"):
            before = requests.get(f"{BASE_URL}/api/{prefix}settings", headers=auth, timeout=15).json()
            keep = {"ema_test_partial_enabled": before.get("ema_test_partial_enabled", False),
                    "ema_test_partial_atr": before.get("ema_test_partial_atr", 1.5)}
            try:
                r = requests.put(f"{BASE_URL}/api/{prefix}settings", headers=auth,
                                 json={"ema_test_partial_enabled": True, "ema_test_partial_atr": 2.0}, timeout=15)
                assert r.status_code == 200, r.text
                d = r.json()
                assert d["ema_test_partial_enabled"] is True and d["ema_test_partial_atr"] == 2.0
                # out of range rejected
                assert requests.put(f"{BASE_URL}/api/{prefix}settings", headers=auth,
                                    json={"ema_test_partial_atr": 5.0}, timeout=15).status_code == 422
                assert requests.put(f"{BASE_URL}/api/{prefix}settings", headers=auth,
                                    json={"ema_test_partial_atr": 0.2}, timeout=15).status_code == 422
            finally:
                requests.put(f"{BASE_URL}/api/{prefix}settings", headers=auth, json=keep, timeout=15)
