# Iter74: per-pair ATR params + /backtest params, btc_shock_pct setting, BTC shock unit tests, /performance/pairs ranking (3 bots)
import os
import sys
import time
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv("/app/frontend/.env")

import position_review  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ZEC = {"sl_atr": 1.5, "tp_atr": 6.0, "atr_period": 20.0}


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    r = sess.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    sess.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return sess


# ---------- Settings ----------
class TestSettings:
    def test_symbol_params_and_defaults(self, s):
        d = s.get(f"{BASE_URL}/api/settings").json()
        assert d["symbol_params"]["ZECUSDT"] == ZEC
        assert d["symbol_params"]["HYPEUSDT"] == ZEC
        assert d["btc_shock_pct"] == 1.5
        assert "HYPEUSDT" not in d["symbols"] and "ZECUSDT" in d["symbols"]

    def test_btc_shock_put_validation(self, s):
        try:
            r = s.put(f"{BASE_URL}/api/settings", json={"btc_shock_pct": 2})
            assert r.status_code == 200, r.text
            assert s.get(f"{BASE_URL}/api/settings").json()["btc_shock_pct"] == 2
            assert s.put(f"{BASE_URL}/api/settings", json={"btc_shock_pct": 11}).status_code == 422
            assert s.put(f"{BASE_URL}/api/settings", json={"btc_shock_pct": -1}).status_code == 422
            assert s.get(f"{BASE_URL}/api/settings").json()["btc_shock_pct"] == 2
        finally:
            s.put(f"{BASE_URL}/api/settings", json={"btc_shock_pct": 1.5})
        assert s.get(f"{BASE_URL}/api/settings").json()["btc_shock_pct"] == 1.5

    def test_fx_has_no_btc_shock(self, s):
        for p in ("fx", "pepper"):
            d = s.get(f"{BASE_URL}/api/{p}/settings").json()
            assert "btc_shock_pct" not in d, p


# ---------- Backtest ----------
class TestBacktest:
    def test_zec_params(self, s):
        r = s.post(f"{BASE_URL}/api/backtest", json={"symbol": "ZECUSDT", "strategy": "all", "timeframe": "1h", "risk_per_trade": 1, "leverage": 3}, timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["params"] == ZEC
        for k in ("win_rate", "return_pct"):
            assert isinstance(d[k], (int, float))
        assert d["profit_factor"] is None or isinstance(d["profit_factor"], (int, float))

    def test_sol_no_params(self, s):
        r = s.post(f"{BASE_URL}/api/backtest", json={"symbol": "SOLUSDT", "strategy": "all", "timeframe": "1h", "risk_per_trade": 1, "leverage": 3}, timeout=90)
        assert r.status_code == 200, r.text
        assert r.json()["params"] is None


# ---------- Pair ranking ----------
class TestPairs:
    def test_structure_bybit(self, s):
        st = s.get(f"{BASE_URL}/api/settings").json()
        d = s.get(f"{BASE_URL}/api/performance/pairs?days=30").json()
        assert d["days"] == 30 and {"since", "rows", "total_pnl", "trades"} <= set(d)
        keys = [r["key"] for r in d["rows"]]
        for sym in st["symbols"]:
            assert sym in keys
        by = {r["key"]: r for r in d["rows"]}
        assert by["HYPEUSDT"]["active"] is False
        assert "BNBUSDT" in by and by["BNBUSDT"]["active"] is (("BNBUSDT") in st["symbols"])
        # inactive w/o trades come last
        flags = [(not r["active"] and not r["trades"]) for r in d["rows"]]
        assert flags == sorted(flags)

    def test_days_clamp(self, s):
        assert s.get(f"{BASE_URL}/api/performance/pairs?days=0").json()["days"] == 1
        assert s.get(f"{BASE_URL}/api/performance/pairs?days=9999").json()["days"] == 365

    @pytest.mark.parametrize("p", ["fx", "pepper"])
    def test_fx_pepper(self, s, p):
        r = s.get(f"{BASE_URL}/api/{p}/performance/pairs?days=30")
        assert r.status_code == 200, r.text
        keys = [x["key"] for x in r.json()["rows"]]
        assert "XAUUSD" in keys and not any(k.endswith("USDT") for k in keys)

    def test_aggregation_with_seeded_trades(self, s):
        db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc)
        sym = "HYPEUSDT"
        before = {r["key"]: r for r in s.get(f"{BASE_URL}/api/performance/pairs?days=30").json()["rows"]}[sym]
        docs = [
            {"symbol": sym, "pnl": 10.0, "fees": 0.5, "closed_at": (now - timedelta(days=1)).isoformat(), "TEST_seed": True},
            {"symbol": sym, "pnl": -4.0, "fees": 0.5, "closed_at": (now - timedelta(days=2)).isoformat(), "TEST_seed": True},
            {"symbol": sym, "pnl": 100.0, "fees": 0.5, "closed_at": (now - timedelta(days=40)).isoformat(), "TEST_seed": True},
        ]
        ids = db.trades.insert_many(docs).inserted_ids
        try:
            r = {x["key"]: x for x in s.get(f"{BASE_URL}/api/performance/pairs?days=30").json()["rows"]}[sym]
            assert r["trades"] == before["trades"] + 2
            if before["trades"] == 0:
                assert r["pnl"] == 6.0 and r["win_rate"] == 50.0 and r["profit_factor"] == 2.5 and r["fees"] == 1.0
            r90 = {x["key"]: x for x in s.get(f"{BASE_URL}/api/performance/pairs?days=90").json()["rows"]}[sym]
            assert r90["trades"] >= 3
        finally:
            db.trades.delete_many({"_id": {"$in": ids}})


