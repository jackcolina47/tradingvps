# Iter77: 3-stage AI signal funnel - unit (ai_funnel, ai_advisor._num, evaluate_signal adjusted_sl/tp), fake-engine integration
# (crypto engine._handle_signal + fx engine_signals._handle_signal) and API settings/config. Run with -n 0.
import os
import sys
import asyncio
import types
import pytest
import requests
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv("/app/frontend/.env")

import ai_funnel  # noqa: E402
import ai_advisor  # noqa: E402
from fx import ai as fx_ai  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


# ---------- Unit: pre_filter ----------
class TestPreFilter:
    def test_rr_low(self):
        assert ai_funnel.pre_filter({"price": 100, "sl": 99, "tp": 101}, 1.5) == ("R:R 1.00 menor al mínimo 1.50", "RR_BAJO")

    def test_rr_ok(self):
        assert ai_funnel.pre_filter({"price": 100, "sl": 99, "tp": 102}, 1.5) == (None, None)

    def test_rr_disabled(self):
        assert ai_funnel.pre_filter({"price": 100, "sl": 99, "tp": 100.5}, 0) == (None, None)

    def test_spread_block(self):
        r, k = ai_funnel.pre_filter({"price": 1.1, "sl": 1.099, "tp": 1.103}, 1.5, 0.0003, 25)
        assert k == "SPREAD" and r.startswith("spread 30 %")

    def test_spread_none(self):
        assert ai_funnel.pre_filter({"price": 1.1, "sl": 1.099, "tp": 1.103}, 1.5, None, 25) == (None, None)

    def test_spread_ok(self):
        assert ai_funnel.pre_filter({"price": 1.1, "sl": 1.099, "tp": 1.103}, 1.5, 0.0002, 25) == (None, None)


# ---------- Unit: fast_cfg ----------
class TestFastCfg:
    @pytest.mark.parametrize("src,prov,exp", [("emergent", "anthropic", "claude-haiku-4-5-20251001"), ("emergent", "openai", "gpt-5.4-mini"),
                                              ("emergent", "gemini", "gemini-2.5-flash"), ("gemini", "gemini", "gemini-2.5-flash-lite")])
    def test_auto(self, monkeypatch, src, prov, exp):
        monkeypatch.setattr(ai_funnel, "_cfg", {**ai_funnel._cfg, "source": src, "provider": prov})
        assert ai_funnel.fast_cfg()["model"] == exp

    def test_override(self, monkeypatch):
        monkeypatch.setattr(ai_funnel, "_cfg", {**ai_funnel._cfg, "source": "emergent", "provider": "anthropic"})
        assert ai_funnel.fast_cfg("claude-sonnet-4-6")["model"] == "claude-sonnet-4-6"


SIG = {"side": "long", "strategy": "trend_ema", "price": 100.0, "sl": 98.0, "tp": 104.0, "score": 4, "reasons": ["a", "b"],
       "indicators": {"rsi": 55, "atr": 1.0}}
CTX = {"htf_tf": "4h", "htf_trend": "ALCISTA", "change_24h": 1.2, "recent_changes": [0.1, -0.2]}


def _patch_ask(monkeypatch, mod, ret=None, exc=None):
    async def fake(chat, prompt):
        if exc:
            raise exc
        return ret
    monkeypatch.setattr(mod, "_ask", fake)
    monkeypatch.setattr(mod, "_chat", lambda *a, **k: types.SimpleNamespace())


# ---------- Unit: quick_filter / final_eval ----------
class TestQuickFilter:
    def test_pass(self, monkeypatch):
        _patch_ask(monkeypatch, ai_funnel, '{"pass_filter":true,"confidence":80,"discard_reason":""}')
        r = asyncio.run(ai_funnel.quick_filter("SOLUSDT", "1h", SIG, CTX, "crypto"))
        assert r["pass"] is True and r["confidence"] == 80 and isinstance(r["ms"], int) and r["model"]

    def test_low_conf(self, monkeypatch):
        _patch_ask(monkeypatch, ai_funnel, '{"pass_filter":true,"confidence":50,"discard_reason":""}')
        assert asyncio.run(ai_funnel.quick_filter("SOLUSDT", "1h", SIG, CTX, "crypto"))["pass"] is False

    def test_threshold_65(self, monkeypatch):
        _patch_ask(monkeypatch, ai_funnel, '{"pass_filter":true,"confidence":65,"discard_reason":""}')
        assert asyncio.run(ai_funnel.quick_filter("SOLUSDT", "1h", SIG, CTX, "crypto"))["pass"] is True

    def test_pass_false(self, monkeypatch):
        _patch_ask(monkeypatch, ai_funnel, '{"pass_filter":false,"confidence":90,"discard_reason":"contra tendencia"}')
        r = asyncio.run(ai_funnel.quick_filter("SOLUSDT", "1h", SIG, CTX, "crypto"))
        assert r["pass"] is False and r["reason"] == "contra tendencia"

    @pytest.mark.parametrize("exc", [asyncio.TimeoutError(), RuntimeError("boom")])
    def test_errors_propagate(self, monkeypatch, exc):
        _patch_ask(monkeypatch, ai_funnel, exc=exc)
        with pytest.raises(type(exc)):
            asyncio.run(ai_funnel.quick_filter("SOLUSDT", "1h", SIG, CTX, "crypto"))

    def test_final_eval(self):
        async def ev(symbol, tf, sig, ctx):
            await asyncio.sleep(0.05)
            return {"decision": "APROBADA", "confidence": 77, "risk": "BAJO", "reasoning": "ok"}
        r = asyncio.run(ai_funnel.final_eval(ev, "SOLUSDT", "1h", SIG, CTX))
        assert r["decision"] == "APROBADA" and r["confidence"] == 77 and r["ms"] >= 40 and r["model"]


