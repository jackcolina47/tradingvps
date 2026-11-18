import asyncio
import os
import time
from types import SimpleNamespace
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
import ai_advisor
import ai_rules
import position_review
import trade_learning
from models import Trade, now_iso


def mkdb(n):
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[f"test_iter102_{n}"]


def trade(pnl):
    t = Trade(symbol="BTCUSDT", side="long", strategy="trend_ema", entry_price=100, exit_price=99, qty=1, leverage=3, margin=33, pnl=pnl, pnl_pct=pnl, fees=0.1, reason="SL", opened_at=now_iso())
    t.id = "x"
    return t


def test_scope_filter_and_winner_not_analysed(monkeypatch):
    async def run():
        db = mkdb("a")
        await db.client.drop_database(db.name)
        await ai_rules.add(db, ai_rules.RuleIn(text="Regla global manual"))
        await ai_rules.auto_add(db, "Solo largos de trend_ema en BTC", "t", "MALA", symbol="BTCUSDT", side="long", strategy="trend_ema")
        await ai_rules.auto_add(db, "Solo cortos", "t", "MALA", side="short")
        n = lambda *a: asyncio.ensure_future(ai_rules.active(db, *a))  # noqa: E731
        a, b, c, d = await n("BTCUSDT", "long", "trend_ema"), await n("BTCUSDT", "short", "trend_ema"), await n("ETHUSDT", "long", "rsi"), await n("BTCUSDT")
        assert (len(a), len(b), len(c), len(d)) == (2, 2, 1, 3)
        calls = []

        async def review(*a, **k):
            calls.append("review")
            return {"verdict": "MALA", "verdict_reason": "r", "lesson": "No comprar tras tres velas verdes", "lesson_scope": {"symbol": False, "side": True, "strategy": False}, "rule_actions": []}

        async def winner(*a, **k):
            calls.append("winner")
        monkeypatch.setattr(trade_learning, "review", review)
        monkeypatch.setattr(trade_learning, "_check_winner", winner)

        async def log(*a):
            pass
        e = SimpleNamespace(db=db, log=log, tg=log)
        await trade_learning.learn(e, trade(5.0))
        assert calls == ["winner"]
        await trade_learning.learn(e, trade(-5.0))
        new = [r for r in await ai_rules.list_rules(db) if r["text"].startswith("No comprar")][0]
        await db.client.drop_database(db.name)
        assert calls == ["winner", "review"] and (new["side"], new["symbol"], new["strategy"]) == ("long", "", "")
    asyncio.run(run())


def test_weekly_cleanup_merges_only_duplicates(monkeypatch):
    async def run():
        db = mkdb("b")
        await db.client.drop_database(db.name)
        for i in range(9):
            await ai_rules.add(db, ai_rules.RuleIn(text=f"Regla de prueba {i}"))
        calls = []

        async def cleanup(rules):
            calls.append(1)
            pick = lambda *ix: [{"id": rules[i]["id"]} for i in ix]  # noqa: E731
            return [{"type": "DUPLICADAS", "rules": pick(0, 1), "merged": "Regla fusionada", "keep_id": None, "reason": "igual"},
                    {"type": "CONTRADICTORIAS", "rules": pick(2, 3), "merged": "Otra", "keep_id": None, "reason": "x"}]
        monkeypatch.setattr(ai_advisor, "cleanup_rules", cleanup)

        async def log(*a):
            pass
        e = SimpleNamespace(db=db, log=log, tg=log)
        await trade_learning._weekly_cleanup(e)
        await trade_learning._weekly_cleanup(e)
        n = len(await ai_rules.list_rules(db))
        await db.client.drop_database(db.name)
        assert n == 8 and len(calls) == 1
    asyncio.run(run())


def test_position_review_skip_logic():
    p = SimpleNamespace(entry_price=100.0, initial_sl=98.0, sl=98.0, side="long")
    assert position_review._move_r(p, 101.0) == 0.5 and position_review._move_r(p, None) is None
    assert position_review.SKIP_R == 0.5 and position_review.SKIP_MAX_SEC == 4 * 3600 and time.time() > 0
