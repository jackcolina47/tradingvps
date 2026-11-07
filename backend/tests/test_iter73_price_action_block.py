# Iter73: FX "Bloqueo por precio extendido" - unit (price_action.facts/hard_block), integration (_handle_signal gate) and API settings
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

import price_action  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ATR = 0.001


def flat(n, start_red_last=True):
    out = []
    for i in range(n):
        green = (i % 2 == 0) if not start_red_last else ((n - 1 - i) % 2 == 1)
        o, c = (1.1000, 1.1001) if green else (1.1001, 1.1000)
        out.append({"open": o, "high": 1.1005, "low": 1.0995, "close": c})
    return out


def run_candles():
    base = flat(30)
    for o in (1.1000, 1.1008, 1.1016):
        base.append({"open": o, "high": o + 0.0009, "low": o - 0.0001, "close": o + 0.0008})
    return base


def swing_candles():
    c = flat(25)
    # peak at 1.1020 then decline to 1.1012, last candle red
    seq = [(1.1000, 1.1008), (1.1008, 1.1015), (1.1015, 1.1018), (1.1018, 1.1016), (1.1016, 1.1014), (1.1014, 1.1013), (1.1013, 1.1012)]
    for i, (o, cl) in enumerate(seq):
        hi = 1.1020 if i == 2 else max(o, cl) + 0.0001
        c.append({"open": o, "high": hi, "low": min(o, cl) - 0.0001, "close": cl})
    return c


# ---------- Unit ----------
class TestPriceActionUnit:
    def test_extended_move_default(self):
        pa = price_action.facts(run_candles(), "long", 1.1024, ATR, max_run=3, swing_atr=1.0)
        assert pa["consecutive_candles"] == 3 and pa["extended_move"] is True
        hb = price_action.hard_block(pa)
        assert hb.startswith("precio extendido:") and "velas grandes" in hb

    def test_extended_move_max4(self):
        pa = price_action.facts(run_candles(), "long", 1.1024, ATR, max_run=4, swing_atr=1.0)
        assert pa["extended_move"] is False
        assert price_action.hard_block(pa) == ""

    def test_near_swing_long(self):
        pa = price_action.facts(swing_candles(), "long", 1.1012, ATR, max_run=3, swing_atr=1.0)
        assert pa["swing_high"] == pytest.approx(1.1020)
        assert pa["near_opposing_swing"] is True, pa
        hb = price_action.hard_block(pa)
        assert hb.startswith("precio extendido:") and "swing contrario" in hb

    def test_near_swing_tight_threshold(self):
        pa = price_action.facts(swing_candles(), "long", 1.1012, ATR, max_run=3, swing_atr=0.25)
        assert pa["near_opposing_swing"] is False
        assert price_action.hard_block(pa) == ""

    def test_short_after_green(self):
        pa = price_action.facts(run_candles(), "short", 1.1024, ATR, max_run=3, swing_atr=1.0)
        assert pa["consecutive_candles"] == 0
        assert price_action.hard_block(pa) == ""

    def test_insufficient_candles(self):
        assert price_action.hard_block(price_action.facts(flat(5), "long", 1.1, ATR)) == ""


# ---------- Integration: _handle_signal gate with fake engine ----------
class _Coll:
    def __init__(self):
        self.docs = []

    async def insert_one(self, d):
        self.docs.append(d)
        class R:
            inserted_id = f"id{len(self.docs)}"
        return R()

    async def update_one(self, *a, **k):
        return None


class _DB:
    def __init__(self):
        self.signals = _Coll()


class _Market:
    tickers = {"EURUSD": {"price": 1.1024, "change_pct": 0.1}}


class _Profile:
    ai_kind = "fx"


def make_engine(SignalsMixin):
    class Fake(SignalsMixin):
        pass
    e = Fake()
    e.signal_keys, e.db, e.market, e.news, e.live, e.profile = set(), _DB(), _Market(), None, False, _Profile()
    e.logs, e.opened = [], []

    async def log(*a, **k): e.logs.append(a)
    async def tg(*a, **k): pass
    async def acc(): return {"equity": 10000, "available": 10000, "daily_pnl_pct": 0}
    async def blocks(*a, **k): return None, None, []
    async def gates(*a, **k): return None, None, 1.0, {}
    async def open_position(*a, **k):
        e.opened.append(a)
        return None, "test-no-open"
    e.log, e.tg, e.account_summary, e._blocks, e._market_gates, e.open_position = log, tg, acc, blocks, gates, open_position
    return e


