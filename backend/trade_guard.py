"""Operating hours (New York time, every day) and the red-news guard shared by the three bots:
no new entries outside the windows or within ±15 min of a high-impact event of an affected currency; 15 min before the event
open positions in profit go to breakeven and positions in loss are closed."""
import time
import asyncio
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import httpx
from models import Position
from fx.symbols import SPECS, CURRENCY_NAMES
from fx.market import market_open

logger = logging.getLogger("guard")
NY = ZoneInfo("America/New_York")
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
BEFORE_MIN = AFTER_MIN = 15
RETRY_SEC = 600
PROTECT_RETRY_SEC = 30
# (name, start minute, end minute) in New York local time
WINDOWS = {
    "fx": [("Londres + Nueva York", 180, 900), ("Sídney + Tokio", 1140, 1440)],   # 03:00–15:00 and 19:00–00:00 NY · pause 15:00–19:00 (rollover) and 00:00–03:00
    "crypto": [("24 horas", 0, 1440)],   # Bybit trades around the clock, every day
}


def _utc(now: datetime = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _hhmm(minute: int) -> str:
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def _tradable(kind: str, at: datetime) -> bool:
    return kind != "fx" or market_open(at)


def active_window(kind: str, now: datetime = None):
    now = _utc(now)
    ny = now.astimezone(NY)
    m = ny.hour * 60 + ny.minute
    if not _tradable(kind, now):
        return None
    return next((w for w in WINDOWS[kind] if w[1] <= m < w[2]), None)


def in_hours(kind: str, now: datetime = None) -> bool:
    return active_window(kind, now) is not None


FRIDAY_BLOCK_MIN, FRIDAY_FLAT_MIN, FRIDAY_END_MIN = 12 * 60, 15 * 60 + 30, 17 * 60   # New York time (forex bots)


def friday(now: datetime = None) -> dict:
    """Forex Friday rule: no new entries from 12:00 NY, every open position is closed from 15:30 NY until the weekly close (17:00 NY)."""
    ny = _utc(now).astimezone(NY)
    mins = ny.hour * 60 + ny.minute
    fri = ny.weekday() == 4
    return {"block": fri and mins >= FRIDAY_BLOCK_MIN, "flat": fri and FRIDAY_FLAT_MIN <= mins < FRIDAY_END_MIN}


def hours_status(kind: str, now: datetime = None) -> dict:
    now = _utc(now)
    ny = now.astimezone(NY)
    midnight = ny.replace(hour=0, minute=0, second=0, microsecond=0)
    win = active_window(kind, now)
    if win and win[2] - win[1] >= 1440:
        change, nxt = None, None   # always open: nothing to count down to
    elif win:
        change, nxt = (midnight + timedelta(minutes=win[2])).astimezone(timezone.utc), None
    else:
        change = None
        for day in range(9):
            for w in WINDOWS[kind]:
                start = (midnight + timedelta(days=day, minutes=w[1])).astimezone(timezone.utc)
                if start > now and _tradable(kind, start + timedelta(seconds=1)) and (change is None or start < change):
                    change, nxt = start, w[0]
            if change:
                break
    return {"in_hours": bool(win), "window": win[0] if win else None, "next_window": nxt, "next_change": change.isoformat() if change else None,
            "seconds_left": max(0, int((change - now).total_seconds())) if change else None, "ny_time": ny.strftime("%H:%M"), "timezone": "America/New_York",
            "market_open": _tradable(kind, now),
            "windows": [{"name": w[0], "start": _hhmm(w[1]), "end": _hhmm(w[2])} for w in sorted(WINDOWS[kind], key=lambda w: w[1])]}


def affects(kind: str, symbol: str, country: str) -> bool:
    """Crypto (USDT pairs) reacts to USD events; a forex/metal pair to events of either of its two currencies."""
    if country == "All":
        return True
    if kind == "crypto":
        return country == "USD"
    spec = SPECS.get(symbol)
    return bool(spec) and country in (spec["base"], spec["quote"])


class RedCalendar:
    """High-impact (red) events of the week, downloaded once per New York day; the last good copy stays in Mongo."""

    def __init__(self):
        self.db = None
        self.events = []
        self.fetched_day = None
        self.fetched_at = None
        self.error = None
        self._retry_at = 0.0
        self.task = None

    async def start(self, db):
        self.db = db
        doc = await db.red_calendar.find_one({"_id": "latest"}) or {}
        self.events, self.fetched_day, self.fetched_at = doc.get("events", []), doc.get("day"), doc.get("fetched_at")
        self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            try:
                if datetime.now(NY).strftime("%Y-%m-%d") != self.fetched_day and time.time() >= self._retry_at:
                    await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("calendar loop")
            await asyncio.sleep(60)

    @staticmethod
    def parse(rows: list) -> list:
        out = []
        for e in rows:
            if e.get("impact") != "High":
                continue
            try:
                t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc).isoformat()
            except (KeyError, ValueError):
                continue
            out.append({"key": hashlib.sha1(f"{e.get('title')}|{e.get('country')}|{t}".encode()).hexdigest()[:12], "title": e.get("title", ""),
                        "country": e.get("country", ""), "country_name": CURRENCY_NAMES.get(e.get("country"), e.get("country", "")), "time": t,
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
        return sorted(out, key=lambda x: x["time"])

    async def refresh(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = await c.get(CALENDAR_URL)
                r.raise_for_status()
                events = self.parse(r.json())
        except Exception as e:
            self.error = f"calendario no disponible: {str(e)[:120]}"
            self._retry_at = time.time() + RETRY_SEC
            logger.warning(self.error)
            return False
        self.events, self.error = events, None
        self.fetched_day, self.fetched_at = datetime.now(NY).strftime("%Y-%m-%d"), datetime.now(timezone.utc).isoformat()
        await self.db.red_calendar.update_one({"_id": "latest"}, {"$set": {"events": events, "day": self.fetched_day, "fetched_at": self.fetched_at}}, upsert=True)
        logger.info(f"calendario rojo actualizado · {len(events)} eventos de alto impacto esta semana")
        return True

    def blocking(self, kind: str, symbol: str, now: datetime = None):
        """The red event whose ±15 min window contains `now` for this pair, if any."""
        now = _utc(now)
        for e in self.events:
            t = datetime.fromisoformat(e["time"])
            if t - timedelta(minutes=BEFORE_MIN) <= now <= t + timedelta(minutes=AFTER_MIN) and affects(kind, symbol, e["country"]):
                return e
        return None

    def pre_event(self, kind: str, symbol: str, now: datetime = None):
        """The red event due within the next 15 min for this pair, if any."""
        now = _utc(now)
        for e in self.events:
            t = datetime.fromisoformat(e["time"])
            if t - timedelta(minutes=BEFORE_MIN) <= now < t and affects(kind, symbol, e["country"]):
                return e
        return None

    async def aclose(self):
        if self.task:
            self.task.cancel()


calendar = RedCalendar()
_protect_tried = {}


def event_label(e: dict) -> str:
    return f"{e['country']} · {e['title']} · {datetime.fromisoformat(e['time']).astimezone(NY).strftime('%H:%M')} NY"


def entry_block(kind: str, symbol: str, now: datetime = None):
    """(reason, code) that forbids a new automatic entry right now, or (None, None)."""
    if not in_hours(kind, now):
        st = hours_status(kind, now)
        return f"fuera del horario operativo · próxima ventana {st['next_window'] or 'n/d'}", "HORARIO"
    e = calendar.blocking(kind, symbol, now)
    if e:
        return f"evento rojo ({event_label(e)}) · bloqueo de {BEFORE_MIN} min antes a {AFTER_MIN} min después", "NOTICIAS"
    return None, None


async def protect_positions(engine, now: datetime = None):
    """15 min before a red event: affected positions in profit → breakeven, in loss → closed. Each position is handled once per event."""
    for doc in await engine.db.positions.find().to_list(200):
        e = calendar.pre_event(engine.guard_kind, doc["symbol"], now)
        if not e or doc.get("news_guard") == e["key"]:
            continue
        tried = (str(doc["_id"]), e["key"])
        if time.time() - _protect_tried.get(tried, 0) < PROTECT_RETRY_SEC:
            continue
        _protect_tried[tried] = time.time()
        if await engine.news_protect(Position.from_mongo(doc), e):
            await engine.db.positions.update_one({"_id": doc["_id"]}, {"$set": {"news_guard": e["key"]}})


def status(kind: str, symbols: list, now: datetime = None) -> dict:
    now = _utc(now)
    events = []
    for e in calendar.events:
        t = datetime.fromisoformat(e["time"])
        start, end = t - timedelta(minutes=BEFORE_MIN), t + timedelta(minutes=AFTER_MIN)
        events.append({**e, "block_from": start.isoformat(), "block_to": end.isoformat(), "ny_time": t.astimezone(NY).strftime("%a %d %H:%M"),
                       "state": "PASADO" if now > end else "BLOQUEANDO" if now >= start else "PROXIMO",
                       "affected": [s for s in symbols if affects(kind, s, e["country"])]})
    blocked = {s: event_label(e) for s in symbols if (e := calendar.blocking(kind, s, now))}
    return {"hours": hours_status(kind, now), "before_min": BEFORE_MIN, "after_min": AFTER_MIN, "blocked": blocked, "events": events,
            "calendar": {"fetched_at": calendar.fetched_at, "day": calendar.fetched_day, "error": calendar.error, "source": "ForexFactory"}}
