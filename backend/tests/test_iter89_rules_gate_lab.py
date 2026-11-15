# Iter89: Bybit opens every signal (only gate = learned rules checked by a cheap AI call), automatic learning on closed trades, Bybit Lab clone.
import os
import sys
import uuid
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
import pandas as pd  # noqa: E402
from models import Settings, Trade, Signal, now_iso  # noqa: E402
from engine import TradingEngine  # noqa: E402
from lab import LabEngine  # noqa: E402
import ai_funnel  # noqa: E402
import ai_rules  # noqa: E402
import ai_advisor  # noqa: E402
import trade_learning  # noqa: E402
import notify  # noqa: E402

TEST_DB = f"test_iter89_{uuid.uuid4().hex[:8]}"


def teardown_module(module):
    async def drop():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        await c.drop_database(TEST_DB)
        c.close()
    asyncio.run(drop())


class FakeMarket:
    provider = "fake"

    def __init__(self):
        self.tickers = {"BTCUSDT": {"price": 100.0, "change_pct": 1.0, "funding_rate": 0.0001}}

    async def fetch_before(self, symbol, tf, end, n):
        return []


SIG = {"side": "long", "strategy": "trend_ema", "price": 100.0, "sl": 98.0, "tp": 104.0, "score": 3, "reasons": ["a"], "indicators": {"atr": 1.0, "rsi": 55},
       "key_ts": 1, "atr": 1.0}
DF = pd.DataFrame({"open": [100.0] * 30, "high": [101.0] * 30, "low": [99.0] * 30, "close": [100.0] * 30, "volume": [1.0] * 30, "ts": list(range(30))})


async def mk(sub="a"):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[f"{TEST_DB}_{sub}"]
    e = TradingEngine(db)
    e.market = FakeMarket()
    await db.settings.insert_one({"_id": "main", **Settings(entry_order_type="market").model_dump()})
    await e._new_account()
    return e, db


def patch_tg(monkeypatch):
    sent = []

    async def fake(text, kind, bot=None):
        sent.append((bot, text))
    monkeypatch.setattr(notify, "notify", fake)
    return sent


def test_no_rules_opens_directly(monkeypatch):
    async def run():
        patch_tg(monkeypatch)
        e, db = await mk("a")

        async def boom(*a, **k):
            raise AssertionError("no AI call expected without rules")
        monkeypatch.setattr(ai_funnel, "rules_check", boom)
        await e._handle_signal("BTCUSDT", dict(SIG), await e.get_settings(), DF, 1, "4h")
        s = await db.signals.find_one()
        assert s["ai_decision"] == "SIN_IA" and await db.positions.count_documents({}) == 1
    asyncio.run(run())


def test_rule_blocks_passes_and_fails_open(monkeypatch):
    async def run():
        patch_tg(monkeypatch)
        e, db = await mk("b")
        rule = await ai_rules.add(db, ai_rules.RuleIn(text="No largos con RSI por encima de 50"))
        calls = []

        async def check(symbol, tf, sig, ctx, rules):
            calls.append(rules)
            return {"violates": True, "rule": rules[0], "reason": "RSI 55", "model": "m", "ms": 1}
        monkeypatch.setattr(ai_funnel, "rules_check", check)
        s = await e.get_settings()
        await e._handle_signal("BTCUSDT", dict(SIG), s, DF, 1, "4h")
        d = await db.signals.find_one()
        assert d["ai_decision"] == "RECHAZADA" and "Regla incumplida" in d["ai_reasoning"] and await db.positions.count_documents({}) == 0
        sh = await db.shadow_positions.find_one()
        assert sh["kind"] == "REGLA" and sh["rule_id"] == rule["id"]
        assert (await ai_rules.list_rules(db))[0]["hits"] == 1

        async def ok(symbol, tf, sig, ctx, rules):
            return {"violates": False, "rule": None, "reason": "", "model": "m", "ms": 1}
        monkeypatch.setattr(ai_funnel, "rules_check", ok)
        await e._handle_signal("BTCUSDT", {**SIG, "key_ts": 2}, s, DF, 1, "4h")
        assert await db.positions.count_documents({}) == 1 and (await db.signals.find_one({"key": {"$regex": ":2$"}}))["ai_decision"] == "APROBADA"
        await db.positions.delete_many({})

        async def down(*a, **k):
            raise RuntimeError("IA caída")
        monkeypatch.setattr(ai_funnel, "rules_check", down)
        await e._handle_signal("BTCUSDT", {**SIG, "key_ts": 3}, s, DF, 1, "4h")
        assert await db.positions.count_documents({}) == 1
        assert "se abre sin filtro" in (await db.signals.find_one({"key": {"$regex": ":3$"}}))["note"]
    asyncio.run(run())


