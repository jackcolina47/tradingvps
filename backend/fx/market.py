"""Yahoo Finance market data for forex & metals (no API key). Forex hours + session calendar."""
import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import httpx
from fx.symbols import SPECS, CONVERSION

logger = logging.getLogger("fx.market")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
SPARK = "https://query1.finance.yahoo.com/v7/finance/spark"
COOLDOWN_SEC = 90
MIN_GAP_SEC = 0.5
# tf -> (yahoo interval, range for ~limit candles, aggregate factor)
TF_MAP = {"1m": ("1m", "5d", 1), "5m": ("5m", "30d", 1), "15m": ("15m", "60d", 1), "1h": ("60m", "180d", 1), "4h": ("60m", "730d", 4), "1d": ("1d", "2y", 1)}
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
# how far back Yahoo serves each interval (intraday history is capped)
YAHOO_MAX_DAYS = {"1m": 7, "5m": 60, "15m": 60, "1h": 730, "4h": 730, "1d": 36500}
HTF = {"1m": "15m", "5m": "1h", "15m": "1h", "1h": "4h", "4h": "1d"}
NY, LONDON, TOKYO, SYDNEY = ZoneInfo("America/New_York"), ZoneInfo("Europe/London"), ZoneInfo("Asia/Tokyo"), ZoneInfo("Australia/Sydney")
SESSIONS = [("sydney", "Sídney", SYDNEY, 7, 16), ("tokyo", "Tokio", TOKYO, 9, 18), ("london", "Londres", LONDON, 8, 17), ("newyork", "Nueva York", NY, 8, 17)]
SESSION_IDS = [s[0] for s in SESSIONS]
SESSION_NAMES = {s[0]: s[1] for s in SESSIONS}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def market_open(now: datetime = None) -> bool:
    """Forex is open from Sunday 17:00 to Friday 17:00 New York time."""
    ny = (now or datetime.now(timezone.utc)).astimezone(NY)
    wd, h = ny.weekday(), ny.hour + ny.minute / 60
    if wd == 5:
        return False
    if wd == 4 and h >= 17:
        return False
    if wd == 6 and h < 17:
        return False
    return True


