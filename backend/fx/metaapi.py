"""MetaApi.cloud REST bridge for MetaTrader 4/5 (HFM) accounts: provisioning (link/deploy/redeploy), account state, symbol specs/ticks,
trades (market order with SL/TP, close, modify), history deals. `base_url` overrides both hosts (local mock / custom endpoint)."""
import time
import uuid
import asyncio
import httpx
from urllib.parse import quote

PROV = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"
MAGIC = 777001
# MetaApi pay-as-you-go infrastructure options (USD, excl. VAT; metaapi.cloud pricing). Type/reliability can't be changed on an existing account.
PLANS = {
    "g2-high": {"type": "cloud-g2", "reliability": "high", "label": "Cloud G2 · fiabilidad alta", "short": "G2 alta", "hour_usd": 0.012, "deploy_usd": 0.072,
                "recommended": True, "note": "La más barata y la más rápida (ticks < 2,5 s). Es la que MetaApi recomienda para producción: sirve igual para demo y para dinero real."},
    "g1-regular": {"type": "cloud-g1", "reliability": "regular", "label": "Cloud G1 · fiabilidad regular", "short": "G1 regular", "hour_usd": 0.019688, "deploy_usd": 0.118125,
                   "recommended": False, "note": "Infraestructura antigua sin redundancia. Solo tiene sentido si el servidor de tu bróker no conecta en G2."},
    "g1-high": {"type": "cloud-g1", "reliability": "high", "label": "Cloud G1 · fiabilidad alta", "short": "G1 alta", "hour_usd": 0.039376, "deploy_usd": 0.23625,
                "recommended": False, "note": "La más cara: G1 con servidores redundantes. Útil solo si necesitas G1 y máxima estabilidad con dinero real."},
}
PLAN_COMMON = {"account_month_usd": 2.1, "undeployed_hour_usd": 0.00105}


def plan_key(acc_type: str, reliability: str) -> str:
    t = "cloud-g2" if (acc_type or "") in ("cloud", "cloud-g2") else "cloud-g1"
    r = "regular" if (reliability or "high") == "regular" and t == "cloud-g1" else "high"
    return next(k for k, p in PLANS.items() if p["type"] == t and p["reliability"] == r)
OK_CODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_DONE_PARTIAL", "TRADE_RETCODE_NO_CHANGES", "ERR_NO_ERROR"}
# the symbol quotes but the account group may not trade it (HFM: each account type has its own symbol suffix)
TRADE_DISABLED_CODES = {"TRADE_RETCODE_TRADE_DISABLED", "ERR_TRADE_DISABLED"}
SUFFIX_HINT = "el símbolo cotiza pero no es operable en esta cuenta: revisa el sufijo de símbolos (HFM: Premium sin sufijo, Zero «b», Pro «r», Cent «c», InfinityX «x»)"
# Result unknown (MetaApi docs): the request may have reached the broker → verify by clientId before retrying.
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
    "TRADE_RETCODE_DISCONNECTED_BEFORE_TRADE": "terminal sin conexión con el servidor de HFM (la orden no llegó al bróker)",
    "TRADE_RETCODE_DISCONNECTED_DURING_TRADE": "la conexión con HFM se cortó durante la orden (resultado desconocido)",
    "ERR_TRADE_TIMED_OUT": "la orden expiró sin respuesta del bróker (resultado desconocido)", "ERR_TRADE_TIMEOUT": "la orden expiró sin respuesta del bróker",
    "TRADE_RETCODE_TIMEOUT": "petición cancelada por tiempo de espera", "ERR_NOT_SYNCHRONIZED": "el terminal aún está sincronizando con HFM",
    "ERR_NO_QUOTES": "sin cotizaciones del bróker", "TRADE_RETCODE_CONNECTION": "sin conexión con el servidor de HFM", "TRADE_RETCODE_PRICE_OFF": "sin precios para procesar la orden",
    "TRADE_RETCODE_LOCKED": "petición bloqueada en procesamiento", "TRADE_RETCODE_ERROR": "error de procesamiento en el bróker", "ERR_NO_CONNECTION": "sin conexión con el servidor de HFM",
    "ERR_SERVER_BUSY": "servidor del bróker ocupado", "ERR_OFF_QUOTES": "fuera de cotización", "ERR_BROKER_BUSY": "bróker ocupado", "ERR_TRADE_CONTEXT_BUSY": "terminal ocupado",
    "ERR_INVESTOR": "contraseña investor: no permite operar", "ERR_MARKET_UNKNOWN_SYMBOL": "símbolo desconocido en HFM (revisa el sufijo)",
    "TRADE_RETCODE_FROZEN": "orden/posición congelada por el bróker", "TRADE_RETCODE_INVALID_FILL": "modo de llenado no permitido", "TRADE_RETCODE_LIMIT_VOLUME": "volumen máximo por símbolo alcanzado",
    "TRADE_RETCODE_LIMIT_POSITIONS": "número máximo de posiciones alcanzado", "TRADE_RETCODE_SERVER_DISABLES_AT": "trading automático deshabilitado por el servidor",
    "TRADE_RETCODE_CLIENT_DISABLES_AT": "trading automático deshabilitado en el terminal", "TRADE_RETCODE_ONLY_REAL": "operación solo permitida en cuentas reales",
}
RETRY_HTTP = {502, 503, 504}
RETRY_DELAYS = (1.0, 2.0, 4.0)


