"""Direct MetaTrader bridge client (own Windows VPS, FastAPI): `Authorization: Bearer <token>` and every route under
/users/current/accounts/{account_id}. Account state, symbol specs/ticks/candles, trades (market order with SL/TP, modify, full or partial close)
and history deals/orders."""
import os
import time
import asyncio
import httpx
from datetime import datetime, timezone
from urllib.parse import quote

DEFAULT_URL = "http://94.72.105.219:8000"
MAGIC = 777001
TRADE_MODE_FULL = "SYMBOL_TRADE_MODE_FULL"
OK_CODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_DONE_PARTIAL", "TRADE_RETCODE_NO_CHANGES", "ERR_NO_ERROR"}
# the symbol quotes but the account group may not trade it (HFM: each account type has its own symbol suffix)
TRADE_DISABLED_CODES = {"TRADE_RETCODE_TRADE_DISABLED", "ERR_TRADE_DISABLED"}
SUFFIX_HINT = "el símbolo cotiza pero no es operable en esta cuenta: revisa el sufijo de símbolos (HFM: Premium sin sufijo, Zero «b», Pro «r», Cent «c», InfinityX «x»)"
# Result unknown: the request may have reached the broker → verify by our comment/clientId before retrying.
UNKNOWN_CODES = {"TRADE_RETCODE_DISCONNECTED_DURING_TRADE", "ERR_TRADE_TIMED_OUT", "ERR_TRADE_TIMEOUT", "ERR_NO_RESULT", "TRADE_RETCODE_TIMEOUT"}
# Failed for sure but transient (terminal/broker connection, quotes, busy) → safe to retry after a short wait.
TRANSIENT_CODES = {"TRADE_RETCODE_DISCONNECTED_BEFORE_TRADE", "ERR_NOT_SYNCHRONIZED", "ERR_NO_QUOTES", "TRADE_RETCODE_CONNECTION", "TRADE_RETCODE_PRICE_OFF",
                   "TRADE_RETCODE_REQUOTE", "TRADE_RETCODE_PRICE_CHANGED", "TRADE_RETCODE_TOO_MANY_REQUESTS", "TRADE_RETCODE_LOCKED", "TRADE_RETCODE_ERROR",
                   "ERR_NO_CONNECTION", "ERR_SERVER_BUSY", "ERR_TOO_FREQUENT_REQUESTS", "ERR_OFF_QUOTES", "ERR_BROKER_BUSY", "ERR_REQUOTE", "ERR_PRICE_CHANGED",
                   "ERR_TRADE_CONTEXT_BUSY", "ERR_TOO_MANY_REQUESTS", "ERR_COMMON_ERROR"}
