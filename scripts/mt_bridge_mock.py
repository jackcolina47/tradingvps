"""Local simulator of our Windows MetaTrader bridge (same REST contract: Bearer token, routes under /users/current/accounts/{account_id})
for end-to-end testing of the FX REAL executor. Accounts: hfm_v2 (HFM Zero, suffix «b», metals GOLDb / SILVERb) and pepperstone_v2.
Run: python3 scripts/mt_bridge_mock.py  (port 8012)  · token: mocktoken
Helpers: POST /mock/price/{symbol}?price=X · POST /mock/close/{position_id}?reason=sl|tp · POST /mock/connection?connected=false · GET /mock/state · POST /mock/reset
         POST /mock/fault?trade_hang=3&trade_code=ERR_TRADE_TIMED_OUT&trade_execute=true&times=1&positions_hang=3&positions_times=1&positions_status=503 (fault injection)
         POST /mock/mt4-partial?enabled=true (the remainder of a partial close gets a new ticket, like MetaTrader 4)
"""
import sys
import time
import asyncio
from datetime import datetime, timezone
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, "/app/backend")
from fx.symbols import SPECS  # noqa: E402

TOKEN = "mocktoken"
BALANCE = 1000.0
COMMISSION_PER_LOT = -3.0
ACCOUNTS = {
    "hfm_v2": {"_id": "hfm_v2", "login": "70012345", "server": "HFMarketsGlobal-Demo", "platform": "mt4", "name": "HFM Zero demo v2", "state": "DEPLOYED", "connectionStatus": "CONNECTED"},
    "pepperstone_v2": {"_id": "pepperstone_v2", "login": "", "server": "", "platform": "mt5", "name": "Pepperstone (sin cuenta)", "state": "UNDEPLOYED", "connectionStatus": "DISCONNECTED"},
}
app = FastAPI()
state = {}


def reset():
    state.clear()
    state.update({"accounts": {k: dict(v) for k, v in ACCOUNTS.items()}, "balance": BALANCE, "positions": {}, "deals": {}, "prices": {}, "seq": 1000, "trade_allowed": True,
                  "connected": True, "suffix": "b", "mt4_partial": False, "requests": [],
                  "balance_deals": [], "orders": [], "faults": {"trade_hang": 0.0, "trade_code": "", "trade_code_num": -10, "trade_times": 0, "trade_execute": False,
                                                                "positions_hang": 0.0, "positions_times": 0, "positions_status": 0, "positions_after_trade": 0}})


reset()


def _not_connected():
    return JSONResponse({"id": 1, "error": "NotSynchronizedError", "message": "Account is not connected to the broker"}, status_code=404)


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def nid():
    state["seq"] += 1
    return str(state["seq"])