def _retry_after(r: "httpx.Response") -> float:
    """Seconds to wait after a 429: MetaApi puts `metadata.recommendedRetryTime` in the body (Retry-After header as fallback)."""
    try:
        t = (r.json().get("metadata") or {}).get("recommendedRetryTime")
        if t:
            from datetime import datetime, timezone
            return max(0.5, min(30.0, (datetime.fromisoformat(t.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()))
    except (ValueError, AttributeError):
        pass
    try:
        return max(0.5, min(30.0, float(r.headers.get("Retry-After") or 2)))
    except ValueError:
        return 2.0


def client_base(region: str) -> str:
    return f"https://mt-client-api-v1.{region}.agiliumtrade.ai"


def market_data_base(region: str) -> str:
    return f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai"


class MetaApiError(Exception):
    """kind: 'rejected' (definitive), 'transient' (failed for sure, retry later) or 'unknown' (may have executed: verify by clientId)."""
    def __init__(self, status: int, msg: str, code: str = "", kind: str = "rejected", retry_after: float = None):
        super().__init__(msg)
        self.status, self.msg, self.code, self.kind, self.retry_after = status, msg, code, kind, retry_after
        self.attempts = 1


class MetaApi:
    def __init__(self, token: str, region: str = "new-york", base_url: str = "", read_timeout: float = 20.0, trade_timeout: float = 90.0):
        self.token, self.region, self.base_url = token, region, (base_url or "").rstrip("/")
        self.read_timeout, self.trade_timeout = read_timeout, trade_timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=8.0))
        self._specs = {}
        self.retries = len(RETRY_DELAYS)  # 0 = single attempt per call (interactive steps that must answer fast)

    def _prov(self) -> str:
        return self.base_url or PROV

    def _cli(self, region: str = None) -> str:
        return self.base_url or client_base(region or self.region)

    async def _call(self, method: str, url: str, json: dict = None, tx: str = None, params: dict = None, trade: bool = False, timeout: float = None):
        """Idempotent calls (GET / provisioning) retry transient failures (network, 429, 5xx) with backoff; a trade request is sent once and a
        network failure is reported as kind='unknown' because MetaApi may still have executed it."""
        headers = {"auth-token": self.token, "Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if tx:
            headers["transaction-id"] = tx
        attempt = 0
        while True:
            try:
                r = await self.client.request(method, url, headers=headers, json=json, params=params,
                                              timeout=httpx.Timeout(timeout or (self.trade_timeout if trade else self.read_timeout), connect=8.0))
            except httpx.HTTPError as e:
                err = MetaApiError(0, f"MetaApi no responde: {type(e).__name__}{(' · ' + str(e)[:80]) if str(e) else ''}", kind="unknown" if trade else "transient")
                err.read_timeout = isinstance(e, httpx.ReadTimeout)  # request was received; the server is just slow to answer
            else:
                if r.status_code == 429:
                    wait = _retry_after(r)
                    err = MetaApiError(429, f"MetaApi: límite de peticiones alcanzado (reintento en {wait:.0f} s)", kind="transient", retry_after=wait)
                    if trade:
                        raise err
                    if attempt < self.retries:
                        await asyncio.sleep(min(wait, 10.0))
                        attempt += 1
                        continue
                    err.attempts = attempt + 1
                    raise err
                elif r.status_code in RETRY_HTTP:
                    err = MetaApiError(r.status_code, f"MetaApi {r.status_code}: servicio temporalmente no disponible", kind="unknown" if trade else "transient")
                else:
                    return self._parse(r)
            if trade or attempt >= self.retries:
                err.attempts = attempt + 1
                raise err
            await asyncio.sleep(RETRY_DELAYS[attempt])
            attempt += 1

    @staticmethod
    def _parse(r: "httpx.Response"):
        if r.status_code == 401:
            raise MetaApiError(401, "MetaApi rechazó el token (401): revisa el token en app.metaapi.cloud/token")
        if r.status_code == 202:
            return {"_retry": True, "retry_after": int(r.headers.get("Retry-After") or 3)}
        if r.status_code >= 400:
            try:
                detail = r.json()
                msg = detail.get("message") or detail.get("error") or str(detail)
            except ValueError:
                msg = r.text[:300]
            kind = "transient" if r.status_code == 404 and "NotSynchronized" in r.text else "rejected"
            raise MetaApiError(r.status_code, f"MetaApi {r.status_code}: {str(msg)[:300]}", kind=kind)
        return None if r.status_code == 204 or not r.content else r.json()

    # ---------- provisioning ----------
    async def list_accounts(self) -> list:
        res = await self._call("GET", f"{self._prov()}/users/current/accounts")
        return res if isinstance(res, list) else (res or {}).get("items", [])

    async def account(self, account_id: str) -> dict:
        return await self._call("GET", f"{self._prov()}/users/current/accounts/{account_id}")

    async def find_account(self, login: str, server: str = "") -> dict | None:
        for a in await self.list_accounts():
            if str(a.get("login")) == str(login) and (not server or str(a.get("server", "")).lower() == server.lower()):
                return a
        return None

    async def provision(self, login: str, password: str, server: str, platform: str, name: str = "hotforex-ai-trading", plan: str = "g2-high", keywords=("HFM", "HotForex"), tx: str = None, tries: int = 6) -> dict:
        """`tx` + `tries=1`: one non-blocking step; returns {"_retry": True, "retry_after": s} while MetaApi is still validating (call again with the same tx)."""
        p = PLANS.get(plan) or PLANS["g2-high"]
        payload = {"login": str(login), "password": password, "name": name, "server": server, "platform": platform, "magic": MAGIC,
                   "type": p["type"], "reliability": p["reliability"], "region": self.region, "manualTrades": False, "keywords": list(keywords)}
        tx = tx or uuid.uuid4().hex[:32]
        for k in range(tries):
            try:
                res = await self._call("POST", f"{self._prov()}/users/current/accounts", json=payload, tx=tx)
            except MetaApiError as e:
                if tries == 1 and getattr(e, "read_timeout", False):  # MetaApi holds the request while it validates with the broker: same transaction, ask again
                    return {"_retry": True, "retry_after": 3, "slow": True}
                raise
            if not (isinstance(res, dict) and res.get("_retry")):
                return res or {}
            if k == tries - 1 and tries == 1:
                return res
            await asyncio.sleep(min(res["retry_after"], 10))
        raise MetaApiError(202, "MetaApi sigue validando la cuenta; vuelve a probar en un minuto")

    async def deploy(self, account_id: str):
        return await self._call("POST", f"{self._prov()}/users/current/accounts/{account_id}/deploy", json={})

    async def redeploy(self, account_id: str):
        return await self._call("POST", f"{self._prov()}/users/current/accounts/{account_id}/redeploy", json={}, params={"executeForAllReplicas": "true"})

    async def undeploy(self, account_id: str):
        return await self._call("POST", f"{self._prov()}/users/current/accounts/{account_id}/undeploy", json={}, params={"executeForAllReplicas": "true"})

    async def delete_account(self, account_id: str):
        return await self._call("DELETE", f"{self._prov()}/users/current/accounts/{account_id}", params={"executeForAllReplicas": "true"})

    # ---------- terminal state ----------
    def _acc(self, account_id: str, region: str = None) -> str:
        return f"{self._cli(region)}/users/current/accounts/{account_id}"

    async def account_info(self, account_id: str, region: str = None, refresh: bool = False) -> dict:
        return await self._call("GET", f"{self._acc(account_id, region)}/account-information", params={"refreshTerminalState": "true"} if refresh else None)

    async def positions(self, account_id: str, region: str = None, refresh: bool = False) -> list:
        return await self._call("GET", f"{self._acc(account_id, region)}/positions", params={"refreshTerminalState": "true"} if refresh else None) or []

    async def spec(self, account_id: str, symbol: str, region: str = None, max_age: float = 3600) -> dict:
        hit = self._specs.get(symbol)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        s = await self._call("GET", f"{self._acc(account_id, region)}/symbols/{quote(symbol, safe='')}/specification")
        self._specs[symbol] = (time.time(), s)
        return s

    async def tick(self, account_id: str, symbol: str, region: str = None) -> dict:
        return await self._call("GET", f"{self._acc(account_id, region)}/symbols/{quote(symbol, safe='')}/current-tick")

    async def symbols(self, account_id: str, region: str = None) -> list:
        """All symbol names known to the terminal (every HFM account group variant: EURUSD, EURUSDb, EURUSDr…)."""
        res = await self._call("GET", f"{self._acc(account_id, region)}/symbols") or []
        return [str(s.get("symbol") if isinstance(s, dict) else s) for s in res]

    async def candles(self, account_id: str, symbol: str, timeframe: str, start_iso: str = None, limit: int = 1000, region: str = None) -> list:
        """Historical OHLCV straight from the MT terminal (the candles MetaTrader shows), loaded backwards from `start_iso` (inclusive; latest
        when empty). Separate market-data host. Returns ascending [ts_ms, o, h, l, c, tick_volume]."""
        from datetime import datetime
        params = {"limit": min(1000, max(1, int(limit)))}
        if start_iso:
            params["startTime"] = start_iso
        host = self.base_url or market_data_base(region or self.region)
        res = await self._call("GET", f"{host}/users/current/accounts/{account_id}/historical-market-data/symbols/{quote(symbol, safe='')}/timeframes/{timeframe}/candles",
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

    async def deals_by_position(self, account_id: str, position_id: str, region: str = None) -> list:
        return await self._call("GET", f"{self._acc(account_id, region)}/history-deals/position/{position_id}") or []

    async def deals_by_time(self, account_id: str, start_iso: str, end_iso: str, region: str = None, limit: int = 1000) -> list:
        return await self._call("GET", f"{self._acc(account_id, region)}/history-deals/time/{quote(start_iso, safe='')}/{quote(end_iso, safe='')}",
                                params={"limit": limit}) or []

    async def orders_by_position(self, account_id: str, position_id: str, region: str = None) -> list:
        return await self._call("GET", f"{self._acc(account_id, region)}/history-orders/position/{position_id}") or []

    async def orders_by_time(self, account_id: str, start_iso: str, end_iso: str, region: str = None, limit: int = 200) -> list:
        return await self._call("GET", f"{self._acc(account_id, region)}/history-orders/time/{quote(start_iso, safe='')}/{quote(end_iso, safe='')}",
                                params={"limit": limit}) or []

    async def find_position_by_client_id(self, account_id: str, client_id: str, region: str = None, tries: int = 6, delay: float = 2.5) -> dict | None:
        """After a trade with unknown result: looks for the position MetaApi tagged with our clientId (docs: Tracking trades)."""
        for i in range(tries):
            for p in await self.positions(account_id, region, refresh=(i == 0)):
                if p.get("clientId") == client_id:
                    return p
            if i < tries - 1:
                await asyncio.sleep(delay)
        return None

    async def order_filled_by_client_id(self, account_id: str, client_id: str, region: str = None, minutes: int = 5) -> dict | None:
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = (end - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        for o in await self.orders_by_time(account_id, start, end.isoformat().replace("+00:00", "Z"), region):
            if o.get("clientId") == client_id and str(o.get("state", "")).endswith("FILLED"):
                return o
        return None

    # ---------- trading ----------
    async def trade(self, account_id: str, payload: dict, region: str = None) -> dict:
        res = await self._call("POST", f"{self._acc(account_id, region)}/trade", json=payload, trade=True) or {}
        code = str(res.get("stringCode") or "")
        if code not in OK_CODES:
            txt = RETCODE_TXT.get(code, res.get("message") or code or "sin código de respuesta")
            if code in TRADE_DISABLED_CODES:
                txt = f"{txt} · {SUFFIX_HINT}"
            kind = "unknown" if code in UNKNOWN_CODES else ("transient" if code in TRANSIENT_CODES else "rejected")
            raise MetaApiError(200, f"MetaTrader: {txt} ({code or res.get('numericCode', '?')})", code, kind=kind)
        return res

    async def market_order(self, account_id: str, symbol: str, side: str, volume: float, sl: float, tp: float, client_id: str, region: str = None) -> dict:
        payload = {"actionType": "ORDER_TYPE_BUY" if side == "long" else "ORDER_TYPE_SELL", "symbol": symbol, "volume": volume,
                   "stopLoss": sl, "clientId": client_id, "magic": MAGIC}
        if tp:
            payload["takeProfit"] = tp
        return await self.trade(account_id, payload, region)

    async def close_position(self, account_id: str, position_id: str, region: str = None, client_id: str = "AIT_close_c") -> dict:
        return await self.trade(account_id, {"actionType": "POSITION_CLOSE_ID", "positionId": str(position_id), "clientId": client_id}, region)

    async def close_position_partially(self, account_id: str, position_id: str, volume: float, region: str = None, client_id: str = "AIT_part_c") -> dict:
        return await self.trade(account_id, {"actionType": "POSITION_PARTIAL", "positionId": str(position_id), "volume": volume, "clientId": client_id}, region)

    async def modify_position(self, account_id: str, position_id: str, sl: float, tp: float, region: str = None) -> dict:
        payload = {"actionType": "POSITION_MODIFY", "positionId": str(position_id), "stopLoss": sl}
        if tp is not None:
            payload["takeProfit"] = tp
        return await self.trade(account_id, payload, region)

    async def aclose(self):
        await self.client.aclose()
