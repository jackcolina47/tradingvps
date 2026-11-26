"""Iter116 — Backend tests for PUT /api/positions/{id}/levels (drag SL/TP) and PUT /api/positions/{id}/rules (edit management of an open position)."""
import os
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


def _price(h):
    d = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json()
    for t in d.get("watchlist", []):
        if t.get("symbol") == "ETHUSDT" and t.get("price"):
            return float(t["price"])
    return None


def _open_btc(h):
    price = _price(h)
    if not price:
        pytest.skip("no ETHUSDT live price")
    # close any already-open ETH position from a previous (possibly parallel) test
    dash = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json()
    for p in dash.get("positions", []):
        if p.get("symbol") == "ETHUSDT":
            _close(h, p["id"])
    body = {"symbol": "ETHUSDT", "side": "long",
            "sl": round(price * 0.98, 2), "tp": round(price * 1.04, 2),
            "atr": max(0.1, price * 0.005), "risk_pct": 0.5}
    r = requests.post(f"{BASE}/api/manual-order", headers=h, json=body, timeout=30)
    if r.status_code == 400 and any(k in r.text.lower() for k in ("pausa", "pérdida", "cerrado", "límite", "ya abierto")):
        pytest.skip(r.text)
    assert r.status_code == 200, r.text
    return r.json(), price


def _close(h, pid):
    try:
        requests.post(f"{BASE}/api/positions/{pid}/close", headers=h, timeout=15)
    except Exception:
        pass


# ---- PUT /api/positions/{id}/rules ----

