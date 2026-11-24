"""Iter103 — Bybit funnel + maker hardening:
  (A) funnel.overextended / overext_discard: 'suelo' / 'techo' only when rich; dropped only when 4H disagrees.
  (B) funnel.run with overext: pair survives, stage1 candidate on the late side is removed before stage2;
      rebound side still reaches stage2.
  (C) funnel.metrics(rich=True) and _row_txt extras; rich=False stays unchanged.
  (D) funnel.levels with SL_BUFFER_ATR=0.25 moves AI SL by 0.25*ATR; buffer changes nothing for atr levels;
      buffer that invalidates MIN_RR falls back to ATR; risk.position_size respects risk% with stop_cost.
  (E) maker.queue_entry in live: happy path, retry on best price within 0.15 ATR, retry too far, two rejects,
      live_size None, client.best exception — in NO case a market order.
  (F) maker.monitor_pending paper/live scenarios.
Pytest file name intentionally does NOT start with 'test_api_' so pytest.ini's loadscope pairs it with others.
Run individually with `-n 0` to avoid the historical cross-suite state collisions.
"""
import os
import sys
import time
import math
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")

import funnel as funnel_mod  # noqa: E402
import maker as maker_mod  # noqa: E402
import risk as risk_mod  # noqa: E402
from models import Settings  # noqa: E402


# ---------------- helpers reused from iter102 ----------------
class _Coll:
    def __init__(self):
        self.docs = []
        self._auto = 0

    async def insert_one(self, d):
        self._auto += 1
        d = {**d, "_id": self._auto}
        self.docs.append(d)

        class _R:
            pass
        _R.inserted_id = self._auto
        return _R

    async def update_one(self, q, u):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(u.get("$set", {}))
        return None

    async def delete_one(self, q):
        self.docs = [d for d in self.docs if not all(d.get(k) == v for k, v in q.items())]
        return None

    def find(self, q=None, *a, **kw):
        rows = list(self.docs)

        class _C:
            def __aiter__(self2):
                self2.i = 0
                return self2

            async def __anext__(self2):
                if self2.i >= len(rows):
                    raise StopAsyncIteration
                v = rows[self2.i]
                self2.i += 1
                return v
        return _C()


class FakeDB:
    def __init__(self):
        self.signals = _Coll()
        self.pending_entries = _Coll()


class FakeMarket:
    def __init__(self):
        self.tickers = {"BTCUSDT": {"price": 100.0}, "ETHUSDT": {"price": 50.0}}


def _trend_df(n=250, price=100.0):
    rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    base = np.full(n, price, dtype=float)
    return pd.DataFrame({
        "ts": [int(t.timestamp() * 1000) for t in rng],
        "open": base, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": [100.0] * n,
        "ema20": base, "ema50": base, "ema200": base, "rsi": [55.0] * n,
        "macd_hist": [0.1] * n, "adx": [25.0] * n, "atr": [1.0] * n, "vol_ratio": [2.0] * n,
    })


class FakeEngine:
    usage_kind = "signal"

    def __init__(self, kind="crypto", mtf=False, funnel_rich=True):
        self.guard_kind = kind
        self.db = FakeDB()
        self.market = FakeMarket()
        self.analysis = {}
        self.funnel_said = {}
        self.funnel_last = {}
        self.signal_keys = set()
        self.running = True
        self.logs = []
        self.shadows = []
        self.opened = []
        self.live = False
        self.funnel_rich = funnel_rich
        self._open_positions = set()
        self._max_positions = 3
        self._open_fail = None
        if mtf:
            self.funnel_mtf = ("4h", "1h")

    async def log(self, level, text):
        self.logs.append((level, text))

    async def funnel_frame(self, symbol, tf, limit=300):
        p = self.market.tickers.get(symbol, {}).get("price", 100.0)
        return _trend_df(price=p), True

    @staticmethod
    def funnel_htf(tf):
        return "4h"

    @staticmethod
    def funnel_digits(symbol):
        return None

    def funnel_analysis(self, symbol, m):
        return {"htf": None}

    def funnel_ctx(self, symbol):
        return {"modo": "PAPER"}

    async def funnel_state(self, settings):
        used = len(self._open_positions)
        return {"acc": {"equity": 500.0, "daily_pnl_pct": 0.0, "daily_paused": False},
                "open": set(self._open_positions),
                "free": max(0, settings.max_positions - used),
                "block": None}

    async def funnel_open(self, symbol, sig, settings, signal_id, acc):
        self.opened.append((symbol, signal_id))
        self._open_positions.add(symbol)
        return True, ""

    async def funnel_shadow(self, *a, **kw):
        self.shadows.append(a)


