"""Puente MetaTrader multi-cuenta para Windows (FastAPI + Uvicorn, puerto 8000).

Cada petición se enruta por `account_id` (hfm_v1, hfm_v2, pepperstone_v1…) definido en config_bridge.json:
  · platform "mt5": librería nativa MetaTrader5 apuntando a su terminal64.exe (un proceso hijo por cuenta, porque la librería
    solo puede estar conectada a un terminal por proceso).
  · platform "mt4": archivos JSON atómicos en <MQL4/Files>/aitb/ que atiende el Expert Advisor HFM_Bridge_EA.mq4 cada 100 ms.
Seguridad: cabecera `Authorization: Bearer <BRIDGE_TOKEN>` + lista de IPs permitidas (`allowed_ips`).
Arranque:  python mt_bridge.py   (lee config_bridge.json de esta carpeta, o la ruta de la variable BRIDGE_CONFIG)."""
import os
import re
import sys
import hmac
import json
import math
import time
import uuid
import logging
import threading
import multiprocessing as mp
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("mt_bridge")
HERE = Path(__file__).resolve().parent
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
FROM_RE = re.compile(r"from #(\d+)")
HEARTBEAT_MAX_AGE = 6.0
MODE_FULL = "SYMBOL_TRADE_MODE_FULL"
TRADE_MODES = {0: "SYMBOL_TRADE_MODE_DISABLED", 1: "SYMBOL_TRADE_MODE_LONGONLY", 2: "SYMBOL_TRADE_MODE_SHORTONLY", 3: "SYMBOL_TRADE_MODE_CLOSEONLY", 4: MODE_FULL}  # MT5 numbering
MT4_TRADE_MODES = {0: "SYMBOL_TRADE_MODE_DISABLED", 1: "SYMBOL_TRADE_MODE_CLOSEONLY", 2: MODE_FULL, 3: "SYMBOL_TRADE_MODE_LONGONLY", 4: "SYMBOL_TRADE_MODE_SHORTONLY"}
SYMBOL_ALIASES = {"XAUUSD": "GOLD", "XAGUSD": "SILVER"}  # HFM Zero: XAUUSD → GOLDb, XAGUSD → SILVERb


def mt4_trade_mode(raw, trade_allowed=None) -> str:
    """MT4 symbol trade mode → standard name. The EA sends the name; an older EA sent MT4's own number (FULL = 2, not MT5's 4)."""
    if isinstance(raw, str) and not raw.strip().lstrip("-").isdigit():
        mode = "SYMBOL_TRADE_MODE_" + raw.strip().upper().replace("SYMBOL_TRADE_MODE_", "")
        mode = mode if mode in TRADE_MODES.values() else MODE_FULL
    else:
        mode = MT4_TRADE_MODES.get(int(raw), MODE_FULL)
    if mode == "SYMBOL_TRADE_MODE_DISABLED" and trade_allowed and int(trade_allowed) == 1:
        return MODE_FULL  # MODE_TRADEALLOWED = 1: the symbol can be traded right now
    return mode
MT4_ERR = {0: "ERR_NO_ERROR", 1: "ERR_NO_RESULT", 2: "ERR_COMMON_ERROR", 3: "ERR_INVALID_TRADE_PARAMETERS", 4: "ERR_SERVER_BUSY", 6: "ERR_NO_CONNECTION",
           8: "ERR_TOO_FREQUENT_REQUESTS", 64: "ERR_ACCOUNT_DISABLED", 65: "ERR_INVALID_ACCOUNT", 128: "ERR_TRADE_TIMEOUT", 129: "ERR_INVALID_PRICE",
           130: "ERR_INVALID_STOPS", 131: "ERR_INVALID_TRADE_VOLUME", 132: "ERR_MARKET_CLOSED", 133: "ERR_TRADE_DISABLED", 134: "ERR_NOT_ENOUGH_MONEY",
           135: "ERR_PRICE_CHANGED", 136: "ERR_OFF_QUOTES", 137: "ERR_BROKER_BUSY", 138: "ERR_REQUOTE", 141: "ERR_TOO_MANY_REQUESTS", 145: "ERR_TRADE_MODIFY_DENIED",
           146: "ERR_TRADE_CONTEXT_BUSY", 148: "ERR_TRADE_TOO_MANY_ORDERS", 4106: "ERR_MARKET_UNKNOWN_SYMBOL", 4108: "TRADE_RETCODE_POSITION_CLOSED",
           4109: "TRADE_RETCODE_CLIENT_DISABLES_AT", 4110: "ERR_LONGS_NOT_ALLOWED", 4111: "ERR_SHORTS_NOT_ALLOWED"}
MT5_RET = {10004: "REQUOTE", 10006: "REJECT", 10007: "CANCEL", 10008: "PLACED", 10009: "DONE", 10010: "DONE_PARTIAL", 10011: "ERROR", 10012: "TIMEOUT", 10013: "INVALID",
           10014: "INVALID_VOLUME", 10015: "INVALID_PRICE", 10016: "INVALID_STOPS", 10017: "TRADE_DISABLED", 10018: "MARKET_CLOSED", 10019: "NO_MONEY",
           10020: "PRICE_CHANGED", 10021: "PRICE_OFF", 10022: "INVALID_EXPIRATION", 10023: "ORDER_CHANGED", 10024: "TOO_MANY_REQUESTS", 10025: "NO_CHANGES",
           10026: "SERVER_DISABLES_AT", 10027: "CLIENT_DISABLES_AT", 10028: "LOCKED", 10029: "FROZEN", 10030: "INVALID_FILL", 10031: "CONNECTION", 10032: "ONLY_REAL",
           10033: "LIMIT_ORDERS", 10034: "LIMIT_VOLUME", 10035: "INVALID_ORDER", 10036: "POSITION_CLOSED", 10038: "INVALID_CLOSE_VOLUME", 10040: "LIMIT_POSITIONS"}
MT5_DEAL_TYPES = {0: "DEAL_TYPE_BUY", 1: "DEAL_TYPE_SELL", 2: "DEAL_TYPE_BALANCE", 3: "DEAL_TYPE_CREDIT", 4: "DEAL_TYPE_CHARGE", 5: "DEAL_TYPE_CORRECTION", 6: "DEAL_TYPE_BONUS"}
MT5_ENTRY = {0: "DEAL_ENTRY_IN", 1: "DEAL_ENTRY_OUT", 2: "DEAL_ENTRY_INOUT", 3: "DEAL_ENTRY_OUT_BY"}


