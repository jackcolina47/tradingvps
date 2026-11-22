# Iter105: HTTP checks over public URL for Pepperstone EMA-only bot and HotForex unchanged.
import os
import time
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
PWD = "BlackCat2233@"

CRYPTO = ["ADAUSDT", "AVAXUSDT", "BNBUSDT", "DOGEUSDT", "HYPEUSDT", "LINKUSDT", "ONDOUSDT",
          "SOLUSDT", "SUIUSDT", "XRPUSDT", "NEARUSDT", "LTCUSDT", "ARBUSDT", "UNIUSDT"]


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"password": PWD}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def s(token):
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return sess


# --- META endpoints -----------------------------------------------------------
def test_pepper_meta(s):
    r = s.get(f"{BASE}/api/pepper/meta", timeout=20)
    assert r.status_code == 200
    d = r.json()
    syms = d["symbols"]
    assert len(syms) == 23, f"expected 23, got {len(syms)}: {syms}"
    assert not [x for x in syms if "JPY" in x or "CHF" in x]
    assert {"AUDCAD", "AUDNZD"} <= set(syms)
    assert set(CRYPTO) <= set(syms)
    assert d.get("strategies") == ["ema_test"]
    assert d.get("ema_only") is True
    assert d.get("profiles") in (None, [])


def test_fx_meta_unchanged(s):
    r = s.get(f"{BASE}/api/fx/meta", timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert len(d["symbols"]) == 12
    assert "USDJPY" in d["symbols"]
    assert d.get("ema_only") is False
    assert isinstance(d.get("profiles"), list) and len(d["profiles"]) > 0


# --- KLINES -------------------------------------------------------------------
def test_pepper_klines_crypto_ok(s):
    r = s.get(f"{BASE}/api/pepper/klines", params={"symbol": "ADAUSDT", "tf": "1h"}, timeout=30)
    assert r.status_code == 200
    assert len(r.json()["candles"]) > 10


def test_pepper_klines_jpy_rejected(s):
    r = s.get(f"{BASE}/api/pepper/klines", params={"symbol": "USDJPY", "tf": "1h"}, timeout=20)
    assert r.status_code == 400


def test_fx_klines_jpy_ok(s):
    r = s.get(f"{BASE}/api/fx/klines", params={"symbol": "USDJPY", "tf": "1h"}, timeout=30)
    assert r.status_code == 200


def test_fx_klines_crypto_rejected(s):
    r = s.get(f"{BASE}/api/fx/klines", params={"symbol": "ADAUSDT", "tf": "1h"}, timeout=20)
    assert r.status_code == 400


# --- SETTINGS persistence -----------------------------------------------------
def test_settings_sl_atr_and_touch_exit_roundtrip(s):
    r0 = s.get(f"{BASE}/api/pepper/settings", timeout=20).json()
    orig_sl = r0.get("ema_sl_atr", 3)
    orig_touch = r0.get("ema_touch_exit_enabled", False)
    try:
        # set to 4 / True
        payload = {**r0, "ema_sl_atr": 4, "ema_touch_exit_enabled": True}
        r = s.put(f"{BASE}/api/pepper/settings", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        got = s.get(f"{BASE}/api/pepper/settings", timeout=20).json()
        assert got["ema_sl_atr"] == 4
        assert got["ema_touch_exit_enabled"] is True
    finally:
        # restore
        r0["ema_sl_atr"] = orig_sl
        r0["ema_touch_exit_enabled"] = orig_touch
        s.put(f"{BASE}/api/pepper/settings", json=r0, timeout=20)


def test_settings_jpy_filtered(s):
    r0 = s.get(f"{BASE}/api/pepper/settings", timeout=20).json()
    orig_syms = r0["symbols"]
    try:
        payload = {**r0, "symbols": ["USDJPY", "EURUSD"]}
        r = s.put(f"{BASE}/api/pepper/settings", json=payload, timeout=20)
        assert r.status_code == 200
        got = s.get(f"{BASE}/api/pepper/settings", timeout=20).json()
        assert got["symbols"] == ["EURUSD"], got["symbols"]
    finally:
        r0["symbols"] = orig_syms
        s.put(f"{BASE}/api/pepper/settings", json=r0, timeout=20)
        # verify restored
        got2 = s.get(f"{BASE}/api/pepper/settings", timeout=20).json()
        assert set(got2["symbols"]) == set(orig_syms)


# --- BACKTEST -----------------------------------------------------------------
@pytest.mark.parametrize("sym", ["SOLUSDT", "EURUSD"])
def test_backtest(s, sym):
    payload = {"symbol": sym, "strategy": "all", "timeframe": "1h", "risk_per_trade": 1, "leverage": 100}
    r = s.post(f"{BASE}/api/pepper/backtest", json=payload, timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    # strategy should be ema_test (forced)
    strat = d.get("strategy") or d.get("config", {}).get("strategy")
    assert strat in ("ema_test", "all"), f"got strategy={strat}"
    trades = d.get("trades", [])
    reasons = {t.get("reason") for t in trades}
    allowed = {"SL", "CRUCE", "BREAKEVEN", "TOQUE_EMA", "FIN_SEMANA", None}
    assert reasons <= allowed, f"unexpected reasons: {reasons - allowed}"
    assert "TP" not in reasons
