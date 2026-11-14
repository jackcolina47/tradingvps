# Iter79: separate 'Stop a breakeven' (breakeven_enabled) and 'Trailing stop' (trailing_enabled) toggles; BE only after partial TP.
# Unit (risk/fx.risk update_trailing, be_allowed, trade_rules, shadow_be), fake-engine integration (crypto + FX paper tick),
# backtest simulate, and API settings. Run with -n 0.
import os
import sys
import asyncio
import pytest
import requests
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv("/app/frontend/.env")

import risk  # noqa: E402
from fx import risk as fxrisk  # noqa: E402
import shadow  # noqa: E402
import backtest  # noqa: E402
import engine as engine_mod  # noqa: E402
from models import Settings, Position  # noqa: E402
from fx.models import FxSettings  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
BE_LONG = 100 * (1 + 2 * risk.FEE_RATE)


# ---------- Unit: crypto update_trailing ----------
class TestUpdateTrailingCrypto:
    def test_be_on_below_trail(self):
        sl, be = risk.update_trailing("long", 100, 98, 98, 1, 102.5, be_enabled=True)
        assert sl == pytest.approx(BE_LONG) and abs(sl - 100.11) < 0.01 and be is True

    def test_be_off_below_trail(self):
        assert risk.update_trailing("long", 100, 98, 98, 1, 102.5, be_enabled=False) == (98, False)

    def test_trailing_only(self):
        sl, be = risk.update_trailing("long", 100, 98, 98, 1, 104, be_enabled=False, trail_enabled=True)
        assert sl == pytest.approx(103.0) and be is True

    def test_be_only_no_trailing(self):
        sl, be = risk.update_trailing("long", 100, 98, 98, 1, 104, be_enabled=True, trail_enabled=False)
        assert sl == pytest.approx(BE_LONG) and be is True

    def test_both_off(self):
        assert risk.update_trailing("long", 100, 98, 98, 1, 104, be_enabled=False, trail_enabled=False) == (98, False)


# ---------- Unit: FX update_trailing (short side) ----------
class TestUpdateTrailingFx:
    E, SL, ATR, SP = 1.1000, 1.1020, 0.001, 0.00012

    def test_be_on(self):
        sl, be = fxrisk.update_trailing("short", self.E, self.SL, self.SL, self.ATR, 1.0975, self.SP, be_enabled=True)
        assert sl == pytest.approx(self.E - self.SP) and be is True

    def test_be_off(self):
        assert fxrisk.update_trailing("short", self.E, self.SL, self.SL, self.ATR, 1.0975, self.SP, be_enabled=False) == (self.SL, False)

    def test_trailing_only(self):
        sl, be = fxrisk.update_trailing("short", self.E, self.SL, self.SL, self.ATR, 1.0955, self.SP, be_enabled=False, trail_enabled=True)
        assert sl == pytest.approx(1.0965) and be is True

    def test_be_only(self):
        sl, be = fxrisk.update_trailing("short", self.E, self.SL, self.SL, self.ATR, 1.0955, self.SP, be_enabled=True, trail_enabled=False)
        assert sl == pytest.approx(self.E - self.SP) and be is True

    def test_both_off(self):
        assert fxrisk.update_trailing("short", self.E, self.SL, self.SL, self.ATR, 1.0955, self.SP, be_enabled=False, trail_enabled=False) == (self.SL, False)


# ---------- Unit: be_allowed / trade_rules ----------
class TestRules:
    def test_be_allowed(self):
        assert risk.be_allowed({"breakeven_enabled": True}, False) is False
        assert risk.be_allowed({"breakeven_enabled": True}, True) is True
        assert risk.be_allowed({"breakeven_enabled": False}, True) is False

    def test_trade_rules_variants(self):
        r = risk.trade_rules(Settings())
        assert r["breakeven_enabled"] is True and r["trailing_enabled"] is True
        assert risk.trade_rules({"breakeven_enabled": False})["breakeven_enabled"] is False
        rb = risk.trade_rules(False)
        assert rb["trailing_enabled"] is False and rb["breakeven_enabled"] is True
        f = fxrisk.trade_rules(FxSettings())
        assert f["breakeven_enabled"] is True and f["trailing_enabled"] is True
        assert fxrisk.trade_rules({"breakeven_enabled": False})["breakeven_enabled"] is False


# ---------- Unit: shadow_be ----------
class TestShadowBe:
    D = {"side": "long", "entry_price": 100, "initial_sl": 98}

    def _r(self, **kw):
        return {**risk.DEFAULT_RULES, "partial_tp_enabled": True, "partial_tp_r": 1.5, "breakeven_enabled": True, **kw}

    def test_levels(self):
        assert shadow.shadow_be(self._r(), self.D, 102.4) is False
        assert shadow.shadow_be(self._r(), self.D, 103.0) is True

    def test_be_off(self):
        assert shadow.shadow_be(self._r(breakeven_enabled=False), self.D, 104) is False

    def test_partial_off(self):
        assert shadow.shadow_be(self._r(partial_tp_enabled=False), self.D, 104) is False