def test_market_filters_no_longer_gate(monkeypatch):
    async def run():
        patch_tg(monkeypatch)
        e, db = await mk("c")
        e.news = type("N", (), {"gate": lambda self, s: ("bloqueo por noticias", 0.5), "context": lambda self, s: None})()
        await db.settings.update_one({"_id": "main"}, {"$set": {"btc_regime_filter": True, "rotation_enabled": True, "min_rr": 9, "ai_enabled": True}})
        await e._handle_signal("BTCUSDT", dict(SIG), await e.get_settings(), DF, -1, "4h")
        assert await db.positions.count_documents({}) == 1
    asyncio.run(run())


def _trade(pnl):
    return Trade(symbol="BTCUSDT", side="long", strategy="trend_ema", entry_price=100, exit_price=99, qty=1, leverage=3, margin=33, pnl=pnl, pnl_pct=pnl,
                 fees=0.1, reason="SL", opened_at=now_iso(), sl=98, tp=104, initial_sl=98, atr=1.0)


def test_learning_adds_rule_and_counts_misses(monkeypatch):
    async def run():
        sent = patch_tg(monkeypatch)
        e, db = await mk("d")

        async def analyze(t, signal, pa, before, during, tf, currency, known_rules=None):
            assert known_rules is not None
            return {"verdict": "EVITABLE", "verdict_reason": "x", "lesson": "No comprar tras tres velas grandes seguidas", "confidence": 80}
        monkeypatch.setattr(ai_advisor, "analyze_trade", analyze)
        monkeypatch.setattr(trade_learning.price_action, "facts", lambda *a, **k: {"summary": []})
        t = _trade(-5.0)
        t.id = str((await db.trades.insert_one(t.to_mongo())).inserted_id)
        await trade_learning.learn(e, t)
        rules = await ai_rules.list_rules(db)
        assert len(rules) == 1 and rules[0]["auto"] and rules[0]["enabled"] and any("Regla aprendida" in x[1] for x in sent)
        assert (await db.trades.find_one())["ai_review"]["verdict"] == "EVITABLE"
        await trade_learning.learn(e, t)  # same lesson twice → no duplicate
        assert len(await ai_rules.list_rules(db)) == 1

        sid = str((await db.signals.insert_one(Signal(symbol="BTCUSDT", side="long", strategy="trend_ema", timeframe="1h", price=100, sl=98, tp=104, score=3,
                                                       reasons=["a"], indicators={"atr": 1}, ai_decision="APROBADA", key="k", context={}).to_mongo())).inserted_id)

        async def check(symbol, tf, sig, ctx, rules):
            return {"violates": True, "rule": rules[0], "reason": "r", "model": "m", "ms": 1}
        monkeypatch.setattr(ai_funnel, "rules_check", check)
        for _ in range(ai_rules.MAX_MISSES):
            w = _trade(8.0)
            w.signal_id = sid
            w.id = str((await db.trades.insert_one(w.to_mongo())).inserted_id)
            await trade_learning.learn(e, w)
        r = (await ai_rules.list_rules(db))[0]
        assert r["misses"] == ai_rules.MAX_MISSES and r["enabled"] is False
    asyncio.run(run())


def test_ema_test_trades_are_not_learned(monkeypatch):
    async def run():
        e, _ = await mk("e")
        t = _trade(-1.0)
        t.exit_mode = "cross"
        called = []
        monkeypatch.setattr(e, "_learn", lambda tr: called.append(tr))
        e._after_close(t)
        assert not called
    asyncio.run(run())


def test_lab_is_isolated_paper_and_tagged(monkeypatch):
    async def run():
        sent = patch_tg(monkeypatch)
        main, db = await mk("f")
        lab = LabEngine(db, main)
        await lab.apply_config(type("C", (), {"trading_mode": "live", "bybit_api_key": "k", "bybit_api_secret": "s"})())
        assert lab.live is False and lab.broker is None and lab.market is main.market
        await lab.db.settings.insert_one({"_id": "main", **Settings(ema_test_enabled=True, ema_test_symbols=["BTCUSDT"]).model_dump()})
        await lab._new_account()
        sig = {**SIG, "strategy": "ema_test", "exit_mode": "cross"}
        await lab._handle_test_signal("BTCUSDT", sig, await lab.get_settings(), DF)
        assert await db.lab_positions.count_documents({}) == 1 and await db.positions.count_documents({}) == 0
        assert sent and all(b == "lab" and "Bybit Lab" in t for b, t in sent)
        p = await db.lab_positions.find_one()
        tr = await lab.close_position(str(p["_id"]), 101.0, "MANUAL")
        assert tr and await db.lab_trades.count_documents({}) == 1 and await db.trades.count_documents({}) == 0
    asyncio.run(run())
