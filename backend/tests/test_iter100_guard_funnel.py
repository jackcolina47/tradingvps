"""Iter100 tests:
 - trade_guard pure functions (windows per bot kind, weekend FX closed, affects(), event states)
 - funnel pure functions (coerce_confidence, stage0, zones, metrics basics, levels fixed/AI/fallback)
 - public API: GET /api/guard, /api/fx/guard, /api/pepper/guard shape + 401; PUT settings ai_min_confidence snap/clamp;
   /api/lab/* 404; /api/dashboard, /api/meta, /api/overview reachable.
Avoids spending real AI tokens (no POST /guard/refresh here unless explicitly invoked, no bot start)."""
import os
import copy
import math
import pytest
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0, "/app/backend")
import trade_guard as tg
import funnel

def _read_fe_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_fe_env()).rstrip("/")
PWD = "BlackCat2233@"
NY = ZoneInfo("America/New_York")


# ----------------- trade_guard pure -----------------
class TestHours:
    """Windows per spec: FX 03-04, 08-12, 19-24 NY; crypto 03-05, 08:30-12, 19-23 NY."""

    def _ny(self, h, m=0, dow_offset=0):
        # pick a Wednesday in a past week to avoid FX weekend boundary interference
        base = datetime(2026, 1, 7, h, m, tzinfo=NY)  # Wed
        return (base + timedelta(days=dow_offset)).astimezone(timezone.utc)

    def test_fx_windows_exact(self):
        assert tg.in_hours("fx", self._ny(3, 30)) is True
        assert tg.in_hours("fx", self._ny(4, 0)) is False  # end-exclusive
        assert tg.in_hours("fx", self._ny(8, 0)) is True
        assert tg.in_hours("fx", self._ny(11, 59)) is True
        assert tg.in_hours("fx", self._ny(12, 0)) is False
        assert tg.in_hours("fx", self._ny(19, 0)) is True
        assert tg.in_hours("fx", self._ny(23, 59)) is True
        assert tg.in_hours("fx", self._ny(2, 59)) is False

    def test_crypto_always_open(self):
        for h, m in ((0, 0), (5, 0), (8, 29), (12, 0), (16, 0), (23, 0), (23, 59)):
            assert tg.in_hours("crypto", self._ny(h, m)) is True

    def test_fx_closed_on_weekend(self):
        # Saturday 10:00 NY: FX market closed (Fri 17 NY -> Sun 17 NY)
        sat = datetime(2026, 1, 10, 10, 0, tzinfo=NY).astimezone(timezone.utc)
        assert tg.in_hours("fx", sat) is False
        # Crypto still open 10am Sat (within 08:30-12)
        assert tg.in_hours("crypto", sat) is True

    def test_hours_status_shape(self):
        s = tg.hours_status("fx", self._ny(10, 0))
        assert set(["in_hours", "window", "next_window", "next_change", "seconds_left", "ny_time", "windows"]).issubset(s.keys())
        assert len(s["windows"]) == 3
        assert s["in_hours"] is True
        # when inside, seconds_left counts until window end
        assert s["seconds_left"] > 0


class TestAffects:
    def test_crypto_only_usd(self):
        assert tg.affects("crypto", "BTCUSDT", "USD") is True
        assert tg.affects("crypto", "BTCUSDT", "EUR") is False

    def test_fx_pair_two_currencies(self):
        assert tg.affects("fx", "EURUSD", "EUR") is True
        assert tg.affects("fx", "EURUSD", "USD") is True
        assert tg.affects("fx", "EURUSD", "JPY") is False

    def test_country_all(self):
        assert tg.affects("fx", "EURUSD", "All") is True


class TestEventStates:
    def test_status_event_states(self):
        now = datetime(2026, 1, 7, 12, 0, tzinfo=timezone.utc)
        ev_past = {"key": "p", "title": "x", "country": "USD", "time": (now - timedelta(hours=1)).isoformat(),
                   "country_name": "USD", "forecast": "", "previous": ""}
        ev_block = {"key": "b", "title": "y", "country": "USD", "time": now.isoformat(),
                    "country_name": "USD", "forecast": "", "previous": ""}
        ev_fut = {"key": "f", "title": "z", "country": "USD", "time": (now + timedelta(hours=1)).isoformat(),
                  "country_name": "USD", "forecast": "", "previous": ""}
        saved = tg.calendar.events
        tg.calendar.events = [ev_past, ev_block, ev_fut]
        try:
            st = tg.status("crypto", ["BTCUSDT"], now)
            states = {e["key"]: e["state"] for e in st["events"]}
            assert states == {"p": "PASADO", "b": "BLOQUEANDO", "f": "PROXIMO"}
            assert st["blocked"]["BTCUSDT"]  # BLOQUEANDO event affects BTCUSDT (USD)
            # affected arrays populated
            by_key = {e["key"]: e for e in st["events"]}
            assert by_key["b"]["affected"] == ["BTCUSDT"]
        finally:
            tg.calendar.events = saved