RETCODE_TXT = {
    "TRADE_RETCODE_REQUOTE": "recotización del precio", "TRADE_RETCODE_REJECT": "orden rechazada por el bróker", "TRADE_RETCODE_CANCEL": "orden cancelada",
    "TRADE_RETCODE_INVALID": "petición inválida", "TRADE_RETCODE_INVALID_VOLUME": "volumen inválido (lote mínimo/paso)", "TRADE_RETCODE_INVALID_PRICE": "precio inválido",
    "TRADE_RETCODE_INVALID_STOPS": "SL/TP demasiado cerca del precio (stops inválidos)", "TRADE_RETCODE_MARKET_CLOSED": "mercado cerrado",
    "TRADE_RETCODE_NO_MONEY": "margen insuficiente", "TRADE_RETCODE_PRICE_CHANGED": "el precio cambió", "TRADE_RETCODE_TOO_MANY_REQUESTS": "demasiadas peticiones",
    "TRADE_RETCODE_TRADE_DISABLED": "trading deshabilitado para este símbolo o cuenta", "ERR_TRADE_DISABLED": "trading deshabilitado para este símbolo o cuenta",
    "TRADE_RETCODE_POSITION_CLOSED": "la posición ya estaba cerrada",
    "TRADE_RETCODE_DISCONNECTED_BEFORE_TRADE": "terminal sin conexión con el servidor del bróker (la orden no llegó)",
    "TRADE_RETCODE_DISCONNECTED_DURING_TRADE": "la conexión con el bróker se cortó durante la orden (resultado desconocido)",
    "ERR_TRADE_TIMED_OUT": "la orden expiró sin respuesta del bróker (resultado desconocido)", "ERR_TRADE_TIMEOUT": "la orden expiró sin respuesta del bróker",
    "TRADE_RETCODE_TIMEOUT": "petición cancelada por tiempo de espera", "ERR_NOT_SYNCHRONIZED": "el terminal aún está sincronizando con el bróker",
    "ERR_NO_QUOTES": "sin cotizaciones del bróker", "TRADE_RETCODE_CONNECTION": "sin conexión con el servidor del bróker", "TRADE_RETCODE_PRICE_OFF": "sin precios para procesar la orden",
    "TRADE_RETCODE_LOCKED": "petición bloqueada en procesamiento", "TRADE_RETCODE_ERROR": "error de procesamiento en el bróker", "ERR_NO_CONNECTION": "sin conexión con el servidor del bróker",
    "ERR_SERVER_BUSY": "servidor del bróker ocupado", "ERR_OFF_QUOTES": "fuera de cotización", "ERR_BROKER_BUSY": "bróker ocupado", "ERR_TRADE_CONTEXT_BUSY": "terminal ocupado",
    "ERR_INVESTOR": "contraseña investor: no permite operar", "ERR_MARKET_UNKNOWN_SYMBOL": "símbolo desconocido en la cuenta (revisa el sufijo)",
    "TRADE_RETCODE_FROZEN": "orden/posición congelada por el bróker", "TRADE_RETCODE_INVALID_FILL": "modo de llenado no permitido", "TRADE_RETCODE_LIMIT_VOLUME": "volumen máximo por símbolo alcanzado",
    "TRADE_RETCODE_LIMIT_POSITIONS": "número máximo de posiciones alcanzado", "TRADE_RETCODE_SERVER_DISABLES_AT": "trading automático deshabilitado por el servidor",
    "TRADE_RETCODE_CLIENT_DISABLES_AT": "trading automático deshabilitado en el terminal", "TRADE_RETCODE_ONLY_REAL": "operación solo permitida en cuentas reales",
}
RETRY_HTTP = {502, 503, 504}
RETRY_DELAYS = (1.0, 2.0, 4.0)
_TRUE = (True, 1, "1", "true", "True")


def env_url() -> str:
    return (os.environ.get("MT_BRIDGE_URL") or DEFAULT_URL).rstrip("/")


def env_token() -> str:
    return os.environ.get("MT_BRIDGE_TOKEN") or ""


def tradable(spec: dict) -> bool:
    """A symbol is fully tradable with tradeMode FULL or an explicit tradeAllowed / allowed flag; a spec without any of them is assumed tradable."""
    mode = spec.get("tradeMode")
    flags = [spec[k] for k in ("tradeAllowed", "allowed") if k in spec]
    return mode == TRADE_MODE_FULL or any(f in _TRUE for f in flags) or (not mode and not flags)


def account_state(acc: dict) -> dict:
    """Normalises the bridge account status ({state, connectionStatus, tradeAllowed} or the /health flavour {deployed, connected, trade_allowed})."""
    acc = acc or {}
    state = acc.get("state") or ("DEPLOYED" if acc.get("deployed") in _TRUE else "")
    conn = acc.get("connectionStatus") or acc.get("connection_status") or acc.get("connection")
    if not conn and "connected" in acc:
        conn = "CONNECTED" if acc["connected"] in _TRUE else "DISCONNECTED"
    allowed = acc.get("tradeAllowed", acc.get("trade_allowed"))
    return {"id": acc.get("_id") or acc.get("id") or acc.get("account_id"), "state": str(state or "").upper(), "connection": str(conn or "").upper(),
            "trade_allowed": None if allowed is None else allowed in _TRUE, "login": acc.get("login"), "server": acc.get("server"),
            "platform": acc.get("platform") or acc.get("version"), "name": acc.get("name")}


def ts_ms(v) -> int:
    """Bridge timestamps: ISO-8601 string or epoch (seconds or milliseconds)."""
    if isinstance(v, (int, float)):
        return int(v if v > 1e11 else v * 1000)
    return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=datetime.fromisoformat(str(v).replace("Z", "+00:00")).tzinfo or timezone.utc).timestamp() * 1000)


def link_of(row: dict):
    """Our tracking id travels as `clientId` and, on MetaTrader, in the order comment."""
    return row.get("clientId") or row.get("comment")


class BridgeError(Exception):
    """kind: 'rejected' (definitive), 'transient' (failed for sure, retry later) or 'unknown' (may have executed: verify by comment/clientId)."""
    def __init__(self, status: int, msg: str, code: str = "", kind: str = "rejected", retry_after: float = None):
        super().__init__(msg)
        self.status, self.msg, self.code, self.kind, self.retry_after = status, msg, code, kind, retry_after
        self.attempts = 1


