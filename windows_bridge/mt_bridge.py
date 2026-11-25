"""Puente MetaTrader 5 multi-cuenta para Windows (FastAPI + Uvicorn, puerto 8000).

Cada petición se enruta por `account_id` (hfm_v1, hfm_v2, pepperstone_v1…) definido en config_bridge.json. Todas las cuentas usan la
librería oficial MetaTrader5 de Python. Esa librería solo puede estar conectada a UN terminal por proceso, así que el puente levanta un
proceso hijo (worker) por cuenta: cada worker llama una sola vez a mt5.initialize(path=<su terminal64.exe>) y nunca cambia de terminal.
Las cuentas no se bloquean entre sí (un candado por cuenta serializa solo las peticiones de ESE terminal) y un worker colgado se reinicia
sin afectar a los demás. Si en la config se indica «login», el worker comprueba que el terminal está en esa cuenta antes de operar.
Seguridad: cabecera `Authorization: Bearer <BRIDGE_TOKEN>` + lista de IPs permitidas (`allowed_ips`).
Arranque:  python mt_bridge.py   (lee config_bridge.json de esta carpeta, o la ruta de la variable BRIDGE_CONFIG)."""
import os
import re
import sys
import hmac
import json
import math
import time
import logging
import threading
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("mt_bridge")
HERE = Path(__file__).resolve().parent
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
MODE_FULL = "SYMBOL_TRADE_MODE_FULL"
TRADE_MODES = {0: "SYMBOL_TRADE_MODE_DISABLED", 1: "SYMBOL_TRADE_MODE_LONGONLY", 2: "SYMBOL_TRADE_MODE_SHORTONLY", 3: "SYMBOL_TRADE_MODE_CLOSEONLY", 4: MODE_FULL}  # MT5 numbering
SYMBOL_ALIASES = {"XAUUSD": "GOLD", "XAGUSD": "SILVER", "GOLD": "XAUUSD", "SILVER": "XAGUSD"}  # same instrument under either name (HFM Zero: GOLDb / SILVERb)
MAX_SUFFIX = 4  # longest broker suffix accepted when matching by prefix (EURUSDb, EURUSD.a, EURUSDm, EURUSD.pro)
DEFAULT_ACCOUNTS = {  # used when config_bridge.json has no «accounts»; every account is MT5
    "hfm_v1": {"platform": "mt5", "terminal_path": r"C:\MT5_HFM_1\terminal64.exe"},
    "hfm_v2": {"platform": "mt5", "terminal_path": r"C:\MT5_HFM_2\terminal64.exe"},
    "pepperstone_v1": {"platform": "mt5", "terminal_path": r"C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe"},
}
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
    """One account: symbol-name resolution (suffixes and aliases), per-account state file and server-time offset."""
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
        """Broker name of `symbol`, with or without suffix on either side, so the same request works on every account type:
        exact → configured map / suffix → case-insensitive → the other name of gold / silver (XAUUSD ↔ GOLD) → USDT → USD for crypto CFDs →
        the request without its own suffix (EURUSDb on an account that lists EURUSD) → the tradable variant with a broker suffix (EURUSD → EURUSDb)."""
        if symbol in self._resolved:
            return self._resolved[symbol]
        names = self.names()
        up = {n.upper(): n for n in names}
        mapped = (self.cfg.get("symbol_map") or {}).get(symbol)
        suffix = self.cfg.get("symbol_suffix") or ""
        found = mapped if mapped in names else None
        plain = None if found else next((c for c in (symbol, f"{symbol}{suffix}") if c in names), None) or up.get(symbol.upper())
        if plain and self._tradable(plain):
            found = plain
        roots = [symbol]
        if not found:  # the instrument under every name it may have, most specific first
            for root, alt in SYMBOL_ALIASES.items():
                if symbol.upper().startswith(root):
                    roots.append(f"{alt}{symbol[len(root):]}")
            if symbol.upper().endswith("USDT"):
                roots.append(symbol[:-1])  # crypto CFDs are quoted in USD at most brokers (SOLUSDT → SOLUSD)
            bare = [m.group(1) for m in (re.match(r"^([A-Z0-9]{3,})[a-z.#_\-+][A-Za-z0-9.#_\-+]{0,%d}$" % (MAX_SUFFIX - 1), r) for r in roots) if m]  # EURUSDb → EURUSD, GOLD.a → GOLD
            for b in list(bare):
                bare += [alt for root, alt in SYMBOL_ALIASES.items() if b.upper() == root]
            roots += [b for b in bare if b not in roots]
            exact = [up[c.upper()] for r in roots for c in (r, f"{r}{suffix}") if c.upper() in up and up[c.upper()] != plain]
            found = next((n for n in exact if self._tradable(n)), None)
            plain = plain or (exact[0] if exact else None)
        if not found:
            cands = sorted((n for r in roots for n in names if norm(n).startswith(norm(r)) and 0 < len(norm(n)) - len(norm(r)) <= MAX_SUFFIX), key=lambda n: (len(n), n))
            cands = [n for n in dict.fromkeys(cands) if n != plain]
            found = next((n for n in cands[:8] if self._tradable(n)), None) or plain or (cands[0] if cands else None)  # listed but not tradable anywhere: keep the plain name
        if not found:
            raise Fault(404, f"Símbolo «{symbol}» desconocido en la cuenta {self.id}")
        self._resolved[symbol] = found
        return found

    def _tradable(self, name: str) -> bool:
        """Full trading allowed on this exact broker name (an account can list EURUSD disabled next to the tradable EURUSDb)."""
        try:
            return self._spec(name)["tradeMode"] == MODE_FULL
        except Fault as e:
            if e.status in (503, 504):
                raise  # terminal down: not a property of the symbol
            return False

    def spec(self, symbol: str) -> dict:
        return self._spec(self.resolve(symbol))

    def lot_size(self, symbol: str, risk_money: float, sl_distance: float) -> dict:
        return lots_for_risk(risk_money, sl_distance, self.spec(symbol))


