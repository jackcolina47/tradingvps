"""Client of our own MetaTrader bridge (windows_bridge/mt_bridge.py on the Windows VPS): account state, symbol specs/ticks, candles,
trades (market order with SL/TP, close, partial close, modify) and history deals for one `account_id` (hfm_v1, pepperstone_v1…)."""
import os
import time
import asyncio
import httpx
from urllib.parse import quote

MAGIC = 777001
OK_CODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_DONE_PARTIAL", "TRADE_RETCODE_NO_CHANGES", "ERR_NO_ERROR"}
# the symbol quotes but the account group may not trade it (HFM: each account type has its own symbol suffix)
TRADE_DISABLED_CODES = {"TRADE_RETCODE_TRADE_DISABLED", "ERR_TRADE_DISABLED"}
SUFFIX_HINT = "el símbolo cotiza pero no es operable en esta cuenta: revisa el sufijo de símbolos (HFM: Premium sin sufijo, Zero «b», Pro «r», Cent «c», InfinityX «x»)"
# Result unknown: the request may have reached the broker → verify by clientId before retrying.
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
    "ERR_TRADE_TIMED_OUT": "la orden expiró sin respuesta del bróker (resultado desconocido)", "ERR_TRADE_TIMEOUT": "la orden expiró sin respuesta del terminal (resultado desconocido)",
    "TRADE_RETCODE_TIMEOUT": "petición cancelada por tiempo de espera", "ERR_NOT_SYNCHRONIZED": "el terminal aún está sincronizando con el bróker",
    "ERR_NO_QUOTES": "sin cotizaciones del bróker", "TRADE_RETCODE_CONNECTION": "sin conexión con el servidor del bróker", "TRADE_RETCODE_PRICE_OFF": "sin precios para procesar la orden",
    "TRADE_RETCODE_LOCKED": "petición bloqueada en procesamiento", "TRADE_RETCODE_ERROR": "error de procesamiento en el bróker",
    "ERR_NO_CONNECTION": "el terminal MetaTrader o su Expert Advisor no responde (la orden no se envió)",
    "ERR_SERVER_BUSY": "servidor del bróker ocupado", "ERR_OFF_QUOTES": "fuera de cotización", "ERR_BROKER_BUSY": "bróker ocupado", "ERR_TRADE_CONTEXT_BUSY": "terminal ocupado",
    "ERR_INVESTOR": "contraseña investor: no permite operar", "ERR_MARKET_UNKNOWN_SYMBOL": "símbolo desconocido en el bróker (revisa el sufijo)",
    "TRADE_RETCODE_FROZEN": "orden/posición congelada por el bróker", "TRADE_RETCODE_INVALID_FILL": "modo de llenado no permitido", "TRADE_RETCODE_LIMIT_VOLUME": "volumen máximo por símbolo alcanzado",
    "TRADE_RETCODE_LIMIT_POSITIONS": "número máximo de posiciones alcanzado", "TRADE_RETCODE_SERVER_DISABLES_AT": "trading automático deshabilitado por el servidor",
    "TRADE_RETCODE_CLIENT_DISABLES_AT": "Algo Trading desactivado en el terminal MetaTrader (actívalo en el VPS Windows)", "TRADE_RETCODE_ONLY_REAL": "operación solo permitida en cuentas reales",
    "ERR_INVALID_STOPS": "SL/TP demasiado cerca del precio (stops inválidos)", "ERR_INVALID_TRADE_VOLUME": "volumen inválido (lote mínimo/paso)", "ERR_MARKET_CLOSED": "mercado cerrado",
    "ERR_NOT_ENOUGH_MONEY": "margen insuficiente", "ERR_INVALID_PRICE": "precio inválido", "ERR_ACCOUNT_DISABLED": "cuenta deshabilitada por el bróker",
    "ERR_TRADE_TOO_MANY_ORDERS": "número máximo de órdenes alcanzado", "ERR_LONGS_NOT_ALLOWED": "compras no permitidas en el Expert Advisor", "ERR_SHORTS_NOT_ALLOWED": "ventas no permitidas en el Expert Advisor",
}
RETRY_HTTP = {502, 503, 504}
RETRY_DELAYS = (1.0, 2.0, 4.0)


def env_defaults(profile_key: str, default_account: str) -> dict:
    """Bridge settings from the environment (the panel value wins when it is filled in)."""
    return {"url": (os.environ.get("MT_BRIDGE_URL") or "").strip().rstrip("/"), "token": (os.environ.get("MT_BRIDGE_TOKEN") or "").strip(),
            "account_id": (os.environ.get(f"MT_BRIDGE_ACCOUNT_{profile_key.upper()}") or default_account).strip()}


