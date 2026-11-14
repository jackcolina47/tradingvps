# Iter82: ema_test immune to AI review, 3×ATR safety SL (paper/real/backtest) and 'pips' on trades/positions (+ API).
# Run with -n 0. Engine tests run on an ISOLATED Mongo db (dropped at end, via iter80 helpers).
import os
import sys
import math
import pytest
import requests
from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_iter80_ema_test import run, _rows, _settings, _pos_doc, _noop, _flat, _alternating_df, TEST_DB, MONGO_URL  # noqa: E402
import test_iter80_ema_test as it80  # noqa: E402
import strategies as st  # noqa: E402
import backtest  # noqa: E402
import position_review  # noqa: E402
import ai_advisor  # noqa: E402
from indicators import build_df  # noqa: E402
from models import pips_for, Trade, Position  # noqa: E402
from risk import DEFAULT_RULES  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
teardown_module = it80.teardown_module


# ---------- (a) position_review immunity ----------
class TestReviewImmunity:
    def test_only_cross_no_llm(self, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("LLM review called for a cross position")
        monkeypatch.setattr(ai_advisor, "review_position", boom)
        monkeypatch.setattr(position_review, "tg", _noop)

        async def body(e, db):
            await db.positions.insert_one(_pos_doc())
            return await position_review.review_open_positions(e, _settings(ai_position_review_mode="auto"), "USDT", "Bybit"), e.market.calls
        out, calls = run(body, monkeypatch, {"UNIUSDT": _flat(), "BTCUSDT": _flat()})
        assert out == [] and calls == []

    def test_cross_plus_normal_only_normal_reviewed(self, monkeypatch):
        seen = []

        async def review(pos, *a, **k):
            seen.append(pos["symbol"])
            return {"action": "MANTENER", "confidence": 90, "reasoning": "test", "r_now": 0.1}
        monkeypatch.setattr(ai_advisor, "review_position", review)
        monkeypatch.setattr(position_review, "tg", _noop)

        async def body(e, db):
            await db.positions.insert_one(_pos_doc())
            await db.positions.insert_one(_pos_doc(symbol="SOLUSDT", strategy="trend_ema", exit_mode=None, price=100.0))
            return await position_review.review_open_positions(e, _settings(ai_position_review_mode="auto"), "USDT", "Bybit")
        out = run(body, monkeypatch, {"UNIUSDT": _flat(), "SOLUSDT": _flat(), "BTCUSDT": _flat()})
        assert seen == ["SOLUSDT"] and len(out) == 1 and out[0]["symbol"] == "SOLUSDT"


# ---------- (b) strategy safety SL ----------
class TestStrategySafetySL:
    @pytest.mark.parametrize("direction,side", [("up", "long"), ("down", "short")])
    def test_sl_is_3_atr(self, direction, side):
        df = build_df(_rows(direction)); i = len(df) - 2
        s = st.ema_test(df, i)
        a = df.iloc[i]
        sl, tp = st._atr_levels(a, side == "long", None, st.SAFETY_SL_ATR, 6.0)
        assert st.SAFETY_SL_ATR == 3.0 and s["side"] == side
        assert s["sl"] == pytest.approx(sl) and abs(s["price"] - s["sl"]) == pytest.approx(3 * float(a["atr"]), rel=1e-6)
        assert "SL de seguridad" in s["reasons"][1]


# ---------- (c) paper monitor ----------
def _cross_pos(side):
    sl = 7.5 if side == "long" else 8.5
    return Position(symbol="UNIUSDT", side=side, strategy="ema_test", entry_price=8.0, qty=1.0, initial_qty=1.0, leverage=3, margin=10,
                    sl=sl, tp=9.0 if side == "long" else 7.0, initial_sl=sl, atr=0.1, fees=0.0, mark_price=8.0, exit_mode="cross").to_mongo()


class TestPaperMonitor:
    @pytest.mark.parametrize("side,hit,miss,far", [("long", 7.4, 7.6, 9.0), ("short", 8.6, 8.4, 7.0)])
    def test_safety_sl(self, monkeypatch, side, hit, miss, far):
        async def body(e, db):
            res = await db.positions.insert_one(_cross_pos(side))
            p = Position.from_mongo(await db.positions.find_one({"_id": res.inserted_id}))
            closed = []

            async def close(pid, price=None, reason=None, *a, **k):
                closed.append((pid, price, reason))
            e.close_position = close
            await e._manage_paper_position(p, miss, dict(DEFAULT_RULES))
            d_miss = await db.positions.find_one({"_id": res.inserted_id})
            await e._manage_paper_position(p, far, dict(DEFAULT_RULES))  # beyond virtual TP (10×ATR) → not closed
            d_far = await db.positions.find_one({"_id": res.inserted_id})
            n_before = len(closed)
            await e._manage_paper_position(p, hit, dict(DEFAULT_RULES))
            return closed, n_before, d_miss, d_far, e.logs, p
        closed, n_before, d_miss, d_far, logs, p = run(body, monkeypatch, {})
        assert n_before == 0
        exp_u = (miss - 8.0) if side == "long" else (8.0 - miss)
        assert d_miss["mark_price"] == miss and d_miss["unrealized_pnl"] == pytest.approx(exp_u, abs=1e-4)
        assert d_far["mark_price"] == far and not d_far.get("partial_done") and not d_far.get("breakeven")
        assert closed == [(p.id, p.sl, "SL")]
        assert any(lv == "RISK" and "SL de seguridad" in m for lv, m in logs)


# ---------- (d) real path ----------
class TestRealPath:
    def test_broker_gets_sl_not_tp(self, monkeypatch):
        async def body(e, db):
            async def size(*a, **k):
                return {"qty": 1.0}
            e.live_size = size
            e.market.tickers.update({"UNIUSDT": {"price": 10.0}})
            await e.open_position("UNIUSDT", {"strategy": "ema_test", "side": "long", "price": 10.0, "sl": 9.4, "tp": 11.2, "atr": 0.2},
                                  _settings(), None, exit_mode="cross")
            return e.broker.opens, await db.positions.find_one({"symbol": "UNIUSDT"})
        opens, pos = run(body, monkeypatch, {}, live=True)
        o = opens[0]
        assert isinstance(o["sl"], float) and o["tp"] is None
        # sl shifted by the slippage-adjusted entry, distance preserved (0.6)
        assert pos["entry_price"] - o["sl"] == pytest.approx(0.6, abs=1e-6) and pos["sl"] == pytest.approx(o["sl"])


# ---------- (e) backtest ----------
def _violent_df(n=900):
    rows, t0 = [], 1_700_000_000_000
    for k in range(n):
        p = 10 + 2 * math.sin(k / 15.0)
        if k % 150 == 120:  # violent bar against whatever position: huge range both ways
            rows.append([t0 + k * 300_000, p, p * 1.4, p * 0.6, p, 1000.0])
        else:
            rows.append([t0 + k * 300_000, p, p * 1.003, p * 0.997, p, 1000.0])
    return build_df(rows)


class TestBacktest:
    def test_violent_has_sl_no_tp(self):
        r = backtest.run_backtest(_violent_df(), "ema_test", 1, 3, True, 10000.0, "UNIUSDT")
        reasons = [t["reason"] for t in r["trades"]]
        assert "SL" in reasons and "TP" not in reasons and set(reasons) <= {"SL", "CRUCE"}, reasons

    def test_smooth_all_cross(self):
        r = backtest.run_backtest(_alternating_df(), "ema_test", 1, 3, True, 10000.0, "UNIUSDT")
        assert r["total_trades"] > 3 and {t["reason"] for t in r["trades"]} == {"CRUCE"}


# ---------- (f) pips ----------
class TestPips:
    def test_pips_for(self):
        assert pips_for("EURUSD", "long", 1.0850, 1.0872) == 22.0
        assert pips_for("XAUUSD", "short", 4000, 4012.5) == -125.0
        assert pips_for("UNIUSDT", "long", 8.0, 8.12) == 150.0
        assert pips_for("UNIUSDT", "short", 8.0, 8.12) == -150.0
        assert pips_for("UNIUSDT", "long", 0, 8.12) is None

    def _trade(self, **kw):
        d = dict(_id=ObjectId(), symbol="EURUSD", side="long", strategy="trend_ema", entry_price=1.0850, exit_price=1.0872, qty=1, leverage=30,
                 margin=10, pnl=22, pnl_pct=2, fees=0, reason="TP", opened_at="2026-01-01T00:00:00+00:00")
        d.update(kw)
        return d

    def test_trade_without_pips_computed(self):
        assert Trade.from_mongo(self._trade()).pips == 22.0

    def test_trade_explicit_pips_kept(self):
        assert Trade.from_mongo(self._trade(pips=-3.3)).pips == -3.3

    def test_position_pips_from_mark(self):
        p = Position.from_mongo({**_cross_pos("long"), "_id": ObjectId(), "mark_price": 8.12, "pips": 999})
        assert p.pips == 150.0
        p2 = Position.from_mongo({**_cross_pos("short"), "_id": ObjectId(), "mark_price": 8.12})
        assert p2.pips == -150.0


# ---------- API ----------
@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


class TestApi:
    def test_fx_dashboard_pips(self, api):
        r = api.get(f"{BASE_URL}/api/fx/dashboard", timeout=60)
        assert r.status_code == 200
        d = r.json()
        trades = d.get("trades") or []
        assert trades and all("pips" in t for t in trades)
        by = {(t["symbol"], round(t["pnl"], 2)): t["pips"] for t in trades}
        print("fx trades", by)
        assert any(s == "XAUUSD" and v == -50.0 for (s, _), v in by.items())
        assert any(s == "EURUSD" and v == 20.0 for (s, _), v in by.items())
        assert all("pips" in p for p in d.get("positions") or [])

    def test_crypto_dashboard(self, api):
        r = api.get(f"{BASE_URL}/api/dashboard", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert all("pips" in t for t in d.get("trades") or [])
        assert all("pips" in p for p in d.get("positions") or [])

    def test_trades_endpoint(self, api):
        r = api.get(f"{BASE_URL}/api/trades", timeout=30)
        print("trades status", r.status_code)
        if r.status_code == 404:
            pytest.skip("/api/trades not present")
        assert r.status_code == 200
        d = r.json()
        items = d if isinstance(d, list) else d.get("trades") or d.get("items") or []
        assert all("pips" in t for t in items)

    def test_fx_trades_endpoint(self, api):
        r = api.get(f"{BASE_URL}/api/fx/trades", timeout=30)
        if r.status_code == 404:
            pytest.skip("/api/fx/trades not present")
        assert r.status_code == 200
        d = r.json()
        items = d if isinstance(d, list) else d.get("trades") or d.get("items") or []
        assert items and all("pips" in t for t in items)

    def test_backtest_ema_test(self, api):
        r = api.post(f"{BASE_URL}/api/backtest", json={"symbol": "UNIUSDT", "strategy": "ema_test", "timeframe": "5m"}, timeout=120)
        assert r.status_code == 200, r.text[:300]
        reasons = {t["reason"] for t in r.json().get("trades") or []}
        print("bt reasons", reasons)
        assert reasons and reasons <= {"CRUCE", "SL"}
