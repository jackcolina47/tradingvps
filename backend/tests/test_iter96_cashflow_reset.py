# Iter96: after a reset in REAL mode, balance operations older than the reset must not be counted again.
import asyncio
import os
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
import cashflow


def iso(mins):
    return (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()


class Broker:
    def __init__(self, flows):
        self.flows, self.since = flows, None

    async def cash_flows(self, since_ms):
        self.since = since_ms
        return self.flows


def test_old_flows_ignored_after_reset():
    async def run():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])["test_iter96"]
        await db.client.drop_database(db.name)
        await db.account.insert_one({"_id": "main", "initial_balance": 1008.5, "day_start_equity": 1008.5, "net_deposits": 0.0, "peak_equity": 1008.5, "created_at": iso(0)})
        logs = []

        async def log(level, msg):
            logs.append(msg)

        async def tg(text, kind):
            pass
        old = [{"id": "hfm:1", "amount": 560.0, "ts": iso(-60 * 24 * 20).replace("+00:00", "Z"), "note": "Client Demo Deposit"}, {"id": "hfm:2", "amount": -11.21, "ts": iso(-5), "note": "AGG"}]
        e = SimpleNamespace(live=True, broker=Broker(old), db=db, cfg=SimpleNamespace(live_since=iso(-60 * 24 * 30)), log=log, tg=tg, mode_label="REAL")
        assert await cashflow.poll(e, "hfm", force=True, currency="USD") is False
        acc = await db.account.find_one({"_id": "main"})
        assert acc["net_deposits"] == 0 and acc["day_start_equity"] == 1008.5 and not logs and await db.cash_flows.count_documents({}) == 0
        e.broker.flows = old + [{"id": "hfm:3", "amount": 100.0, "ts": iso(1), "note": "new"}]
        assert await cashflow.poll(e, "hfm", force=True, currency="USD") is True
        acc = await db.account.find_one({"_id": "main"})
        await db.client.drop_database(db.name)
        assert acc["net_deposits"] == 100.0 and acc["day_start_equity"] == 1108.5
    asyncio.run(run())
