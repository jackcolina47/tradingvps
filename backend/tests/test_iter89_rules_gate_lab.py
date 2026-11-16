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


def test_apply_levels_bounds():
    import ai_funnel as f
    base = {"side": "long", "price": 100.0, "sl": 98.0, "tp": 104.0}
    assert f.apply_levels(dict(base), None, None) is None
    s = dict(base); assert f.apply_levels(s, 97.0, None) and s["sl"] == 97.0 and s["tp"] == 104.0 and s["default_sl"] == 98.0
    s = dict(base); f.apply_levels(s, 90.0, None); assert abs(s["sl"] - 96.4) < 1e-9          # capped at 1.8x
    s = dict(base); f.apply_levels(s, 99.9, None); assert abs(s["sl"] - 98.8) < 1e-9          # floor 0.6x
    s = dict(base); assert f.apply_levels(s, 101.0, 99.0) is None and s["sl"] == 98.0          # wrong side ignored
    s = dict(base); f.apply_levels(s, None, 100.5, min_rr=1.5); assert s["tp"] == 103.0        # R:R floor
    s = dict(base); f.apply_levels(s, None, 150.0); assert s["tp"] == 110.0                    # cap 2.5x
    s = {"side": "short", "price": 100.0, "sl": 102.0, "tp": 96.0}; f.apply_levels(s, 103.0, 95.0); assert (s["sl"], s["tp"]) == (103.0, 95.0)
    s = dict(base); f.apply_levels(s, 99.5, None, spread=0.5); assert s["sl"] == 98.0 or s["sl"] <= 98.0  # never tighter than 4x spread


def test_levels_applied_on_open(monkeypatch):
    async def run():
        e, db = await mk("g")
        await ai_rules.add(db, ai_rules.RuleIn(text="Stop detrás del último swing"))

        async def ok(symbol, tf, sig, ctx, rules):
            return {"violates": False, "rule": None, "reason": "swing en 97", "sl": 97.0, "tp": None, "model": "m", "ms": 1}
        monkeypatch.setattr(ai_funnel, "rules_check", ok)
        await e._handle_signal("BTCUSDT", dict(SIG), await e.get_settings(), DF, 1, "4h")
        s = await db.signals.find_one({})
        p = await db.positions.find_one({})
        await db.client.drop_database(db.name)
        assert s["ai_levels_applied"] and s["sl"] == 97.0 and s["default_sl"] == SIG["sl"]
        assert abs((p["entry_price"] - p["sl"]) - 3.0) < 0.2
    asyncio.run(run())


def test_no_cap_and_ai_maintenance(monkeypatch):
    async def run():
        e, db = await mk("h")
        for i in range(40):
            await ai_rules.add(db, ai_rules.RuleIn(text=f"Regla número {i} de prueba"))
        rules = await ai_rules.list_rules(db)
        assert len(rules) == 40 and len(await ai_rules.active(db, "BTCUSDT")) == 40

        async def fake_review(engine, t, currency, unit, known):
            assert len(known) == 40
            return {"verdict": "ACEPTABLE", "verdict_reason": "ok", "lesson": "", "rule_actions": [
                {"n": 1, "action": "DESACTIVAR", "reason": "dudosa"}, {"n": 2, "action": "MODIFICAR", "text": "Regla reescrita por la IA", "reason": "más precisa"},
                {"n": 3, "action": "ELIMINAR", "reason": "duplicada"}, {"n": 99, "action": "ELIMINAR"}, {"n": 4, "action": "BORRAR_TODO"}]}
        monkeypatch.setattr(trade_learning, "review", fake_review)
        tr = Trade(symbol="BTCUSDT", side="long", strategy="trend_ema", entry_price=100, exit_price=99, qty=1, leverage=3, margin=33, pnl=-1.0, pnl_pct=-3, fees=0.1,
                   reason="SL", opened_at=now_iso())
        tr.id = "x"
        await trade_learning.learn(e, tr)
        after = {r["id"]: r for r in await ai_rules.list_rules(db)}
        ev = await ai_rules.events(db)
        await db.client.drop_database(db.name)
        assert len(after) == 39 and rules[2]["id"] not in after
        assert after[rules[0]["id"]]["enabled"] is False and after[rules[1]["id"]]["text"] == "Regla reescrita por la IA"
        assert sorted(x["action"] for x in ev) == ["DESACTIVAR", "ELIMINAR", "MODIFICAR"]
    asyncio.run(run())
