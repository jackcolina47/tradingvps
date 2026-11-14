# Iter81: explicit retest of iteration_80 fixes (EMA test cross positions don't use a slot; toggle-off closes test positions outside symbols).
# Isolated throwaway Mongo db (dropped at end). Run with -n 0.
import os
import sys
import asyncio
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_iter80_ema_test as t80  # noqa: E402
from test_iter80_ema_test import run, _settings, _pos_doc, _rows, engine_mod  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def drop_db():
    yield

    async def d():
        c = AsyncIOMotorClient(t80.MONGO_URL)
        await c.drop_database(t80.TEST_DB)
        c.close()
    asyncio.run(d())


SIG = {"strategy": "trend_ema", "side": "long", "price": 100.0, "sl": 98.0, "tp": 106.0, "atr": 1.0}


# Fix 1: slot cap ignores cross positions, still counts normal ones
class TestSlot:
    def test_cross_no_slot_and_normal_open_ok(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(symbol="UNIUSDT"))
            docs = await e._open_and_pending()
            block = e._slot_block(docs, _settings(max_positions=1), "short")
            e.market.tickers["SOLUSDT"] = {"price": 100.0}
            await e.open_position("SOLUSDT", SIG, _settings(max_positions=1), None)
            return docs, block, await db.positions.count_documents({"symbol": "SOLUSDT"})
        docs, block, n = run(body, monkeypatch, {})
        assert all("exit_mode" in d for d in docs) and docs[0]["exit_mode"] == "cross"
        assert block is None and n == 1

    def test_normal_position_still_blocks(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(symbol="ETHUSDT", strategy="trend_ema", exit_mode=None))
            docs = await e._open_and_pending()
            block = e._slot_block(docs, _settings(max_positions=1), "short")
            e.market.tickers["SOLUSDT"] = {"price": 100.0}
            await e.open_position("SOLUSDT", SIG, _settings(max_positions=1), None)
            return block, await db.positions.count_documents({"symbol": "SOLUSDT"})
        block, n = run(body, monkeypatch, {})
        assert block and "máximo" in block
        assert n == 0, "normal slot cap not enforced by open_position"

    def test_mixed_cross_plus_normal_blocks_at_cap1(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(symbol="UNIUSDT"))
            await db.positions.insert_one(_pos_doc(symbol="ETHUSDT", strategy="trend_ema", exit_mode=None))
            return e._slot_block(await e._open_and_pending(), _settings(max_positions=2), "short"), \
                e._slot_block(await e._open_and_pending(), _settings(max_positions=1), "short")
        b2, b1 = run(body, monkeypatch, {})
        assert b2 is None and b1


# Fix 2: toggle off, pair outside symbols -> close on cross, no new signal, no regular evaluate
class TestToggleOffClose:
    def _patch_eval(self, monkeypatch):
        calls = []
        orig = engine_mod.evaluate

        def rec(df, i, *a, **k):
            calls.append(id(df))
            return orig(df, i, *a, **k)
        monkeypatch.setattr(engine_mod, "evaluate", rec)
        return calls

    def test_toggle_off_closes_no_reverse_no_evaluate(self, monkeypatch):
        calls = self._patch_eval(monkeypatch)

        async def body(e, db):
            await db.positions.insert_one(_pos_doc(side="long"))
            await e.scan_once(_settings(ema_test_enabled=False, symbols=["BTCUSDT"]))
            tr = await db.trades.find_one({"symbol": "UNIUSDT"})
            return (e.market.calls, tr, await db.positions.count_documents({"symbol": "UNIUSDT"}),
                    await db.signals.count_documents({"symbol": "UNIUSDT"}), e.normal_signals)
        mcalls, tr, open_n, sig_n, normal = run(body, monkeypatch, {"UNIUSDT": _rows("down")})
        assert "UNIUSDT" in mcalls
        assert tr and tr["reason"] == "CRUCE" and tr["exit_mode"] == "cross" and tr["tags"] == ["Cruce EMAs Especial"]
        assert open_n == 0 and sig_n == 0
        assert len(calls) == 1, f"evaluate called {len(calls)} times (expected only BTCUSDT)"
        assert all(s != "UNIUSDT" for s, _ in normal)

    def test_toggle_on_reverses(self, monkeypatch):
        calls = self._patch_eval(monkeypatch)

        async def body(e, db):
            await db.positions.insert_one(_pos_doc(side="long"))
            await e.scan_once(_settings(ema_test_enabled=True, symbols=["BTCUSDT"]))
            return (await db.trades.find_one({"symbol": "UNIUSDT"}), await db.positions.find({"symbol": "UNIUSDT"}).to_list(5))
        tr, pos = run(body, monkeypatch, {"UNIUSDT": _rows("down")})
        assert tr and tr["reason"] == "CRUCE"
        assert len(pos) == 1 and pos[0]["side"] == "short" and pos[0]["exit_mode"] == "cross"
        assert len(calls) == 1

    def test_toggle_off_no_cross_keeps_open(self, monkeypatch):
        async def body(e, db):
            await db.positions.insert_one(_pos_doc(side="long"))
            await e.scan_once(_settings(ema_test_enabled=False, symbols=["BTCUSDT"]))
            return await db.positions.count_documents({"symbol": "UNIUSDT"}), await db.trades.count_documents({})
        open_n, tr_n = run(body, monkeypatch, {"UNIUSDT": _rows("up")})
        assert open_n == 1 and tr_n == 0