# ----------------- funnel pure -----------------
class TestCoerce:
    @pytest.mark.parametrize("raw,expected", [
        (10, 10), (90, 90), (44, 40), (45, 40), (46, 50), (95, 90), (3, 10), (-5, 10),
        (120, 90), ("70", 70), (None, 60), ("abc", 60), (55.0, 60),
    ])
    def test_values(self, raw, expected):
        assert funnel.coerce_confidence(raw) == expected


class TestStage0:
    def _m(self, **over):
        base = {"atr": 1.0, "adx": 10, "activity": 0.5, "activity_kind": "rango", "trend": "MIXTA",
                "demand": {"dist_atr": 2.0}, "supply": {"dist_atr": 2.0}}
        base.update(over)
        return base

    def test_flat_quiet_discarded(self):
        assert funnel.stage0(self._m()) is not None

    def test_low_adx_discarded_even_near_zone(self):
        assert "ADX" in funnel.stage0(self._m(adx=17.9, demand={"dist_atr": 0.5}, activity=2.0, trend="ALCISTA"))

    def test_adx_ok_range_activity_ignored(self):
        assert funnel.stage0(self._m(adx=18, activity=0.3)) is None

    def test_weak_volume_discarded(self):
        assert "volumen" in funnel.stage0(self._m(adx=25, activity=0.54, activity_kind="volumen"))

    def test_adx_and_volume_ok_passes(self):
        assert funnel.stage0(self._m(adx=18, activity=0.55, activity_kind="volumen")) is None

    def test_overextension_depends_on_4h(self):
        floor = dict(adx=25, activity=2.0, activity_kind="volumen", rsi=30, dist_ema20_atr=-2.4)
        assert funnel.stage0(self._m(rich=True, **floor)) is None
        assert funnel.overext_discard(self._m(**floor), "BAJISTA") is None   # not a rich engine
        assert "suelo" in funnel.overext_discard(self._m(rich=True, **floor), "BAJISTA")
        assert "suelo" in funnel.overext_discard(self._m(rich=True, **floor), "MIXTA")
        assert funnel.overext_discard(self._m(rich=True, **floor), "ALCISTA") is None
        roof = {**floor, "rsi": 70, "dist_ema20_atr": 2.1}
        assert "techo" in funnel.overext_discard(self._m(rich=True, **roof), "ALCISTA")
        assert funnel.overext_discard(self._m(rich=True, **roof), "BAJISTA") is None
        assert funnel.overext_discard(self._m(rich=True, **{**floor, "dist_ema20_atr": -1.5}), "BAJISTA") is None

    def test_ai_stop_buffer(self):
        s = type("S", (), {"atr_levels_enabled": False, "atr_sl_mult": 2.0, "atr_tp_mult": 4.0})()
        assert funnel.levels("long", 100.0, 1.0, s, 99.0, 103.0, buffer_atr=0.25) == (98.75, 103.0, "ia")
        assert funnel.levels("short", 100.0, 1.0, s, 101.0, 97.0, buffer_atr=0.25) == (101.25, 97.0, "ia")
        assert funnel.levels("long", 100.0, 1.0, s, 99.0, 103.0) == (99.0, 103.0, "ia")

    def test_next_trigger(self):
        base = 1_800_000_000 // 900 * 900
        assert funnel.next_trigger(base + 3) == base + 8
        assert funnel.next_trigger(base + 8) == base + 908
        assert funnel.next_trigger(base + 500) == base + 908

    def test_no_atr_discarded(self):
        assert funnel.stage0(self._m(atr=0)) == "sin ATR válido"


class TestLevels:
    class S:
        atr_levels_enabled = True
        atr_sl_mult = 1.5
        atr_tp_mult = 3.0

    def test_atr_fixed_long(self):
        s = self.S()
        sl, tp, src = funnel.levels("long", 100.0, 1.0, s)
        assert sl == 100 - 1.5 and tp == 100 + 3.0 and src == "atr"

    def test_atr_fixed_short(self):
        s = self.S()
        sl, tp, src = funnel.levels("short", 100.0, 1.0, s)
        assert sl == 100 + 1.5 and tp == 100 - 3.0 and src == "atr"

    def test_ai_levels_ok(self):
        s = self.S()
        s.atr_levels_enabled = False
        sl, tp, src = funnel.levels("long", 100.0, 1.0, s, ai_sl=99.0, ai_tp=102.0)
        assert src == "ia" and sl == 99.0 and tp == 102.0

    def test_ai_levels_bad_rr_fallback(self):
        s = self.S()
        s.atr_levels_enabled = False
        sl, tp, src = funnel.levels("long", 100.0, 1.0, s, ai_sl=99.5, ai_tp=100.1)  # SL 0.5 ATR, but R:R < 1
        assert src == "atr"

    def test_ai_levels_bad_distance(self):
        s = self.S()
        s.atr_levels_enabled = False
        sl, tp, src = funnel.levels("long", 100.0, 1.0, s, ai_sl=90.0, ai_tp=120.0)  # SL 10 ATR: out of band
        assert src == "atr"


