"""Phase 1: operating hours (New York, every day) and the red-news guard (±15 min block, breakeven / close before the event)."""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
import trade_guard as tg
from models import Position

NY = ZoneInfo("America/New_York")


def ny(day, h, m=0):
    return datetime(2026, 11, day, h, m, tzinfo=NY).astimezone(timezone.utc)   # Nov 2026: 16 Mon … 20 Fri, 21 Sat, 22 Sun


@pytest.mark.parametrize("h,m,ok", [(2, 59, False), (3, 0, True), (3, 59, True), (4, 0, True), (7, 59, True), (8, 0, True), (11, 59, True), (12, 0, True),
                                    (14, 59, True), (15, 0, False), (16, 55, False), (18, 59, False), (19, 0, True), (23, 59, True), (0, 0, False)])
def test_fx_windows(h, m, ok):
    assert tg.in_hours("fx", ny(18, h, m)) is ok


@pytest.mark.parametrize("h,m,ok", [(0, 0, True), (3, 0, True), (5, 0, True), (8, 29, True), (12, 0, True), (16, 30, True), (23, 0, True), (23, 59, True)])
def test_crypto_windows(h, m, ok):
    assert tg.in_hours("crypto", ny(18, h, m)) is ok


def test_weekends():
    assert tg.in_hours("crypto", ny(21, 9)) and tg.in_hours("crypto", ny(22, 20))      # Bybit: Saturday and Sunday too
    assert not tg.in_hours("fx", ny(21, 9)) and not tg.in_hours("fx", ny(20, 19))      # forex market closed (Sat, Fri after 17:00)
    assert not tg.in_hours("fx", ny(22, 9)) and tg.in_hours("fx", ny(22, 19))          # reopens Sunday 17:00 → 19:00 window


def test_hours_status_countdown():
    st = tg.hours_status("crypto", ny(18, 6))
    assert st["in_hours"] and st["window"] == "24 horas" and st["next_change"] is None and st["seconds_left"] is None
    st = tg.hours_status("fx", ny(18, 1))
    assert not st["in_hours"] and st["next_window"] == "Londres + Nueva York" and st["seconds_left"] == 120 * 60
    st = tg.hours_status("fx", ny(18, 9))
    assert st["in_hours"] and st["window"] == "Londres + Nueva York" and st["seconds_left"] == 6 * 3600
    st = tg.hours_status("fx", ny(18, 16))   # 15:00–19:00 pause → next window is the night session
    assert not st["in_hours"] and st["next_window"] == "Sídney + Tokio" and st["seconds_left"] == 3 * 3600
    assert [(w["start"], w["end"]) for w in st["windows"]] == [("03:00", "15:00"), ("19:00", "00:00")]
    st = tg.hours_status("fx", ny(20, 16))   # Friday afternoon → next tradable window is Sunday 19:00
    assert datetime.fromisoformat(st["next_change"]) == ny(22, 19)
    assert tg.hours_status("fx", ny(18, 20))["seconds_left"] == 4 * 3600   # 19:00–00:00 ends at midnight


def test_affects():
    assert tg.affects("fx", "EURUSD", "USD") and tg.affects("fx", "EURUSD", "EUR") and not tg.affects("fx", "EURUSD", "JPY")
    assert tg.affects("fx", "XAUUSD", "USD") and not tg.affects("fx", "XAUUSD", "EUR")
    assert tg.affects("crypto", "BTCUSDT", "USD") and not tg.affects("crypto", "BTCUSDT", "EUR") and tg.affects("crypto", "BTCUSDT", "All")


@pytest.fixture
def event(monkeypatch):
    t = ny(18, 8, 30)
    e = {"key": "k1", "title": "CPI m/m", "country": "USD", "time": t.isoformat()}
    monkeypatch.setattr(tg.calendar, "events", [e])
    tg._protect_tried.clear()
    return t


def test_block_window(event):
    for delta, blocked in [(-16, False), (-15, True), (0, True), (15, True), (16, False)]:
        assert bool(tg.calendar.blocking("fx", "EURUSD", event + timedelta(minutes=delta))) is blocked
    assert tg.calendar.blocking("fx", "EURJPY", event) is None
    assert tg.entry_block("fx", "EURUSD", event)[1] == "NOTICIAS" and tg.entry_block("fx", "EURJPY", event) == (None, None)
    assert tg.entry_block("fx", "EURJPY", ny(18, 16))[1] == "HORARIO"
    assert tg.calendar.pre_event("crypto", "BTCUSDT", event - timedelta(minutes=10)) and not tg.calendar.pre_event("crypto", "BTCUSDT", event + timedelta(minutes=1))


def test_parse_only_high():
    rows = [{"title": "A", "country": "USD", "date": "2026-11-18T08:30:00-05:00", "impact": "High"}, {"title": "B", "country": "EUR", "date": "2026-11-18T05:00:00-05:00", "impact": "Medium"}]
    out = tg.RedCalendar.parse(rows)
    assert [e["title"] for e in out] == ["A"] and out[0]["time"] == "2026-11-18T13:30:00+00:00"


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n):
        return list(self.docs)


class _Positions:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *a):
        return _Cursor(self.docs)

    async def update_one(self, q, u):
        next(d for d in self.docs if d["_id"] == q["_id"]).update(u["$set"])


class _Engine:
    guard_kind = "fx"

    def __init__(self, docs, ok=True):
        self.db = type("DB", (), {"positions": _Positions(docs)})()
        self.calls, self.ok = [], ok

    async def news_protect(self, p: Position, e):
        self.calls.append(p.symbol)
        return self.ok


def _doc(i, symbol):
    return {"_id": str(i), "symbol": symbol, "side": "long", "strategy": "x", "entry_price": 1.0, "qty": 1, "leverage": 1, "margin": 1, "sl": 0.9, "tp": 1.2,
            "initial_sl": 0.9, "atr": 0.01, "fees": 0, "mark_price": 1.0}


def test_protect_once_per_event(event):
    eng = _Engine([_doc(1, "EURUSD"), _doc(2, "EURJPY")])
    now = event - timedelta(minutes=10)
    asyncio.run(tg.protect_positions(eng, now))
    asyncio.run(tg.protect_positions(eng, now))
    assert eng.calls == ["EURUSD"] and eng.db.positions.docs[0]["news_guard"] == "k1" and "news_guard" not in eng.db.positions.docs[1]
    eng = _Engine([_doc(3, "EURUSD")])
    asyncio.run(tg.protect_positions(eng, event - timedelta(minutes=20)))
    assert eng.calls == []


def test_protect_retries_after_failure(event):
    eng = _Engine([_doc(4, "EURUSD")], ok=False)
    now = event - timedelta(minutes=10)
    asyncio.run(tg.protect_positions(eng, now))
    assert "news_guard" not in eng.db.positions.docs[0]
    asyncio.run(tg.protect_positions(eng, now))
    assert eng.calls == ["EURUSD"]            # throttled for 30 s
    tg._protect_tried.clear()
    asyncio.run(tg.protect_positions(eng, now))
    assert eng.calls == ["EURUSD", "EURUSD"]