# ---------- Unit: btc_shock ----------
def kl(closes):
    return [[i * 900000, c, c, c, c, 1] for i, c in enumerate(closes)]


def fake_engine(n=1, closes=None, running=True):
    e = SimpleNamespace(running=running, _btc_shock_last=0.0)
    e.db = SimpleNamespace(positions=SimpleNamespace(count_documents=AsyncMock(return_value=n)))
    e.market = SimpleNamespace(fetch_klines=AsyncMock(return_value=kl(closes or [100] * 6)))
    return e


def st(**kw):
    base = dict(btc_shock_pct=1.5, ai_position_review_enabled=True, ai_enabled=True)
    base.update(kw)
    return SimpleNamespace(**base)


run = lambda c: asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


class TestBtcShockUnit:
    def test_drop_30m(self):
        e = fake_engine(closes=[100, 100, 100, 100, 100, 98])
        out = run(position_review.btc_shock(e, st()))
        assert out.startswith("ALERTA BTC: Bitcoin ha caído") and e._btc_shock_last > 0

    def test_rise_1h(self):
        # 1h +1.6%, 30m < 1.5
        e = fake_engine(closes=[100, 100, 100, 101, 101.2, 101.6])
        out = run(position_review.btc_shock(e, st()))
        assert "ha subido" in out

    def test_below_threshold(self):
        assert run(position_review.btc_shock(fake_engine(closes=[100, 100, 100, 100, 100, 101]), st())) == ""

    def test_no_alts(self):
        e = fake_engine(n=0, closes=[100] * 5 + [95])
        assert run(position_review.btc_shock(e, st())) == ""
        e.market.fetch_klines.assert_not_called()

    def test_off_and_disabled(self):
        c = [100] * 5 + [95]
        assert run(position_review.btc_shock(fake_engine(closes=c), st(btc_shock_pct=0))) == ""
        assert run(position_review.btc_shock(fake_engine(closes=c), st(ai_position_review_enabled=False))) == ""
        assert run(position_review.btc_shock(fake_engine(closes=c), st(ai_enabled=False))) == ""
        assert run(position_review.btc_shock(fake_engine(closes=c, running=False), st())) == ""

    def test_cooldown(self):
        e = fake_engine(closes=[100] * 5 + [95])
        e._btc_shock_last = time.time() - 600
        assert run(position_review.btc_shock(e, st())) == ""
        e._btc_shock_last = time.time() - 31 * 60
        assert run(position_review.btc_shock(e, st())) != ""


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n):
        return self.docs


class TestReviewTrigger:
    def test_trigger_filters_btc_and_passes_line(self):
        from bson import ObjectId
        def pos(sym):
            return {"_id": ObjectId(), "symbol": sym, "side": "long", "entry_price": 100.0, "qty": 1.0, "sl": 95.0, "tp": 120.0,
                    "initial_sl": 95.0, "strategy": "ema", "opened_at": datetime.now(timezone.utc).isoformat(), "atr": 1.0, "leverage": 3, "margin": 33.0, "fees": 0.05, "mark_price": 100.0}
        docs = [pos("BTCUSDT"), pos("ZECUSDT"), pos("SOLUSDT")]
        candles = [[i * 3600000, 100, 101, 99, 100, 1] for i in range(61)]
        logs = []
        e = SimpleNamespace(running=True)
        e.db = SimpleNamespace(positions=SimpleNamespace(find=lambda *a, **k: _Cursor(docs), update_one=AsyncMock()),
                               ai_reviews=SimpleNamespace(update_one=AsyncMock()))
        e.market = SimpleNamespace(fetch_klines=AsyncMock(return_value=candles))
        e.log = AsyncMock(side_effect=lambda lvl, msg: logs.append((lvl, msg)))
        e.close_position = AsyncMock()
        sett = SimpleNamespace(timeframe="1h", ai_position_review_mode="notify", ai_position_review_min_confidence=70)
        review = AsyncMock(return_value={"action": "MANTENER", "confidence": 60, "reasoning": "ok", "r_now": 0.0})
        trig = "ALERTA BTC: Bitcoin ha caído un -2.00 % en la última hora."
        with patch.object(position_review.ai_advisor, "review_position", review), patch.object(position_review, "tg", AsyncMock()) as tg:
            out = run(position_review.review_open_positions(e, sett, "USDT", "Bybit", trigger=trig))
        assert [o["symbol"] for o in out] == ["ZECUSDT", "SOLUSDT"]
        assert review.await_count == 2
        for c in review.await_args_list:
            assert c.args[-1].startswith(trig)
        assert any(trig in m and "2 altcoin" in m for _, m in logs)
        assert tg.await_count >= 1 and trig in tg.await_args_list[0].args[0]
        saved = e.db.ai_reviews.update_one.await_args.args[1]["$set"]
        assert saved["trigger"] == trig
