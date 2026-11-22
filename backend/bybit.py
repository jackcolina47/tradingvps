import hmac
import json
import time
import hashlib
import logging
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode
import httpx

logger = logging.getLogger("bybit")
ENV_URLS = {"mainnet": "https://api.bybit.com", "testnet": "https://api-testnet.bybit.com", "demo": "https://api-demo.bybit.com"}
LEVERAGE_NOT_MODIFIED = 110043
_instruments: dict = {}


class BybitError(Exception):
    def __init__(self, code, msg, status=200):
        super().__init__(f"Bybit {code}: {msg}")
        self.code, self.msg, self.status = code, msg, status


def round_step(value: float, step: str) -> str:
    v, st = Decimal(str(value)), Decimal(str(step))
    out = (v / st).to_integral_value(rounding=ROUND_DOWN) * st
    return format(out.normalize(), "f")


class BybitClient:
    def __init__(self, api_key: str, api_secret: str, env: str = "mainnet", base_url: str = "", recv_window: int = 5000):
        self.key, self.secret, self.env = api_key, api_secret, env
        self.base_url = (base_url or ENV_URLS.get(env, ENV_URLS["mainnet"])).rstrip("/")
        self.recv = str(recv_window)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15)
        self._lev_cache: dict = {}

    async def aclose(self):
        await self._client.aclose()

    def _sign(self, ts: str, payload: str) -> str:
        return hmac.new(self.secret.encode(), (ts + self.key + self.recv + payload).encode(), hashlib.sha256).hexdigest()

    async def call(self, method: str, path: str, params: dict = None, body: dict = None, auth: bool = True) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        ts = str(int(time.time() * 1000))
        headers = {"Content-Type": "application/json"}
        kwargs = {}
        if method == "GET":
            query = urlencode(params)
            kwargs["params"] = params
            payload = query
        else:
            payload = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
            kwargs["content"] = payload
        if auth:
            headers.update({"X-BAPI-API-KEY": self.key, "X-BAPI-SIGN": self._sign(ts, payload),
                            "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": self.recv})
        r = await self._client.request(method, path, headers=headers, **kwargs)
        try:
            data = r.json()
        except ValueError:
            raise BybitError(r.status_code, r.text[:160] or "respuesta no válida", r.status_code)
        if r.status_code >= 400 or data.get("retCode", 0) != 0:
            raise BybitError(data.get("retCode", r.status_code), data.get("retMsg", r.text[:160]), r.status_code)
        return data.get("result", {})

    async def server_time(self) -> int:
        res = await self.call("GET", "/v5/market/time", auth=False)
        return int(res.get("timeSecond", 0))

    async def wallet(self) -> dict:
        res = await self.call("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        acc = (res.get("list") or [{}])[0]
        usdt = next((c for c in acc.get("coin", []) if c.get("coin") == "USDT"), {})
        f = lambda v: float(v) if v not in (None, "") else 0.0  # noqa: E731
        return {
            "account_type": acc.get("accountType", "UNIFIED"),
            "equity": f(acc.get("totalEquity")), "available": f(acc.get("totalAvailableBalance")),
            "wallet_balance": f(acc.get("totalWalletBalance")), "unrealised": f(acc.get("totalPerpUPL")),
            "margin_balance": f(acc.get("totalMarginBalance")), "usdt_wallet": f(usdt.get("walletBalance")),
            "usdt_equity": f(usdt.get("equity")),
        }

    async def best(self, symbol: str) -> dict:
        """Best bid / ask of the order book."""
        res = await self.call("GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}, auth=False)
        t = (res.get("list") or [{}])[0]
        return {"bid": float(t.get("bid1Price") or 0), "ask": float(t.get("ask1Price") or 0)}

    async def instrument(self, symbol: str) -> dict:
        key = (self.base_url, symbol)
        hit = _instruments.get(key)
        if hit and time.time() - hit["ts"] < 3600:
            return hit["data"]
        res = await self.call("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": symbol}, auth=False)
        lst = res.get("list") or []
        if not lst:
            raise BybitError(10001, f"Instrumento {symbol} no encontrado")
        lot, pf = lst[0]["lotSizeFilter"], lst[0]["priceFilter"]
        data = {"qty_step": lot["qtyStep"], "min_qty": lot["minOrderQty"], "max_qty": lot.get("maxMktOrderQty", lot.get("maxOrderQty")),
                "tick_size": pf["tickSize"], "min_notional": float(lot.get("minNotionalValue", 5) or 5), "status": lst[0].get("status")}
        _instruments[key] = {"ts": time.time(), "data": data}
        return data

    async def set_leverage(self, symbol: str, leverage: int):
        if self._lev_cache.get(symbol) == leverage:
            return
        try:
            await self.call("POST", "/v5/position/set-leverage",
                            body={"category": "linear", "symbol": symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)})
        except BybitError as e:
            if e.code != LEVERAGE_NOT_MODIFIED:
                raise
        self._lev_cache[symbol] = leverage

    async def market_order(self, symbol: str, side: str, qty: str, sl: str = None, tp: str = None,
                           link_id: str = None, reduce_only: bool = False) -> dict:
        body = {"category": "linear", "symbol": symbol, "side": side, "orderType": "Market", "qty": qty,
                "timeInForce": "IOC", "positionIdx": 0}
        if reduce_only:
            body.update(reduceOnly=True, closeOnTrigger=True)
        else:
            body.update(tpslMode="Full", tpOrderType="Market", slOrderType="Market")
            if sl:
                body["stopLoss"] = sl
            if tp:
                body["takeProfit"] = tp
        if link_id:
            body["orderLinkId"] = link_id
        return await self.call("POST", "/v5/order/create", body=body)

    async def limit_order(self, symbol: str, side: str, qty: str, price: str, sl: str = None, tp: str = None, link_id: str = None) -> dict:
        """Post-only limit entry (maker fee); Bybit rejects it instead of crossing the book."""
        body = {"category": "linear", "symbol": symbol, "side": side, "orderType": "Limit", "qty": qty, "price": price,
                "timeInForce": "PostOnly", "positionIdx": 0, "tpslMode": "Full", "tpOrderType": "Market", "slOrderType": "Market"}
        if sl:
            body["stopLoss"] = sl
        if tp:
            body["takeProfit"] = tp
        if link_id:
            body["orderLinkId"] = link_id
        return await self.call("POST", "/v5/order/create", body=body)

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        return await self.call("POST", "/v5/order/cancel", body={"category": "linear", "symbol": symbol, "orderId": order_id})

    async def set_stop(self, symbol: str, sl: str):
        return await self.call("POST", "/v5/position/trading-stop",
                               body={"category": "linear", "symbol": symbol, "tpslMode": "Full", "positionIdx": 0,
                                     "stopLoss": sl, "slOrderType": "Market"})

    async def order(self, symbol: str, order_id: str) -> dict:
        res = await self.call("GET", "/v5/order/realtime", {"category": "linear", "symbol": symbol, "orderId": order_id})
        lst = res.get("list") or []
        if not lst:
            res = await self.call("GET", "/v5/order/history", {"category": "linear", "symbol": symbol, "orderId": order_id})
            lst = res.get("list") or []
        if not lst:
            return {}
        o = lst[0]
        f = lambda v: float(v) if v not in (None, "") else 0.0  # noqa: E731
        return {"status": o.get("orderStatus"), "avg_price": f(o.get("avgPrice")), "exec_qty": f(o.get("cumExecQty")),
                "fee": f(o.get("cumExecFee")), "order_id": o.get("orderId")}

    async def positions(self, symbol: str = None) -> list:
        params = {"category": "linear", "settleCoin": "USDT"} if not symbol else {"category": "linear", "symbol": symbol}
        res = await self.call("GET", "/v5/position/list", params)
        out = []
        f = lambda v: float(v) if v not in (None, "") else 0.0  # noqa: E731
        for p in res.get("list") or []:
            if f(p.get("size")) <= 0:
                continue
            out.append({"symbol": p["symbol"], "side": "long" if p.get("side") == "Buy" else "short", "size": f(p.get("size")),
                        "entry": f(p.get("avgPrice")), "mark": f(p.get("markPrice")), "unrealised": f(p.get("unrealisedPnl")),
                        "leverage": f(p.get("leverage")), "sl": f(p.get("stopLoss")), "tp": f(p.get("takeProfit")),
                        "position_idx": int(p.get("positionIdx", 0))})
        return out

    async def closed_pnl(self, symbol: str, start_ms: int = None, limit: int = 20) -> list:
        res = await self.call("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": symbol, "startTime": start_ms, "limit": limit})
        f = lambda v: float(v) if v not in (None, "") else 0.0  # noqa: E731
        return [{"order_id": r.get("orderId"), "side": r.get("side"), "qty": f(r.get("qty")), "entry": f(r.get("avgEntryPrice")),
                 "exit": f(r.get("avgExitPrice")), "pnl": f(r.get("closedPnl")), "fee": f(r.get("openFee")) + f(r.get("closeFee")),
                 "time": int(r.get("updatedTime") or r.get("createdTime") or 0)} for r in res.get("list") or []]

    async def transaction_log(self, start_ms: int, end_ms: int, cursor: str = None, limit: int = 50) -> dict:
        """Unified-account transaction log (window ≤ 7 days). Returns {"list": [...], "cursor": next}."""
        res = await self.call("GET", "/v5/account/transaction-log", {"accountType": "UNIFIED", "currency": "USDT", "startTime": start_ms,
                                                                       "endTime": end_ms, "limit": limit, "cursor": cursor or None})
        return {"list": res.get("list") or [], "cursor": res.get("nextPageCursor") or ""}
