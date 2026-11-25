# Iter101: Pepperstone = single fixed strategy «Cruce EMA 10 y 20 Personalizado» (no AI), crypto CFDs 24/7, configurable SL ×ATR, EMA21 touch exit.
import asyncio
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
import pytest
from motor.motor_asyncio import AsyncIOMotorClient
import ai_advisor
import notify
import pandas as pd
from fx import engine_signals
from fx.engine import FxEngine
from fx.broker import HFM, PEPPERSTONE
from fx.models import FxSettings
from fx.symbols import SPECS, AVAILABLE, PEPPER_SYMBOLS, CRYPTO
from fx.backtest import simulate, build_fx_df
from fx import risk
from strategies import ema_test, ema_test_params, EMA_TEST, TOUCH_REASON, CLOSE_EMA_REASON
from indicators import build_df

H = 3600_000


def sig(symbol_price=0.5, atr=0.01, side="long", ts=1):
    d = 1 if side == "long" else -1
    return {"side": side, "strategy": EMA_TEST, "price": symbol_price, "sl": symbol_price - d * 3 * atr, "tp": symbol_price + d * 6 * atr, "score": 3, "reasons": ["cruce"],
            "indicators": {"atr": atr}, "atr": atr, "key_ts": ts, "exit_mode": "cross"}


class FakeMarket:
    def __init__(self):
        self.tickers = {"ADAUSDT": {"price": 0.5}, "SOLUSDT": {"price": 150.0}, "EURUSD": {"price": 1.1}, "XRPUSDT": {"price": 2.0}}

    async def fetch_tickers(self, *a, **k):
        return self.tickers


async def mk(name, profile=PEPPERSTONE, **settings):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[f"test_iter101_{name}"]
    await db.client.drop_database(db.name)
    e = FxEngine(db, profile)
    e.market = FakeMarket()
    e.news = None
    await e.db.settings.insert_one({"_id": "main", **FxSettings(symbols=list(profile.symbols), **settings).model_dump()})
    await e._new_account(10000.0)
    return e, db


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    async def none(*a, **k):
        return None

    async def boom(*a, **k):
        raise AssertionError("no AI call expected in the EMA-only bot")
    monkeypatch.setattr(notify, "notify", none)
    monkeypatch.setattr(ai_advisor, "balance_block", boom)


def test_symbol_lists():
    assert not [s for s in PEPPER_SYMBOLS if "JPY" in s or "CHF" in s]
    assert {"AUDCAD", "AUDNZD", "XAUUSD", "EURUSD"} <= set(PEPPER_SYMBOLS)
    assert sorted(CRYPTO) == sorted(f"{c}USDT" for c in "ADA AVAX BNB DOGE HYPE LINK ONDO SOL SUI XRP NEAR LTC ARB UNI".split()) and not set(CRYPTO) & set(PEPPER_SYMBOLS)  # no crypto CFDs for UK accounts
    assert len(PEPPER_SYMBOLS) == 9
    assert len(AVAILABLE) == 12 and not set(AVAILABLE) & set(CRYPTO) and "USDJPY" in AVAILABLE  # HotForex untouched
    assert tuple(HFM.symbols) == tuple(AVAILABLE) and PEPPERSTONE.ema_only and not HFM.ema_only


def test_settings_forced():
    async def run():
        e, db = await mk("a", ai_enabled=True, ema_test_adx_filter=True, htf_filter=True)
        await e.db.settings.update_one({"_id": "main"}, {"$set": {"symbols": ["USDJPY", "EURUSD", "ADAUSDT"]}})
        s = await e.get_settings()
        await db.client.drop_database(db.name)
        assert s.symbols == ["EURUSD"] == s.ema_test_symbols and s.ema_test_enabled  # JPY and crypto are dropped
        assert not (s.ai_enabled or s.ema_test_adx_filter or s.htf_filter or s.ai_position_review_enabled or s.rules_auto_learn)
    asyncio.run(run())


def test_crypto_opens_on_friday_block_and_forex_does_not(monkeypatch):
    async def run():
        monkeypatch.setattr(engine_signals, "friday_stage", lambda: "block")
        e, db = await mk("b")
        s = await e.get_settings()
        await e._handle_test_signal("ADAUSDT", sig(), s)
        await e._handle_test_signal("EURUSD", sig(1.1, 0.001, ts=2), s)
        pos = await e.db.positions.find({}).to_list(10)
        notes = {d["symbol"]: d.get("note") or "" async for d in e.db.signals.find({})}
        await db.client.drop_database(db.name)
        assert [p["symbol"] for p in pos] == ["ADAUSDT"] and pos[0]["exit_mode"] == "cross" and "viernes" in notes["EURUSD"]
        # exact 1 % risk: 10 000 × 1 % = 100 USD over a 0.03 stop with 1 000 ADA per lot → 3.33 lots
        assert pos[0]["qty"] == pytest.approx(3.33) and pos[0]["sl"] == pytest.approx(0.47)
    asyncio.run(run())