# ---------- Unit: _num / evaluate_signal adjusted fields ----------
class TestAdvisor:
    def test_num(self):
        assert ai_advisor._num("1.2") == 1.2 and ai_advisor._num(3) == 3.0
        assert ai_advisor._num(None) is None and ai_advisor._num("null") is None and ai_advisor._num("x") is None

    @pytest.mark.parametrize("mod,args", [(ai_advisor, ()), (fx_ai, ("fx",))])
    def test_eval_adjusted(self, monkeypatch, mod, args):
        assert "adjusted_sl" in mod.SYSTEM and "adjusted_tp" in mod.SYSTEM
        _patch_ask(monkeypatch, mod, '{"decision":"APROBAR","confidence":70,"risk":"BAJO","reasoning":"x","adjusted_sl":"97.5","adjusted_tp":null}')
        ctx = {**CTX, "sessions": [], "open_positions": 0, "daily_pnl_pct": 0}
        sym = "SOLUSDT" if mod is ai_advisor else "EURUSD"
        r = asyncio.run(mod.evaluate_signal(sym, "1h", SIG, ctx, *args))
        assert r["decision"] == "APROBADA" and r["adjusted_sl"] == 97.5 and r["adjusted_tp"] is None


# ---------- Integration: fake engines ----------
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


def flat(n):
    return [{"open": 1.1000 + (i % 2) * 1e-4, "high": 1.1005, "low": 1.0995, "close": 1.1001 - (i % 2) * 1e-4} for i in range(n)]


def _patches(monkeypatch, mod, fast=None, fast_exc=None, ev=None):
    calls = {"fast": 0, "eval": 0, "shadows": []}

    async def fake_shadow(engine, sid, symbol, sig, settings, kind, *a, **k):
        calls["shadows"].append(kind)

    async def fake_quick(*a, **k):
        calls["fast"] += 1
        if fast_exc:
            raise fast_exc
        return fast

    async def fake_eval(*a, **k):
        calls["eval"] += 1
        return dict(ev or {"decision": "RECHAZADA", "confidence": 10, "risk": "ALTO", "reasoning": "t"})

    async def fake_rules(*a, **k): return []
    monkeypatch.setattr(mod, "open_shadow", fake_shadow)
    monkeypatch.setattr(mod, "evaluate_signal", fake_eval)
    monkeypatch.setattr(mod.ai_funnel, "quick_filter", fake_quick)
    monkeypatch.setattr(mod.ai_rules, "active", fake_rules)
    return calls


def run_fx(monkeypatch, sl, tp, horizon="short", funnel=True, **kw):
    from fx import engine_signals as es
    from fx.models import FxSettings
    calls = _patches(monkeypatch, es, **kw)

    class Fake(es.SignalsMixin):
        pass
    e = Fake()
    e.signal_keys, e.db, e.news, e.live = set(), _DB(), None, False
    e.market = types.SimpleNamespace(tickers={"EURUSD": {"price": 1.1, "change_pct": 0.1}})
    e.profile, e.cfg, e.opened = types.SimpleNamespace(ai_kind="fx"), types.SimpleNamespace(ai_fast_model=""), []

    async def noop(*a, **k): pass
    async def acc(): return {"equity": 10000, "available": 10000, "daily_pnl_pct": 0}
    async def blocks(*a, **k): return None, None, []
    async def gates(*a, **k): return None, None, 1.0, {}
    async def open_position(*a, **k):
        e.opened.append(a)
        return {"id": "p"}, ""
    e.log, e.tg, e.account_summary, e._blocks, e._market_gates, e.open_position = noop, noop, acc, blocks, gates, open_position
    settings = FxSettings(ai_enabled=True, ai_funnel_enabled=funnel, price_action_block=False)
    sig = {"strategy": "fix_reversion" if horizon == "long" else "trend_ema", "side": "long", "price": 1.1, "sl": sl, "tp": tp,
           "score": 3, "reasons": ["t"], "indicators": {"atr": 0.001}, "key_ts": 1, "atr": 0.001}
    asyncio.run(e._handle_signal("EURUSD", sig, settings, pd.DataFrame(flat(40)), 1, "4h", horizon=horizon))
    return e, calls