class BridgeError(Exception):
    """kind: 'rejected' (definitive), 'transient' (failed for sure, retry later) or 'unknown' (may have executed: verify by clientId)."""
    def __init__(self, status: int, msg: str, code: str = "", kind: str = "rejected", retry_after: float = None):
        super().__init__(msg)
        self.status, self.msg, self.code, self.kind, self.retry_after = status, msg, code, kind, retry_after
        self.attempts = 1


class MtBridge:
    def __init__(self, base_url: str, token: str, read_timeout: float = 20.0, trade_timeout: float = 60.0, transport=None):
        self.base_url, self.token = (base_url or "").rstrip("/"), token
        self.read_timeout, self.trade_timeout = read_timeout, trade_timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=8.0), transport=transport)
        self._specs = {}
        self.retries = len(RETRY_DELAYS)  # 0 = single attempt per call (interactive steps that must answer fast)

    async def _call(self, method: str, path: str, json: dict = None, params: dict = None, trade: bool = False, timeout: float = None):
        """Reads retry transient failures (network, 5xx) with backoff; a trade request is sent once and a network failure is reported as
        kind='unknown' because the terminal may still have executed it."""
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        attempt = 0
        while True:
            try:
                r = await self.client.request(method, f"{self.base_url}{path}", headers=headers, json=json, params=params,
                                              timeout=httpx.Timeout(timeout or (self.trade_timeout if trade else self.read_timeout), connect=8.0))
            except httpx.HTTPError as e:
                sent = not isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))  # never reached the bridge → nothing was executed
                err = BridgeError(0, f"El puente MT no responde: {type(e).__name__}{(' · ' + str(e)[:80]) if str(e) else ''}", kind="unknown" if trade and sent else "transient")
            else:
                if r.status_code in RETRY_HTTP:
                    err = BridgeError(r.status_code, f"Puente MT {r.status_code}: {self._detail(r)}", kind="unknown" if trade and r.status_code == 504 else "transient")
                else:
                    return self._parse(r)
            if trade or attempt >= self.retries:
                err.attempts = attempt + 1
                raise err
            await asyncio.sleep(RETRY_DELAYS[attempt])
            attempt += 1

    @staticmethod
    def _detail(r: "httpx.Response") -> str:
        try:
            d = r.json()
            return str(d.get("detail") or d.get("message") or d)[:300]
        except (ValueError, AttributeError):
            return r.text[:300] or "sin respuesta"

    def _parse(self, r: "httpx.Response"):
        if r.status_code == 401:
            raise BridgeError(401, "El puente MT rechazó el token (401): revisa MT_BRIDGE_TOKEN / el token guardado en el panel")
        if r.status_code == 403:
            raise BridgeError(403, f"El puente MT rechazó la conexión (403): {self._detail(r)}")
        if r.status_code >= 400:
            raise BridgeError(r.status_code, f"Puente MT {r.status_code}: {self._detail(r)}")
        return None if r.status_code == 204 or not r.content else r.json()

    # ---------- bridge / terminal state ----------
    async def list_accounts(self) -> list:
        return await self._call("GET", "/users/current/accounts") or []

    async def account(self, account_id: str) -> dict:
        return await self._call("GET", f"/users/current/accounts/{account_id}")

    async def reconnect(self, account_id: str) -> dict:
        return await self._call("POST", f"/users/current/accounts/{account_id}/reconnect", json={}, timeout=45.0)

    def _acc(self, account_id: str) -> str:
        return f"/users/current/accounts/{account_id}"

    async def account_info(self, account_id: str, refresh: bool = False) -> dict:
        return await self._call("GET", f"{self._acc(account_id)}/account-information")

    async def positions(self, account_id: str, refresh: bool = False) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/positions") or []

    async def spec(self, account_id: str, symbol: str, max_age: float = 3600) -> dict:
        hit = self._specs.get(symbol)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        s = await self._call("GET", f"{self._acc(account_id)}/symbols/{quote(symbol, safe='')}/specification")
        self._specs[symbol] = (time.time(), s)
        return s

    async def tick(self, account_id: str, symbol: str) -> dict:
        return await self._call("GET", f"{self._acc(account_id)}/symbols/{quote(symbol, safe='')}/current-tick")

    async def symbols(self, account_id: str) -> list:
        """All symbol names known to the terminal (every account group variant: EURUSD, EURUSDb, EURUSDr…)."""
        res = await self._call("GET", f"{self._acc(account_id)}/symbols") or []
        return [str(s.get("symbol") if isinstance(s, dict) else s) for s in res]

    async def lot_size(self, account_id: str, symbol: str, risk_money: float, sl_distance: float) -> dict:
        """Lots that risk `risk_money` (account currency) with a stop `sl_distance` away, quantized to the broker's lot step."""
        return await self._call("POST", f"{self._acc(account_id)}/lot-size", json={"symbol": symbol, "riskMoney": risk_money, "slDistance": sl_distance})

    async def candles(self, account_id: str, symbol: str, timeframe: str, start_iso: str = None, limit: int = 1000) -> list:
        """Historical OHLCV straight from the MT terminal (the candles MetaTrader shows), loaded backwards from `start_iso` (inclusive; latest
        when empty). Returns ascending [ts_ms, o, h, l, c, tick_volume] in UTC."""
        from datetime import datetime
        params = {"limit": min(1000, max(1, int(limit)))}
        if start_iso:
            params["startTime"] = start_iso
        res = await self._call("GET", f"{self._acc(account_id)}/historical-market-data/symbols/{quote(symbol, safe='')}/timeframes/{timeframe}/candles",
                               params=params, timeout=45.0) or []
        rows = []
        for c in res:
            try:
                ts = int(datetime.fromisoformat(str(c["time"]).replace("Z", "+00:00")).timestamp() * 1000)
                rows.append([ts, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c.get("tickVolume") or c.get("volume") or 0)])
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort()
        return rows

    async def deals_by_position(self, account_id: str, position_id: str) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/history-deals/position/{position_id}") or []

    async def deals_by_time(self, account_id: str, start_iso: str, end_iso: str, limit: int = 1000) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/history-deals/time/{quote(start_iso, safe='')}/{quote(end_iso, safe='')}", params={"limit": limit}) or []

    async def orders_by_time(self, account_id: str, start_iso: str, end_iso: str, limit: int = 200) -> list:
        return await self._call("GET", f"{self._acc(account_id)}/history-orders/time/{quote(start_iso, safe='')}/{quote(end_iso, safe='')}", params={"limit": limit}) or []

    async def find_position_by_client_id(self, account_id: str, client_id: str, tries: int = 6, delay: float = 2.5) -> dict | None:
        """After a trade with unknown result: looks for the position carrying our clientId (stored in the MetaTrader comment)."""
        for i in range(tries):
            for p in await self.positions(account_id):
                if p.get("clientId") == client_id:
                    return p
            if i < tries - 1:
                await asyncio.sleep(delay)
        return None

    async def order_filled_by_client_id(self, account_id: str, client_id: str, minutes: int = 5) -> dict | None:
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = (end - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        for o in await self.orders_by_time(account_id, start, end.isoformat().replace("+00:00", "Z")):
            if o.get("clientId") == client_id and str(o.get("state", "")).endswith("FILLED"):
                return o
        return None

    # ---------- trading ----------
    async def trade(self, account_id: str, payload: dict) -> dict:
        res = await self._call("POST", f"{self._acc(account_id)}/trade", json=payload, trade=True) or {}
        code = str(res.get("stringCode") or "")
        if code not in OK_CODES:
            txt = RETCODE_TXT.get(code, res.get("message") or code or "sin código de respuesta")
            if code in TRADE_DISABLED_CODES:
                txt = f"{txt} · {SUFFIX_HINT}"
            kind = "unknown" if code in UNKNOWN_CODES else ("transient" if code in TRANSIENT_CODES else "rejected")
            raise BridgeError(200, f"MetaTrader: {txt} ({code or res.get('numericCode', '?')})", code, kind=kind)
        return res

    async def market_order(self, account_id: str, symbol: str, side: str, volume: float, sl: float, tp: float, client_id: str) -> dict:
        payload = {"actionType": "ORDER_TYPE_BUY" if side == "long" else "ORDER_TYPE_SELL", "symbol": symbol, "volume": volume,
                   "stopLoss": sl, "clientId": client_id, "magic": MAGIC}
        if tp:
            payload["takeProfit"] = tp
        return await self.trade(account_id, payload)

    async def close_position(self, account_id: str, position_id: str, client_id: str = "AIT_close_c") -> dict:
        return await self.trade(account_id, {"actionType": "POSITION_CLOSE_ID", "positionId": str(position_id), "clientId": client_id})

    async def close_position_partially(self, account_id: str, position_id: str, volume: float, client_id: str = "AIT_part_c") -> dict:
        return await self.trade(account_id, {"actionType": "POSITION_PARTIAL", "positionId": str(position_id), "volume": volume, "clientId": client_id})

    async def modify_position(self, account_id: str, position_id: str, sl: float, tp: float) -> dict:
        payload = {"actionType": "POSITION_MODIFY", "positionId": str(position_id), "stopLoss": sl}
        if tp is not None:
            payload["takeProfit"] = tp
        return await self.trade(account_id, payload)

    async def aclose(self):
        await self.client.aclose()