class Fault(Exception):
    """HTTP error returned to the caller (404 unknown symbol/account, 503 terminal down, 504 no answer)."""
    def __init__(self, status: int, detail: str, picked: bool = False):
        super().__init__(detail)
        self.status, self.detail, self.picked = status, detail, picked


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def result(code: str, numeric: int = 0, message: str = "", **extra) -> dict:
    return {"stringCode": code, "numericCode": numeric, "message": message or code, **extra}


def floor_step(v: float, step: float) -> float:
    return round(math.floor(v / step + 1e-9) * step, 8) if step and step > 0 else v


def quantize_volume(volume: float, spec: dict) -> float:
    """Lots floored to the broker's step and capped to its maximum; 0.0 when below the minimum lot."""
    vol = min(floor_step(float(volume), spec["volumeStep"]), spec["maxVolume"])
    return vol if vol >= spec["minVolume"] - 1e-9 else 0.0


def lots_for_risk(risk_money: float, sl_distance: float, spec: dict) -> dict:
    """Lots that lose `risk_money` (account currency) if price moves `sl_distance` against the position."""
    tick_size, tick_value = spec.get("tickSize") or 0, spec.get("tickValue") or 0
    if risk_money <= 0 or sl_distance <= 0 or tick_size <= 0 or tick_value <= 0:
        raise Fault(400, "riskMoney, slDistance, tickSize y tickValue deben ser mayores que cero")
    loss_per_lot = sl_distance / tick_size * tick_value
    raw = risk_money / loss_per_lot
    lots = quantize_volume(raw, spec)
    return {"symbol": spec["symbol"], "lots": lots, "rawLots": round(raw, 6), "lossPerLot": round(loss_per_lot, 4), "riskMoney": round(lots * loss_per_lot, 2),
            "minVolume": spec["minVolume"], "volumeStep": spec["volumeStep"], "belowMinimum": lots == 0.0}


class Driver:
    """Shared by both platforms: symbol-name resolution (suffixes), per-account state file and server-time offset."""
    platform = ""

    def __init__(self, account_id: str, cfg: dict, state_dir: Path):
        self.id, self.cfg = account_id, cfg
        self.lock = threading.RLock()
        self._names = (0.0, [])
        self._resolved = {}
        self._state_path = state_dir / f"{account_id}.json"
        self.state = {"alias": {}, "chain": {}, "client": {}, "offset": None}
        if self._state_path.exists():
            try:
                self.state.update(json.loads(self._state_path.read_text("utf-8")))
            except ValueError:
                log.warning("%s: estado ilegible, se reinicia", account_id)

    def save_state(self):
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state), "utf-8")
        os.replace(tmp, self._state_path)

    # ---- server time ↔ UTC ----
    def offset(self) -> int:
        """Seconds the broker server clock is ahead of UTC (config value, else detected from live ticks and remembered)."""
        cfg = self.cfg.get("server_utc_offset_hours")
        if cfg is not None:
            return int(float(cfg) * 3600)
        if self.state.get("offset") is None:
            self._learn_clock()  # first use: read the terminal clock before converting any time
        return int(self.state.get("offset") or 0)

    def _learn_clock(self):
        pass

    def learn_offset(self, server_ts: float, utc_ts: float, fresh: bool):
        if not fresh or self.cfg.get("server_utc_offset_hours") is not None:
            return
        raw = server_ts - utc_ts
        off = round(raw / 1800) * 1800
        if abs(raw - off) <= 90 and -43200 <= off <= 50400 and off != self.state.get("offset"):
            self.state["offset"] = off
            self.save_state()
            log.info("%s: hora del servidor = UTC%+.1f h", self.id, off / 3600)

    def to_utc(self, server_ts: float) -> float:
        return server_ts - self.offset()

    def to_server(self, utc_ts: float) -> float:
        return utc_ts + self.offset()

    # ---- symbols ----
    def names(self) -> list:
        if time.time() - self._names[0] > 300 or not self._names[1]:
            self._names = (time.time(), self._symbols())
        return self._names[1]

    def resolve(self, symbol: str) -> str:
        """Broker name of `symbol`: exact, configured map/suffix, case-insensitive, XAUUSD/XAGUSD → GOLD/SILVER, USDT → USD for crypto CFDs, or the tradable variant with a suffix (EURUSD → EURUSD.a / EURUSDm)."""
        if symbol in self._resolved:
            return self._resolved[symbol]
        names = self.names()
        mapped = (self.cfg.get("symbol_map") or {}).get(symbol)
        suffix = self.cfg.get("symbol_suffix") or ""
        found = next((c for c in (mapped, symbol, f"{symbol}{suffix}") if c and c in names), None)
        if not found:
            found = next((n for n in names if n.upper() == symbol.upper()), None)
        if not found:  # gold / silver under their other name, keeping whatever suffix came with the symbol (XAUUSDb → GOLDb)
            up = {n.upper(): n for n in names}
            for root, alt in SYMBOL_ALIASES.items():
                if symbol.upper().startswith(root):
                    rest = symbol[len(root):]
                    found = next((up[c.upper()] for c in (f"{alt}{rest}", f"{alt}{rest}{suffix}") if c.upper() in up), None)
        roots = [symbol] + ([symbol[:-1]] if symbol.upper().endswith("USDT") else [])  # crypto CFDs are quoted in USD at most brokers (SOLUSDT → SOLUSD)
        if not found:
            found = next((n for r in roots[1:] for n in names if n.upper() == r.upper()), None)
        if not found:
            cands = sorted((n for r in roots for n in names if norm(n).startswith(norm(r)) and len(norm(n)) - len(norm(r)) <= 4), key=lambda n: (len(n), n))
            for n in cands[:6]:
                try:
                    if self._spec(n)["tradeMode"] == MODE_FULL:
                        found = n
                        break
                except Fault:
                    continue
            found = found or (cands[0] if cands else None)
        if not found:
            raise Fault(404, f"Símbolo «{symbol}» desconocido en la cuenta {self.id}")
        self._resolved[symbol] = found
        return found

    def spec(self, symbol: str) -> dict:
        return self._spec(self.resolve(symbol))

    def lot_size(self, symbol: str, risk_money: float, sl_distance: float) -> dict:
        return lots_for_risk(risk_money, sl_distance, self.spec(symbol))