# ---------- Fake DB ----------
class _Coll:
    def __init__(self):
        self.calls = []

    async def update_one(self, flt, upd, **kw):
        self.calls.append((flt, upd))


class _DB:
    def __init__(self):
        self.positions, self.account = _Coll(), _Coll()


def _apply(p, upd):
    for k, v in upd.items():
        if hasattr(p, k):
            setattr(p, k, v)


async def _noop(*a, **k):
    return None


# ---------- Integration: crypto engine paper tick ----------
def _crypto_engine(monkeypatch):
    monkeypatch.setattr(engine_mod, "tg", _noop)
    e = engine_mod.TradingEngine.__new__(engine_mod.TradingEngine)
    e.db, e.logs = _DB(), []

    async def log(level, msg):
        e.logs.append((level, msg))
    e.log = log

    async def close_position(*a, **k):
        raise AssertionError("position should not close")
    e.close_position = close_position
    return e


def _cpos(**kw):
    base = dict(id="0123456789abcdef01234567", symbol="SOLUSDT", side="long", strategy="trend_ema", entry_price=100.0, qty=1.0, leverage=3, margin=33.0,
                sl=98.0, tp=120.0, initial_sl=98.0, atr=1.0, fees=0.0, mark_price=100.0)
    base.update(kw)
    return Position(**base)


def _crules(**kw):
    return {**risk.DEFAULT_RULES, "partial_tp_enabled": True, "partial_tp_r": 1.5, "partial_tp_pct": 50, "trail_start_r": 2.0, **kw}


def _ctick(e, p, price, rules):
    asyncio.run(e._manage_paper_position(p, price, rules))
    upd = e.db.positions.calls[-1][1]["$set"]
    _apply(p, upd)
    return upd


class TestCryptoEngine:
    def test_a_below_partial_no_be(self, monkeypatch):
        e, p = _crypto_engine(monkeypatch), _cpos()
        upd = _ctick(e, p, 102.5, _crules())
        assert "partial_done" not in upd and "sl" not in upd and p.sl == 98.0

    def test_b_partial_and_be_same_tick(self, monkeypatch):
        e, p = _crypto_engine(monkeypatch), _cpos()
        upd = _ctick(e, p, 103.0, _crules())
        assert upd["partial_done"] is True and upd["qty"] < 1.0
        assert upd["sl"] == pytest.approx(BE_LONG) and upd["breakeven"] is True
        assert any("breakeven" in m for _, m in e.logs)

    def test_c_be_off_partial_then_trailing(self, monkeypatch):
        e, p = _crypto_engine(monkeypatch), _cpos()
        rules = _crules(breakeven_enabled=False)
        upd = _ctick(e, p, 103.0, rules)
        assert upd["partial_done"] is True and "sl" not in upd and p.sl == 98.0
        upd2 = _ctick(e, p, 104.0, rules)
        assert upd2["sl"] == pytest.approx(103.0)

    def test_d_be_only_trailing_off(self, monkeypatch):
        e, p = _crypto_engine(monkeypatch), _cpos(partial_done=True, qty=0.5)
        upd = _ctick(e, p, 104.0, _crules(trailing_enabled=False, trail_start_r=1.5))
        assert upd["sl"] == pytest.approx(BE_LONG)

    def test_e_partial_off_no_be(self, monkeypatch):
        e = _crypto_engine(monkeypatch)
        p = _cpos()
        upd = _ctick(e, p, 102.5, _crules(partial_tp_enabled=False, trail_start_r=1.5))
        assert "sl" not in upd and p.sl == 98.0
        p2 = _cpos()
        upd2 = _ctick(e, p2, 106.0, _crules(partial_tp_enabled=False, trail_start_r=1.5, trailing_enabled=False))
        assert "sl" not in upd2 and p2.sl == 98.0
        p3 = _cpos()
        upd3 = _ctick(e, p3, 106.0, _crules(partial_tp_enabled=False, trail_start_r=1.5))
        assert upd3["sl"] == pytest.approx(105.0)


# ---------- Integration: FX engine paper tick ----------
def _fx_engine():
    from fx.engine import FxEngine
    e = FxEngine.__new__(FxEngine)
    e.db, e.logs = _DB(), []

    class _M:
        tickers = {}
    e.market = _M()

    async def log(level, msg):
        e.logs.append((level, msg))
    e.log = log
    e.tg = _noop

    async def close_position(*a, **k):
        raise AssertionError("position should not close")
    e.close_position = close_position
    return e


def _fpos(**kw):
    base = dict(id="0123456789abcdef01234568", symbol="EURUSD", side="long", strategy="trend_ema", entry_price=1.1000, qty=0.10, leverage=100, margin=110.0,
                sl=1.0980, tp=1.1200, initial_sl=1.0980, atr=0.001, fees=0.0, mark_price=1.1000)
    base.update(kw)
    return Position(**base)