def _settings(**over):
    base = dict(ai_min_confidence=60, entry_order_type="market", max_positions=3,
                symbols=["BTCUSDT", "ETHUSDT"], atr_levels_enabled=True, atr_sl_mult=2.0,
                atr_tp_mult=4.0, maker_wait_bars=2, timeframe="15m", risk_per_trade=1.0, leverage=10)
    base.update(over)
    return Settings(**base)


async def _force_in_hours(monkeypatch):
    import trade_guard
    import ai_rules
    monkeypatch.setattr(trade_guard, "hours_status",
                        lambda kind, now=None: {"in_hours": True, "window": "Test", "next_window": None,
                                                "next_change": None, "seconds_left": 0, "ny_time": "",
                                                "windows": []})
    monkeypatch.setattr(trade_guard.calendar, "blocking", lambda kind, symbol: None)
    monkeypatch.setattr(trade_guard.calendar, "events", [])
    monkeypatch.setattr(trade_guard, "affects", lambda kind, symbol, country: False)

    async def _active(db, symbol):
        return []

    async def _hit(db, rid):
        return None
    monkeypatch.setattr(ai_rules, "active", _active)
    monkeypatch.setattr(ai_rules, "hit", _hit)


# =========================================================
# A. overextended / overext_discard matrix
# =========================================================
class TestOverextendedMatrix:
    def _m(self, rsi, dist, rich=True):
        return {"rich": rich, "rsi": rsi, "dist_ema20_atr": dist}

    def test_no_rich_never_discards(self):
        m = self._m(20, -3.0, rich=False)
        assert funnel_mod.overextended(m) is None
        for t in ("ALCISTA", "BAJISTA", "MIXTA", None):
            assert funnel_mod.overext_discard(m, t) is None

    def test_suelo_detect(self):
        m = self._m(30, -2.5)
        assert funnel_mod.overextended(m) == "suelo"

    def test_techo_detect(self):
        m = self._m(70, 2.5)
        assert funnel_mod.overextended(m) == "techo"

    def test_neutral(self):
        assert funnel_mod.overextended(self._m(50, 0.0)) is None
        # RSI ok but ATR extreme: not overextended
        assert funnel_mod.overextended(self._m(50, -3.0)) is None
        # RSI extreme but ATR mild: not overextended
        assert funnel_mod.overextended(self._m(25, -1.0)) is None

    def test_suelo_kept_when_4h_alcista(self):
        m = self._m(30, -2.5)
        assert funnel_mod.overext_discard(m, "ALCISTA") is None

    def test_suelo_discarded_otherwise(self):
        m = self._m(30, -2.5)
        for t in ("BAJISTA", "MIXTA", None):
            assert funnel_mod.overext_discard(m, t) is not None
            assert "suelo" in funnel_mod.overext_discard(m, t)

    def test_techo_kept_when_4h_bajista(self):
        m = self._m(70, 2.5)
        assert funnel_mod.overext_discard(m, "BAJISTA") is None

    def test_techo_discarded_otherwise(self):
        m = self._m(70, 2.5)
        for t in ("ALCISTA", "MIXTA", None):
            r = funnel_mod.overext_discard(m, t)
            assert r is not None and "techo" in r

    def test_stage0_does_not_mention_overextension(self):
        # stage0 only returns ATR/ADX/volume reasons now
        m = {"atr": 1.0, "adx": 25.0, "activity_kind": "volumen", "activity": 2.0}
        assert funnel_mod.stage0(m) is None
        assert funnel_mod.MIN_ADX == 18
        assert funnel_mod.MIN_VOLUME == 0.55

    def test_overext_keep_table(self):
        # Floor rebound = long (bounce from oversold in uptrend); ceiling = short in downtrend
        assert funnel_mod.OVEREXT_KEEP["suelo"] == ("ALCISTA", "long")
        assert funnel_mod.OVEREXT_KEEP["techo"] == ("BAJISTA", "short")


# =========================================================
# B. funnel.run with overext + bias interplay
# =========================================================
def _patch_metrics_stretched(monkeypatch, target_price, kind, atr=1.0):
    """Force metrics() to return a stretched pair for the row whose price == target_price (kind='suelo' or 'techo')."""
    rsi = 25.0 if kind == "suelo" else 75.0
    dist = -2.5 if kind == "suelo" else 2.5
    real_metrics = funnel_mod.metrics

    def fake_metrics(df, i, has_volume, p, rich=False):
        m = real_metrics(df, i, has_volume, p, rich)
        if abs(p - target_price) < 1e-9:
            m["rsi"] = rsi
            m["dist_ema20_atr"] = dist
            m["atr"] = atr
        return m
    monkeypatch.setattr(funnel_mod, "metrics", fake_metrics)