# =====================================================================================================================
# MT4 · atomic JSON files answered by HFM_Bridge_EA.mq4
# =====================================================================================================================
class Mt4Driver(Driver):
    platform = "mt4"

    def __init__(self, account_id: str, cfg: dict, state_dir: Path):
        super().__init__(account_id, cfg, state_dir)
        self.dir = Path(cfg["files_dir"]) / "aitb"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.magic = int(cfg.get("magic") or 777001)
        self.slippage = int(cfg.get("slippage_points") or 30)

    def heartbeat(self) -> dict | None:
        """status.json written by the EA every second; None when the EA / terminal is not running."""
        p = self.dir / "status.json"
        try:
            if time.time() - p.stat().st_mtime > HEARTBEAT_MAX_AGE:
                return None
            hb = json.loads(p.read_text("utf-8", errors="replace"))
        except (OSError, ValueError):
            return None
        self.learn_offset(hb.get("server_time", 0), hb.get("gmt_time", 0), hb.get("tick_age", 99) <= 3)
        return hb

    def _learn_clock(self):
        self.heartbeat()

    def request(self, cmd: str, timeout: float = 10.0, **params) -> dict:
        """Writes req_<id>.json atomically and waits for res_<id>.json. Fault.picked tells whether the EA took the request before the timeout."""
        rid = uuid.uuid4().hex[:16]
        req, res, tmp = self.dir / f"req_{rid}.json", self.dir / f"res_{rid}.json", self.dir / f"req_{rid}.tmp"
        tmp.write_text(json.dumps({"id": rid, "cmd": cmd, "ts": int(time.time()), **params}, separators=(",", ":")), "ascii")
        os.replace(tmp, req)
        end = time.time() + timeout
        while time.time() < end:
            if res.exists():
                try:
                    data = json.loads(res.read_text("utf-8", errors="replace"))
                except (OSError, ValueError):
                    time.sleep(0.02)  # the EA is still renaming the file
                    continue
                try:
                    res.unlink()
                except OSError:
                    pass
                return data
            time.sleep(0.02)
        picked = True
        try:
            req.unlink()
            picked = False
        except OSError:
            pass
        raise Fault(504, f"El Expert Advisor de {self.id} no respondió a «{cmd}» en {timeout:.0f} s" + ("" if picked else " (no llegó a leer la petición: ¿EA activo en un gráfico?)"), picked)

    def _ok(self, r: dict, what: str) -> dict:
        if not r.get("ok"):
            err = int(r.get("err") or 0)
            raise Fault(404 if err == 4106 else 502, f"{what}: {MT4_ERR.get(err, f'error MT4 {err}')}")
        return r

    def status(self) -> dict:
        hb = self.heartbeat()
        base = {"_id": self.id, "platform": "mt4", "type": "bridge", "reliability": "own", "region": "vps-windows"}
        if not hb:
            return {**base, "state": "UNDEPLOYED", "connectionStatus": "DISCONNECTED", "detail": "El Expert Advisor no escribe su latido: abre el terminal MT4, activa Algo Trading y arrastra HFM_Bridge_EA a un gráfico"}
        return {**base, "state": "DEPLOYED", "connectionStatus": "CONNECTED" if hb.get("connected") else "DISCONNECTED", "login": str(hb.get("login", "")), "server": hb.get("server", ""),
                "broker": hb.get("company", ""), "tradeAllowed": bool(hb.get("trade_allowed")), "detail": "" if hb.get("connected") else "Terminal MT4 sin conexión con el servidor del bróker"}

    def account_info(self) -> dict:
        hb = self.heartbeat()
        if not hb:
            raise Fault(503, self.status()["detail"])
        return {"login": str(hb.get("login", "")), "server": hb.get("server", ""), "broker": hb.get("company", ""), "name": hb.get("name", ""), "currency": hb.get("currency", ""),
                "balance": hb.get("balance", 0), "equity": hb.get("equity", 0), "margin": hb.get("margin", 0), "freeMargin": hb.get("free_margin", 0),
                "leverage": hb.get("leverage", 0), "tradeAllowed": bool(hb.get("trade_allowed")), "platform": "mt4"}

    def _symbols(self) -> list:
        return self._ok(self.request("symbols", 15), "symbols")["symbols"]

    def _spec(self, name: str) -> dict:
        r = self._ok(self.request("spec", 8, symbol=name), f"spec {name}")
        return {"symbol": name, "digits": int(r["digits"]), "point": r["point"], "minVolume": r["min_lot"], "volumeStep": r["lot_step"], "maxVolume": r["max_lot"],
                "contractSize": r["contract"], "tickSize": r["tick_size"], "tickValue": r["tick_value"], "tradeMode": mt4_trade_mode(r["trade_mode"], r.get("trade_allowed")),
                "stopsLevel": r.get("stops_level", 0), "bid": r["bid"], "ask": r["ask"], "time": r.get("time", 0)}

    def tick(self, symbol: str) -> dict:
        s = self.spec(symbol)
        if not s["bid"] or not s["ask"]:
            raise Fault(503, f"Sin cotización de {s['symbol']} en {self.id}")
        return {"symbol": s["symbol"], "bid": s["bid"], "ask": s["ask"], "spread": round(s["ask"] - s["bid"], s["digits"]), "time": iso(self.to_utc(s["time"]) if s["time"] else time.time())}

    def candles(self, symbol: str, tf: str, start_utc: float | None, limit: int) -> list:
        name = self.resolve(symbol)
        for attempt in range(4):  # MT4 downloads missing history in the background: first answers may be empty (4066)
            r = self.request("candles", 30, symbol=name, tf=TF_MIN[tf], start=int(self.to_server(start_utc)) if start_utc else 0, limit=limit)
            if r.get("ok") and r.get("rows"):
                return [{"time": iso(self.to_utc(t)), "open": o, "high": h, "low": lo, "close": c, "tickVolume": v} for t, o, h, lo, c, v in r["rows"]]
            if attempt < 3:
                time.sleep(0.8)
        self._ok(r, f"candles {name}")
        return []

    # ---- positions with stable ids across partial closes (MT4 gives the remainder a new ticket and the comment «from #old») ----
    def _root(self, ticket: int, comment: str) -> str:
        t = str(ticket)
        root = self.state["alias"].get(t)
        if root is None:
            m = FROM_RE.search(comment or "")
            if not m:
                return t
            root = self.state["alias"].get(m.group(1), m.group(1))
            self._link(root, ticket)
        return root

    def _link(self, root: str, ticket: int, client_id: str = None):
        self.state["alias"][str(ticket)] = root
        chain = self.state["chain"].setdefault(root, [int(root)])
        if int(ticket) not in chain:
            chain.append(int(ticket))
        if client_id:
            self.state["client"][root] = client_id
        self.save_state()

    def _raw_positions(self) -> list:
        return [p for p in self._ok(self.request("positions", 10), "positions")["positions"] if int(p["type"]) in (0, 1)]

    def positions(self) -> list:
        out = []
        for p in self._raw_positions():
            root = self._root(p["ticket"], p.get("comment", ""))
            out.append({"id": root, "ticket": p["ticket"], "symbol": p["symbol"], "type": "POSITION_TYPE_BUY" if int(p["type"]) == 0 else "POSITION_TYPE_SELL", "volume": p["lots"],
                        "openPrice": p["open_price"], "currentPrice": p["price"], "stopLoss": p["sl"] or None, "takeProfit": p["tp"] or None, "profit": p["profit"], "swap": p["swap"],
                        "commission": p["commission"], "clientId": self.state["client"].get(root) or p.get("comment", ""), "comment": p.get("comment", ""), "magic": p.get("magic"),
                        "time": iso(self.to_utc(p["open_time"]))})
        return out

    def _history(self, start_utc: float, end_utc: float) -> list:
        return self._ok(self.request("history", 30, **{"from": int(self.to_server(start_utc)), "to": int(self.to_server(end_utc)) + 3600}), "history")["orders"]

    def _deal(self, o: dict, root: str, out: bool) -> dict:
        buy = int(o["type"]) == 0
        return {"id": f"{o['ticket']}{'o' if out else 'i'}", "positionId": root, "symbol": o["symbol"], "type": "DEAL_TYPE_SELL" if buy == out else "DEAL_TYPE_BUY",
                "entryType": "DEAL_ENTRY_OUT" if out else "DEAL_ENTRY_IN", "volume": o["lots"], "price": o["close_price"] if out else o["open_price"],
                "profit": o["profit"] if out else 0.0, "swap": o["swap"] if out else 0.0, "commission": o["commission"] if out else 0.0,
                "time": iso(self.to_utc(o["close_time"] if out else o["open_time"])), "comment": o.get("comment", ""), "clientId": self.state["client"].get(root) or o.get("comment", "")}

    def deals_by_position(self, position_id: str) -> list:
        """Synthetic MT5-style deals of one position: one IN (total volume) and one OUT per closed part."""
        root = str(position_id)
        tickets = set(self.state["chain"].get(root, [int(root)]))
        closed = [o for o in self._history(time.time() - 120 * 86400, time.time()) if int(o["type"]) in (0, 1) and (o["ticket"] in tickets or self._root(o["ticket"], o.get("comment", "")) == root)]
        if not closed:
            return []
        still_open = sum(p["volume"] for p in self.positions() if p["id"] == root)
        first = min(closed, key=lambda o: o["open_time"])
        entry = {**self._deal(first, root, False), "volume": round(sum(o["lots"] for o in closed) + still_open, 8)}
        return [entry] + [self._deal(o, root, True) for o in sorted(closed, key=lambda o: o["close_time"])]

    def deals_by_time(self, start_utc: float, end_utc: float) -> list:
        deals = []
        for o in self._history(start_utc, end_utc):
            t = int(o["type"])
            if not start_utc <= self.to_utc(o["close_time"]) <= end_utc:
                continue
            if t in (6, 7):
                deals.append({"id": str(o["ticket"]), "type": "DEAL_TYPE_BALANCE" if t == 6 else "DEAL_TYPE_CREDIT", "profit": o["profit"], "time": iso(self.to_utc(o["close_time"])), "comment": o.get("comment", "")})
            elif t in (0, 1):
                deals.append(self._deal(o, self._root(o["ticket"], o.get("comment", "")), True))
        return deals

    def orders_by_time(self, start_utc: float, end_utc: float) -> list:
        rows = [{"id": str(p["ticket"]), "positionId": p["id"], "clientId": p["clientId"], "state": "ORDER_STATE_FILLED", "symbol": p["symbol"], "time": p["time"]}
                for p in self.positions() if start_utc <= parse_iso(p["time"]) <= end_utc]
        for o in self._history(start_utc, end_utc):
            if int(o["type"]) in (0, 1) and start_utc <= self.to_utc(o["open_time"]) <= end_utc:
                root = self._root(o["ticket"], o.get("comment", ""))
                rows.append({"id": str(o["ticket"]), "positionId": root, "clientId": self.state["client"].get(root) or o.get("comment", ""), "state": "ORDER_STATE_FILLED",
                             "symbol": o["symbol"], "time": iso(self.to_utc(o["open_time"]))})
        return rows

    def _trade_request(self, cmd: str, **params) -> dict:
        """Trade command → MetaApi-style result. No answer: ERR_NO_CONNECTION when the EA never took the file (not executed), ERR_TRADE_TIMEOUT when it did (unknown)."""
        if not self.heartbeat():
            return result("ERR_NO_CONNECTION", 6, "El Expert Advisor de MT4 no está activo")
        try:
            r = self.request(cmd, 45, **params)
        except Fault as e:
            return result("ERR_TRADE_TIMEOUT" if e.picked else "ERR_NO_CONNECTION", 128 if e.picked else 6, e.detail)
        err = int(r.get("err") or 0)
        if r.get("ok") or (cmd == "modify" and err == 1):
            return {**result("ERR_NO_ERROR"), **r}
        return result(MT4_ERR.get(err, f"ERR_MT4_{err}"), err, r.get("msg") or f"error MT4 {err}")

    def trade(self, body: dict) -> dict:
        try:
            return self._trade(body)
        except Fault as e:  # only the reads done BEFORE sending the order can raise: nothing was executed
            if e.status in (503, 504):
                return result("ERR_NO_CONNECTION", 6, e.detail)
            raise

    def _trade(self, body: dict) -> dict:
        action = body.get("actionType", "")
        if action in ("ORDER_TYPE_BUY", "ORDER_TYPE_SELL"):
            s = self.spec(body["symbol"])
            vol = quantize_volume(body["volume"], s)
            if not vol:
                return result("ERR_INVALID_TRADE_VOLUME", 131, f"{body['volume']} lotes por debajo del mínimo {s['minVolume']} de {s['symbol']}")
            d = s["digits"]
            r = self._trade_request("open", symbol=s["symbol"], side=0 if action.endswith("BUY") else 1, lots=vol, sl=round(float(body.get("stopLoss") or 0), d),
                                    tp=round(float(body.get("takeProfit") or 0), d), comment=str(body.get("clientId") or "")[:31], magic=int(body.get("magic") or self.magic), slippage=self.slippage)
            if r["stringCode"] == "ERR_NO_ERROR":
                r.update(orderId=str(r["ticket"]), positionId=str(r["ticket"]), volume=vol, symbol=s["symbol"])
            return r
        pid = str(body.get("positionId") or "")
        pos = next((p for p in self.positions() if p["id"] == pid), None)
        if not pos:
            return result("TRADE_RETCODE_POSITION_CLOSED", 4108, f"La posición {pid} no está abierta en {self.id}")
        if action == "POSITION_MODIFY":
            d = self._spec(pos["symbol"])["digits"]
            tp = body.get("takeProfit")
            r = self._trade_request("modify", ticket=pos["ticket"], sl=round(float(body.get("stopLoss") or 0), d), tp=round(float(tp), d) if tp is not None else -1)
            return {**r, "positionId": pid}
        if action in ("POSITION_CLOSE_ID", "POSITION_PARTIAL"):
            lots = 0.0
            if action == "POSITION_PARTIAL":
                lots = floor_step(float(body["volume"]), self._spec(pos["symbol"])["volumeStep"])
                if lots <= 0:
                    return result("ERR_INVALID_TRADE_VOLUME", 131, "Volumen de cierre parcial inválido")
                lots = 0.0 if lots >= pos["volume"] - 1e-9 else lots
            r = self._trade_request("close", ticket=pos["ticket"], lots=lots, slippage=self.slippage)
            if r["stringCode"] == "ERR_NO_ERROR":
                if r.get("new_ticket"):
                    self._link(pid, int(r["new_ticket"]), pos["clientId"])
                r.update(orderId=str(pos["ticket"]), positionId=pid)
            return r
        raise Fault(400, f"actionType no soportado: {action}")

    def reconnect(self) -> dict:
        self._names, self._resolved = (0.0, []), {}
        return self.status()


