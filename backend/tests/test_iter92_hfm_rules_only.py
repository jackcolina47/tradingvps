# Iter92: HotForex = rules-only entries + automatic learning (like Bybit); Pepperstone keeps the AI approval.
import asyncio
import os
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
import pytest
from motor.motor_asyncio import AsyncIOMotorClient
import ai_funnel
import ai_rules
import notify
import trade_learning
from fx import engine_signals
from fx.engine import FxEngine
from fx.broker import HFM, PEPPERSTONE
from fx.models import FxSettings
from models import Trade, now_iso
from indicators import build_df

DF = build_df([[i * 3600_000, 1.1, 1.101, 1.099, 1.1 + (i % 5) * 0.0002, 0] for i in range(300)])
SIG = {"side": "long", "strategy": "trend_ema", "price": 1.1000, "sl": 1.0980, "tp": 1.1040, "score": 3, "reasons": ["x"], "indicators": {"atr": 0.001, "rsi": 55}, "atr": 0.001, "key_ts": 1}


class FakeMarket:
    tickers = {"EURUSD": {"price": 1.1000, "change_pct": 0.1}}

    async def fetch_tickers(self, *a, **k):
        return self.tickers


async def mk(profile, name):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[f"test_iter92_{name}"]
    await db.client.drop_database(db.name)
    e = FxEngine(db, profile)
    e.market = FakeMarket()
    e.news = None
    await e.db.settings.insert_one({"_id": "main", **FxSettings().model_dump()})
    await e._new_account(10000.0)

    async def no_block(*a, **k):
        return None, None, []
    e._blocks = no_block
    return e, db


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(notify, "notify", none)
    monkeypatch.setattr(engine_signals, "market_status", lambda: {"sessions": [{"id": "london", "name": "Londres", "open": True}], "open": True})


def test_hfm_opens_without_ai(monkeypatch):
    async def run():
        e, db = await mk(HFM, "a")

        async def boom(*a, **k):
            raise AssertionError("no AI call expected")
        monkeypatch.setattr(ai_funnel, "rules_check", boom)
        monkeypatch.setattr(engine_signals, "evaluate_signal", boom)
        await e._handle_signal("EURUSD", dict(SIG), await e.get_settings(), DF, 1, "4h")
        n, s = await e.db.positions.count_documents({}), await e.db.signals.find_one({})
        await db.client.drop_database(db.name)
        assert n == 1 and s["ai_decision"] == "SIN_IA"
    asyncio.run(run())


def test_hfm_rule_blocks_and_adjusts(monkeypatch):
    async def run():
        e, db = await mk(HFM, "b")
        rule = await ai_rules.add(e.db, ai_rules.RuleIn(text="No largos en rango"))
        out = {"violates": True, "rule": rule, "reason": "rango", "sl": None, "tp": None, "model": "m", "ms": 1}

        async def check(symbol, tf, sig, ctx, rules, market=None):
            assert market == trade_learning.FX_MARKET
            return out
        monkeypatch.setattr(ai_funnel, "rules_check", check)
        await e._handle_signal("EURUSD", dict(SIG), await e.get_settings(), DF, 1, "4h")
        assert await e.db.positions.count_documents({}) == 0
        sh = await e.db.shadow_positions.find_one({})
        assert sh["kind"] == "REGLA" and sh["rule_id"] == rule["id"]
        out.update(violates=False, rule=None, sl=1.0975)
        await e._handle_signal("EURUSD", {**SIG, "key_ts": 2}, await e.get_settings(), DF, 1, "4h")
        s = await e.db.signals.find_one({"ai_decision": "APROBADA"})
        n = await e.db.positions.count_documents({})
        await db.client.drop_database(db.name)
        assert n == 1 and s["ai_levels_applied"] and s["sl"] == 1.0975
    asyncio.run(run())


