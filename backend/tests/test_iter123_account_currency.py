"""Iter123: the FX bots show and size in the real account's deposit currency (Pepperstone UK: GBP)."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from fx import risk
from fx.broker import PEPPERSTONE
from test_iter101_pepper_ema_only import mk, quiet  # noqa: F401

TICK = {"GBPUSD": {"price": 1.25}, "EURUSD": {"price": 1.1}, "USDCAD": {"price": 1.4}}


def test_account_usd_rate():
    assert risk.account_usd("USD", TICK) == 1.0 and risk.account_usd("", TICK) == 1.0 and risk.account_usd("gbp", TICK) == 1.25
    assert risk.account_usd("CAD", TICK) == pytest.approx(1 / 1.4) and risk.account_usd("SEK", TICK) == 1.0


def test_sizing_uses_the_account_currency():
    """£10,000 is $12,500: 1 % risk must be £100 (= $125), not $100."""
    usd = risk.position_size(10000, 10000, 1.0, 1.1, 1.095, 30, "EURUSD", TICK)
    fxr = risk.account_usd("GBP", TICK)
    gbp = risk.position_size(10000 * fxr, 10000 * fxr, 1.0, 1.1, 1.095, 30, "EURUSD", TICK)
    assert usd[0] == 0.2 and gbp[0] == 0.25 and gbp[2] == pytest.approx(1.0, abs=0.01)


def test_engine_currency_and_summary():
    class Store:
        def __init__(self, e):
            self.e, self.saved = e, []

        async def update(self, patch):
            self.saved.append(patch)
            self.e.cfg.hfm_currency = patch["hfm_currency"]

    async def run():
        e, db = await mk("ccy", PEPPERSTONE)
        e.cfg = NS(hfm_currency="", trading_mode="paper", hfm_alert_after_sec=180, mt_server="")
        assert e.ccy == "USD" and (await e.account_summary())["currency"] == "USD"
        e.live, e.store = True, Store(e)
        assert e.ccy == "USD"  # nothing verified yet

        async def info():
            return {"balance": 5000.0, "equity": 5050.0, "available": 4900.0, "unrealised": 50.0, "margin": 150.0, "currency": "GBP"}
        e._broker_info = info
        s = await e.account_summary()
        await db.client.drop_database(db.name)
        assert s["currency"] == "GBP" and s["equity"] == 5050.0 and e.store.saved == [{"hfm_currency": "GBP"}] and e.ccy == "GBP"
        e.live = False
        assert e.ccy == "USD"  # paper account is always in USD
    asyncio.run(run())
