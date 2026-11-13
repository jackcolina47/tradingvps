# Iter78: regression for "Error IA: 'FxConfig' object has no attribute 'ai_fast_model'" - stage 1 must not read engine cfg.
# Fake engines (FxConfig / cfg without ai_fast_model) run the REAL ai_funnel.quick_filter with _ask mocked. Run with -n 0.
import os
import re
import sys
import types
import asyncio
import pytest
import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_iter77_ai_funnel as t77  # noqa: E402
import ai_funnel  # noqa: E402
from config import AppConfig  # noqa: E402
from fx.config import FxConfig  # noqa: E402

BASE_URL = t77.BASE_URL
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_JSON = '{"pass_filter":true,"confidence":85,"discard_reason":""}'
APPROVE = {"decision": "APROBADA", "confidence": 80, "risk": "BAJO", "reasoning": "ok", "adjusted_sl": None, "adjusted_tp": None}


# ---------- Static: no cfg.ai_fast_model reads in engines ----------
def test_no_cfg_ai_fast_model_in_engines():
    for f in ("engine.py", "fx/engine_signals.py", "fx/engine.py", "fx/engine_positions.py", "fx/engine_account.py", "ai_funnel.py"):
        src = open(os.path.join(ROOT, f)).read()
        assert not re.search(r"cfg\.ai_fast_model", src), f
    assert not hasattr(FxConfig(), "ai_fast_model")
    assert "ai_funnel.configure(c.ai_fast_model)" in open(os.path.join(ROOT, "config.py")).read()


# ---------- Unit: configure / fast_cfg ----------
@pytest.fixture
def anthropic_cfg(monkeypatch):
    monkeypatch.setattr(ai_funnel, "_cfg", {**ai_funnel._cfg, "source": "emergent", "provider": "anthropic"})
    yield
    ai_funnel.configure("")


def test_configure_runtime(anthropic_cfg):
    ai_funnel.configure("gpt-5.4-mini")
    assert ai_funnel.fast_cfg()["model"] == "gpt-5.4-mini"
    assert ai_funnel.fast_cfg("x")["model"] == "x"
    ai_funnel.configure("")
    assert ai_funnel.fast_cfg()["model"] == "claude-haiku-4-5-20251001"
    ai_funnel.configure(None)
    assert ai_funnel.fast_cfg()["model"] == "claude-haiku-4-5-20251001"


def test_configure_from_appconfig(anthropic_cfg):
    c = AppConfig.model_validate({"ai_fast_model": "gpt-5.4-mini"})
    ai_funnel.configure(c.ai_fast_model)
    assert ai_funnel.fast_cfg()["model"] == "gpt-5.4-mini"


# ---------- Integration: fake engines, real quick_filter, mocked _ask ----------
def _common(monkeypatch, mod, seen):
    async def fake_ask(chat, prompt):
        return PASS_JSON

    def fake_chat(system, sid, cfg):
        seen.append(cfg["model"])
        return types.SimpleNamespace()

    async def fake_eval(*a, **k):
        return dict(APPROVE)

    async def fake_shadow(*a, **k): pass
    async def fake_rules(*a, **k): return []
    monkeypatch.setattr(ai_funnel, "_ask", fake_ask)
    monkeypatch.setattr(ai_funnel, "_chat", fake_chat)
    monkeypatch.setattr(mod, "evaluate_signal", fake_eval)
    monkeypatch.setattr(mod, "open_shadow", fake_shadow)
    monkeypatch.setattr(mod.ai_rules, "active", fake_rules)