class TestZonesMetrics:
    def _df(self, n=200):
        import numpy as np
        rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
        base = 100 + np.sin(np.linspace(0, 20, n)) * 2
        df = pd.DataFrame({
            "ts": [int(t.timestamp() * 1000) for t in rng],
            "open": base, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": [100.0] * n,
            "ema20": base, "ema50": base, "ema200": base, "rsi": [50.0] * n,
            "macd_hist": [0.0] * n, "adx": [20.0] * n, "atr": [1.0] * n, "vol_ratio": [1.0] * n,
        })
        return df

    def test_metrics_shape(self):
        df = self._df()
        m = funnel.metrics(df, len(df) - 2, True, float(df["close"].iloc[-2]))
        for k in ("price", "trend", "rsi", "adx", "atr", "activity", "activity_kind", "demand", "supply", "candle_ts"):
            assert k in m
        assert m["activity_kind"] == "volumen"

    def test_zones_returns_both_sides(self):
        df = self._df()
        z = funnel.zones(df, len(df) - 2, 1.0, float(df["close"].iloc[-2]))
        assert set(z.keys()) == {"demand", "supply"}


# ----------------- HTTP API smoke -----------------
@pytest.fixture(scope="module")
def token():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": PWD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class TestGuardAPI:
    @pytest.mark.parametrize("prefix,kind", [("/api", "crypto"), ("/api/fx", "fx"), ("/api/pepper", "fx")])
    def test_guard_shape(self, prefix, kind, h):
        r = requests.get(f"{BASE_URL}{prefix}/guard", headers=h, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("hours", "blocked", "events", "calendar"):
            assert k in data, f"missing {k} in {prefix}/guard"
        hrs = data["hours"]
        for k in ("in_hours", "window", "next_window", "next_change", "seconds_left", "ny_time", "windows"):
            assert k in hrs
        assert len(hrs["windows"]) == (3 if kind == "fx" else 1)
        # windows should match expected per spec
        got = sorted([(w["start"], w["end"]) for w in hrs["windows"]])
        if kind == "fx":
            assert got == sorted([("03:00", "04:00"), ("08:00", "12:00"), ("19:00", "00:00")])
        else:
            assert got == [("00:00", "00:00")] and hrs["in_hours"] is True and hrs["next_change"] is None

    def test_guard_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/guard", timeout=15)
        assert r.status_code in (401, 403)


class TestSettingsSnap:
    @pytest.mark.parametrize("prefix", ["/api", "/api/fx", "/api/pepper"])
    @pytest.mark.parametrize("raw,expected", [(44, 40), (45, 40), (46, 50), (95, 90), (3, 10), (70, 70)])
    def test_snap_clamp(self, prefix, raw, expected, h):
        # read current settings to send a full / minimal payload
        before = requests.get(f"{BASE_URL}{prefix}/settings", headers=h, timeout=15)
        assert before.status_code == 200
        r = requests.put(f"{BASE_URL}{prefix}/settings", headers=h, json={"ai_min_confidence": raw}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        # endpoint may return {"settings": {...}} or settings itself
        s = data.get("settings", data)
        assert s["ai_min_confidence"] == expected, f"{prefix} {raw}->{s['ai_min_confidence']} != {expected}"

    @pytest.mark.parametrize("prefix", ["/api", "/api/fx", "/api/pepper"])
    def test_restore_60(self, prefix, h):
        r = requests.put(f"{BASE_URL}{prefix}/settings", headers=h, json={"ai_min_confidence": 60}, timeout=20)
        assert r.status_code == 200
        s = r.json().get("settings", r.json())
        assert s["ai_min_confidence"] == 60


class TestBaselineEndpoints:
    @pytest.mark.parametrize("path", [
        "/api/dashboard", "/api/fx/dashboard", "/api/pepper/dashboard",
        "/api/meta", "/api/overview", "/api/fx/overview", "/api/pepper/overview",
    ])
    def test_200(self, path, h):
        r = requests.get(f"{BASE_URL}{path}", headers=h, timeout=25)
        assert r.status_code == 200, f"{path} -> {r.status_code}"

    def test_lab_overview_404(self, h):
        r = requests.get(f"{BASE_URL}/api/lab/overview", headers=h, timeout=15)
        assert r.status_code == 404