async def price_of(symbol: str) -> float:
    base = _split(symbol)[0]
    if base in state["prices"]:
        return state["prices"][base]
    spec = SPECS.get(base)
    try:
        async with httpx.AsyncClient(timeout=6, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{spec['yahoo']}", params={"interval": "1m", "range": "1d"})
        p = float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        p = {"XAUUSD": 2650.0, "XAGUSD": 31.0, "USDJPY": 150.0, "EURJPY": 162.0, "GBPJPY": 195.0}.get(base, 1.1)
    state["prices"][base] = p
    return p


# HFM lists every account group's symbols (EURUSD, EURUSDb, EURUSDr…) but only the group matching the account (state["suffix"]) is tradable
GROUP_SUFFIXES = ("", "b", "r", "c", "x", ".r")


# in the Zero group ("b") metals are NOT base+suffix (XAUUSDb / XAGUSDb → 404): they are GOLDb and SILVERb
ODD_NAMES = {"b": {"XAUUSD": "GOLDb", "XAGUSD": "SILVERb"}}


def _split(symbol: str):
    for suf, names in ODD_NAMES.items():
        for base, odd in names.items():
            if symbol == odd:
                return base, suf
            if symbol == f"{base}{suf}":
                return symbol, None  # the plain base+suffix name does not exist in that group
    for base in SPECS:
        if symbol.startswith(base) and symbol[len(base):] in GROUP_SUFFIXES:
            return base, symbol[len(base):]
    return symbol, None


def _spec(symbol: str):
    base, suf = _split(symbol)
    return base, (SPECS.get(base) if suf is not None else None)


def _tradable(symbol: str) -> bool:
    return _split(symbol)[1] == state["suffix"]


def _pnl(p: dict, price: float) -> float:
    base, spec = _spec(p["symbol"])
    move = (price - p["openPrice"]) if p["type"] == "POSITION_TYPE_BUY" else (p["openPrice"] - price)
    usd = move * spec["contract"] * p["volume"]
    return round(usd / price if spec["quote"] != "USD" else usd, 2)


def _retcode(code: str, num: int, msg: str, **extra):
    return {"numericCode": num, "stringCode": code, "message": msg, **extra}


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/mock"):
        return await call_next(request)
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        return JSONResponse({"detail": "Token inválido"}, status_code=401)
    state["requests"].append(f"{request.method} {request.url.path}")
    return await call_next(request)


# ---------- bridge / account state ----------
def _acc_view(acc: dict) -> dict:
    return {**acc, "tradeAllowed": state["trade_allowed"], "trade_allowed": state["trade_allowed"]}


@app.get("/health")
async def health():
    return {"status": "ok", "accounts": {k: _acc_view(a) for k, a in state["accounts"].items()}}


@app.get("/users/current/accounts/{acc_id}")
async def get_account(acc_id: str):
    acc = state["accounts"].get(acc_id)
    return _acc_view(acc) if acc else JSONResponse({"detail": f"Cuenta {acc_id} no configurada en el puente"}, status_code=404)


@app.post("/users/current/accounts/{acc_id}/reconnect")
async def reconnect(acc_id: str):
    acc = state["accounts"].get(acc_id)
    if not acc:
        return JSONResponse({"detail": f"Cuenta {acc_id} no configurada en el puente"}, status_code=404)
    if acc["login"]:
        state["connected"] = True
        acc.update(state="DEPLOYED", connectionStatus="CONNECTED")
    return {"ok": True, **_acc_view(acc)}


@app.post("/users/current/accounts/{acc_id}/lot-size")
async def lot_size(acc_id: str, request: Request):
    body = await request.json()
    base, spec = _spec(body.get("symbol", ""))
    if not spec:
        return JSONResponse({"detail": "Symbol not found"}, status_code=404)
    risk_usd = _equity() * float(body.get("riskPercent") or 1) / 100
    dist = abs(float(body.get("entryPrice") or await price_of(base)) - float(body.get("stopLoss") or 0)) or spec["pip"] * 10
    return {"symbol": body.get("symbol"), "volume": max(0.01, round(risk_usd / (dist * spec["contract"]), 2)), "riskAmount": round(risk_usd, 2)}


# ---------- client API ----------
def _equity():
    return round(state["balance"] + sum(p["profit"] + p["swap"] + p["commission"] for p in state["positions"].values()), 2)


@app.get("/users/current/accounts/{acc_id}/account-information")
async def account_information(acc_id: str):
    if acc_id not in state["accounts"]:
        return JSONResponse({"message": "Account not found"}, status_code=404)
    if not state["connected"]:
        return _not_connected()
    margin = round(sum(p["margin"] for p in state["positions"].values()), 2)
    eq = _equity()
    return {"balance": round(state["balance"], 2), "equity": eq, "margin": margin, "freeMargin": round(eq - margin, 2), "leverage": 100, "currency": "USD",
            "server": state["accounts"][acc_id]["server"], "name": state["accounts"][acc_id]["name"]}


@app.get("/users/current/accounts/{acc_id}/positions")
async def positions(acc_id: str):
    f = state["faults"]
    if f["positions_times"] > 0:
        f["positions_times"] -= 1
        await asyncio.sleep(f["positions_hang"])
        if f["positions_status"]:
            return JSONResponse({"error": "InjectedFault", "message": "Injected fault"}, status_code=f["positions_status"])
    if not state["connected"]:
        return _not_connected()
    out = []
    for p in state["positions"].values():
        price = await price_of(p["symbol"])
        p["currentPrice"], p["profit"] = price, _pnl(p, price)
        out.append({k: v for k, v in p.items() if k != "margin"})
    return out


@app.get("/users/current/accounts/{acc_id}/symbols/{symbol}/specification")
async def specification(acc_id: str, symbol: str):
    base, spec = _spec(symbol)
    if not spec:
        return JSONResponse({"message": f"Symbol {symbol} not found"}, status_code=404)
    return {"symbol": symbol, "tickSize": spec["pip"] / 10, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01, "digits": spec["digits"],
            "contractSize": spec["contract"], "baseCurrency": spec["base"], "quoteCurrency": spec["quote"],
            "tradeMode": "SYMBOL_TRADE_MODE_FULL" if _tradable(symbol) else "SYMBOL_TRADE_MODE_DISABLED"}


@app.get("/users/current/accounts/{acc_id}/symbols")
async def symbols(acc_id: str):
    if not state["connected"]:
        return _not_connected()
    return [ODD_NAMES.get(suf, {}).get(base, f"{base}{suf}") for base in SPECS for suf in GROUP_SUFFIXES if suf != ".r"]


@app.get("/users/current/accounts/{acc_id}/symbols/{symbol}/current-tick")
async def tick(acc_id: str, symbol: str):
    base, spec = _spec(symbol)
    if not spec:
        return JSONResponse({"message": f"Symbol {symbol} not found"}, status_code=404)
    p = await price_of(base)
    return {"symbol": symbol, "time": now_iso(), "bid": round(p - spec["spread"] / 2, spec["digits"]), "ask": round(p + spec["spread"] / 2, spec["digits"])}


MOCK_TF = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@app.get("/users/current/accounts/{acc_id}/historical-market-data/symbols/{symbol}/timeframes/{tf}/candles")
async def candles(acc_id: str, symbol: str, tf: str, startTime: str = None, limit: int = 1000):
    """Historical market data: deterministic synthetic OHLCV (seeded per symbol/tf/candle) loaded
    backwards from startTime (inclusive) so the chart's terminal source can be tested offline."""
    import math
    import random
    if not state["connected"]:
        return _not_connected()
    base, spec = _spec(symbol)
    if not spec or tf not in MOCK_TF:
        return JSONResponse({"message": f"Symbol {symbol} not found"}, status_code=404)
    step = MOCK_TF[tf]
    end = int(datetime.fromisoformat(startTime.replace("Z", "+00:00")).timestamp()) if startTime else int(time.time())
    end -= end % step
    price = await price_of(base)
    out = []
    for i in range(min(max(int(limit), 1), 1000)):
        t = end - (min(max(int(limit), 1), 1000) - 1 - i) * step
        rnd = random.Random(f"{base}{tf}{t}")
        o = price * (1 + 0.004 * math.sin(t / (step * 37)) + rnd.uniform(-0.0015, 0.0015))
        c = o * (1 + rnd.uniform(-0.002, 0.002))
        h, lo = max(o, c) * (1 + rnd.uniform(0, 0.001)), min(o, c) * (1 - rnd.uniform(0, 0.001))
        at = datetime.fromtimestamp(t, timezone.utc)
        out.append({"symbol": symbol, "timeframe": tf, "time": at.isoformat().replace("+00:00", "Z"), "brokerTime": at.strftime("%Y-%m-%d %H:%M:%S.000"),
                    "open": round(o, spec["digits"]), "high": round(h, spec["digits"]), "low": round(lo, spec["digits"]), "close": round(c, spec["digits"]),
                    "tickVolume": rnd.randint(100, 2000), "spread": 10, "volume": 0})
    return out


def _close(p: dict, price: float, comment: str = "") -> dict:
    profit = _pnl(p, price)
    state["balance"] += profit + p["swap"] + p["commission"]
    deal = {"id": nid(), "platform": "mt5", "type": "DEAL_TYPE_SELL" if p["type"] == "POSITION_TYPE_BUY" else "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_OUT",
            "symbol": p["symbol"], "positionId": p["id"], "orderId": nid(), "volume": p["volume"], "price": price, "profit": profit, "swap": p["swap"],
            "commission": 0.0, "time": now_iso(), "comment": comment, "magic": p["magic"]}
    state["deals"][p["id"]].append(deal)
    del state["positions"][p["id"]]
    return deal


@app.post("/users/current/accounts/{acc_id}/trade")
async def trade(acc_id: str, request: Request):
    body = await request.json()
    action = body.get("actionType")
    if not state["connected"]:
        return _not_connected()
    if not state["trade_allowed"]:
        return _retcode("TRADE_RETCODE_TRADE_DISABLED", 10027, "Trade disabled")
    f = state["faults"]
    fault = None
    if f["trade_times"] > 0:
        f["trade_times"] -= 1
        fault = dict(f)
        if fault["positions_after_trade"]:
            f["positions_times"] = fault["positions_after_trade"]
        if fault["trade_code"] and not fault["trade_execute"]:
            return _retcode(fault["trade_code"], fault["trade_code_num"], "Injected fault")
    res = await _trade(body, action)
    if fault:
        if fault["trade_hang"]:
            await asyncio.sleep(fault["trade_hang"])
        if fault["trade_code"]:
            return _retcode(fault["trade_code"], fault["trade_code_num"], "Injected fault (executed)")
    return res


async def _trade(body: dict, action: str):
    if action in ("ORDER_TYPE_BUY", "ORDER_TYPE_SELL"):
        symbol = body.get("symbol", "")
        base, spec = _spec(symbol)
        if not spec:
            return JSONResponse({"message": f"Symbol {symbol} not found"}, status_code=404)
        if not _tradable(symbol):
            return _retcode("ERR_TRADE_DISABLED", 133, "Trade is disabled")
        vol = float(body.get("volume") or 0)
        if vol < 0.01 or round(vol / 0.01) * 0.01 - vol > 1e-9:
            return _retcode("TRADE_RETCODE_INVALID_VOLUME", 10014, "Invalid volume")
        price = await price_of(base)
        long = action == "ORDER_TYPE_BUY"
        entry = round(price + (spec["spread"] / 2 if long else -spec["spread"] / 2), spec["digits"])
        sl, tp = body.get("stopLoss"), body.get("takeProfit")
        if sl is not None and ((long and sl >= entry) or (not long and sl <= entry)):
            return _retcode("TRADE_RETCODE_INVALID_STOPS", 10016, "Invalid stops")
        pid, oid = nid(), nid()
        margin = vol * spec["contract"] * (entry if spec["quote"] == "USD" and spec["base"] != "USD" else 1.0) / 100
        state["positions"][pid] = {"id": pid, "platform": "mt5", "type": "POSITION_TYPE_BUY" if long else "POSITION_TYPE_SELL", "symbol": symbol, "magic": body.get("magic", 0),
                                   "time": now_iso(), "updateTime": now_iso(), "openPrice": entry, "currentPrice": price, "volume": vol, "swap": 0.0,
                                   "profit": _pnl({"type": "POSITION_TYPE_BUY" if long else "POSITION_TYPE_SELL", "openPrice": entry, "volume": vol, "symbol": symbol}, price),
                                   "commission": round(COMMISSION_PER_LOT * vol, 2), "stopLoss": sl, "takeProfit": tp, "comment": body.get("comment"), "margin": margin}
        state["deals"][pid] = [{"id": nid(), "type": "DEAL_TYPE_BUY" if long else "DEAL_TYPE_SELL", "entryType": "DEAL_ENTRY_IN", "symbol": symbol, "positionId": pid,
                                "orderId": oid, "volume": vol, "price": entry, "profit": 0.0, "swap": 0.0, "commission": round(COMMISSION_PER_LOT * vol, 2), "time": now_iso()}]
        state["orders"].append({"id": oid, "type": "ORDER_TYPE_BUY" if long else "ORDER_TYPE_SELL", "state": "ORDER_STATE_FILLED", "symbol": symbol, "positionId": pid,
                                "volume": vol, "openPrice": entry, "time": now_iso(), "doneTime": now_iso(), "comment": body.get("comment"), "magic": body.get("magic", 0)})
        return _retcode("TRADE_RETCODE_DONE", 10009, "Request completed", orderId=oid, positionId=pid)
    pid = str(body.get("positionId") or "")
    p = state["positions"].get(pid)
    if action == "POSITION_CLOSE_ID" and not body.get("volume"):
        if not p:
            return _retcode("TRADE_RETCODE_POSITION_CLOSED", 10045, "Position already closed")
        deal = _close(p, await price_of(p["symbol"]), "")
        return _retcode("TRADE_RETCODE_DONE", 10009, "Request completed", orderId=deal["orderId"], positionId=pid)
    if action == "POSITION_CLOSE_ID":
        if not p:
            return _retcode("TRADE_RETCODE_POSITION_CLOSED", 10045, "Position already closed")
        vol = float(body.get("volume") or 0)
        if vol < 0.01 or vol > p["volume"] + 1e-9:
            return _retcode("TRADE_RETCODE_INVALID_VOLUME", 10014, "Invalid volume")
        price = await price_of(p["symbol"])
        if vol >= p["volume"] - 1e-9:
            deal = _close(p, price, "")
            return _retcode("TRADE_RETCODE_DONE", 10009, "Request completed", orderId=deal["orderId"], positionId=pid)
        part = {**p, "volume": vol}
        profit = _pnl(part, price)
        state["balance"] += profit
        deal = {"id": nid(), "platform": "mt5", "type": "DEAL_TYPE_SELL" if p["type"] == "POSITION_TYPE_BUY" else "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_OUT",
                "symbol": p["symbol"], "positionId": pid, "orderId": nid(), "volume": vol, "price": price, "profit": profit, "swap": 0.0,
                "commission": 0.0, "time": now_iso(), "comment": "partial", "magic": p["magic"]}
        state["deals"][pid].append(deal)
        p["volume"] = round(p["volume"] - vol, 2)
        p["profit"] = _pnl(p, price)
        p["updateTime"] = now_iso()
        if state["mt4_partial"]:   # MetaTrader 4: the remainder is a new ticket whose comment is «from #old»
            new = nid()
            state["positions"][new] = {**state["positions"].pop(pid), "id": new, "comment": f"from #{pid}"}
            state["deals"][new] = state["deals"][pid]
        return _retcode("TRADE_RETCODE_DONE", 10009, "Request completed", orderId=deal["orderId"], positionId=pid)
    if action == "POSITION_MODIFY":
        if not p:
            return _retcode("TRADE_RETCODE_INVALID", 10013, "Position not found")
        if body.get("stopLoss") is not None:
            p["stopLoss"] = body["stopLoss"]
        if body.get("takeProfit") is not None:
            p["takeProfit"] = body["takeProfit"]
        return _retcode("TRADE_RETCODE_DONE", 10009, "Request completed", positionId=pid)
    return _retcode("TRADE_RETCODE_INVALID", 10013, f"Unsupported action {action}")


@app.get("/users/current/accounts/{acc_id}/history-deals/position/{pid}")
async def deals(acc_id: str, pid: str):
    return state["deals"].get(pid, [])


@app.get("/users/current/accounts/{acc_id}/history-deals/time/{start}/{end}")
async def deals_by_time(acc_id: str, start: str, end: str, limit: int = 1000):
    if not state["connected"]:
        return _not_connected()
    rows = state["balance_deals"] + [d for ds in state["deals"].values() for d in ds]
    rows = [d for d in rows if start <= d["time"].replace("+00:00", "Z") <= end]
    return sorted(rows, key=lambda d: d["time"])[:limit]


@app.get("/users/current/accounts/{acc_id}/history-orders/position/{pid}")
async def orders(acc_id: str, pid: str):
    return [o for o in state["orders"] if o["positionId"] == pid]


@app.get("/users/current/accounts/{acc_id}/history-orders/time/{start}/{end}")
async def orders_by_time(acc_id: str, start: str, end: str, limit: int = 200):
    if not state["connected"]:
        return _not_connected()
    rows = [o for o in state["orders"] if start <= o["doneTime"].replace("+00:00", "Z") <= end]
    return sorted(rows, key=lambda o: o["doneTime"])[:limit]


# ---------- helpers ----------
@app.post("/mock/fault")
async def set_fault(trade_hang: float = 0.0, trade_code: str = "", trade_code_num: int = -10, trade_execute: bool = False, times: int = 1,
                    positions_hang: float = 0.0, positions_times: int = 0, positions_status: int = 0,
                    positions_after_trade: int = 0):
    """Fault injection for the next `times` trade requests: hang `trade_hang` seconds after executing (client timeout → result unknown),
    and/or answer with `trade_code` (executed first when trade_execute=true, otherwise without executing). `positions_hang` delays the next
    `positions_times` GET /positions calls (and answers with HTTP `positions_status`, e.g. 503, when set); `positions_after_trade` arms that
    many failing GET /positions calls right after the faulted trade (the bridge stops answering at the moment of the order)."""
    state["faults"].update(trade_hang=trade_hang, trade_code=trade_code, trade_code_num=trade_code_num, trade_execute=trade_execute, trade_times=times,
                           positions_hang=positions_hang, positions_times=positions_times, positions_status=positions_status, positions_after_trade=positions_after_trade)
    return {"ok": True, "faults": state["faults"]}
@app.post("/mock/price/{symbol}")
async def set_price(symbol: str, price: float):
    state["prices"][symbol] = price
    return {"ok": True, "symbol": symbol, "price": price}


@app.post("/mock/close/{pid}")
async def force_close(pid: str, reason: str = "sl"):
    p = state["positions"].get(pid)
    if not p:
        return JSONResponse({"message": "position not found"}, status_code=404)
    price = p["stopLoss"] if reason == "sl" else p["takeProfit"]
    deal = _close(p, float(price), f"[{reason} {price}]")
    return {"ok": True, "deal": deal}


@app.post("/mock/mt4-partial")
async def mock_mt4_partial(enabled: bool = True):
    state["mt4_partial"] = enabled
    return {"ok": True, "mt4_partial": enabled}


@app.post("/mock/account-group")
async def mock_account_group(suffix: str = ""):
    """Simulates the HFM account type: '' Premium, 'b' Zero, 'r' Pro, 'c' Cent, 'x' InfinityX. Other groups' symbols quote but are trade-disabled."""
    if suffix not in GROUP_SUFFIXES:
        return JSONResponse({"message": f"suffix must be one of {GROUP_SUFFIXES}"}, status_code=400)
    state["suffix"] = suffix
    return {"ok": True, "suffix": suffix}


@app.post("/mock/trade-allowed")
async def set_trade_allowed(allowed: bool = True):
    state["trade_allowed"] = allowed
    return {"ok": True, "trade_allowed": allowed}


@app.post("/mock/deposit")
async def mock_deposit(amount: float, comment: str = ""):
    """Simulates a balance operation on the MT account (deposit > 0, withdrawal < 0) as a DEAL_TYPE_BALANCE deal."""
    state["balance"] += amount
    deal = {"id": nid(), "platform": "mt5", "type": "DEAL_TYPE_BALANCE", "entryType": "DEAL_ENTRY_IN", "time": now_iso(), "profit": amount,
            "swap": 0.0, "commission": 0.0, "comment": comment or ("Deposit" if amount > 0 else "Withdrawal")}
    state["balance_deals"].append(deal)
    return {"ok": True, "balance": round(state["balance"], 2), "deal": deal}


@app.post("/mock/connection")
async def set_connection(connected: bool = True):
    """Simulates the terminal losing/recovering its broker connection (provisioning status + client API errors)."""
    state["connected"] = connected
    for acc in state["accounts"].values():
        acc["connectionStatus"] = "CONNECTED" if connected else "DISCONNECTED_FROM_BROKER"
    return {"ok": True, "connected": connected}


@app.get("/mock/state")
async def mock_state():
    return {"balance": round(state["balance"], 2), "equity": _equity(), "positions": list(state["positions"].values()), "accounts": list(state["accounts"].values()),
            "deals": state["deals"], "prices": state["prices"], "trade_allowed": state["trade_allowed"], "connected": state["connected"], "faults": state["faults"],
            "orders": state["orders"], "suffix": state["suffix"], "requests": state["requests"][-200:]}


@app.post("/mock/reset")
async def mock_reset():
    reset()
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8012, log_level="warning")
