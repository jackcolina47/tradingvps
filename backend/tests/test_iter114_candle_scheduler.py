"""Iter114 — the funnel scan follows the candle close of each bot's timeframe (+5 s, UTC), always evaluates closed candles and adapts the
higher-timeframe context (15m → 4H/1H · 1h → 1D/4H)."""
import asyncio
import inspect
from datetime import datetime, timezone
import pandas as pd
import pytest
import funnel
from tests.test_iter103_bybit_hardening import FakeEngine, _settings, _force_in_hours


def utc(h, m=0, s=0, day=18):
    return datetime(2026, 11, day, h, m, s, tzinfo=timezone.utc).timestamp()


def hms(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


class TestNextTrigger:
    def test_delay_is_5_seconds(self):
        assert funnel.TRIGGER_DELAY_SEC == 5 and not hasattr(funnel, "TRIGGER_MIN")

    @pytest.mark.parametrize("tf,now,expected,wait", [
        ("15m", utc(14, 7, 30), "14:15:05", 455), ("15m", utc(14, 0, 0), "14:00:05", 5), ("15m", utc(14, 0, 5), "14:15:05", 900), ("15m", utc(14, 44, 59), "14:45:05", 6),
        ("15m", utc(14, 45, 6), "15:00:05", 899),
        ("1h", utc(14, 7, 30), "15:00:05", 3155), ("1h", utc(14, 15, 5), "15:00:05", 2700), ("1h", utc(14, 59, 59), "15:00:05", 6), ("1h", utc(15, 0, 5), "16:00:05", 3600),
        ("4h", utc(14, 7, 30), "16:00:05", 6755), ("4h", utc(0, 0, 0), "00:00:05", 5), ("4h", utc(3, 59, 59), "04:00:05", 6), ("4h", utc(20, 0, 5), "00:00:05", 14400),
        ("5m", utc(14, 7, 30), "14:10:05", 155), ("30m", utc(14, 7, 30), "14:30:05", 1355), ("1m", utc(14, 7, 30), "14:08:05", 35),
    ])
    def test_exact_wait(self, tf, now, expected, wait):
        t = funnel.next_trigger(tf, now)
        assert hms(t) == expected and t - now == wait

    def test_15m_marks(self):
        t, got = utc(14, 0, 0), []
        for _ in range(5):
            t = funnel.next_trigger("15m", t)
            got.append(hms(t))
        assert got == ["14:00:05", "14:15:05", "14:30:05", "14:45:05", "15:00:05"]

    def test_1h_only_on_the_hour(self):
        t, got = utc(13, 59, 0), []
        for _ in range(3):
            t = funnel.next_trigger("1h", t)
            got.append(hms(t))
        assert got == ["14:00:05", "15:00:05", "16:00:05"]

    def test_4h_utc_marks(self):
        t, got = utc(23, 0, 0, day=17), []
        for _ in range(6):
            t = funnel.next_trigger("4h", t)
            got.append(hms(t))
        assert got == ["00:00:05", "04:00:05", "08:00:05", "12:00:05", "16:00:05", "20:00:05"]

    def test_default_is_15m(self):
        assert funnel.next_trigger(now=utc(14, 7)) == funnel.next_trigger("15m", utc(14, 7))


class _Eng:
    def __init__(self):
        self.logs = []

    async def log(self, level, text):
        self.logs.append((level, text))


class TestSchedule:
    @pytest.mark.asyncio
    async def test_logs_on_start_and_on_timeframe_change_only(self, monkeypatch):
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 7, 30))
        eng = _Eng()
        assert hms(await funnel.schedule(eng, "15m")) == "14:15:05"
        assert eng.logs == [("INFO", "Próximo escaneo al cierre de vela 15m: 14:15:05 UTC")]
        await funnel.schedule(eng, "15m")
        assert len(eng.logs) == 1
        assert hms(await funnel.schedule(eng, "1h")) == "15:00:05"
        assert eng.logs[-1] == ("INFO", "Próximo escaneo al cierre de vela 1h: 15:00:05 UTC") and len(eng.logs) == 2
        assert hms(await funnel.schedule(eng, "4h")) == "16:00:05" and eng.logs[-1][1].endswith("vela 4h: 16:00:05 UTC")

    def test_both_engines_schedule_with_their_own_timeframe(self):
        import engine
        from fx import engine_signals
        for mod in (engine, engine_signals):
            src = inspect.getsource(mod)
            assert "await funnel.schedule(self, settings.timeframe)" in src and "next_trigger()" not in src

    @pytest.mark.asyncio
    async def test_scan_loop_recalculates_without_restart(self, monkeypatch):
        """Changing the timeframe (update_settings sets scan_event) wakes the loop, which reschedules with the new timeframe."""
        import engine as crypto
        monkeypatch.setattr(funnel.time, "time", lambda: utc(14, 7, 30))
        real_sleep = asyncio.sleep
        monkeypatch.setattr(crypto.asyncio, "sleep", lambda s: real_sleep(0))

        class Loop(_Eng):
            def __init__(self):
                super().__init__()
                self.tf, self.scan_event, self.scans, self.next_scan_ts = "15m", asyncio.Event(), [], None

            async def get_settings(self):
                return _settings(timeframe=self.tf)

            async def scan_once(self, settings):
                self.scans.append(settings.timeframe)
        eng = Loop()
        task = asyncio.create_task(crypto.TradingEngine._scan_loop(eng))
        for _ in range(20):
            await real_sleep(0)
        assert eng.scans == ["15m"] and hms(eng.next_scan_ts) == "14:15:05"
        eng.tf = "1h"
        eng.scan_event.set()
        for _ in range(20):
            await real_sleep(0)
        task.cancel()
        assert eng.scans == ["15m", "1h"] and hms(eng.next_scan_ts) == "15:00:05"
        assert [t for _, t in eng.logs] == ["Próximo escaneo al cierre de vela 15m: 14:15:05 UTC", "Próximo escaneo al cierre de vela 1h: 15:00:05 UTC"]