# =====================================================================================================================
# MT5 · native MetaTrader5 library, one child process per account
# =====================================================================================================================
class Mt5Core:
    """Runs inside the child process (or with a fake `mt5` module in tests)."""
    def __init__(self, mt5, cfg: dict):
        self.mt5, self.cfg = mt5, cfg
        self.ready = False

    def connect(self) -> bool:
        kw = {k: self.cfg[c] for k, c in (("login", "login"), ("password", "password"), ("server", "server")) if self.cfg.get(c)}
        if "login" in kw:
            kw["login"] = int(kw["login"])
        self.ready = bool(self.mt5.initialize(path=self.cfg["terminal_path"], timeout=int(self.cfg.get("init_timeout_ms") or 60000), **kw))
        return self.ready

    def _need(self):
        if not self.ready and not self.connect():
            raise Fault(503, f"No se pudo iniciar el terminal MT5 ({self.cfg['terminal_path']}): {self.mt5.last_error()}")
        ti = self.mt5.terminal_info()
        if ti is None:
            self.ready = False
            raise Fault(503, f"El terminal MT5 no responde: {self.mt5.last_error()}")
        return ti

    def status(self) -> dict:
        try:
            ti = self._need()
        except Fault as e:
            return {"state": "UNDEPLOYED", "connectionStatus": "DISCONNECTED", "detail": e.detail}
        ai = self.mt5.account_info()
        return {"state": "DEPLOYED", "connectionStatus": "CONNECTED" if ti.connected and ai else "DISCONNECTED", "login": str(ai.login) if ai else "", "server": ai.server if ai else "",
                "broker": ai.company if ai else "", "tradeAllowed": bool(ti.trade_allowed and ai and ai.trade_allowed and ai.trade_expert),
                "detail": "" if ti.connected else "Terminal MT5 sin conexión con el servidor del bróker"}

    def account_info(self) -> dict:
        ti = self._need()
        ai = self.mt5.account_info()
        if ai is None:
            raise Fault(503, f"MT5 sin sesión de cuenta: {self.mt5.last_error()}")
        return {"login": str(ai.login), "server": ai.server, "broker": ai.company, "name": ai.name, "currency": ai.currency, "balance": ai.balance, "equity": ai.equity, "margin": ai.margin,
                "freeMargin": ai.margin_free, "leverage": ai.leverage, "tradeAllowed": bool(ti.trade_allowed and ai.trade_allowed and ai.trade_expert), "platform": "mt5"}

    def server_clock(self, probes: list) -> dict:
        """Latest tick time of a few liquid symbols, and whether ticks are flowing right now (needed to trust the server-time offset)."""
        def latest():
            ts = [t.time_msc for t in (self.mt5.symbol_info_tick(s) for s in probes) if t]
            return max(ts) if ts else 0
        self._need()
        a = latest()
        time.sleep(1.5)
        b = latest()
        return {"server_time": b / 1000, "utc_time": time.time(), "fresh": bool(b and b != a)}

    def symbols(self) -> list:
        self._need()
        return [s.name for s in (self.mt5.symbols_get() or [])]

    def spec(self, name: str) -> dict:
        self._need()
        self.mt5.symbol_select(name, True)
        s = self.mt5.symbol_info(name)
        if s is None:
            raise Fault(404, f"Símbolo «{name}» desconocido en MT5")
        return {"symbol": name, "digits": s.digits, "point": s.point, "minVolume": s.volume_min, "volumeStep": s.volume_step, "maxVolume": s.volume_max, "contractSize": s.trade_contract_size,
                "tickSize": s.trade_tick_size, "tickValue": s.trade_tick_value, "tradeMode": TRADE_MODES.get(s.trade_mode, MODE_FULL), "stopsLevel": s.trade_stops_level,
                "fillingMode": s.filling_mode, "bid": s.bid, "ask": s.ask, "time": s.time}

    def candles(self, name: str, tf: str, start_server: float | None, limit: int) -> list:
        self._need()
        self.mt5.symbol_select(name, True)
        frame = getattr(self.mt5, {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "30m": "TIMEFRAME_M30", "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4",
                                   "1d": "TIMEFRAME_D1", "1w": "TIMEFRAME_W1"}[tf])
        rates = None
        for _ in range(3):  # the terminal may still be downloading this history
            rates = (self.mt5.copy_rates_from(name, frame, datetime.fromtimestamp(start_server, timezone.utc), limit) if start_server
                     else self.mt5.copy_rates_from_pos(name, frame, 0, limit))
            if rates is not None and len(rates):
                break
            time.sleep(0.7)
        if rates is None:
            raise Fault(502, f"MT5 no devolvió velas de {name}: {self.mt5.last_error()}")
        return [[int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["tick_volume"])] for r in rates]

    def positions(self) -> list:
        self._need()
        return [{"id": str(p.ticket), "ticket": p.ticket, "symbol": p.symbol, "type": "POSITION_TYPE_BUY" if p.type == 0 else "POSITION_TYPE_SELL", "volume": p.volume, "openPrice": p.price_open,
                 "currentPrice": p.price_current, "stopLoss": p.sl or None, "takeProfit": p.tp or None, "profit": p.profit, "swap": p.swap, "commission": 0.0, "clientId": p.comment,
                 "comment": p.comment, "magic": p.magic, "time": p.time} for p in (self.mt5.positions_get() or [])]

    def _deal(self, d) -> dict:
        return {"id": str(d.ticket), "positionId": str(d.position_id), "orderId": str(d.order), "symbol": d.symbol, "type": MT5_DEAL_TYPES.get(d.type, f"DEAL_TYPE_{d.type}"),
                "entryType": MT5_ENTRY.get(d.entry, ""), "volume": d.volume, "price": d.price, "profit": d.profit, "swap": d.swap, "commission": d.commission + getattr(d, "fee", 0.0),
                "time": d.time, "comment": d.comment, "clientId": d.comment, "magic": d.magic}

    def deals_by_position(self, position_id: str) -> list:
        self._need()
        return [self._deal(d) for d in (self.mt5.history_deals_get(position=int(position_id)) or [])]

    def deals_by_time(self, start_server: float, end_server: float) -> list:
        self._need()
        f, t = datetime.fromtimestamp(start_server, timezone.utc), datetime.fromtimestamp(end_server + 3600, timezone.utc)
        return [self._deal(d) for d in (self.mt5.history_deals_get(f, t) or [])]

    def orders_by_time(self, start_server: float, end_server: float) -> list:
        self._need()
        f, t = datetime.fromtimestamp(start_server, timezone.utc), datetime.fromtimestamp(end_server + 3600, timezone.utc)
        return [{"id": str(o.ticket), "positionId": str(o.position_id), "clientId": o.comment, "state": "ORDER_STATE_FILLED" if o.state == 4 else f"ORDER_STATE_{o.state}",
                 "symbol": o.symbol, "time": o.time_done or o.time_setup} for o in (self.mt5.history_orders_get(f, t) or [])]

    def _filling(self, s: dict) -> int:
        mode = int(s.get("fillingMode") or 0)
        return self.mt5.ORDER_FILLING_FOK if mode & 1 else self.mt5.ORDER_FILLING_IOC if mode & 2 else self.mt5.ORDER_FILLING_RETURN

    def _send(self, req: dict) -> dict:
        res = self.mt5.order_send(req)
        if res is None:  # the request never left the terminal
            code, msg = self.mt5.last_error()
            return result("ERR_NO_CONNECTION", code, f"order_send sin respuesta: {msg}")
        name = MT5_RET.get(res.retcode)
        return result(f"TRADE_RETCODE_{name}" if name else f"TRADE_RETCODE_{res.retcode}", res.retcode, res.comment, orderId=str(res.order or ""), dealId=str(res.deal or ""),
                      price=res.price, volume=res.volume)

    def trade(self, body: dict) -> dict:
        ti = self._need()
        if not ti.connected:
            return result("TRADE_RETCODE_CONNECTION", 10031, "Terminal MT5 sin conexión con el bróker")
        action, mt5 = body.get("actionType", ""), self.mt5
        dev = int(self.cfg.get("deviation_points") or 30)
        if action in ("ORDER_TYPE_BUY", "ORDER_TYPE_SELL"):
            s = self.spec(body["symbol"])
            vol = quantize_volume(body["volume"], s)
            if not vol:
                return result("TRADE_RETCODE_INVALID_VOLUME", 10014, f"{body['volume']} lotes por debajo del mínimo {s['minVolume']} de {s['symbol']}")
            buy, d = action.endswith("BUY"), s["digits"]
            tick = mt5.symbol_info_tick(s["symbol"])
            if tick is None or not tick.ask or not tick.bid:
                return result("TRADE_RETCODE_PRICE_OFF", 10021, f"Sin cotización de {s['symbol']}")
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": s["symbol"], "volume": vol, "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": tick.ask if buy else tick.bid,
                   "sl": round(float(body.get("stopLoss") or 0), d), "tp": round(float(body.get("takeProfit") or 0), d), "deviation": dev, "magic": int(body.get("magic") or 777001),
                   "comment": str(body.get("clientId") or "")[:31], "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(s)}
            r = self._send(req)
            if r["stringCode"] in ("TRADE_RETCODE_DONE", "TRADE_RETCODE_DONE_PARTIAL", "TRADE_RETCODE_PLACED"):
                pos = mt5.positions_get(ticket=int(r["orderId"])) if r["orderId"] else None
                if not pos:  # netting accounts / brokers where the position ticket differs from the order ticket
                    pos = [p for p in (mt5.positions_get(symbol=s["symbol"]) or []) if p.comment == req["comment"]]
                r["positionId"] = str(pos[0].ticket) if pos else r["orderId"]
            return r
        pid = str(body.get("positionId") or "")
        found = mt5.positions_get(ticket=int(pid)) if pid.isdigit() else None
        if not found:
            return result("TRADE_RETCODE_POSITION_CLOSED", 10036, f"La posición {pid} no está abierta")
        p = found[0]
        s = self.spec(p.symbol)
        if action == "POSITION_MODIFY":
            tp = body.get("takeProfit")
            r = self._send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": round(float(body.get("stopLoss") or 0), s["digits"]),
                            "tp": round(float(tp), s["digits"]) if tp is not None else p.tp})
            return {**r, "positionId": pid}
        if action in ("POSITION_CLOSE_ID", "POSITION_PARTIAL"):
            vol = p.volume
            if action == "POSITION_PARTIAL":
                vol = min(floor_step(float(body["volume"]), s["volumeStep"]), p.volume)
                if vol <= 0:
                    return result("TRADE_RETCODE_INVALID_VOLUME", 10014, "Volumen de cierre parcial inválido")
            tick = mt5.symbol_info_tick(p.symbol)
            if tick is None:
                return result("TRADE_RETCODE_PRICE_OFF", 10021, f"Sin cotización de {p.symbol}")
            buy = p.type == 0
            r = self._send({"action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol, "volume": vol, "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                            "price": tick.bid if buy else tick.ask, "deviation": dev, "magic": p.magic, "comment": str(body.get("clientId") or "")[:31], "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": self._filling(s)})
            return {**r, "positionId": pid}
        raise Fault(400, f"actionType no soportado: {action}")

    def reconnect(self) -> dict:
        self.mt5.shutdown()
        self.ready = False
        return self.status()