def test_max_positions_now_applies():
    async def run():
        e, db = await mk("c", max_positions=1)
        s = await e.get_settings()
        await e._handle_test_signal("ADAUSDT", sig(), s)
        await e._handle_test_signal("SOLUSDT", sig(150.0, 2.0, ts=2), s)
        n = await e.db.positions.count_documents({})
        note = (await e.db.signals.find_one({"symbol": "SOLUSDT"}))["note"]
        await db.client.drop_database(db.name)
        assert n == 1 and "máximo de posiciones" in note
    asyncio.run(run())


def _row_df(low, high, ema21, ema9=None, ts=10 * H, close=None, prev_close=None):  # default: EMA9 above EMA21 (no opposite cross for a long), close at the high
    row = {"ts": ts, "low": low, "high": high, "close": high if close is None else close, "ema21": ema21, "ema9": ema21 + 0.01 if ema9 is None else ema9}
    return pd.DataFrame([{**row, "close": row["close"] if prev_close is None else prev_close}, row])


def test_close_beyond_ema21_exit_always_on():
    """Pepperstone: a wick through the EMA21 keeps the trade open; a candle CLOSING on the wrong side closes it. No toggle."""
    async def run():
        out = {}
        for side in ("long", "short"):
            e, db = await mk(f"d{side}", ema_touch_exit_enabled=False, timeframe="1h")
            s = await e.get_settings()
            await e._handle_test_signal("EURUSD", sig(1.1, 0.002, side), s)
            opened = datetime.fromtimestamp(10 * H / 1000 + 60, tz=timezone.utc)  # entry 1 min into the 10:00 candle
            await e.db.positions.update_one({}, {"$set": {"opened_at": opened.isoformat()}})
            up = side == "long"
            e9 = 1.11 if up else 1.09   # EMA9 still on the right side of the EMA21 (1.10): no opposite cross
            wrong, right = (1.095, 1.105) if up else (1.105, 1.095)
            await e._exit_on_cross("EURUSD", _row_df(1.09, 1.11, 1.10, e9, ts=9 * H, close=wrong), 1, s)   # closed beyond the EMA21 but before the entry → ignored
            before = await e.db.positions.count_documents({})
            await e._exit_on_cross("EURUSD", _row_df(1.09, 1.11, 1.10, e9, close=right), 1, s)             # wick crossed the EMA21 and came back: stays open
            wick = await e.db.positions.count_documents({})
            await e._exit_on_cross("EURUSD", _row_df(1.09, 1.11, 1.10, e9, close=wrong), 1, s)             # closed beyond the EMA21
            t = await e.db.trades.find_one({})
            out[side] = (before, wick, await e.db.positions.count_documents({}), (t or {}).get("reason"))
            await db.client.drop_database(db.name)
        assert out == {"long": (1, 1, 0, CLOSE_EMA_REASON), "short": (1, 1, 0, CLOSE_EMA_REASON)}
    asyncio.run(run())


def test_hotforex_keeps_its_optional_touch_exit():
    async def run():
        out = {}
        for on in (False, True):
            e, db = await mk(f"h{int(on)}", HFM, ema_touch_exit_enabled=on, ema_test_enabled=True, ema_test_symbols=["EURUSD"], timeframe="1h", sessions_allowed=[], close_before_weekend=False)
            s = await e.get_settings()
            await e.db.positions.insert_one({"symbol": "EURUSD", "side": "long", "strategy": EMA_TEST, "exit_mode": "cross", "entry_price": 1.1, "qty": 0.1, "initial_qty": 0.1, "leverage": 100,
                                             "margin": 110.0, "sl": 1.09, "tp": 1.2, "initial_sl": 1.09, "atr": 0.002, "fees": 0.0, "mark_price": 1.1, "live": False,
                                             "opened_at": datetime.fromtimestamp(10 * H / 1000 + 60, tz=timezone.utc).isoformat()})
            await e._exit_on_cross("EURUSD", _row_df(1.09, 1.11, 1.10, 1.11, close=1.095), 1, s)  # touched AND closed below the EMA21
            t = await e.db.trades.find_one({})
            out[on] = (await e.db.positions.count_documents({}), (t or {}).get("reason"))
            await db.client.drop_database(db.name)
        assert out == {False: (1, None), True: (0, TOUCH_REASON)}
    asyncio.run(run())


def test_sl_multiplier():
    rows, p = [], 100.0
    for i in range(120):
        p += -0.3 if i < 80 else 0.9
        rows.append([i * H, p, p + 0.5, p - 0.5, p, 1])
    df = build_df(rows)
    i = next(k for k in range(60, 119) if ema_test(df, k))
    base, wide = ema_test(df, i), ema_test(df, i, params=ema_test_params(FxSettings(ema_sl_atr=5.0)))
    assert abs(base["price"] - base["sl"]) == pytest.approx(3 * base["atr"]) and abs(wide["price"] - wide["sl"]) == pytest.approx(5 * wide["atr"])
    assert ema_test_params(FxSettings()) == {}