def test_pepperstone_still_uses_ai(monkeypatch):
    async def run():
        e, db = await mk(PEPPERSTONE, "c")
        called = []

        async def final(fn, *a, **k):
            called.append(1)
            return {"decision": "RECHAZADA", "confidence": 80, "risk": "ALTO", "reasoning": "no", "model": "m", "ms": 1}
        monkeypatch.setattr(ai_funnel, "final_eval", final)
        s = await e.get_settings()
        s.ai_funnel_enabled, s.price_action_block, s.seasonality_bias, s.vol_filter_enabled, s.usd_regime_filter, s.fomc_rule = (False,) * 6
        await e._handle_signal("EURUSD", dict(SIG), s, DF, 1, "4h")
        n = await e.db.positions.count_documents({})
        doc = await e.db.signals.find_one({})
        await db.client.drop_database(db.name)
        assert called and n == 0, (doc["ai_decision"], doc.get("note"))
    asyncio.run(run())


def test_learning_only_hfm(monkeypatch):
    async def run():
        seen = []

        async def learn(engine, trade, currency, unit):
            seen.append((engine.profile.key, currency, unit))
        monkeypatch.setattr(trade_learning, "learn", learn)
        for prof, nm in ((HFM, "d"), (PEPPERSTONE, "e")):
            e, db = await mk(prof, nm)
            await e._learn(Trade(symbol="EURUSD", side="long", strategy="trend_ema", entry_price=1.1, exit_price=1.09, qty=0.1, leverage=100, margin=10, pnl=-5, pnl_pct=-1, fees=0,
                                 reason="SL", opened_at=now_iso())) if prof.rules_only else None
            await db.client.drop_database(db.name)
        assert seen == [("hfm", "USD", "s")] and not PEPPERSTONE.rules_only
    asyncio.run(run())


def test_dual_pair_only_hfm():
    async def run():
        out = {}
        for prof, nm in ((HFM, "f"), (PEPPERSTONE, "g")):
            e, db = await mk(prof, nm)
            docs = [{"symbol": "EURUSD", "exit_mode": "cross"}]
            out[prof.key] = (e._pair_taken(docs, "EURUSD", False), e._pair_taken(docs, "EURUSD", True), e._pair_taken([{"symbol": "EURUSD"}], "EURUSD", True),
                             e._pair_taken([{"symbol": "EURUSD"}], "EURUSD", False))
            s = await e.get_settings()
            pos, _ = await e.open_position("EURUSD", dict(SIG), s, "s1", exit_mode="cross")
            pos2, _ = await e.open_position("EURUSD", dict(SIG), s, "s2")
            pos3, _ = await e.open_position("EURUSD", dict(SIG), s, "s3")
            out[prof.key] += (bool(pos), bool(pos2), bool(pos3))
            await db.client.drop_database(db.name)
        assert out["hfm"] == (False, True, False, True, True, True, False)
        assert out["pepperstone"] == (True, True, True, True, True, False, False)
    asyncio.run(run())


def test_fail_safe_when_ai_down(monkeypatch):
    async def run():
        async def down(*a, **k):
            raise TimeoutError()
        monkeypatch.setattr(ai_funnel, "rules_check", down)
        monkeypatch.setattr(ai_funnel, "final_eval", down)
        out = {}
        for prof, nm in ((HFM, "h"), (PEPPERSTONE, "i")):
            e, db = await mk(prof, nm)
            await ai_rules.add(e.db, ai_rules.RuleIn(text="Regla cualquiera de prueba"))
            s = await e.get_settings()
            s.ai_funnel_enabled, s.price_action_block, s.seasonality_bias, s.vol_filter_enabled, s.usd_regime_filter, s.fomc_rule = (False,) * 6
            await e._handle_signal("EURUSD", dict(SIG), s, DF, 1, "4h")
            doc = await e.db.signals.find_one({})
            out[prof.key] = (await e.db.positions.count_documents({}), doc["ai_decision"], doc.get("note") or "")
            await db.client.drop_database(db.name)
        assert out["hfm"][0] == 0 and out["hfm"][1] == "BLOQUEADA" and "fail-safe activo" in out["hfm"][2], out
        assert out["pepperstone"][0] == 0 and out["pepperstone"][1] == "ERROR", out
    asyncio.run(run())