class MtBridge:
    def __init__(self, url: str, token: str, read_timeout: float = 20.0, trade_timeout: float = 90.0, transport=None):
        self.url, self.token = (url or env_url()).rstrip("/"), token
        self.read_timeout, self.trade_timeout = read_timeout, trade_timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=8.0), transport=transport)
        self._specs = {}

    async def _call(self, method: str, path: str, json: dict = None, params: dict = None, trade: bool = False, timeout: float = None):
        """Idempotent calls retry transient failures (network, 429, 5xx) with backoff; a trade request is sent once and a network failure is
        reported as kind='unknown' because the terminal may still have executed it."""
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        attempt = 0
        while True:
            try:
                r = await self.client.request(method, f"{self.url}{path}", headers=headers, json=json, params=params,
                                              timeout=httpx.Timeout(timeout or (self.trade_timeout if trade else self.read_timeout), connect=8.0))
            except httpx.HTTPError as e:
                err = BridgeError(0, f"El puente MT no responde: {type(e).__name__}{(' · ' + str(e)[:80]) if str(e) else ''}", kind="unknown" if trade else "transient")
            else:
                if r.status_code == 429:
                    try:
                        wait = max(0.5, min(30.0, float(r.headers.get("Retry-After") or 2)))
                    except ValueError:
                        wait = 2.0
                    err = BridgeError(429, f"Puente MT: límite de peticiones alcanzado (reintento en {wait:.0f} s)", kind="transient", retry_after=wait)
                elif r.status_code in RETRY_HTTP:
                    err = BridgeError(r.status_code, f"Puente MT {r.status_code}: servicio temporalmente no disponible", kind="unknown" if trade else "transient")
                else:
                    return self._parse(r)
            if trade or attempt >= len(RETRY_DELAYS):
                err.attempts = attempt + 1
                raise err
            await asyncio.sleep(min(err.retry_after or RETRY_DELAYS[attempt], 10.0))
            attempt += 1

    @staticmethod
    def _parse(r: "httpx.Response"):
        if r.status_code == 401:
            raise BridgeError(401, "El puente MT rechazó el token (401): revisa el Token del Puente")
        if r.status_code >= 400:
            try:
                detail = r.json()
                msg = (detail.get("detail") or detail.get("message") or detail.get("error") or str(detail)) if isinstance(detail, dict) else str(detail)
            except ValueError:
                msg = r.text[:300]
            kind = "transient" if r.status_code == 404 and "NotSynchronized" in r.text else "rejected"
            raise BridgeError(r.status_code, f"Puente MT {r.status_code}: {str(msg)[:300]}", kind=kind)
        return None if r.status_code == 204 or not r.content else r.json()

    def _acc(self, account_id: str) -> str:
        return f"/users/current/accounts/{quote(str(account_id), safe='')}"

    # ---------- bridge / account state ----------
    async def health(self) -> dict:
        return await self._call("GET", "/health") or {}

    async def account(self, account_id: str) -> dict:
        return account_state(await self._call("GET", self._acc(account_id)))

    async def reconnect(self, account_id: str):
        return await self._call("POST", f"{self._acc(account_id)}/reconnect", json={}, timeout=60.0)

    async def account_info(self, account_id: str) -> dict:
        return await self._call("GET", f"{self._acc(account_id)}/account-information") or {}

    async def positions(self, account_id: str) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/positions") or []

    # ---------- symbols ----------
    async def symbols(self, account_id: str) -> list:
        """All symbol names known to the terminal (every account group variant: EURUSD, EURUSDb, GOLDb…)."""
        res = await self._call("GET", f"{self._acc(account_id)}/symbols") or []
        if isinstance(res, dict):
            res = res.get("symbols") or res.get("items") or []
        return [str((s.get("symbol") or s.get("name")) if isinstance(s, dict) else s) for s in res]

    async def spec(self, account_id: str, symbol: str, max_age: float = 3600) -> dict:
        hit = self._specs.get(symbol)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        s = await self._call("GET", f"{self._acc(account_id)}/symbols/{quote(symbol, safe='')}/specification") or {}
        self._specs[symbol] = (time.time(), s)
        return s

    async def tick(self, account_id: str, symbol: str) -> dict:
        return await self._call("GET", f"{self._acc(account_id)}/symbols/{quote(symbol, safe='')}/current-tick") or {}

    async def candles(self, account_id: str, symbol: str, timeframe: str, start_iso: str = None, limit: int = 1000) -> list:
        """Historical OHLCV straight from the MT terminal, loaded backwards from `start_iso` (inclusive; latest when empty).
        Returns ascending [ts_ms, o, h, l, c, tick_volume]."""
        params = {"limit": min(1000, max(1, int(limit)))}
        if start_iso:
            params["startTime"] = start_iso
        res = await self._call("GET", f"{self._acc(account_id)}/historical-market-data/symbols/{quote(symbol, safe='')}/timeframes/{timeframe}/candles",
                               params=params, timeout=45.0) or []
        rows = []
        for c in res:
            try:
                rows.append([ts_ms(c["time"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c.get("tickVolume") or c.get("volume") or 0)])
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort()
        return rows

    # ---------- history ----------
    async def deals_by_position(self, account_id: str, position_id: str) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/history-deals/position/{quote(str(position_id), safe='')}") or []

    async def deals_by_time(self, account_id: str, start_iso: str, end_iso: str, limit: int = 1000) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/history-deals/time/{quote(start_iso, safe='')}/{quote(end_iso, safe='')}", params={"limit": limit}) or []

    async def orders_by_time(self, account_id: str, start_iso: str, end_iso: str, limit: int = 200) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/history-orders/time/{quote(start_iso, safe='')}/{quote(end_iso, safe='')}", params={"limit": limit}) or []

    async def find_position_by_client_id(self, account_id: str, client_id: str, tries: int = 6, delay: float = 2.5) -> dict | None:
        """After a trade with unknown result: looks for the position carrying our tracking id (clientId or MT comment)."""
        for i in range(tries):
            for p in await self.positions(account_id):
                if link_of(p) == client_id:
                    return p
            if i < tries - 1:
                await asyncio.sleep(delay)
        return None

    async def order_filled_by_client_id(self, account_id: str, client_id: str, minutes: int = 5) -> dict | None:
        from datetime import timedelta
        end = datetime.now(timezone.utc)
        start = (end - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        for o in await self.orders_by_time(account_id, start, end.isoformat().replace("+00:00", "Z")):
            if link_of(o) == client_id and str(o.get("state") or "FILLED").endswith("FILLED"):
                return o
        return None

    # ---------- trading ----------
    async def lot_size(self, account_id: str, payload: dict) -> dict:
        return await self._call("POST", f"{self._acc(account_id)}/lot-size", json=payload) or {}

    async def trade(self, account_id: str, payload: dict) -> dict:
        res = await self._call("POST", f"{self._acc(account_id)}/trade", json=payload, trade=True)
        res = res if isinstance(res, dict) else {}
        code = str(res.get("stringCode") or "")
        ok = code in OK_CODES or (not code and bool(res) and res.get("success", res.get("ok", True)) in _TRUE and not res.get("error"))
        if not ok:
            txt = RETCODE_TXT.get(code, res.get("message") or res.get("error") or code or "sin código de respuesta")
            if code in TRADE_DISABLED_CODES:
                txt = f"{txt} · {SUFFIX_HINT}"
            kind = "unknown" if code in UNKNOWN_CODES or not res else ("transient" if code in TRANSIENT_CODES else "rejected")
            raise BridgeError(200, f"MetaTrader: {txt} ({code or res.get('numericCode', '?')})", code, kind=kind)
        return res

    async def market_order(self, account_id: str, symbol: str, side: str, volume: float, sl: float, tp: float, client_id: str) -> dict:
        payload = {"actionType": "ORDER_TYPE_BUY" if side == "long" else "ORDER_TYPE_SELL", "symbol": symbol, "volume": volume,
                   "stopLoss": sl, "comment": client_id, "clientId": client_id, "magic": MAGIC}
        if tp:
            payload["takeProfit"] = tp
        return await self.trade(account_id, payload)

    async def close_position(self, account_id: str, position_id: str, volume: float = None, client_id: str = "AIT_close_c") -> dict:
        """POSITION_CLOSE_ID: the whole position, or `volume` lots of it (partial close)."""
        payload = {"actionType": "POSITION_CLOSE_ID", "positionId": str(position_id), "comment": client_id, "clientId": client_id}
        if volume:
            payload["volume"] = volume
        return await self.trade(account_id, payload)

    async def modify_position(self, account_id: str, position_id: str, sl: float, tp: float) -> dict:
        payload = {"actionType": "POSITION_MODIFY", "positionId": str(position_id), "stopLoss": sl}
        if tp is not None:
            payload["takeProfit"] = tp
        return await self.trade(account_id, payload)

    async def aclose(self):
        await self.client.aclose()