def _df(last_open_ts, n=10, step=900):
    return pd.DataFrame({"ts": [int((last_open_ts - (n - 1 - k) * step) * 1000) for k in range(n)]})


class TestClosedCandles:
    def test_forming_candle_is_skipped(self):
        """t + 5 s with the new candle already published: evaluate the one that just closed."""
        df = _df(utc(14, 15))
        assert funnel.closed_index(df, "15m", utc(14, 15, 5)) == len(df) - 2
        assert funnel.closed_index(df, "15m", utc(14, 29, 59)) == len(df) - 2

    def test_new_candle_not_published_yet(self):
        """t + 5 s and the exchange still ends on the candle that just closed: that is the one to evaluate, not the previous one."""
        df = _df(utc(14, 0))
        assert funnel.closed_index(df, "15m", utc(14, 15, 5)) == len(df) - 1
        assert funnel.closed_index(df, "15m", utc(14, 14, 59)) == len(df) - 2

    @pytest.mark.parametrize("tf,step", [("1h", 3600), ("4h", 14400), ("1d", 86400)])
    def test_other_timeframes(self, tf, step):
        df = _df(utc(12), step=step)
        assert funnel.closed_index(df, tf, utc(12) + step - 1) == len(df) - 2
        assert funnel.closed_index(df, tf, utc(12) + step + 5) == len(df) - 1

    @pytest.mark.parametrize("tf,now,default,expected", [("15m", utc(14, 15, 5), 30, 4), ("15m", utc(14, 20), 30, 30), ("1h", utc(15, 0, 5), 45, 4),
                                                         ("1h", utc(15, 0, 40), 60, 39), ("4h", utc(16, 0, 5), 300, 4), ("4h", utc(17), 300, 300), ("15m", utc(14, 15, 0), 30, 0)])
    def test_cache_never_older_than_the_candle_close(self, tf, now, default, expected):
        assert funnel.fresh_sec(tf, default, now) == expected

    def test_engines_refetch_after_the_close(self):
        import engine
        from fx import engine_signals
        assert "cache_sec=funnel.fresh_sec(tf, 30)" in inspect.getsource(engine.TradingEngine.funnel_frame)
        assert inspect.getsource(engine_signals.SignalsMixin.funnel_frame).count("funnel.fresh_sec(tf,") == 2

    @pytest.mark.asyncio
    async def test_run_evaluates_the_closed_candle(self, monkeypatch):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        seen, real = [], funnel.metrics

        def spy(df, i, has_volume, p, rich=False):
            seen.append((i, len(df), int(df["ts"].iloc[-1]) // 1000))
            return real(df, i, has_volume, p, rich)
        monkeypatch.setattr(funnel, "metrics", spy)

        async def none(*a, **k):
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", none)
        await funnel.run(eng, _settings(symbols=["BTCUSDT"]))
        i, n, last = seen[0]
        assert i == n - 1                                   # synthetic frame ends long ago: its last row is closed
        monkeypatch.setattr(funnel.time, "time", lambda: last + 5)   # 5 s into the last row: it is the forming candle
        seen.clear()
        await funnel.run(eng, _settings(symbols=["BTCUSDT"]))
        assert seen[0][0] == n - 2


class TestContextHierarchy:
    @pytest.mark.parametrize("tf,ctx", [("15m", ("4h", "1h")), ("5m", ("4h", "1h")), ("30m", ("4h", "1h")), ("1h", ("1d", "4h")), ("4h", ("1d", "4h"))])
    def test_mtf_of(self, tf, ctx):
        assert funnel.mtf_of(tf) == ctx

    def test_ttl_for_every_context_frame(self):
        assert all(t in funnel.MTF_TTL and t in funnel.TF_SECONDS for ctx in (*funnel.MTF_BY_TF.values(), funnel.MTF_DEFAULT) for t in ctx)

    def test_relabel(self):
        txt = "Evalúa primero 4H, después 1H; a favor de 4H/1H"
        assert funnel._relabel(txt, ("4h", "1h")) == txt and funnel._relabel(txt, None) == txt
        assert funnel._relabel(txt, ("1d", "4h")) == "Evalúa primero 1D, después 4H; a favor de 1D/4H"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tf,frames", [("15m", ["4h", "1h"]), ("1h", ["1d", "4h"])])
    async def test_run_fetches_the_right_frames(self, monkeypatch, tf, frames):
        await _force_in_hours(monkeypatch)
        eng = FakeEngine(mtf=True, funnel_rich=True)
        asked, real = [], eng.funnel_frame

        async def frame(symbol, t, limit=300):
            asked.append(t)
            return await real(symbol, t, limit)
        eng.funnel_frame = frame
        got = {}

        async def s1(kind, t, rows, limit, usage_kind):
            got.update(rows)
            return {"candidates": [], "model": "fast", "ms": 1}
        monkeypatch.setattr(funnel, "stage1", s1)
        await funnel.run(eng, _settings(symbols=["BTCUSDT"], timeframe=tf))
        assert asked == [tf, *frames]
        m = got["BTCUSDT"]
        assert list(m["mtf"]) == frames and "demand" in m["mtf"][frames[0]] and "demand" not in m["mtf"][frames[1]]
        assert eng.analysis["BTCUSDT"]["mtf"].keys() == set(frames)
        assert f"lado a favor de {frames[0].upper()}" in funnel._mtf_txt(m)
        assert any(f"Embudo {'/'.join(f.upper() for f in frames)} → {tf}" in t for lv, t in eng.logs if lv == "SCAN")

    def test_locks_follow_the_context(self):
        m = {"atr": 1.0, "price": 100.0, "close": 100.0, "ema20": 100.2, "ema50": 99.4, "swing_high": 101.5, "swing_low": 98.5, "turn": None,
             "mtf": {"1d": {"trend": "ALCISTA"}, "4h": {"trend": "ALCISTA"}}}
        assert funnel.pullback_block(m, "long") is None
        m["mtf"]["1d"]["trend"] = "MIXTA"
        assert funnel.pullback_block(m, "long") == "1D y 4H no apoyan el lado"
        z = {"low": 101.5, "high": 102.0, "touches": 2, "dist_atr": 1.5}
        r = {"atr": 1.0, "tf": "1h", "supply": None, "demand": None, "mtf": {"1d": {"supply": z, "demand": None}, "4h": {}}}
        assert "oferta de 1d" in funnel.rr_block(r, "long", 100.0, 1.25, 2.0, False) and "75%" in funnel.rr_block(r, "long", 100.0, 1.25, 2.0, False)

    def test_monitor_loops_untouched(self):
        import engine
        from fx import engine as fxe
        for cls in (engine.TradingEngine, fxe.FxEngine):
            src = inspect.getsource(cls._monitor_loop)
            assert "asyncio.sleep(5)" in src and "next_trigger" not in src and "schedule" not in src