def test_rules_update_persists_and_log_written(h):
    pos, _ = _open_btc(h)
    pid = pos["id"]
    try:
        body = {"breakeven_enabled": False, "trailing_enabled": True, "trail_start_r": 1.0, "trail_atr_mult": 1.5,
                "partial_tp_enabled": True, "partial_tp_r": 1.0, "partial_tp_pct": 40, "ai_review_minutes": 10}
        r = requests.put(f"{BASE}/api/positions/{pid}/rules", headers=h, json=body, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["rules"]["trailing_enabled"] is True
        assert d["rules"]["trail_atr_mult"] == 1.5
        assert d["ai_review_minutes"] == 10
        # check dashboard reflects
        dash = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json()
        found = next((p for p in dash["positions"] if p["id"] == pid), None)
        assert found and found["rules"] and found["ai_review_minutes"] == 10
        # log
        logs_txt = requests.get(f"{BASE}/api/logs/export?hours=24&fmt=txt", headers=h, timeout=15).text
        assert "gestión de la posición editada" in logs_txt.lower()
    finally:
        _close(h, pid)


def test_rules_clear_sets_null(h):
    pos, _ = _open_btc(h)
    pid = pos["id"]
    try:
        # first set some rules
        requests.put(f"{BASE}/api/positions/{pid}/rules", headers=h, json={"breakeven_enabled": True, "be_start_r": 1.0, "ai_review_minutes": 5}, timeout=15)
        # now clear: empty body -> rules None, ai_review_minutes None
        r = requests.put(f"{BASE}/api/positions/{pid}/rules", headers=h, json={}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["rules"] in (None, {})
        assert d.get("ai_review_minutes") is None
    finally:
        _close(h, pid)


@pytest.mark.parametrize("payload", [
    {"be_start_r": 5},
    {"partial_tp_pct": 10},
    {"ai_review_minutes": 7},
    {"trail_atr_mult": 4},
])
def test_rules_validation_422(h, payload):
    pos, _ = _open_btc(h)
    pid = pos["id"]
    try:
        r = requests.put(f"{BASE}/api/positions/{pid}/rules", headers=h, json=payload, timeout=15)
        assert r.status_code == 422, (payload, r.text)
    finally:
        _close(h, pid)


def test_rules_unknown_id_404(h):
    r = requests.put(f"{BASE}/api/positions/000000000000000000000000/rules", headers=h, json={}, timeout=15)
    assert r.status_code == 404
    r = requests.put(f"{BASE}/api/positions/notanid/rules", headers=h, json={}, timeout=15)
    assert r.status_code == 404


def test_rules_bot_settings_untouched(h):
    s0 = requests.get(f"{BASE}/api/settings", headers=h, timeout=15).json()
    pos, _ = _open_btc(h)
    pid = pos["id"]
    try:
        requests.put(f"{BASE}/api/positions/{pid}/rules", headers=h, json={"breakeven_enabled": False, "trailing_enabled": False, "partial_tp_enabled": False}, timeout=15)
        s1 = requests.get(f"{BASE}/api/settings", headers=h, timeout=15).json()
        for k in ("breakeven_enabled", "trailing_enabled", "partial_tp_enabled", "be_start_r", "trail_start_r", "trail_atr_mult", "partial_tp_r", "partial_tp_pct"):
            assert s0.get(k) == s1.get(k), k
    finally:
        _close(h, pid)


# ---- PUT /api/positions/{id}/levels ----

def test_levels_move_sl_and_tp_persists_and_log(h):
    pos, price = _open_btc(h)
    pid = pos["id"]
    try:
        new_sl = round(price * 0.97, 2)
        new_tp = round(price * 1.05, 2)
        r = requests.put(f"{BASE}/api/positions/{pid}/levels", headers=h, json={"sl": new_sl, "tp": new_tp}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert abs(d["sl"] - new_sl) < 0.5
        assert abs(d["tp"] - new_tp) < 0.5
        # persistence
        dash = requests.get(f"{BASE}/api/dashboard", headers=h, timeout=15).json()
        found = next((p for p in dash["positions"] if p["id"] == pid), None)
        assert found and abs(found["sl"] - new_sl) < 0.5 and abs(found["tp"] - new_tp) < 0.5
        # log line
        logs_txt = requests.get(f"{BASE}/api/logs/export?hours=24&fmt=txt", headers=h, timeout=15).text
        assert "niveles movidos a mano" in logs_txt.lower()
    finally:
        _close(h, pid)


def test_levels_only_sl(h):
    pos, price = _open_btc(h)
    pid = pos["id"]
    try:
        new_sl = round(price * 0.975, 2)
        r = requests.put(f"{BASE}/api/positions/{pid}/levels", headers=h, json={"sl": new_sl}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert abs(d["sl"] - new_sl) < 0.5
        assert abs(d["tp"] - pos["tp"]) < 0.5  # unchanged
    finally:
        _close(h, pid)


def test_levels_breakeven_true_when_sl_past_entry(h):
    pos, price = _open_btc(h)
    pid = pos["id"]
    try:
        # long: move SL to entry+something (past entry in favour)
        new_sl = round(pos["entry_price"] * 1.0005, 2)
        # keep SL below current price
        if new_sl >= price:
            pytest.skip("entry above current price for the long")
        r = requests.put(f"{BASE}/api/positions/{pid}/levels", headers=h, json={"sl": new_sl}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["breakeven"] is True
    finally:
        _close(h, pid)


def test_levels_invalid_sl_above_price_long_400(h):
    pos, price = _open_btc(h)
    pid = pos["id"]
    try:
        bad_sl = round(price * 1.02, 2)
        r = requests.put(f"{BASE}/api/positions/{pid}/levels", headers=h, json={"sl": bad_sl}, timeout=15)
        assert r.status_code == 400
        assert "inválidos" in r.text.lower() or "invalidos" in r.text.lower()
    finally:
        _close(h, pid)


def test_levels_invalid_tp_below_price_long_400(h):
    pos, price = _open_btc(h)
    pid = pos["id"]
    try:
        bad_tp = round(price * 0.98, 2)
        r = requests.put(f"{BASE}/api/positions/{pid}/levels", headers=h, json={"tp": bad_tp}, timeout=15)
        assert r.status_code == 400
    finally:
        _close(h, pid)


def test_levels_unknown_id_404(h):
    r = requests.put(f"{BASE}/api/positions/000000000000000000000000/levels", headers=h, json={"sl": 1, "tp": 2}, timeout=15)
    assert r.status_code == 404
    r = requests.put(f"{BASE}/api/positions/notanid/levels", headers=h, json={"sl": 1, "tp": 2}, timeout=15)
    assert r.status_code == 404