def mt5_worker(conn, cfg: dict):
    """Child process: owns the MetaTrader5 library connection of ONE terminal and answers (method, args) messages."""
    import MetaTrader5 as mt5
    core = Mt5Core(mt5, cfg)
    while True:
        try:
            method, args = conn.recv()
        except EOFError:
            break
        try:
            conn.send(("ok", getattr(core, method)(*args)))
        except Fault as e:
            conn.send(("fault", (e.status, e.detail)))
        except Exception as e:  # noqa: BLE001 - reported to the parent, the worker keeps serving
            conn.send(("fault", (500, f"{type(e).__name__}: {e}")))


class Mt5Driver(Driver):
    platform = "mt5"
    PROBES = ("EURUSD", "XAUUSD", "GBPUSD", "BTCUSD")

    def __init__(self, account_id: str, cfg: dict, state_dir: Path, core: Mt5Core = None):
        super().__init__(account_id, cfg, state_dir)
        self.core, self.proc, self.conn = core, None, None  # `core` given → runs in-process (tests)
        self._clock_at = 0.0

    def _spawn(self):
        self.conn, child = mp.Pipe()
        self.proc = mp.Process(target=mt5_worker, args=(child, self.cfg), daemon=True, name=f"mt5-{self.id}")
        self.proc.start()

    def call(self, method: str, *args, timeout: float = 60.0):
        with self.lock:
            if self.core:
                return getattr(self.core, method)(*args)
            if not self.proc or not self.proc.is_alive():
                self._spawn()
            self.conn.send((method, args))
            if not self.conn.poll(timeout):
                self.proc.kill()
                self.proc = None
                raise Fault(504, f"El terminal MT5 de {self.id} no respondió a «{method}» en {timeout:.0f} s (proceso reiniciado)", picked=True)
            kind, payload = self.conn.recv()
            if kind == "fault":
                raise Fault(*payload)
            return payload

    def _clock(self):
        if self.cfg.get("server_utc_offset_hours") is None and time.time() - self._clock_at > 3600:
            try:
                probes = [n for n in (self._resolved.get(p) or (p if p in self.names() else None) for p in self.PROBES) if n]
                c = self.call("server_clock", probes or self.names()[:3])
                self.learn_offset(c["server_time"], c["utc_time"], c["fresh"])
                if c["fresh"]:
                    self._clock_at = time.time()
            except Fault:
                pass

    def _learn_clock(self):
        self._clock()

    def _t(self, row: dict) -> dict:
        return {**row, "time": iso(self.to_utc(row["time"]))}

    def status(self) -> dict:
        try:
            st = self.call("status", timeout=90)
        except Fault as e:
            st = {"state": "UNDEPLOYED", "connectionStatus": "DISCONNECTED", "detail": e.detail}
        return {"_id": self.id, "platform": "mt5", "type": "bridge", "reliability": "own", "region": "vps-windows", **st}

    def account_info(self) -> dict:
        return self.call("account_info")

    def _symbols(self) -> list:
        return self.call("symbols")

    def _spec(self, name: str) -> dict:
        return self.call("spec", name)

    def tick(self, symbol: str) -> dict:
        self._clock()
        s = self.spec(symbol)
        if not s["bid"] or not s["ask"]:
            raise Fault(503, f"Sin cotización de {s['symbol']} en {self.id}")
        return {"symbol": s["symbol"], "bid": s["bid"], "ask": s["ask"], "spread": round(s["ask"] - s["bid"], s["digits"]), "time": iso(self.to_utc(s["time"]) if s["time"] else time.time())}

    def candles(self, symbol: str, tf: str, start_utc: float | None, limit: int) -> list:
        self._clock()
        rows = self.call("candles", self.resolve(symbol), tf, self.to_server(start_utc) if start_utc else None, limit)
        return [{"time": iso(self.to_utc(t)), "open": o, "high": h, "low": lo, "close": c, "tickVolume": v} for t, o, h, lo, c, v in rows]

    def positions(self) -> list:
        self._clock()
        return [self._t(p) for p in self.call("positions")]

    def deals_by_position(self, position_id: str) -> list:
        return [self._t(d) for d in self.call("deals_by_position", position_id)]

    def deals_by_time(self, start_utc: float, end_utc: float) -> list:
        return [d for d in (self._t(d) for d in self.call("deals_by_time", self.to_server(start_utc), self.to_server(end_utc))) if start_utc <= parse_iso(d["time"]) <= end_utc]

    def orders_by_time(self, start_utc: float, end_utc: float) -> list:
        return [o for o in (self._t(o) for o in self.call("orders_by_time", self.to_server(start_utc), self.to_server(end_utc))) if start_utc <= parse_iso(o["time"]) <= end_utc]

    def trade(self, body: dict) -> dict:
        if body.get("symbol"):
            body = {**body, "symbol": self.resolve(body["symbol"])}
        try:
            return self.call("trade", body, timeout=60)
        except Fault as e:
            if e.status == 504:
                return result("TRADE_RETCODE_TIMEOUT", 10012, e.detail)  # unknown: the caller verifies by clientId
            if e.status == 503:
                return result("ERR_NO_CONNECTION", 6, e.detail)  # terminal not attached: the order was never sent
            raise

    def reconnect(self) -> dict:
        self._names, self._resolved = (0.0, []), {}
        try:
            self.call("reconnect", timeout=90)
        except Fault:
            pass
        return self.status()