class TestFunnelRunOverext:
    @pytest.mark.asyncio
    async def test_suelo_with_4h_bajista_dropped_in_stage0(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings()
        _patch_metrics_stretched(monkeypatch, 100.0, "suelo")

        async def fake_mtf(engine, symbol, price):
            return {"4h": {"tf": "4h", "trend": "BAJISTA", "rsi": 40, "adx": 20, "dist_ema50_atr": 0, "demand": None, "supply": None},
                    "1h": {"tf": "1h", "trend": "BAJISTA", "rsi": 40, "adx": 20, "dist_ema50_atr": 0}}
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)

        called = {"s1": 0, "s2": 0}

        async def fake_stage1(*a, **kw):
            called["s1"] += 1
            return {"candidates": [], "model": "fast", "ms": 1}

        async def fake_stage2(*a, **kw):
            called["s2"] += 1
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None,
                    "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        # BTCUSDT (suelo 4H BAJISTA) dropped; ETHUSDT (normal) still eligible → stage1 sees only ETH
        assert called["s1"] == 1
        # BTCUSDT marked as DESCARTADO at stage 0
        assert eng.analysis["BTCUSDT"]["funnel"]["state"] == "DESCARTADO"
        assert "sobreextendido" in eng.analysis["BTCUSDT"]["funnel"]["text"]

    @pytest.mark.asyncio
    async def test_suelo_with_4h_alcista_survives_and_short_removed(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings()
        _patch_metrics_stretched(monkeypatch, 100.0, "suelo")

        async def fake_mtf(engine, symbol, price):
            return {"4h": {"tf": "4h", "trend": "ALCISTA", "rsi": 55, "adx": 25, "dist_ema50_atr": 0, "demand": None, "supply": None},
                    "1h": {"tf": "1h", "trend": "ALCISTA", "rsi": 55, "adx": 20, "dist_ema50_atr": 0}}
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)

        s1_calls = []
        s2_calls = []

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            s1_calls.append(list(rows.keys()))
            # Stage1 proposes SHORT on the stretched pair: this must be dropped BEFORE stage2
            return {"candidates": [{"symbol": "BTCUSDT", "side": "short", "reason": "ruptura"}],
                    "model": "fast", "ms": 1}

        async def fake_stage2(kind, symbol, tf, cand, *a, **kw):
            s2_calls.append((symbol, cand["side"]))
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None,
                    "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert "BTCUSDT" in s1_calls[0], "BTC survived stage0"
        assert s2_calls == [], "late-side short on suelo must not reach stage2"
        # A RISK log about dropping the late candidate
        assert any("sobreextendido en suelo" in t and "SHORT" in t for lv, t in eng.logs if lv == "RISK")

    @pytest.mark.asyncio
    async def test_suelo_long_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings()
        _patch_metrics_stretched(monkeypatch, 100.0, "suelo")

        async def fake_mtf(engine, symbol, price):
            return {"4h": {"tf": "4h", "trend": "ALCISTA", "rsi": 55, "adx": 25, "dist_ema50_atr": 0, "demand": None, "supply": None},
                    "1h": {"tf": "1h", "trend": "ALCISTA", "rsi": 55, "adx": 20, "dist_ema50_atr": 0}}
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)

        s2_calls = []

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "rebote"}],
                    "model": "fast", "ms": 1}

        async def fake_stage2(kind, symbol, tf, cand, *a, **kw):
            s2_calls.append((symbol, cand["side"]))
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None,
                    "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert ("BTCUSDT", "long") in s2_calls

    @pytest.mark.asyncio
    async def test_techo_with_4h_alcista_dropped_in_stage0(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings()
        _patch_metrics_stretched(monkeypatch, 100.0, "techo")

        async def fake_mtf(engine, symbol, price):
            return {"4h": {"tf": "4h", "trend": "ALCISTA", "rsi": 60, "adx": 25, "dist_ema50_atr": 0, "demand": None, "supply": None},
                    "1h": {"tf": "1h", "trend": "ALCISTA", "rsi": 60, "adx": 20, "dist_ema50_atr": 0}}
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)

        async def fake_stage1(*a, **kw):
            return {"candidates": [], "model": "fast", "ms": 1}

        async def fake_stage2(*a, **kw):
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None, "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        assert "sobreextendido" in eng.analysis["BTCUSDT"]["funnel"]["text"]

    @pytest.mark.asyncio
    async def test_techo_with_4h_bajista_long_removed_short_reaches_stage2(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        s = _settings()
        _patch_metrics_stretched(monkeypatch, 100.0, "techo")

        async def fake_mtf(engine, symbol, price):
            return {"4h": {"tf": "4h", "trend": "BAJISTA", "rsi": 40, "adx": 25, "dist_ema50_atr": 0, "demand": None, "supply": None},
                    "1h": {"tf": "1h", "trend": "BAJISTA", "rsi": 40, "adx": 20, "dist_ema50_atr": 0}}
        monkeypatch.setattr(funnel_mod, "_mtf", fake_mtf)

        s2_calls = []

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "long", "reason": "x"}], "model": "fast", "ms": 1}

        async def fake_stage2(kind, symbol, tf, cand, *a, **kw):
            s2_calls.append((symbol, cand["side"]))
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None, "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        # Late LONG on techo was removed
        assert s2_calls == []
        # Now test the rebound side (short) reaches stage2
        eng2 = FakeEngine(mtf=True, funnel_rich=True)
        _patch_metrics_stretched(monkeypatch, 100.0, "techo")
        s2b = []

        async def fake_stage1_b(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": "BTCUSDT", "side": "short", "reason": "rebote"}], "model": "fast", "ms": 1}

        async def fake_stage2_b(kind, symbol, tf, cand, *a, **kw):
            s2b.append((symbol, cand["side"]))
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None, "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1_b)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2_b)
        await funnel_mod.run(eng2, s)
        assert ("BTCUSDT", "short") in s2b

    @pytest.mark.asyncio
    async def test_fx_engine_without_funnel_rich_does_not_discard(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(kind="fx", mtf=False, funnel_rich=False)
        s = _settings()
        # Even with stretched RSI/ATR numbers, non-rich metrics won't have 'rich' key
        _patch_metrics_stretched(monkeypatch, 100.0, "suelo")

        s1_rows = []

        async def fake_stage1(kind, tf, rows, limit, usage_kind):
            s1_rows.append(list(rows.keys()))
            return {"candidates": [], "model": "fast", "ms": 1}

        async def fake_stage2(*a, **kw):
            return {"decision": "PASS", "confidence": 0, "sl": None, "tp": None, "rule": None, "reasoning": "", "model": "deep", "ms": 1}
        monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
        monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)

        await funnel_mod.run(eng, s)
        # No funnel_rich → metrics has no 'rich' key → overextended returns None → BTCUSDT remains eligible
        assert s1_rows and "BTCUSDT" in s1_rows[0]


# =========================================================
# C. metrics(rich=True) extras and _row_txt extras
# =========================================================
def _choppy_df(n=250, price=100.0):
    rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    # oscillating close around EMA20 to force many crosses
    osc = price + np.sin(np.arange(n) * 0.8) * 2.0
    ema20 = pd.Series(osc).ewm(span=20, adjust=False).mean().values
    return pd.DataFrame({
        "ts": [int(t.timestamp() * 1000) for t in rng],
        "open": osc, "high": osc + 0.5, "low": osc - 0.5, "close": osc, "volume": [100.0] * n,
        "ema20": ema20, "ema50": osc, "ema200": osc, "rsi": [55.0] * n,
        "macd_hist": [0.1] * n, "adx": [25.0] * n, "atr": [1.0] * n, "vol_ratio": [2.0] * n,
    })


class TestMetricsRich:
    def test_rich_adds_keys(self):
        df = _trend_df()
        i = len(df) - 2
        m = funnel_mod.metrics(df, i, True, 100.0, rich=True)
        for k in ("rich", "ema9", "ema21", "fast_cross", "adx_slope", "swing_high",
                  "swing_low", "swing_high_atr", "swing_low_atr", "ema20_crosses", "regime"):
            assert k in m, k
        assert m["rich"] is True
        assert m["fast_cross"] in ("ALCISTA", "BAJISTA")
        assert m["adx_slope"] in ("SUBIENDO", "BAJANDO", "PLANA")

    def test_rich_false_omits_keys(self):
        df = _trend_df()
        i = len(df) - 2
        m = funnel_mod.metrics(df, i, True, 100.0, rich=False)
        assert "rich" not in m
        assert "ema9" not in m
        assert "regime" not in m
        # _row_txt: no extra line
        txt = funnel_mod._row_txt("BTCUSDT", m)
        assert "cruce_emas_rapidas" not in txt
        assert "pendiente_adx" not in txt

    def test_row_txt_rich_includes_extras(self):
        df = _trend_df()
        i = len(df) - 2
        m = funnel_mod.metrics(df, i, True, 100.0, rich=True)
        m["tf"] = "15m"
        txt = funnel_mod._row_txt("BTCUSDT", m)
        assert "cruce_emas_rapidas" in txt
        assert "pendiente_adx" in txt
        assert "swing high" in txt and "swing low" in txt

    def test_choppy_regime(self):
        df = _choppy_df()
        i = len(df) - 2
        m = funnel_mod.metrics(df, i, True, df["close"].iloc[i], rich=True)
        assert m["ema20_crosses"] >= funnel_mod.RANGE_CROSSES
        assert m["regime"] == "RANGO / CHOPPY"

    def test_trend_regime_none(self):
        df = _trend_df()
        i = len(df) - 2
        m = funnel_mod.metrics(df, i, True, 100.0, rich=True)
        # Trend df is flat (no crosses): regime None
        assert m["regime"] is None

    def test_swing_high_touches_actual_bar(self):
        df = _trend_df()
        i = len(df) - 2
        m = funnel_mod.metrics(df, i, True, 100.0, rich=True)
        last20_high = float(df["high"].iloc[max(0, i - funnel_mod.SWING_BARS + 1):i + 1].max())
        assert m["swing_high"] <= last20_high + 1e-9


# =========================================================
# D. funnel.levels with buffer_atr and risk.position_size
# =========================================================
class TestLevelsBuffer:
    def _s(self, **kw):
        return _settings(**kw)

    def test_long_ai_sl_moved_by_025_atr(self):
        s = self._s(atr_levels_enabled=False)
        price, atr = 100.0, 1.0
        ai_sl, ai_tp = 98.8, 103.0   # risk=1.2, reward=3 (comfortable above MIN_RR even with buffer)
        sl, tp, src = funnel_mod.levels("long", price, atr, s, ai_sl=ai_sl, ai_tp=ai_tp, buffer_atr=0.25)
        assert src == "ia"
        assert abs(sl - (98.8 - 0.25)) < 1e-9
        assert abs(tp - 103.0) < 1e-9

    def test_short_ai_sl_moved_by_025_atr(self):
        s = self._s(atr_levels_enabled=False)
        price, atr = 100.0, 1.0
        ai_sl, ai_tp = 101.2, 97.0
        sl, tp, src = funnel_mod.levels("short", price, atr, s, ai_sl=ai_sl, ai_tp=ai_tp, buffer_atr=0.25)
        assert src == "ia"
        assert abs(sl - (101.2 + 0.25)) < 1e-9

    def test_atr_source_ignores_buffer(self):
        s = self._s(atr_levels_enabled=True)
        price, atr = 100.0, 1.0
        sl_no_buf, _, src1 = funnel_mod.levels("long", price, atr, s, ai_sl=99.0, ai_tp=103.0, buffer_atr=0.0)
        sl_buf, _, src2 = funnel_mod.levels("long", price, atr, s, ai_sl=99.0, ai_tp=103.0, buffer_atr=0.25)
        assert src1 == "atr" and src2 == "atr"
        assert sl_no_buf == sl_buf

    def test_buffer_breaks_rr_falls_back_to_atr(self):
        s = self._s(atr_levels_enabled=False)
        price, atr = 100.0, 1.0
        # risk=1.2, reward=1.3 → R:R OK without buffer, but with buffer 0.25 ATR new risk=1.45 → reward=1.3<1.45 → ATR fallback
        sl, tp, src = funnel_mod.levels("long", price, atr, s, ai_sl=98.8, ai_tp=101.3, buffer_atr=0.25)
        assert src == "atr"
        # ATR fallback: sl = 100 - 2*1 = 98
        assert abs(sl - 98.0) < 1e-9

    def test_position_size_risk_including_stop_cost(self):
        equity, risk_pct = 1000.0, 1.0
        entry, sl = 100.0, 99.0
        qty, margin = risk_mod.position_size(equity, equity, risk_pct, entry, sl, 10)
        loss = (abs(entry - sl) + risk_mod.stop_cost(entry, sl)) * qty
        assert loss <= equity * risk_pct / 100 + 1e-6


# =========================================================
# E. maker.queue_entry live cases
# =========================================================
class FakeBrokerOK:
    def __init__(self, best=None, raise_on_place=False):
        self.placed = []
        self._best = best or {"bid": 99.8, "ask": 100.2}
        self.raise_on_place = raise_on_place

        class _Client:
            def __init__(self2, best):
                self2._best = best

            async def best(self2, symbol):
                return self2._best
        self.client = _Client(self._best)

    async def open_limit(self, symbol, side, qty, px, sl, tp, leverage):
        self.placed.append({"symbol": symbol, "side": side, "qty": qty, "price": px, "sl": sl, "tp": tp})
        if self.raise_on_place:
            raise RuntimeError("Bybit rejected 110045")
        return {"order_id": f"O{len(self.placed)}", "link_id": f"L{len(self.placed)}", "qty": qty}


class LiveEngine(FakeEngine):
    def __init__(self, best_book=None, place_sequence=None, live_size_result=None, best_exc=False):
        super().__init__()
        self.live = True
        self._best_exc = best_exc
        # place_sequence: list of booleans, True=raise, False=ok
        self._place_sequence = list(place_sequence or [False])
        self._live_size_result = live_size_result if live_size_result != "none" else None
        if live_size_result is None:
            self._live_size_result = {"qty": 0.001, "margin": 10.0, "equity": 1000.0}
        self.open_position_calls = []

        class _Client:
            def __init__(self2, best, exc):
                self2._best = best
                self2._exc = exc

            async def best(self2, symbol):
                if self2._exc:
                    raise RuntimeError("connection reset")
                return self2._best

        class _Broker:
            def __init__(self2, outer):
                self2.outer = outer
                self2.client = _Client(best_book or {"bid": 99.8, "ask": 100.2}, best_exc)
                self2.placed = []

            async def open_limit(self2, symbol, side, qty, px, sl, tp, leverage):
                raise_it = self2.outer._place_sequence.pop(0) if self2.outer._place_sequence else False
                self2.placed.append({"symbol": symbol, "side": side, "qty": qty, "price": px, "sl": sl, "tp": tp})
                if raise_it:
                    raise RuntimeError("Bybit rejected 110045")
                return {"order_id": f"O{len(self2.placed)}", "link_id": f"L{len(self2.placed)}", "qty": qty}
        self.broker = _Broker(self)

    async def live_size(self, symbol, entry, sl, risk_pct, leverage):
        if self._live_size_result is None:
            self._open_fail = "tamaño por debajo del lote mínimo o margen insuficiente"
            return None
        return self._live_size_result

    async def open_position(self, *a, **kw):
        self.open_position_calls.append((a, kw))
        return {"ok": True}


def _sig(side="long", price=100.0, atr=1.0, sl=98.0, tp=104.0):
    return {"side": side, "price": price, "atr": atr, "sl": sl, "tp": tp, "strategy": "ai_funnel", "score": 2}


_FAKE_OID = "a" * 24


class TestQueueEntryLive:
    @pytest.mark.asyncio
    async def test_happy_path_long(self):
        eng = LiveEngine(place_sequence=[False])
        sig = _sig("long")
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is not None
        assert len(eng.broker.placed) == 1
        assert len(eng.db.pending_entries.docs) == 1
        assert eng.open_position_calls == []

    @pytest.mark.asyncio
    async def test_retry_after_reject_long(self):
        # First attempt raises; retry best bid=99.95 close to original 99.98 (<0.15 ATR)
        orig_limit = maker_mod.limit_price("long", 100.0)   # 99.98
        best = {"bid": 99.95, "ask": 100.05}
        eng = LiveEngine(best_book=best, place_sequence=[True, False])
        sig = _sig("long")
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is not None
        assert len(eng.broker.placed) == 2
        # Second placement at the bid
        assert abs(eng.broker.placed[1]["price"] - 99.95) < 1e-9
        # SL/TP distances preserved
        d2 = eng.broker.placed[1]
        assert abs((100.0 - 98.0) - (d2["price"] - d2["sl"])) < 1e-9
        assert abs((104.0 - 100.0) - (d2["tp"] - d2["price"])) < 1e-9
        assert any("reintento" in t for lv, t in eng.logs if lv == "ORDER")
        assert len(eng.db.pending_entries.docs) == 1

    @pytest.mark.asyncio
    async def test_retry_after_reject_short(self):
        orig_limit = maker_mod.limit_price("short", 100.0)   # 100.02
        best = {"bid": 99.9, "ask": 100.08}
        eng = LiveEngine(best_book=best, place_sequence=[True, False])
        sig = _sig("short", sl=102.0, tp=96.0)
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is not None
        # Second placement at ask
        assert abs(eng.broker.placed[1]["price"] - 100.08) < 1e-9

    @pytest.mark.asyncio
    async def test_two_rejects_discard(self):
        best = {"bid": 99.95, "ask": 100.05}
        eng = LiveEngine(best_book=best, place_sequence=[True, True])
        sig = _sig("long")
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is None
        assert eng.open_position_calls == []
        assert len(eng.db.pending_entries.docs) == 0
        assert eng._open_fail
        assert any("entrada descartada (sin orden a mercado)" in t for lv, t in eng.logs if lv == "RISK")

    @pytest.mark.asyncio
    async def test_price_moved_too_far(self):
        # best bid far from original limit (>0.15 ATR with atr=1.0 → >0.15)
        best = {"bid": 99.5, "ask": 100.5}
        eng = LiveEngine(best_book=best, place_sequence=[True])
        sig = _sig("long", atr=1.0)
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is None
        assert len(eng.broker.placed) == 1   # No second placement
        assert eng.open_position_calls == []

    @pytest.mark.asyncio
    async def test_live_size_none_discards(self):
        eng = LiveEngine(place_sequence=[False], live_size_result="none")
        sig = _sig("long")
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is None
        assert eng.open_position_calls == []
        assert len(eng.db.pending_entries.docs) == 0

    @pytest.mark.asyncio
    async def test_best_raises_discards(self):
        eng = LiveEngine(place_sequence=[True], best_exc=True)
        sig = _sig("long")
        s = _settings(entry_order_type="maker")
        res = await maker_mod.queue_entry(eng, "BTCUSDT", sig, s, _FAKE_OID, 1.0, "normal", "short", "15m")
        assert res is None
        assert eng.open_position_calls == []


# =========================================================
# F. maker.monitor_pending scenarios
# =========================================================
class _MonitorEngine(FakeEngine):
    def __init__(self, live=False, order_states=None):
        super().__init__()
        self.live = live
        self.open_position_calls = []
        self._order_states = order_states or []

        class _Client:
            def __init__(self2, states):
                self2.states = states
                self2.i = 0

            async def order(self2, symbol, order_id):
                if self2.i < len(self2.states):
                    v = self2.states[self2.i]
                    self2.i += 1
                    return v
                return self2.states[-1] if self2.states else {}

        class _Broker:
            def __init__(self2, states):
                self2.client = _Client(states)
                self2.cancelled = []

            async def cancel(self2, symbol, order_id):
                self2.cancelled.append((symbol, order_id))
                return True
        self.broker = _Broker(self._order_states) if live else None

    async def get_settings(self):
        return _settings(entry_order_type="maker")

    async def open_position(self, symbol, sig, settings, signal_id, risk_factor, risk_reason, horizon="short", entry_type="market", fill=None, exit_mode=None):
        self.open_position_calls.append({"symbol": symbol, "entry_type": entry_type, "fill": fill})
        return {"ok": True}

    async def account_summary(self):
        return {"equity": 500.0}


class TestMonitorPending:
    @pytest.mark.asyncio
    async def test_paper_expired_discarded(self, monkeypatch):
        eng = _MonitorEngine(live=False)
        sig = _sig("long")
        # Insert expired paper pending
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await eng.db.pending_entries.insert_one({"symbol": "BTCUSDT", "side": "long", "sig": sig,
                                                 "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "limit_price": 99.98,
                                                 "sl": 97.98, "tp": 103.98, "expires_at": past,
                                                 "wait_bars": 2, "live": False, "strategy": "ai_funnel", "risk_factor": 1.0, "risk_reason": "normal", "horizon": "short"})
        await maker_mod.monitor_pending(eng, {"BTCUSDT": {"price": 100.1}})
        assert len(eng.db.pending_entries.docs) == 0
        assert eng.open_position_calls == []

    @pytest.mark.asyncio
    async def test_paper_fill_when_price_crosses(self):
        eng = _MonitorEngine(live=False)
        sig = _sig("long")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await eng.db.pending_entries.insert_one({"symbol": "BTCUSDT", "side": "long", "sig": sig,
                                                 "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "limit_price": 99.98,
                                                 "sl": 97.98, "tp": 103.98, "expires_at": future,
                                                 "wait_bars": 2, "live": False, "strategy": "ai_funnel", "risk_factor": 1.0, "risk_reason": "normal", "horizon": "short"})
        # Price crossed below limit (long fill condition)
        await maker_mod.monitor_pending(eng, {"BTCUSDT": {"price": 99.9}})
        assert len(eng.db.pending_entries.docs) == 0
        assert len(eng.open_position_calls) == 1
        assert eng.open_position_calls[0]["entry_type"] == "maker"

    @pytest.mark.asyncio
    async def test_live_cancelled_no_fill_discarded(self):
        eng = _MonitorEngine(live=True, order_states=[{"status": "Cancelled", "exec_qty": 0}])
        sig = _sig("long")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await eng.db.pending_entries.insert_one({"symbol": "BTCUSDT", "side": "long", "sig": sig,
                                                 "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "limit_price": 99.98,
                                                 "sl": 97.98, "tp": 103.98, "expires_at": future,
                                                 "wait_bars": 2, "live": True, "strategy": "ai_funnel", "risk_factor": 1.0, "risk_reason": "normal", "horizon": "short",
                                                 "order_id": "O1", "link_id": "L1"})
        await maker_mod.monitor_pending(eng, {"BTCUSDT": {"price": 100.0}})
        assert eng.open_position_calls == []
        assert len(eng.db.pending_entries.docs) == 0

    @pytest.mark.asyncio
    async def test_live_expired_partial_fill_opens(self):
        # First order() call returns a "New" status (not Filled/Cancelled), then after cancel, returns partial exec_qty
        states = [{"status": "New", "exec_qty": 0}, {"status": "Cancelled", "exec_qty": 0.001, "avg_price": 99.97, "fee": 0.01}]
        eng = _MonitorEngine(live=True, order_states=states)
        sig = _sig("long")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await eng.db.pending_entries.insert_one({"symbol": "BTCUSDT", "side": "long", "sig": sig,
                                                 "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "limit_price": 99.98,
                                                 "sl": 97.98, "tp": 103.98, "expires_at": past,
                                                 "wait_bars": 2, "live": True, "strategy": "ai_funnel", "risk_factor": 1.0, "risk_reason": "normal", "horizon": "short",
                                                 "order_id": "O1", "link_id": "L1"})
        await maker_mod.monitor_pending(eng, {"BTCUSDT": {"price": 100.0}})
        assert len(eng.open_position_calls) == 1
        call = eng.open_position_calls[0]
        assert call["entry_type"] == "maker"
        assert call["fill"]["qty"] == 0.001
        # And NOT market_fallback
        assert call["entry_type"] != "market_fallback"

    @pytest.mark.asyncio
    async def test_live_expired_no_fill_discarded(self):
        states = [{"status": "New", "exec_qty": 0}, {"status": "Cancelled", "exec_qty": 0}]
        eng = _MonitorEngine(live=True, order_states=states)
        sig = _sig("long")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await eng.db.pending_entries.insert_one({"symbol": "BTCUSDT", "side": "long", "sig": sig,
                                                 "signal_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "limit_price": 99.98,
                                                 "sl": 97.98, "tp": 103.98, "expires_at": past,
                                                 "wait_bars": 2, "live": True, "strategy": "ai_funnel", "risk_factor": 1.0, "risk_reason": "normal", "horizon": "short",
                                                 "order_id": "O1", "link_id": "L1"})
        await maker_mod.monitor_pending(eng, {"BTCUSDT": {"price": 100.0}})
        assert eng.open_position_calls == []
        assert len(eng.db.pending_entries.docs) == 0
        assert eng.broker.cancelled == [("BTCUSDT", "O1")]


# =========================================================
# G. API smoke (dashboards + pending entries)
# =========================================================
def _read_fe_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_fe_env()).rstrip("/")


@pytest.fixture(scope="module")
def token():
    import requests
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


class TestApiSmoke:
    def test_dashboards(self, token):
        import requests
        h = {"Authorization": f"Bearer {token}"}
        for p in ("/api/dashboard", "/api/fx/dashboard", "/api/pepper/dashboard"):
            r = requests.get(f"{BASE_URL}{p}", headers=h, timeout=25)
            assert r.status_code == 200, f"{p} -> {r.status_code}"

    def test_pending_entries(self, token):
        import requests
        h = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"{BASE_URL}/api/market/context", headers=h, timeout=25)
        assert r.status_code == 200
        data = r.json()
        assert "pending_entries" in data

    def test_settings_toggles_on_three_bots(self, token):
        import requests
        h = {"Authorization": f"Bearer {token}"}
        keys = ("atr_levels_enabled", "breakeven_enabled", "trailing_enabled", "partial_tp_enabled")
        for p in ("/api/settings", "/api/fx/settings", "/api/pepper/settings"):
            r = requests.get(f"{BASE_URL}{p}", headers=h, timeout=25)
            assert r.status_code == 200
            d = r.json()
            for k in keys:
                assert d.get(k) is True, f"{p}:{k} not True → {d.get(k)}"