def run_crypto(monkeypatch, sl, tp, horizon="short", funnel=True, **kw):
    import engine as ce
    from models import Settings
    calls = _patches(monkeypatch, ce, **kw)

    async def noop(*a, **k): pass
    monkeypatch.setattr(ce, "tg", noop)
    e = ce.TradingEngine.__new__(ce.TradingEngine)
    e.signal_keys, e.db, e.news, e.live, e.opened = set(), _DB(), None, False, []
    e.market = types.SimpleNamespace(tickers={"SOLUSDT": {"price": 100.0, "change_pct": 0.5}})
    e.cfg = types.SimpleNamespace(ai_fast_model="", funding_enabled=False)
    e.bybit = {"down_since": None}

    async def acc(): return {"equity": 500, "available": 500, "daily_pnl_pct": 0, "daily_paused": False}
    async def oap(*a, **k): return []
    async def open_position(*a, **k):
        e.opened.append(a)
        return {"id": "p"}
    e.log, e.account_summary, e._open_and_pending, e.open_position = noop, acc, oap, open_position
    settings = Settings(ai_enabled=True, ai_funnel_enabled=funnel, btc_regime_filter=False, rotation_enabled=False, entry_order_type="market")
    rows = [{"open": 100, "high": 101, "low": 99, "close": 100 + (i % 2) * 0.1, "volume": 1} for i in range(40)]
    sig = {"strategy": "multi_horizon" if horizon == "long" else "trend_ema", "side": "long", "price": 100.0, "sl": sl, "tp": tp,
           "score": 3, "reasons": ["t"], "indicators": {"atr": 1.0}, "key_ts": 1, "atr": 1.0}
    asyncio.run(e._handle_signal("SOLUSDT", sig, settings, pd.DataFrame(rows), 1, "4h", horizon=horizon))
    return e, calls


FAIL = {"pass": False, "confidence": 40, "reason": "ruido", "model": "Claude Haiku 4.5", "ms": 300}
PASS = {"pass": True, "confidence": 85, "reason": "", "model": "Claude Haiku 4.5", "ms": 300}
APPROVE = {"decision": "APROBADA", "confidence": 80, "risk": "BAJO", "reasoning": "ok", "adjusted_sl": 1.0985, "adjusted_tp": None}


@pytest.fixture(params=["fx", "crypto"])
def runner(request):
    if request.param == "fx":
        return request.param, lambda mp, rr=2.0, **kw: run_fx(mp, 1.098, 1.1 + 0.002 * rr, **kw)
    return request.param, lambda mp, rr=2.0, **kw: run_crypto(mp, 98.0, 100.0 + 2.0 * rr, **kw)