def run_gate(monkeypatch, settings_kw, horizon="short", side="long", ai_enabled=True):
    from fx import engine_signals as es
    from fx.models import FxSettings
    shadows, ai_ctx = [], []

    async def fake_shadow(engine, sid, symbol, sig, settings, kind, acc, *a, **k):
        shadows.append(kind)

    async def fake_eval(symbol, tf, sig, ctx, kind):
        ai_ctx.append(ctx)
        return {"decision": "RECHAZADA", "confidence": 10, "risk": "ALTO", "reasoning": "test"}

    async def fake_rules(*a, **k): return []
    monkeypatch.setattr(es, "open_shadow", fake_shadow)
    monkeypatch.setattr(es, "evaluate_signal", fake_eval)
    monkeypatch.setattr(es.ai_rules, "active", fake_rules)
    rows = run_candles() + [{"open": 1.1024, "high": 1.1026, "low": 1.1022, "close": 1.1025}]
    df = pd.DataFrame(rows)
    settings = FxSettings(**settings_kw, ai_enabled=ai_enabled)
    sig = {"strategy": "fix_reversion" if horizon == "long" else "trend_ema", "side": side, "price": 1.1024, "sl": 1.1004, "tp": 1.1064,
           "score": 3, "reasons": ["t"], "indicators": {"atr": ATR}, "key_ts": 1, "atr": ATR}
    eng = make_engine(es.SignalsMixin)
    asyncio.run(eng._handle_signal("EURUSD", sig, settings, df, 1, "1h", horizon=horizon))
    return eng, shadows, ai_ctx


class TestHandleSignalGate:
    def test_block_on(self, monkeypatch):
        eng, shadows, ai_ctx = run_gate(monkeypatch, {"price_action_block": True})
        doc = eng.db.signals.docs[0]
        assert doc["ai_decision"] == "PRECIO_EXTENDIDO"
        assert doc["note"].startswith("Bloqueada por precio extendido: precio extendido:")
        assert shadows == ["PRECIO_EXTENDIDO"] and ai_ctx == [] and eng.opened == []

    def test_block_off_goes_to_ai(self, monkeypatch):
        eng, shadows, ai_ctx = run_gate(monkeypatch, {"price_action_block": False})
        assert eng.db.signals.docs[0]["ai_decision"] == "RECHAZADA"
        assert ai_ctx and ai_ctx[0]["price_action"]["extended_move"] is True
        assert shadows == ["RECHAZADA"]

    def test_long_horizon_never_blocked(self, monkeypatch):
        eng, shadows, ai_ctx = run_gate(monkeypatch, {"price_action_block": True}, horizon="long")
        assert eng.db.signals.docs[0]["ai_decision"] != "PRECIO_EXTENDIDO"
        assert "PRECIO_EXTENDIDO" not in shadows

    def test_max4_not_blocked(self, monkeypatch):
        eng, shadows, _ = run_gate(monkeypatch, {"price_action_block": True, "price_action_max_candles": 4})
        assert eng.db.signals.docs[0]["ai_decision"] != "PRECIO_EXTENDIDO"

    def test_kinds_and_blockby(self):
        from fx.shadow import KINDS
        from fx.engine_core import BLOCK_BY
        assert "PRECIO_EXTENDIDO" in KINDS and BLOCK_BY["PRECIO_EXTENDIDO"] == "precio extendido"


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    yield s
    for p in ("fx", "pepper"):
        s.put(f"{BASE_URL}/api/{p}/settings", json={"price_action_block": True, "price_action_max_candles": 3, "price_action_swing_atr": 1.0})


class TestSettingsAPI:
    def test_defaults(self, client):
        for p in ("fx", "pepper"):
            d = client.get(f"{BASE_URL}/api/{p}/settings").json()
            assert d["price_action_block"] is True and d["price_action_max_candles"] == 3 and d["price_action_swing_atr"] == 1.0, (p, d)

    def test_bybit_no_keys(self, client):
        d = client.get(f"{BASE_URL}/api/settings").json()
        assert not [k for k in d if k.startswith("price_action")]

    def test_put_isolation(self, client):
        r = client.put(f"{BASE_URL}/api/pepper/settings", json={"price_action_block": False, "price_action_max_candles": 4, "price_action_swing_atr": 1.5})
        assert r.status_code == 200, r.text
        d = client.get(f"{BASE_URL}/api/pepper/settings").json()
        assert d["price_action_block"] is False and d["price_action_max_candles"] == 4 and d["price_action_swing_atr"] == 1.5
        f = client.get(f"{BASE_URL}/api/fx/settings").json()
        assert f["price_action_block"] is True and f["price_action_max_candles"] == 3 and f["price_action_swing_atr"] == 1.0

    @pytest.mark.parametrize("payload", [{"price_action_max_candles": 1}, {"price_action_max_candles": 6},
                                         {"price_action_swing_atr": 0.2}, {"price_action_swing_atr": 3.5}])
    def test_validation(self, client, payload):
        for p in ("fx", "pepper"):
            assert client.put(f"{BASE_URL}/api/{p}/settings", json=payload).status_code == 422

    def test_restore(self, client):
        for p in ("fx", "pepper"):
            r = client.put(f"{BASE_URL}/api/{p}/settings", json={"price_action_block": True, "price_action_max_candles": 3, "price_action_swing_atr": 1.0})
            assert r.status_code == 200
            d = client.get(f"{BASE_URL}/api/{p}/settings").json()
            assert d["price_action_block"] is True and d["price_action_max_candles"] == 3
