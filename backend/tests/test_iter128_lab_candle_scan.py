"""Iter128: Bybit Labs scan once per closed candle of their timeframe, 7 s after the close (no more fixed 60 s scans)."""
import asyncio
import time
from types import SimpleNamespace as NS

import lab as lab_mod
import market


def test_next_candle_scan_delay():
    base = 1_700_000_000 - 1_700_000_000 % 14400
    assert market.next_candle_scan("4h", base + 3, 7) == base + 7 and market.next_candle_scan("4h", base + 7, 7) == base + 14400 + 7
    assert market.next_candle_scan("1h", base + 100, 7) == base + 3607 and market.next_candle_scan("1h", base + 100) == base + 3600 + market.CANDLE_DELAY_SEC
    L = lab_mod.LabEngine
    assert (L.candle_sync, L.candle_only, L.candle_delay) == (True, True, 7)


def test_lab_loop_scans_only_at_candle_close(monkeypatch):
    scans, t0 = [], time.time()
    monkeypatch.setattr(market, "next_candle_scan", lambda tf, now, delay=8: t0 + 0.6 * (int((now - t0) / 0.6) + 1))  # a «candle» every 0.6 s

    async def sleep0(_):
        return None
    real_sleep = asyncio.sleep
    monkeypatch.setattr(market.asyncio, "sleep", sleep0)

    class Pos:
        async def count_documents(self, q):
            return 3  # open cross positions must NOT trigger in-between scans in the labs

    async def run(only):
        scans.clear()
        e = NS(candle_sync=True, candle_only=only, candle_delay=7, scan_full=False, scan_event=asyncio.Event(), next_scan_ts=None, db=NS(positions=Pos()))

        async def get_settings():
            return NS(timeframe="1h", ema_test_enabled=True, scan_interval_sec=60)

        async def scan_once(settings, normal):
            scans.append((round(time.time() - t0, 1), normal))
        e.get_settings, e.scan_once = get_settings, scan_once
        monkeypatch.setattr(market, "TEST_SCAN_SEC", 0.1)
        task = asyncio.create_task(market.candle_scan_loop(e))
        await real_sleep(1.5)
        e.scan_full = True   # «Escanear ahora»
        e.scan_event.set()
        await real_sleep(0.1)
        task.cancel()
        return list(scans)
    lab = asyncio.run(run(True))
    t0 = time.time()
    main = asyncio.run(run(False))
    assert len(lab) == 4 and [n for _, n in lab[1:]] == [True, True, True]  # startup + 2 candle closes + manual scan
    assert any(not n for _, n in main[1:])  # other bots keep their in-between «test» scans