# =====================================================================================================================
# MT5 · native MetaTrader5 library, one worker process per account (the library holds ONE terminal connection per process)
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
        if self.cfg.get("portable"):
            kw["portable"] = True
        self.ready = bool(self.mt5.initialize(path=self.cfg["terminal_path"], timeout=int(self.cfg.get("init_timeout_ms") or 60000), **kw))
        return self.ready

    def _check_login(self, ai):
        """This worker must be attached to ITS account: another login in the terminal means a wrong `terminal_path` (never trade on it)."""
        want = self.cfg.get("login")
        if want and ai is not None and int(ai.login) != int(want):
            raise Fault(503, f"El terminal {self.cfg['terminal_path']} está en la cuenta {ai.login}, pero esta cuenta del puente espera el login {want}: revisa «terminal_path» y «login»")

    def _need(self):
        if not self.ready and not self.connect():
            raise Fault(503, f"No se pudo iniciar el terminal MT5 ({self.cfg['terminal_path']}): {self.mt5.last_error()}")
        ti = self.mt5.terminal_info()
        if ti is None:
            self.ready = False
            raise Fault(503, f"El terminal MT5 no responde: {self.mt5.last_error()}")
        self._check_login(self.mt5.account_info())
        return ti

    def status(self) -> dict:
        try:
            ti = self._need()
        except Fault as e:
            return {"state": "UNDEPLOYED", "connectionStatus": "DISCONNECTED", "detail": e.detail}
        ai = self.mt5.account_info()
        return {"state": "DEPLOYED", "heartbeatAt": int(time.time()), "connectionStatus": "CONNECTED" if ti.connected and ai else "DISCONNECTED", "login": str(ai.login) if ai else "", "server": ai.server if ai else "",
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
        try:
            if body.get("symbol"):
                body = {**body, "symbol": self.resolve(body["symbol"])}
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
    cfg = json.loads(p.read_text("utf-8-sig"))
    cfg["token"] = os.environ.get("BRIDGE_TOKEN") or cfg.get("token") or ""
    if len(cfg["token"]) < 24:
        raise SystemExit("BRIDGE_TOKEN ausente o demasiado corto (mínimo 24 caracteres): ponlo en config_bridge.json («token») o en la variable de entorno BRIDGE_TOKEN")
    if not cfg.get("allowed_ips") and not cfg.get("allow_any_ip"):
        raise SystemExit("Falta «allowed_ips» en config_bridge.json: añade la IP pública de tu VPS Linux (o «allow_any_ip»: true bajo tu responsabilidad)")
    cfg["accounts"] = cfg.get("accounts") or {k: dict(v) for k, v in DEFAULT_ACCOUNTS.items()}
    paths = {}
    for acc_id, a in cfg["accounts"].items():
        a.setdefault("platform", "mt5")
        if a["platform"] != "mt5":
            raise SystemExit(f"Cuenta «{acc_id}»: «platform» {a['platform']!r} ya no está soportada. Este puente solo usa MetaTrader 5: pon \"platform\": \"mt5\" y «terminal_path».")
        if not a.get("terminal_path"):
            a["terminal_path"] = (DEFAULT_ACCOUNTS.get(acc_id) or {}).get("terminal_path")
        if not a.get("terminal_path"):
            raise SystemExit(f"Cuenta «{acc_id}»: falta «terminal_path» (ruta completa al terminal64.exe de ESA cuenta)")
        key = os.path.normcase(os.path.normpath(a["terminal_path"]))
        if key in paths:
            raise SystemExit(f"Las cuentas «{paths[key]}» y «{acc_id}» apuntan al mismo terminal ({a['terminal_path']}): cada cuenta necesita su propia instalación de MetaTrader 5")
        paths[key] = acc_id
    return cfg


def build_drivers(cfg: dict, core_factory=None) -> dict:
    """One Mt5Driver (own lock + own worker process) per account. `core_factory(account_id, cfg)` → in-process Mt5Core (tests)."""
    state_dir = Path(cfg.get("state_dir") or HERE / "state")
    state_dir.mkdir(parents=True, exist_ok=True)
    return {acc_id: Mt5Driver(acc_id, a, state_dir, core=core_factory(acc_id, a) if core_factory else None) for acc_id, a in cfg["accounts"].items()}


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

    def all_status() -> list:
        """Every terminal asked at the same time: one that is closed or slow does not delay the others."""
        with ThreadPoolExecutor(max_workers=max(1, len(drivers))) as pool:
            return list(pool.map(lambda d: d.status(), drivers.values()))

    @app.get("/health", dependencies=dep)
    def health():
        return {"ok": True, "time": iso(time.time()), "accounts": all_status()}

    @app.get(base, dependencies=dep)
    def accounts():
        return all_status()

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