def test_backtest_close_beyond_ema21_and_crypto_costs():
    rows, p = [], 0.5
    for i in range(400):
        p *= 1 + (0.004 if (i // 40) % 2 == 0 else -0.004)
        rows.append([i * H, p, p * 1.003, p * 0.997, p, 100])
    df = build_fx_df(rows)
    from strategies import STRATEGIES
    sigs = {i: s for i in range(60, len(df) - 1) if (s := STRATEGIES[EMA_TEST](df, i, None, None, None))}

    def run(ema_only, touch=False):
        return simulate(df, sigs, 1.0, 100, {**risk.DEFAULT_RULES, "ema_touch_exit_enabled": touch}, initial=10000.0, symbol="ADAUSDT", weekend_flat=False, costs=PEPPERSTONE.costs(), ema_only=ema_only)
    pep, hfm_off, hfm_on = run(True), run(False), run(False, True)
    reasons = lambda r: {t["reason"] for t in r["trades"]}  # noqa: E731
    assert CLOSE_EMA_REASON in reasons(pep) and TOUCH_REASON not in reasons(pep)
    assert hfm_off["trades"] and not {CLOSE_EMA_REASON, TOUCH_REASON} & reasons(hfm_off) and TOUCH_REASON in reasons(hfm_on) and CLOSE_EMA_REASON not in reasons(hfm_on)
    c = PEPPERSTONE.costs("razor")
    assert c.side_cost("ADAUSDT", 1.0, {}) == pytest.approx(SPECS["ADAUSDT"]["spread"] / 2 * 1000) and c.side_cost("EURUSD", 1.0, {}) > 3.5


def test_ema21_closes_have_their_own_telegram_message():
    from types import SimpleNamespace as NS
    sent = []

    async def run():
        e = NS(bk="Pepperstone", ccy="GBP")
        e.log = lambda *a, **k: asyncio.sleep(0)

        async def tg(msg, kind):
            sent.append(msg)
        e.tg = tg
        p = NS(live=False, side="long", symbol="EURUSD", entry_price=1.1)
        from fx.engine_positions import PositionsMixin as M
        await M._announce_close(e, p, NS(pnl=-1.25, pnl_pct=-10.0, exit_price=1.09), "TOQUE_EMA")
        await M._announce_close(e, p, NS(pnl=2.5, pnl_pct=20.0, exit_price=1.12), "CRUCE")
        await M._announce_close(e, p, NS(pnl=-3.0, pnl_pct=-5.0, exit_price=1.09), "CIERRE_EMA21")
    asyncio.run(run())
    assert "toque de la EMA 21" in sent[0] and "Perdió <b>1.25 GBP</b>" in sent[0] and "toque" not in sent[1]
    assert "cierre de vela al otro lado de la EMA 21" in sent[2] and "cerró por debajo de la EMA 21" in sent[2] and "Perdió <b>3.00 GBP</b>" in sent[2]


def test_ema_exits_three_independent_rules():
    from fx import risk
    base = {**risk.DEFAULT_RULES}
    ex = lambda r, price, sl=97.0, done=False, side="long", entry=100.0, be=100.1: risk.ema_exits({**base, **r}, side, entry, 1.0, price, sl, be, done)  # noqa: E731
    assert ex({}, 105) == {"partial": False, "sl": None, "why": ""}                                                        # all off → nothing
    assert ex({"ema_partial_on": True, "ema_partial_at_atr": 2.5}, 102.5)["partial"] and not ex({"ema_partial_on": True, "ema_partial_at_atr": 2.5}, 102.4)["partial"]
    assert ex({"ema_partial_on": True}, 105, done=True)["partial"] is False and ex({"ema_partial_on": True}, 105)["sl"] is None   # partial never moves the stop
    assert ex({"ema_be_on": True, "ema_be_atr": 0.7}, 100.7) == {"partial": False, "sl": 100.1, "why": "breakeven"} and ex({"ema_be_on": True, "ema_be_atr": 0.7}, 100.6)["sl"] is None
    assert ex({"ema_trail_on": True, "ema_trail_atr": 12}, 105)["sl"] is None                                              # wider than the SL: never loosens it
    assert ex({"ema_trail_on": True, "ema_trail_atr": 0.25}, 105) == {"partial": False, "sl": 104.75, "why": "trailing"}
    assert ex({"ema_trail_on": True, "ema_trail_atr": 2}, 101, sl=99.5)["sl"] is None                                      # only tightens
    assert ex({"ema_trail_on": True, "ema_trail_atr": 2, "ema_be_on": True, "ema_be_atr": 1}, 101)["why"] == "breakeven"    # BE (100.1) beats trail (99)
    assert ex({"ema_trail_on": True, "ema_trail_atr": 2}, 95, sl=103.0, side="short", be=99.9) == {"partial": False, "sl": 97.0, "why": "trailing"}
