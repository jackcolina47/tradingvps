import asyncio
from types import SimpleNamespace
import market
from market import next_candle_scan

D = 1_780_000_000 - 1_780_000_000 % 86400  # a UTC midnight


def test_schedule():
    assert next_candle_scan("15m", D + 1) == D + 8
    assert next_candle_scan("15m", D + 8) == D + 908
    assert next_candle_scan("15m", D + 899) == D + 908
    assert next_candle_scan("1h", D + 3600 + 9) == D + 7208
    assert next_candle_scan("4h", D + 100) == D + 14408
    assert next_candle_scan("5m", D + 301) == D + 308


def test_loop_normal_only_at_candle_close(monkeypatch):
    clock = {"t": D + 880.0}
    calls = []
    monkeypatch.setattr(market.time, "time", lambda: clock["t"])

    class Ev:
        def clear(self):
            pass

        async def wait(self):
            raise asyncio.TimeoutError

    async def wait_for(coro, timeout):
        coro.close()
        clock["t"] += timeout
        if len(calls) >= 6:
            raise asyncio.CancelledError
        raise asyncio.TimeoutError

    async def sleep(s):
        pass
    monkeypatch.setattr(market.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(market.asyncio, "sleep", sleep)

    class Pos:
        async def count_documents(self, q):
            return 0

    async def run(ema):
        calls.clear()
        clock["t"] = D + 880.0
        st = SimpleNamespace(timeframe="15m", ema_test_enabled=ema, scan_interval_sec=60)

        async def gs():
            return st

        async def scan(settings, normal):
            calls.append((round(clock["t"] - D), normal))
        e = SimpleNamespace(candle_sync=True, scan_full=False, scan_event=Ev(), get_settings=gs, scan_once=scan, db=SimpleNamespace(positions=Pos()), next_scan_ts=None)
        try:
            await market.candle_scan_loop(e)
        except asyncio.CancelledError:
            pass
        return list(calls)
    off = asyncio.run(run(False))
    assert off[0] == (880, False) and [c for c in off[1:]] == [(908, True), (1808, True), (2708, True), (3608, True), (4508, True)]
    on = asyncio.run(run(True))
    assert on[:3] == [(880, False), (908, True), (968, False)] and all(n is False for t, n in on[2:])