def next_change(now: datetime = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    ny = now.astimezone(NY)
    if market_open(now):
        days = (4 - ny.weekday()) % 7
        target = (ny + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
        if target <= ny:
            target += timedelta(days=7)
    else:
        days = (6 - ny.weekday()) % 7
        target = (ny + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
        if target <= ny:
            target += timedelta(days=7)
    return target.astimezone(timezone.utc)


def friday_stage(now: datetime = None) -> str:
    """Friday rule (New York time): "block" from 12:00 (no new entries), "flat" from 15:30 (everything is closed), else ""."""
    ny = (now or datetime.now(timezone.utc)).astimezone(NY)
    h = ny.hour + ny.minute / 60
    return "" if ny.weekday() != 4 or h < 12 else "flat" if h >= 15.5 else "block"


def closing_soon(minutes: int = 30, now: datetime = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return market_open(now) and (next_change(now) - now) <= timedelta(minutes=minutes)


def sessions(now: datetime = None) -> list:
    now = now or datetime.now(timezone.utc)
    out = []
    for sid, name, tz, o, c in SESSIONS:
        loc = now.astimezone(tz)
        active = market_open(now) and loc.weekday() < 5 and o <= loc.hour < c
        out.append({"id": sid, "name": name, "open": active, "local": loc.strftime("%H:%M")})
    return out


def open_sessions(now: datetime = None) -> list:
    return [s["id"] for s in sessions(now) if s["open"]]


def in_sessions(allowed, now: datetime = None) -> bool:
    """True when no session filter is set or at least one allowed session is open."""
    return not allowed or any(s in allowed for s in open_sessions(now))


def session_names(ids) -> str:
    return ", ".join(SESSION_NAMES[s] for s in ids if s in SESSION_NAMES)


def market_status(now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"open": market_open(now), "next_change": next_change(now).isoformat(), "closing_soon": closing_soon(30, now), "sessions": sessions(now)}


def _aggregate(rows: list, factor: int, step_sec: int) -> list:
    out, bucket = [], {}
    for ts, o, h, lo, c, v in rows:
        key = ts - ts % (step_sec * 1000)
        if key not in bucket:
            bucket[key] = [key, o, h, lo, c, v]
            out.append(bucket[key])
        else:
            b = bucket[key]
            b[2], b[3], b[4], b[5] = max(b[2], h), min(b[3], lo), c, b[5] + v
    return out


def _normalize(rows: list, step_sec: int) -> list:
    """Yahoo appends the forming bar twice: once at its bucket start and once more stamped with the last trade time (O=H=L=C). Snap every
    timestamp to the bucket start and fold duplicates, so a timeframe never shows two candles for the same period (chart) and `len-2` really
    is the last closed candle (engine)."""
    out, bucket = [], {}
    for ts, o, h, lo, c, v in rows:
        key = ts - ts % (step_sec * 1000)
        if key not in bucket:
            bucket[key] = [key, o, h, lo, c, v]
            out.append(bucket[key])
        else:
            b = bucket[key]
            b[2], b[3], b[4], b[5] = max(b[2], h), min(b[3], lo), c, max(b[5], v)
    return out


class FxMarket:
    provider = "yahoo"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=12, headers=HEADERS, follow_redirects=True)
        self.tickers = {}
        self.tickers_ts = 0.0
        self._kcache = {}
        self.error = None
        self._lock = asyncio.Lock()
        self._last_req = 0.0
        self._cooldown_until = 0.0

    async def detect(self):
        try:
            await self.fetch_tickers(["EURUSD"], cache_sec=0)
            self.error = None
        except Exception as e:
            self.error = str(e)[:160]
            logger.warning(f"Yahoo no accesible: {e}")
        return self.provider

    async def _get(self, url: str, params: dict) -> dict:
        """Serialized, throttled GET with a cooldown after a 429 so the bot never hammers Yahoo."""
        if time.time() < self._cooldown_until:
            raise RuntimeError(f"Yahoo en pausa por límite de peticiones ({int(self._cooldown_until - time.time())}s)")
        async with self._lock:
            gap = MIN_GAP_SEC - (time.time() - self._last_req)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last_req = time.time()
            r = await self.client.get(url, params=params)
        if r.status_code == 429:
            self._cooldown_until = time.time() + COOLDOWN_SEC
            raise RuntimeError("Yahoo: límite de peticiones (429)")
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"Yahoo: respuesta no válida (HTTP {r.status_code})")

    async def _chart(self, ticker: str, params: dict) -> dict:
        data = await self._get(CHART.format(t=ticker), params)
        res = (data.get("chart") or {}).get("result")
        if not res:
            err = ((data.get("chart") or {}).get("error") or {}).get("description") or "sin resultado"
            raise RuntimeError(f"Yahoo {ticker}: {err}")
        return res[0]

    @staticmethod
    def _rows(res: dict) -> list:
        ts, q = res.get("timestamp") or [], (res.get("indicators") or {}).get("quote", [{}])[0]
        rows = []
        for i, t in enumerate(ts):
            o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, lo, c):
                continue
            rows.append([int(t) * 1000, float(o), float(h), float(lo), float(c), float((q.get("volume") or [0] * len(ts))[i] or 0)])
        return rows

    async def fetch_tickers(self, symbols: list = None, cache_sec: int = 20) -> dict:
        """One spark request for every wanted symbol (+ conversion pairs): price, day change, high/low."""
        wanted = list(dict.fromkeys((symbols or list(SPECS)) + CONVERSION))
        if self.tickers and time.time() - self.tickers_ts < cache_sec and all(s in self.tickers for s in wanted):
            return self.tickers
        by_yahoo = {SPECS[s]["yahoo"]: s for s in wanted}
        try:
            data = await self._get(SPARK, {"symbols": ",".join(by_yahoo), "range": "1d", "interval": "15m"})
            results = (data.get("spark") or {}).get("result") or []
        except Exception as e:
            self.error = str(e)[:160]
            if self.tickers:
                return self.tickers
            raise
        out = dict(self.tickers)
        for item in results:
            sym = by_yahoo.get(item.get("symbol"))
            resp = (item.get("response") or [{}])[0]
            meta = resp.get("meta") or {}
            price = meta.get("regularMarketPrice")
            if not sym or price is None:
                continue
            closes = [c for c in ((resp.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [] if c is not None]
            out[sym] = {"symbol": sym, "price": float(price), "change_pct": float(meta.get("regularMarketChangePercent") or 0),
                        "high": float(meta.get("regularMarketDayHigh") or max(closes, default=price)), "low": float(meta.get("regularMarketDayLow") or min(closes, default=price)),
                        "volume_usdt": float(meta.get("regularMarketVolume") or 0), "spread": SPECS[sym]["spread"], "updated_at": int(meta.get("regularMarketTime") or time.time())}
        missing = [s for s in wanted if s not in out]
        self.error = f"sin precio para {', '.join(missing[:4])}" if missing and len(missing) == len(wanted) else None
        if missing:
            logger.warning(f"tickers sin precio: {missing[:6]}")
        self.tickers, self.tickers_ts = out, time.time()
        return out

    async def fetch_klines(self, symbol: str, tf: str, limit: int = 300, cache_sec: int = 30) -> list:
        key = (symbol, tf, limit)
        hit = self._kcache.get(key)
        if hit and time.time() - hit[0] < cache_sec:
            return hit[1]
        interval, rng, factor = TF_MAP[tf]
        if tf == "1m":
            rng = "1d" if limit <= 300 else "5d"
        elif tf == "5m":
            rng = "5d" if limit <= 300 else "30d"
        elif tf == "15m":
            rng = "10d" if limit <= 300 else "60d"
        elif tf == "1h":
            rng = "60d" if limit <= 300 else "180d"
        elif tf == "4h":
            rng = "120d" if limit <= 300 else "730d"
        try:
            res = await self._chart(SPECS[symbol]["yahoo"], {"interval": interval, "range": rng, "includePrePost": "false"})
            rows = _normalize(self._rows(res), TF_SECONDS[tf] // factor)
        except Exception as e:
            if hit:
                logger.warning(f"klines {symbol} {tf}: {e} → caché de hace {int(time.time() - hit[0])}s")
                return hit[1]
            raise RuntimeError(f"sin datos de mercado para {symbol} {tf}: {e}")
        if factor > 1:
            rows = _aggregate(rows, factor, TF_SECONDS[tf])
        rows = rows[-limit:]
        if not rows:
            raise RuntimeError(f"sin velas para {symbol} {tf}")
        self._kcache[key] = (time.time(), rows)
        return rows

    async def fetch_before(self, symbol: str, tf: str, end_ts: int, limit: int = 500, cache_sec: int = 600) -> list:
        """Up to `limit` candles strictly older than `end_ts` (unix s) via Yahoo period1/period2; window ×1.5 covers weekends. Empty once Yahoo's
        history cap for the interval is reached."""
        key = (symbol, tf, "before", end_ts, limit)
        hit = self._kcache.get(key)
        if hit and time.time() - hit[0] < cache_sec:
            return hit[1]
        interval, _, factor = TF_MAP[tf]
        step = TF_SECONDS[tf]
        earliest = int(time.time()) - YAHOO_MAX_DAYS[tf] * 86400 + 3600
        start = max(earliest, end_ts - int(limit * step * 1.5))
        if start >= end_ts:
            return []
        res = await self._chart(SPECS[symbol]["yahoo"], {"interval": interval, "period1": start, "period2": end_ts, "includePrePost": "false"})
        rows = _normalize(self._rows(res), step // factor)
        if factor > 1:
            rows = _aggregate(rows, factor, step)
        rows = [r for r in rows if r[0] // 1000 < end_ts][-limit:]
        self._kcache[key] = (time.time(), rows)
        return rows

    async def aclose(self):
        await self.client.aclose()
