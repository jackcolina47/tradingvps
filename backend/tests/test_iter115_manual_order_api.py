"""Iter115 — API-level tests for POST /api/manual-order with per-trade rules & ai_review_minutes."""
import os
import time
import pytest
import requests

def _base():
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if not v:
        from pathlib import Path
        for line in Path("/app/frontend/.env").read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                v = line.split("=", 1)[1].strip()
                break
    return v.rstrip("/")


BASE = _base()
PWD = "BlackCat2233@"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"password": PWD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- settings snapshot untouched ---

def test_settings_unchanged_before_after(h):
    s0 = requests.get(f"{BASE}/api/settings", headers=h, timeout=15).json()
    # trigger a validation-rejected request to be sure nothing persists
    r = requests.post(f"{BASE}/api/manual-order", headers=h, json={"symbol": "BTCUSDT", "side": "long", "sl": 1, "tp": 2, "atr": 1, "risk_pct": 1, "ai_review_minutes": 7}, timeout=15)
    assert r.status_code == 422, r.text
    s1 = requests.get(f"{BASE}/api/settings", headers=h, timeout=15).json()
    for k in ("breakeven_enabled", "be_start_r", "trailing_enabled", "trail_start_r", "trail_atr_mult", "partial_tp_enabled", "partial_tp_r", "partial_tp_pct"):
        assert s0.get(k) == s1.get(k), k


# --- validation ---

@pytest.mark.parametrize("field,value", [("ai_review_minutes", 7), ("ai_review_minutes", 35), ("be_start_r", 5), ("trail_atr_mult", 4), ("partial_tp_pct", 20), ("partial_tp_pct", 80)])
def test_validation_422(h, field, value):
    body = {"symbol": "BTCUSDT", "side": "long", "sl": 1, "tp": 2, "atr": 1, "risk_pct": 1, field: value}
    r = requests.post(f"{BASE}/api/manual-order", headers=h, json=body, timeout=15)
    assert r.status_code == 422, (field, value, r.text)


# --- live-fire manual order (paper) ---

def _price(h):
    d = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json()
    for t in d.get("watchlist", []):
        if t.get("symbol") == "BTCUSDT" and t.get("price"):
            return float(t["price"])
    return None


def _close(h, pid):
    requests.post(f"{BASE}/api/positions/{pid}/close", headers=h, timeout=15)


def test_manual_order_stores_rules_and_ai_review_and_does_not_mutate_settings(h):
    price = _price(h)
    if not price:
        pytest.skip("no BTCUSDT live price")
    s_before = requests.get(f"{BASE}/api/settings", headers=h, timeout=15).json()
    sl = round(price * 0.99, 2)
    tp = round(price * 1.02, 2)
    body = {"symbol": "BTCUSDT", "side": "long", "sl": sl, "tp": tp, "atr": max(0.1, price * 0.001), "risk_pct": 0.5,
            "breakeven_enabled": False, "trailing_enabled": True, "trail_start_r": 1.0, "trail_atr_mult": 1.5,
            "partial_tp_enabled": True, "partial_tp_r": 1.0, "partial_tp_pct": 40, "ai_review_minutes": 5}
    r = requests.post(f"{BASE}/api/manual-order", headers=h, json=body, timeout=30)
    if r.status_code == 400 and ("pausa" in r.text.lower() or "pérdida" in r.text.lower() or "cerrado" in r.text.lower()):
        pytest.skip(f"env limitation: {r.text}")
    assert r.status_code == 200, r.text
    pos = r.json()
    pid = pos["id"]
    try:
        assert pos.get("ai_review_minutes") == 5
        rules = pos.get("rules") or {}
        assert rules.get("breakeven_enabled") is False
        assert rules.get("trailing_enabled") is True
        assert rules.get("trail_start_r") == 1.0
        assert rules.get("trail_atr_mult") == 1.5
        assert rules.get("partial_tp_enabled") is True
        assert rules.get("partial_tp_r") == 1.0
        assert rules.get("partial_tp_pct") == 40
        # dashboard positions reflect same
        d = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json()
        found = next((p for p in d.get("positions", []) if p["id"] == pid), None)
        assert found and found.get("ai_review_minutes") == 5 and (found.get("rules") or {}).get("trail_atr_mult") == 1.5
        # settings untouched
        s_after = requests.get(f"{BASE}/api/settings", headers=h, timeout=15).json()
        for k in ("breakeven_enabled", "be_start_r", "trailing_enabled", "trail_start_r", "trail_atr_mult", "partial_tp_enabled", "partial_tp_r", "partial_tp_pct"):
            assert s_before.get(k) == s_after.get(k), k
        # log line present
        time.sleep(1)
        logs = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json().get("logs", [])
        assert any("gestión propia de la orden manual" in (l.get("message") or "") for l in logs), "missing expected log line"
    finally:
        _close(h, pid)


def test_manual_order_without_overrides_is_backward_compatible(h):
    price = _price(h)
    if not price:
        pytest.skip("no BTCUSDT live price")
    sl = round(price * 0.99, 2); tp = round(price * 1.02, 2)
    body = {"symbol": "BTCUSDT", "side": "long", "sl": sl, "tp": tp, "atr": max(0.1, price * 0.001), "risk_pct": 0.5}
    r = requests.post(f"{BASE}/api/manual-order", headers=h, json=body, timeout=30)
    if r.status_code == 400 and ("pausa" in r.text.lower() or "pérdida" in r.text.lower() or "cerrado" in r.text.lower()):
        pytest.skip(f"env limitation: {r.text}")
    assert r.status_code == 200, r.text
    pos = r.json()
    try:
        assert pos.get("rules") in (None, {}, {"$each": []}) or pos.get("rules") is None
        assert pos.get("ai_review_minutes") in (None, 0)
    finally:
        _close(h, pos["id"])
