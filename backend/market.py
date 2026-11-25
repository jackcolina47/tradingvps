import time
import asyncio
import logging
import httpx

logger = logging.getLogger("market")

BYBIT_URL = "https://api.bybit.com"
BITGET_URL = "https://api.bitget.com"
TF_MAP = {"1m": ("1", "1m"), "5m": ("5", "5m"), "15m": ("15", "15m"), "1h": ("60", "1H"), "4h": ("240", "4H"), "1d": ("D", "1D")}
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
HTF = {"1m": "15m", "5m": "1h", "15m": "1h", "1h": "4h", "4h": "1d"}


CANDLE_DELAY_SEC = 8   # entries are scanned this long after each candle close, so the exchange has consolidated the closed candle
TEST_SCAN_SEC = 60     # cadence of the «Test cruce EMA» scan, which is not tied to the candle clock


def next_candle_scan(tf: str, now: float, delay: float = CANDLE_DELAY_SEC) -> float:
    """Next UTC-aligned candle close of `tf` + CANDLE_DELAY_SEC (15m → hh:00:08, hh:15:08…; 4h → 00:00:08, 04:00:08… UTC)."""
    sec = TF_SECONDS[tf]
    due = now - now % sec + delay
    return due if now < due else due + sec


async def candle_scan_loop(engine):
    """Entry scanner shared by every bot. Normal strategies (and their AI calls) run once per closed candle, CANDLE_DELAY_SEC after the close;
    the «Test cruce EMA» keeps its own fixed cadence, except in engines with `candle_only` (Bybit Labs: one scan per closed candle, `candle_delay` seconds after it). SL/TP/trailing live in the independent monitor loop."""
    await asyncio.sleep(2)
    tf, due, first = None, 0.0, True
    while True:
        settings = await engine.get_settings()
        now = time.time()
        sync, delay, only = engine.candle_sync, getattr(engine, "candle_delay", CANDLE_DELAY_SEC), getattr(engine, "candle_only", False)
        if settings.timeframe != tf:
            tf, due = settings.timeframe, next_candle_scan(settings.timeframe, now, delay)
        normal = not sync or engine.scan_full or now >= due
        if now >= due:
            due = next_candle_scan(tf, now, delay)
        engine.scan_full = False
        test = not only and (not sync or settings.ema_test_enabled or await engine.db.positions.count_documents({"exit_mode": "cross"}) > 0)
        if normal or test or first:
            try:
                await engine.scan_once(settings, normal)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("scan error")
                await engine.log("ERROR", f"Error en escaneo: {e}")
        first = False
        wait = settings.scan_interval_sec if not sync else max(0.5, min(due - time.time(), TEST_SCAN_SEC) if test else due - time.time())
        engine.next_scan_ts = time.time() + wait if not sync else due
        try:
            await asyncio.wait_for(engine.scan_event.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        engine.scan_event.clear()


class MarketData:
    """Bybit public data with automatic Bitget USDT-perp mirror when Bybit is geo-blocked or fails."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=12)
        self.provider = None
        self.tickers = {}
        self.tickers_ts = 0.0
        self._kcache = {}
        self._fallback_warned = 0.0
        self.funding_intervals = {}
        self._intervals_ts = 0.0

    async def detect(self):
        try:
            r = await self.client.get(f"{BYBIT_URL}/v5/market/time")
            if r.status_code == 200 and r.json().get("retCode") == 0:
                self.provider = "bybit"
                return self.provider
        except Exception as e:
            logger.warning(f"Bybit no accesible: {e}")
        self.provider = "bitget"
        return self.provider

    async def _bybit(self, path: str, params: dict):
        err = "Bybit sin respuesta"
        for attempt in range(2):
            try:
                r = await self.client.get(f"{BYBIT_URL}{path}", params=params)
                data = r.json()
                if r.status_code == 200 and data.get("retCode") == 0:
                    return data["result"]
                err = f"Bybit {data.get('retCode', r.status_code)}: {data.get('retMsg') or 'error'}"
            except Exception as e:
                err = f"Bybit: {type(e).__name__} {str(e)[:80]}"
            await asyncio.sleep(0.4 * (attempt + 1))
        raise RuntimeError(err)

    async def _bitget(self, path: str, params: dict):
        r = await self.client.get(f"{BITGET_URL}{path}", params=params)
        data = r.json()
        if r.status_code != 200 or str(data.get("code")) != "00000":
            raise RuntimeError(f"Bitget {data.get('code', r.status_code)}: {data.get('msg') or 'error'}")
        return data["data"]

    def _warn_fallback(self, what: str, err: str):
        if time.time() - self._fallback_warned > 300:
            self._fallback_warned = time.time()
            logger.warning(f"{what}: {err} → usando espejo Bitget")

    async def fetch_tickers(self) -> dict:
        out = {}
        if self.provider == "bybit":
            try:
                rows = (await self._bybit("/v5/market/tickers", {"category": "linear"}))["list"]
            except RuntimeError as e:
                self._warn_fallback("tickers", str(e))
                rows = None
            if rows is not None:
                for t in rows:
                    out[t["symbol"]] = {
                        "symbol": t["symbol"], "price": float(t["lastPrice"]),
                        "change_pct": float(t.get("price24hPcnt") or 0) * 100,
                        "high": float(t.get("highPrice24h") or 0), "low": float(t.get("lowPrice24h") or 0),
                        "volume_usdt": float(t.get("turnover24h") or 0),
                        "funding_rate": float(t.get("fundingRate") or 0),
                    }
        if not out:
            for t in await self._bitget("/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"}):
                out[t["symbol"]] = {
                    "symbol": t["symbol"], "price": float(t["lastPr"]),
                    "change_pct": float(t.get("change24h") or 0) * 100,
                    "high": float(t.get("high24h") or 0), "low": float(t.get("low24h") or 0),
                    "volume_usdt": float(t.get("quoteVolume") or 0),
                    "funding_rate": float(t.get("fundingRate") or 0),
                }
        intervals = await self.fetch_funding_intervals()
        for sym, t in out.items():
            t["funding_interval_h"] = intervals.get(sym, 8)
        self.tickers = out
        self.tickers_ts = time.time()
        return out

    async def fetch_funding_intervals(self, cache_sec: int = 3600) -> dict:
        """Funding settlement interval in hours per symbol (Bybit instruments-info / Bitget contracts). Cached 1h, retried in 5 min on failure."""
        if self.funding_intervals and time.time() - self._intervals_ts < cache_sec:
            return self.funding_intervals
        out = {}
        if self.provider == "bybit":
            try:
                cursor = ""
                while True:
                    res = await self._bybit("/v5/market/instruments-info", {"category": "linear", "limit": 1000, **({"cursor": cursor} if cursor else {})})
                    for it in res["list"]:
                        mins = int(it.get("fundingInterval") or 0)
                        if mins:
                            out[it["symbol"]] = mins // 60 if mins % 60 == 0 else mins / 60
                    cursor = res.get("nextPageCursor") or ""
                    if not cursor:
                        break
            except RuntimeError as e:
                self._warn_fallback("intervalos de funding", str(e))
                out = {}
        if not out:
            try:
                for it in await self._bitget("/api/v2/mix/market/contracts", {"productType": "USDT-FUTURES"}):
                    h = float(it.get("fundInterval") or 0)
                    if h:
                        out[it["symbol"]] = int(h) if h.is_integer() else h
            except Exception as e:
                logger.warning(f"intervalos de funding: {e} → se asume 8h")
                self._intervals_ts = time.time() - cache_sec + 300
                return self.funding_intervals
        self.funding_intervals, self._intervals_ts = out, time.time()
        return out

    async def fetch_klines(self, symbol: str, tf: str, limit: int = 300, cache_sec: int = 8) -> list:
        key = (symbol, tf, limit)
        hit = self._kcache.get(key)
        if hit and time.time() - hit[0] < cache_sec:
            return hit[1]
        bybit_tf, bitget_tf = TF_MAP[tf]
        rows = None
        if self.provider == "bybit":
            try:
                rows = list(reversed((await self._bybit("/v5/market/kline", {
                    "category": "linear", "symbol": symbol, "interval": bybit_tf, "limit": limit}))["list"]))
            except RuntimeError as e:
                self._warn_fallback(f"klines {symbol} {tf}", str(e))
        if rows is None:
            try:
                rows = await self._bitget("/api/v2/mix/market/candles", {
                    "symbol": symbol, "granularity": bitget_tf, "limit": limit, "productType": "USDT-FUTURES"})
            except Exception as e:
                if hit:
                    logger.warning(f"klines {symbol} {tf}: {e} → usando caché de hace {int(time.time() - hit[0])}s")
                    return hit[1]
                raise RuntimeError(f"sin datos de mercado para {symbol} {tf}: {e}")
        data = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in rows]
        self._kcache[key] = (time.time(), data)
        return data

    async def fetch_before(self, symbol: str, tf: str, end_ms: int, limit: int = 500, cache_sec: int = 600) -> list:
        """Up to `limit` candles strictly older than `end_ms` for the chart's infinite history (Bybit `end` is inclusive → end-1; Bitget
        history-candles `endTime` is exclusive, 200/page)."""
        key = (symbol, tf, "before", end_ms, limit)
        hit = self._kcache.get(key)
        if hit and time.time() - hit[0] < cache_sec:
            return hit[1]
        bybit_tf, bitget_tf = TF_MAP[tf]
        data, end = [], end_ms
        while len(data) < limit:
            rows = None
            if self.provider == "bybit":
                try:
                    rows = (await self._bybit("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": bybit_tf,
                                                                    "limit": min(1000, limit - len(data)), "end": end - 1}))["list"]
                except RuntimeError as e:
                    self._warn_fallback(f"historial {symbol} {tf}", str(e))
            if rows is None:
                rows = await self._bitget("/api/v2/mix/market/history-candles", {"symbol": symbol, "granularity": bitget_tf, "limit": min(200, limit - len(data)),
                                                                                   "productType": "USDT-FUTURES", "endTime": end})
            older = sorted([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in rows if int(k[0]) < end)
            if not older:
                break
            data, end = older + data, older[0][0]
        self._kcache[key] = (time.time(), data[-limit:])
        return data[-limit:]

    async def fetch_history(self, symbol: str, tf: str, total: int = 2000, cache_sec: int = 600) -> list:
        """Up to `total` candles paginating backwards (Bybit `end` / Bitget history-candles). Returns what it could get (≥ 1 page)."""
        key = (symbol, tf, "history", total)
        hit = self._kcache.get(key)
        if hit and time.time() - hit[0] < cache_sec:
            return hit[1]
        data = list(await self.fetch_klines(symbol, tf, 1000, cache_sec=300))
        bybit_tf, bitget_tf = TF_MAP[tf]
        while len(data) < total:
            oldest, end = data[0][0], data[0][0] - 1
            rows = None
            if self.provider == "bybit":
                try:
                    rows = (await self._bybit("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": bybit_tf,
                                                                    "limit": min(1000, total - len(data)), "end": end}))["list"]
                except RuntimeError as e:
                    self._warn_fallback(f"historial {symbol} {tf}", str(e))
            if rows is None:
                try:
                    rows = await self._bitget("/api/v2/mix/market/history-candles", {"symbol": symbol, "granularity": bitget_tf, "limit": min(200, total - len(data)),
                                                                                       "productType": "USDT-FUTURES", "endTime": oldest})
                except Exception as e:
                    logger.warning(f"historial {symbol} {tf}: {e} → usando {len(data)} velas")
                    break
            older = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in rows if int(k[0]) < oldest]
            if not older:
                break
            data = sorted(older) + data
        self._kcache[key] = (time.time(), data[-total:])
        return data[-total:]