# =====================================================================================================================
# HTTP API
# =====================================================================================================================
def load_config(path: str = None) -> dict:
    p = Path(path or os.environ.get("BRIDGE_CONFIG") or HERE / "config_bridge.json")
    cfg = json.loads(p.read_text("utf-8"))
    cfg["token"] = os.environ.get("BRIDGE_TOKEN") or cfg.get("token") or ""
    if len(cfg["token"]) < 24:
        raise SystemExit("BRIDGE_TOKEN ausente o demasiado corto (mínimo 24 caracteres): ponlo en config_bridge.json («token») o en la variable de entorno BRIDGE_TOKEN")
    if not cfg.get("allowed_ips") and not cfg.get("allow_any_ip"):
        raise SystemExit("Falta «allowed_ips» en config_bridge.json: añade la IP pública de tu VPS Linux (o «allow_any_ip»: true bajo tu responsabilidad)")
    for acc_id, a in (cfg.get("accounts") or {}).items():
        need = "files_dir" if a.get("platform") == "mt4" else "terminal_path" if a.get("platform") == "mt5" else None
        if not need or not a.get(need):
            raise SystemExit(f"Cuenta «{acc_id}»: «platform» debe ser mt4 (con «files_dir») o mt5 (con «terminal_path»)")
    return cfg