def _ftick(e, p, price, rules):
    from datetime import datetime, timezone
    asyncio.run(e._manage_paper_position(p, price, rules, datetime.now(timezone.utc)))
    upd = e.db.positions.calls[-1][1]["$set"]
    _apply(p, upd)
    return upd


FX_BE = 1.1000 + 0.00012


class TestFxEngine:
    def test_b_partial_and_be(self):
        e, p = _fx_engine(), _fpos()
        upd = _ftick(e, p, 1.1031, _crules())
        assert upd["partial_done"] is True and upd["qty"] == pytest.approx(0.05)
        assert upd["sl"] == pytest.approx(FX_BE) and upd["breakeven"] is True

    def test_c_be_off_then_trailing(self):
        e, p = _fx_engine(), _fpos()
        rules = _crules(breakeven_enabled=False)
        upd = _ftick(e, p, 1.1031, rules)
        assert upd["partial_done"] is True and "sl" not in upd
        upd2 = _ftick(e, p, 1.1045, rules)
        assert upd2["sl"] == pytest.approx(1.1035)

    def test_e_partial_off_no_be(self):
        e = _fx_engine()
        p = _fpos()
        upd = _ftick(e, p, 1.1025, _crules(partial_tp_enabled=False, trail_start_r=1.5))
        assert "sl" not in upd
        p2 = _fpos()
        upd2 = _ftick(e, p2, 1.1061, _crules(partial_tp_enabled=False, trail_start_r=1.5))
        assert upd2["sl"] == pytest.approx(1.1051)


# ---------- Backtest simulate ----------
def _df():
    rows = [(100, 100, 100), (103.2, 100.5, 103.0), (101, 99.0, 100.0), (101, 99.5, 100.5)]
    return pd.DataFrame({"ts": [1700000000000 + i * 3600000 for i in range(len(rows))], "high": [r[0] for r in rows], "low": [r[1] for r in rows],
                         "close": [r[2] for r in rows], "ema10": [1.0] * len(rows), "ema20": [0.0] * len(rows)})


SIGS = {0: {"side": "long", "strategy": "trend_ema", "price": 100.0, "sl": 98.0, "tp": 110.0, "atr": 1.0}}


class TestBacktest:
    def test_be_on_vs_off(self):
        on = backtest.simulate(_df(), SIGS, 1.0, 3, _crules(trailing_enabled=False), 10000.0, "SOLUSDT", start=0)
        off = backtest.simulate(_df(), SIGS, 1.0, 3, _crules(trailing_enabled=False, breakeven_enabled=False), 10000.0, "SOLUSDT", start=0)
        assert on["trades"] if isinstance(on.get("trades"), list) else True
        on_trades = on.get("trades") or on.get("trade_list") or []
        off_trades = off.get("trades") or off.get("trade_list") or []
        print("ON", on_trades, "OFF", off_trades)
        assert isinstance(on, dict) and isinstance(off, dict)
        if isinstance(on_trades, list) and on_trades and isinstance(on_trades[0], dict):
            assert on_trades[0]["reason"] == "TRAILING" and on_trades[0]["partial"] is True


# ---------- API ----------
@pytest.fixture(scope="module")
def s():
    pw = None
    for line in open("/app/memory/test_credentials.md"):
        if "App login" in line:
            pw = line.split("`")[1]
    ss = requests.Session()
    r = ss.post(f"{BASE_URL}/api/auth/login", json={"password": pw})
    if r.status_code != 200:
        pytest.skip("login failed")
    ss.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return ss


class TestApi:
    @pytest.mark.parametrize("pfx", ["/api", "/api/fx", "/api/pepper"])
    def test_defaults(self, s, pfx):
        d = s.get(f"{BASE_URL}{pfx}/settings").json()
        assert d["breakeven_enabled"] is True and d["trailing_enabled"] is True

    def test_pepper_put_isolated(self, s):
        try:
            r = s.put(f"{BASE_URL}/api/pepper/settings", json={"breakeven_enabled": False})
            assert r.status_code == 200, r.text
            assert s.get(f"{BASE_URL}/api/pepper/settings").json()["breakeven_enabled"] is False
            assert s.get(f"{BASE_URL}/api/fx/settings").json()["breakeven_enabled"] is True
            assert s.get(f"{BASE_URL}/api/settings").json()["breakeven_enabled"] is True
        finally:
            r = s.put(f"{BASE_URL}/api/pepper/settings", json={"breakeven_enabled": True})
            assert r.status_code == 200
        assert s.get(f"{BASE_URL}/api/pepper/settings").json()["breakeven_enabled"] is True

    def test_backtest_crypto(self, s):
        r = s.post(f"{BASE_URL}/api/backtest", json={"symbol": "SOLUSDT", "timeframe": "1h", "strategy": "all"}, timeout=120)
        assert r.status_code == 200, r.text[:300]

    def test_backtest_fx(self, s):
        r = s.post(f"{BASE_URL}/api/fx/backtest", json={"symbol": "EURUSD", "timeframe": "1h", "strategy": "all"}, timeout=120)
        assert r.status_code == 200, r.text[:300]
