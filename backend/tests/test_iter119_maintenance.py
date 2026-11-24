"""Iter119: bounded signal keys, cached trade totals, atomic paper partial close, indexes and the user-triggered clean-up of old logs / equity points."""
import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from bson import ObjectId  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
import maintenance  # noqa: E402
from models import Settings, Position, Trade  # noqa: E402
from engine import TradingEngine  # noqa: E402
from risk import trade_rules  # noqa: E402

TEST_DB = f"test_iter119_{uuid.uuid4().hex[:8]}"  # fresh database per run: nothing is dropped


def _db(sub):
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[f"{TEST_DB}_{sub}"]


def _iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_recent_keys_bounded():
    k = maintenance.RecentKeys(3)
    for x in "abcd":
        k.add(x)
    assert len(k) == 3 and "a" not in k and "d" in k
    k.add("b")
    k.add("e")
    assert "b" in k and "c" not in k
    k.clear()
    assert len(k) == 0


def test_engine_uses_bounded_keys_and_models_drop_regime():
    assert isinstance(TradingEngine(_db("k")).signal_keys, maintenance.RecentKeys)
    fields = Settings.model_fields
    assert not {"btc_regime_filter", "btc_regime_sma", "rotation_enabled", "rotation_lookback_days"} & set(fields)
    assert Settings(btc_regime_filter=True, rotation_enabled=True).entry_order_type  # stored legacy fields are ignored, not rejected


def test_trade_totals_cached_and_invalidated():
    async def run():
        e = TradingEngine(_db("t"))
        await e.db.trades.insert_many([{"pnl": 5.0}, {"pnl": -2.0}])
        st = await maintenance.trade_totals(e)
        assert (st["n"], st["wins"], st["realized"], st["gw"], st["gl"]) == (2, 1, 3.0, 5.0, -2.0)
        await e.db.trades.insert_one({"pnl": 1.0})
        assert (await maintenance.trade_totals(e))["n"] == 2  # cached
        e._trade_totals = None
        assert (await maintenance.trade_totals(e))["n"] == 3
    asyncio.run(run())


def test_record_trade_invalidates_totals():
    async def run():
        e = TradingEngine(_db("r"))
        await e.db.settings.insert_one({"_id": "main", **Settings(entry_order_type="market").model_dump()})
        await e._new_account()
        assert (await e.account_summary())["total_trades"] == 0
        p = Position(symbol="BTCUSDT", side="long", strategy="trend_ema", entry_price=100.0, qty=1.0, leverage=1, margin=100.0, sl=98.0, tp=104.0, initial_sl=98.0, atr=1.0, fees=0.0, mark_price=100.0)
        await e._record_trade(p, 104.0, 4.0, 0.1, "TP")
        s = await e.account_summary()
        assert s["total_trades"] == 1 and s["wins"] == 1 and s["realized_pnl"] == 4.0
    asyncio.run(run())


def test_paper_partial_not_credited_after_manual_close():
    async def run():
        e = TradingEngine(_db("p"))
        await e.db.settings.insert_one({"_id": "main", **Settings(entry_order_type="market", partial_tp_enabled=True, partial_tp_r=1.0, partial_tp_pct=50).model_dump()})
        await e._new_account()

        async def quiet(*a, **k):
            return None
        e.tg = quiet
        rules = trade_rules(await e.get_settings())
        mk = lambda: Position(symbol="BTCUSDT", side="long", strategy="trend_ema", entry_price=100.0, qty=1.0, leverage=1, margin=100.0, sl=98.0, tp=110.0, initial_sl=98.0, atr=1.0, fees=0.0, mark_price=100.0)  # noqa: E731
        bal0 = (await e.db.account.find_one({"_id": "main"}))["balance"]

        gone = mk()
        gone.id = str(ObjectId())  # closed by hand between the monitor's read and its tick
        await e._manage_paper_position(gone, 103.0, rules)
        assert (await e.db.account.find_one({"_id": "main"}))["balance"] == bal0

        live = mk()
        live.id = str((await e.db.positions.insert_one(live.to_mongo())).inserted_id)
        await e._manage_paper_position(live, 103.0, rules)
        doc = await e.db.positions.find_one({"_id": ObjectId(live.id)})
        bal1 = (await e.db.account.find_one({"_id": "main"}))["balance"]
        assert doc["partial_done"] is True and doc["qty"] == 0.5 and bal1 > bal0

        stale = mk()
        stale.id = live.id  # a second task still holding the pre-partial copy
        await e._manage_paper_position(stale, 103.0, rules)
        assert (await e.db.account.find_one({"_id": "main"}))["balance"] == bal1
    asyncio.run(run())


def test_indexes_preview_and_cleanup():
    async def run():
        e = TradingEngine(_db("c"))
        db = e.db
        await maintenance.ensure_all([e])
        assert "key_1" in await db.signals.index_information() and "ts_1" in await db.equity.index_information()
        assert {"rule_id_1", "closed_at_1"} <= set(await db.shadow_trades.index_information())
        await db.logs.insert_many([{"ts": _iso(40), "message": "viejo"}, {"ts": _iso(29), "message": "reciente"}, {"ts": _iso(0), "message": "hoy"}])
        old = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=20)
        pts = [{"ts": (old + timedelta(minutes=m)).isoformat(), "equity": 1000 + m} for m in range(0, 180, 10)]  # 3 hours × 6 points
        recent = [{"ts": (datetime.now(timezone.utc) - timedelta(minutes=m)).isoformat(), "equity": 1} for m in range(5)]
        await db.equity.insert_many(pts + recent)
        await db.signals.insert_one({"key": "x", "created_at": _iso(400)})
        await db.shadow_trades.insert_one({"rule_id": "r", "closed_at": _iso(400), "pnl": 1.0})

        pv = await maintenance.preview([e])
        assert (pv["old_logs"], pv["old_equity"], pv["logs"], pv["equity"]) == (1, 18, 3, 23)
        assert await db.logs.count_documents({}) == 3  # the preview removes nothing

        out = await maintenance.prune([e])
        assert out == {"logs": 1, "equity": 15}
        assert [d["message"] async for d in db.logs.find().sort("ts", 1)] == ["reciente", "hoy"]
        kept = [d["equity"] async for d in db.equity.find({"ts": {"$lt": _iso(14)}}).sort("ts", 1)]
        assert kept == [1000, 1060, 1120]  # first point of each hour
        assert await db.equity.count_documents({}) == 8
        assert await db.signals.count_documents({}) == 1 and await db.shadow_trades.count_documents({}) == 1  # never touched
        assert await maintenance.prune([e]) == {"logs": 0, "equity": 0}
    asyncio.run(run())