def build_drivers(cfg: dict) -> dict:
    state_dir = Path(cfg.get("state_dir") or HERE / "state")
    state_dir.mkdir(parents=True, exist_ok=True)
    return {acc_id: (Mt4Driver if a["platform"] == "mt4" else Mt5Driver)(acc_id, a, state_dir) for acc_id, a in cfg["accounts"].items()}


def create_app(cfg: dict, drivers: dict) -> FastAPI:
    app = FastAPI(title="MT Bridge", docs_url=None, redoc_url=None, openapi_url=None)
    allowed = set(cfg.get("allowed_ips") or []) | {"127.0.0.1", "::1"}

    def guard(request: Request):
        ip = request.client.host if request.client else ""
        if not cfg.get("allow_any_ip") and ip not in allowed:
            log.warning("IP no permitida: %s %s", ip, request.url.path)
            raise HTTPException(403, f"IP {ip} no permitida en este puente (allowed_ips)")
        auth = request.headers.get("authorization") or ""
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:].strip().encode(), cfg["token"].encode()):
            raise HTTPException(401, "Token inválido")

    def drv(account_id: str) -> Driver:
        d = drivers.get(account_id)
        if not d:
            raise HTTPException(404, f"Cuenta «{account_id}» no definida en config_bridge.json (disponibles: {', '.join(drivers)})")
        return d

    @app.exception_handler(Fault)
    async def fault_handler(_: Request, e: Fault):
        return JSONResponse({"detail": e.detail}, status_code=e.status)

    base = "/users/current/accounts"
    dep = [Depends(guard)]

    @app.get("/health", dependencies=dep)
    def health():
        return {"ok": True, "time": iso(time.time()), "accounts": [d.status() for d in drivers.values()]}

    @app.get(base, dependencies=dep)
    def accounts():
        return [d.status() for d in drivers.values()]

    @app.get(base + "/{account_id}", dependencies=dep)
    def account(account_id: str):
        return drv(account_id).status()

    @app.post(base + "/{account_id}/reconnect", dependencies=dep)
    def reconnect(account_id: str):
        return drv(account_id).reconnect()

    @app.get(base + "/{account_id}/account-information", dependencies=dep)
    def account_information(account_id: str):
        return drv(account_id).account_info()

    @app.get(base + "/{account_id}/positions", dependencies=dep)
    def positions(account_id: str):
        return drv(account_id).positions()

    @app.get(base + "/{account_id}/symbols", dependencies=dep)
    def symbols(account_id: str):
        return drv(account_id).names()

    @app.get(base + "/{account_id}/symbols/{symbol}/specification", dependencies=dep)
    def specification(account_id: str, symbol: str):
        return drv(account_id).spec(symbol)

    @app.get(base + "/{account_id}/symbols/{symbol}/current-tick", dependencies=dep)
    def current_tick(account_id: str, symbol: str):
        return drv(account_id).tick(symbol)

    @app.get(base + "/{account_id}/historical-market-data/symbols/{symbol}/timeframes/{timeframe}/candles", dependencies=dep)
    def candles(account_id: str, symbol: str, timeframe: str, limit: int = 1000, startTime: str = None):
        if timeframe not in TF_MIN:
            raise HTTPException(400, f"Temporalidad no soportada: {timeframe} ({', '.join(TF_MIN)})")
        return drv(account_id).candles(symbol, timeframe, parse_iso(startTime) if startTime else None, max(1, min(1000, limit)))

    @app.get(base + "/{account_id}/history-deals/position/{position_id}", dependencies=dep)
    def deals_position(account_id: str, position_id: str):
        return drv(account_id).deals_by_position(position_id)

    @app.get(base + "/{account_id}/history-deals/time/{start}/{end}", dependencies=dep)
    def deals_time(account_id: str, start: str, end: str, limit: int = 1000):
        return drv(account_id).deals_by_time(parse_iso(start), parse_iso(end))[:limit]

    @app.get(base + "/{account_id}/history-orders/time/{start}/{end}", dependencies=dep)
    def orders_time(account_id: str, start: str, end: str, limit: int = 200):
        return drv(account_id).orders_by_time(parse_iso(start), parse_iso(end))[-limit:]

    @app.post(base + "/{account_id}/lot-size", dependencies=dep)
    def lot_size(account_id: str, body: dict):
        return drv(account_id).lot_size(str(body.get("symbol") or ""), float(body.get("riskMoney") or 0), float(body.get("slDistance") or 0))

    @app.post(base + "/{account_id}/trade", dependencies=dep)
    def trade(account_id: str, body: dict):
        d = drv(account_id)
        with d.lock:  # one trade at a time per terminal
            res = d.trade(body)
        log.info("%s trade %s %s → %s %s", account_id, body.get("actionType"), body.get("symbol") or body.get("positionId"), res.get("stringCode"), res.get("message", ""))
        return res

    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s · %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(HERE / "mt_bridge.log", encoding="utf-8")])
    import uvicorn
    cfg = load_config()
    drivers = build_drivers(cfg)
    log.info("Cuentas: %s · IPs permitidas: %s", ", ".join(f"{k} ({d.platform})" for k, d in drivers.items()), "cualquiera" if cfg.get("allow_any_ip") else ", ".join(cfg["allowed_ips"]))
    uvicorn.run(create_app(cfg, drivers), host=cfg.get("host") or "0.0.0.0", port=int(cfg.get("port") or 8000), log_level="warning")


if __name__ == "__main__":
    mp.freeze_support()
    main()