class TestFunnelIntegration:
    def test_a_rr_low(self, monkeypatch, runner):
        _, run = runner
        e, c = run(monkeypatch, rr=1.0, fast=PASS)
        d = e.db.signals.docs[0]
        assert d["ai_decision"] == "RR_BAJO" and d["note"].startswith("Bloqueada por R:R insuficiente"), d
        assert c["shadows"] == ["RR_BAJO"] and c["eval"] == 0 and c["fast"] == 0

    def test_b_spread_fx(self, monkeypatch):
        e, c = run_fx(monkeypatch, 1.1 - 0.0004, 1.1 + 0.0012, fast=PASS)
        d = e.db.signals.docs[0]
        assert d["ai_decision"] == "SPREAD" and d["note"].startswith("Bloqueada por spread"), d
        assert c["shadows"] == ["SPREAD"] and c["eval"] == 0

    def test_c_fast_reject(self, monkeypatch, runner):
        _, run = runner
        e, c = run(monkeypatch, fast=FAIL)
        d = e.db.signals.docs[0]
        assert d["ai_decision"] == "RECHAZADA" and d["ai_stage"] == 1 and d["ai_reasoning"].startswith("[Filtro rápido]")
        assert d["ai_model"] == "Claude Haiku 4.5" and d["ai_ms"] == 300
        assert c["shadows"] == ["FILTRO_IA"] and c["eval"] == 0 and e.opened == []

    def test_d_fast_error(self, monkeypatch, runner):
        _, run = runner
        e, c = run(monkeypatch, fast_exc=RuntimeError("provider down"))
        d = e.db.signals.docs[0]
        assert d["ai_decision"] == "ERROR" and c["shadows"] == ["ERROR"] and c["eval"] == 0 and e.opened == []

    def test_e_pass_and_approve(self, monkeypatch, runner):
        name, run = runner
        e, c = run(monkeypatch, fast=PASS, ev=APPROVE)
        d = e.db.signals.docs[0]
        assert d["ai_decision"] == "APROBADA" and d["ai_stage"] == 2 and d["ai_adjusted_sl"] == 1.0985 and d["ai_adjusted_tp"] is None
        assert d["ai_ms"] >= 300 and d["ai_model"] and c["fast"] == 1 and c["eval"] == 1 and len(e.opened) == 1

    def test_f_funnel_off(self, monkeypatch, runner):
        _, run = runner
        e, c = run(monkeypatch, funnel=False, fast=FAIL)
        d = e.db.signals.docs[0]
        assert c["fast"] == 0 and c["eval"] == 1 and d["ai_stage"] == 2

    def test_g_long_horizon(self, monkeypatch, runner):
        _, run = runner
        e, c = run(monkeypatch, rr=1.0, horizon="long", fast=FAIL)
        d = e.db.signals.docs[0]
        assert d["ai_decision"] != "RR_BAJO" and c["fast"] == 0 and c["eval"] == 1 and d["ai_stage"] == 2

    def test_kinds(self):
        import shadow
        from fx import shadow as fxs
        import engine as ce
        from fx.engine_core import BLOCK_BY
        for k in ("RR_BAJO", "SPREAD", "FILTRO_IA"):
            assert k in shadow.KINDS and k in fxs.KINDS
        for k in ("RR_BAJO", "SPREAD"):
            assert k in ce.BLOCK_BY and k in BLOCK_BY


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    yield s
    for p in ("", "/fx", "/pepper"):
        s.put(f"{BASE_URL}/api{p}/settings", json={"min_rr": 1.5, "ai_funnel_enabled": True})
    for p in ("/fx", "/pepper"):
        s.put(f"{BASE_URL}/api{p}/settings", json={"max_spread_sl_pct": 25})
    s.put(f"{BASE_URL}/api/config", json={"ai_fast_model": ""})


class TestFunnelAPI:
    def test_defaults(self, client):
        for p in ("", "/fx", "/pepper"):
            d = client.get(f"{BASE_URL}/api{p}/settings").json()
            assert d["ai_funnel_enabled"] is True and d["min_rr"] == 1.5, (p, d.get("min_rr"))
            if p:
                assert d["max_spread_sl_pct"] == 25
            else:
                assert "max_spread_sl_pct" not in d

    @pytest.mark.parametrize("p", ["", "/fx", "/pepper"])
    def test_validation(self, client, p):
        assert client.put(f"{BASE_URL}/api{p}/settings", json={"min_rr": 7}).status_code == 422
        assert client.put(f"{BASE_URL}/api{p}/settings", json={"min_rr": -1}).status_code == 422

    def test_spread_validation(self, client):
        assert client.put(f"{BASE_URL}/api/fx/settings", json={"max_spread_sl_pct": 101}).status_code == 422

    def test_pepper_isolation(self, client):
        r = client.put(f"{BASE_URL}/api/pepper/settings", json={"min_rr": 2, "ai_funnel_enabled": False})
        assert r.status_code == 200, r.text
        d = client.get(f"{BASE_URL}/api/pepper/settings").json()
        assert d["min_rr"] == 2 and d["ai_funnel_enabled"] is False
        f = client.get(f"{BASE_URL}/api/fx/settings").json()
        assert f["min_rr"] == 1.5 and f["ai_funnel_enabled"] is True
        r = client.put(f"{BASE_URL}/api/pepper/settings", json={"min_rr": 1.5, "ai_funnel_enabled": True})
        assert r.status_code == 200
        d = client.get(f"{BASE_URL}/api/pepper/settings").json()
        assert d["min_rr"] == 1.5 and d["ai_funnel_enabled"] is True

    def test_config(self, client):
        d = client.get(f"{BASE_URL}/api/config").json()
        assert d["ai_fast_model"] == "" and d["fast_model_auto"] == "claude-haiku-4-5-20251001"
        assert "anthropic" in d["fast_models"] and isinstance(d["fast_models"]["anthropic"], list)
        r = client.put(f"{BASE_URL}/api/config", json={"ai_fast_model": "gpt-5.4-mini"})
        assert r.status_code == 200, r.text
        assert client.get(f"{BASE_URL}/api/config").json()["ai_fast_model"] == "gpt-5.4-mini"
        r = client.put(f"{BASE_URL}/api/config", json={"ai_fast_model": ""})
        assert r.status_code == 200
        assert client.get(f"{BASE_URL}/api/config").json()["ai_fast_model"] == ""