def test_fx_handle_signal_with_fxconfig(monkeypatch, anthropic_cfg):
    from fx import engine_signals as es
    from fx.models import FxSettings
    seen = []
    _common(monkeypatch, es, seen)

    class Fake(es.SignalsMixin):
        pass
    e = Fake()
    e.signal_keys, e.db, e.news, e.live, e.opened = set(), t77._DB(), None, False, []
    e.market = types.SimpleNamespace(tickers={"EURUSD": {"price": 1.1, "change_pct": 0.1}})
    e.profile, e.cfg = types.SimpleNamespace(ai_kind="fx"), FxConfig()

    async def noop(*a, **k): pass
    async def acc(): return {"equity": 10000, "available": 10000, "daily_pnl_pct": 0}
    async def blocks(*a, **k): return None, None, []
    async def gates(*a, **k): return None, None, 1.0, {}
    async def open_position(*a, **k):
        e.opened.append(a)
        return {"id": "p"}, ""
    e.log, e.tg, e.account_summary, e._blocks, e._market_gates, e.open_position = noop, noop, acc, blocks, gates, open_position
    settings = FxSettings(ai_enabled=True, ai_funnel_enabled=True, price_action_block=False)
    sig = {"strategy": "trend_ema", "side": "long", "price": 1.1, "sl": 1.098, "tp": 1.104, "score": 3, "reasons": ["t"],
           "indicators": {"atr": 0.001}, "key_ts": 1, "atr": 0.001}
    asyncio.run(e._handle_signal("EURUSD", sig, settings, pd.DataFrame(t77.flat(40)), 1, "4h", horizon="short"))
    d = e.db.signals.docs[0]
    assert d["ai_decision"] == "APROBADA" and d["ai_stage"] == 2, d
    assert "FxConfig" not in (d.get("note") or "") and seen == ["claude-haiku-4-5-20251001"]
    assert len(e.opened) == 1


def test_crypto_handle_signal_cfg_without_attr(monkeypatch, anthropic_cfg):
    import engine as ce
    from models import Settings
    seen = []
    _common(monkeypatch, ce, seen)

    async def noop(*a, **k): pass
    monkeypatch.setattr(ce, "tg", noop)
    e = ce.TradingEngine.__new__(ce.TradingEngine)
    e.signal_keys, e.db, e.news, e.live, e.opened = set(), t77._DB(), None, False, []
    e.market = types.SimpleNamespace(tickers={"SOLUSDT": {"price": 100.0, "change_pct": 0.5}})
    e.cfg = types.SimpleNamespace(funding_enabled=False)
    e.bybit = {"down_since": None}
    ai_funnel.configure("gpt-5.4-mini")

    async def acc(): return {"equity": 500, "available": 500, "daily_pnl_pct": 0, "daily_paused": False}
    async def oap(*a, **k): return []
    async def open_position(*a, **k):
        e.opened.append(a)
        return {"id": "p"}
    e.log, e.account_summary, e._open_and_pending, e.open_position = noop, acc, oap, open_position
    settings = Settings(ai_enabled=True, ai_funnel_enabled=True, btc_regime_filter=False, rotation_enabled=False, entry_order_type="market")
    rows = [{"open": 100, "high": 101, "low": 99, "close": 100 + (i % 2) * 0.1, "volume": 1} for i in range(40)]
    sig = {"strategy": "trend_ema", "side": "long", "price": 100.0, "sl": 98.0, "tp": 104.0, "score": 3, "reasons": ["t"],
           "indicators": {"atr": 1.0}, "key_ts": 1, "atr": 1.0}
    asyncio.run(e._handle_signal("SOLUSDT", sig, settings, pd.DataFrame(rows), 1, "4h", horizon="short"))
    d = e.db.signals.docs[0]
    assert d["ai_decision"] == "APROBADA" and d["ai_stage"] == 2, d
    assert "attribute" not in (d.get("note") or "") and seen == ["gpt-5.4-mini"]


# ---------- API round trip ----------
def test_api_config_round_trip():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    try:
        assert s.put(f"{BASE_URL}/api/config", json={"ai_fast_model": "gpt-5.4-mini"}).status_code == 200
        assert s.get(f"{BASE_URL}/api/config").json()["ai_fast_model"] == "gpt-5.4-mini"
    finally:
        assert s.put(f"{BASE_URL}/api/config", json={"ai_fast_model": ""}).status_code == 200
    assert s.get(f"{BASE_URL}/api/config").json()["ai_fast_model"] == ""
