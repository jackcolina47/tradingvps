"""HFM live broker for the FX engine (REAL mode): MetaApi account info/positions cache, symbol mapping (suffix), lot quantization,
market orders with SL/TP, close, stop updates and realized result from history deals."""
import re
import time
import uuid
import asyncio
import logging
from fx.metaapi import MetaApi, MetaApiError
from fx.symbols import SPECS

logger = logging.getLogger("fx.live")
_REASON_RE = re.compile(r"\[(sl|tp)\b", re.I)
_LINK_RE = re.compile(r"^AIT_([0-9a-f]{8})_")
_LINK_OPEN_RE = re.compile(r"^AIT_[0-9a-f]{8}_o$")
BALANCE_DEAL_TYPES = {"DEAL_TYPE_BALANCE", "DEAL_TYPE_CREDIT", "DEAL_TYPE_BONUS", "DEAL_TYPE_CORRECTION"}
BALANCE_DEAL_LABELS = {"DEAL_TYPE_BALANCE": "depósito/retiro", "DEAL_TYPE_CREDIT": "crédito del bróker", "DEAL_TYPE_BONUS": "bono", "DEAL_TYPE_CORRECTION": "corrección"}
RETRY_DELAYS = (4.0, 10.0)  # transient broker/terminal failures: 3 attempts in ~15 s
RECONCILE_BUDGET_SEC = 45.0  # max time spent verifying an order with unknown result (engine lock is held meanwhile)


def new_link() -> str:
    """clientId in MetaApi's required `${strategyId}_${positionId}_${orderId}` format (≤ 31 chars, stored in the MT comment)."""
    return f"AIT_{uuid.uuid4().hex[:8]}_o"


def close_link(link_id: str) -> str:
    m = _LINK_RE.match(link_id or "")
    return f"AIT_{m.group(1) if m else uuid.uuid4().hex[:8]}_c"


def is_our_link(client_id) -> bool:
    """True for positions opened by this bot (clientId AIT_<8hex>_o), even if the DB record is missing."""
    return bool(_LINK_OPEN_RE.match(str(client_id or "")))


# HFM symbol suffix per account type (hfm.com → "What symbol suffixes are used for each account type")
HFM_SUFFIXES = {"": "Premium", "b": "Zero", "r": "Pro", "c": "Cent", "x": "InfinityX"}
TRADE_MODE_FULL = "SYMBOL_TRADE_MODE_FULL"
TRADE_MODE_TXT = {TRADE_MODE_FULL: "operable", "SYMBOL_TRADE_MODE_DISABLED": "deshabilitado", "SYMBOL_TRADE_MODE_CLOSEONLY": "solo cierre",
                  "SYMBOL_TRADE_MODE_LONGONLY": "solo compras", "SYMBOL_TRADE_MODE_SHORTONLY": "solo ventas"}
SUFFIX_PROBE = "EURUSD"
# alternative root names brokers use for the same instrument (matched on the account's symbol list, case/punctuation-insensitive)
SYMBOL_ALIASES = {"XAUUSD": ["GOLD"], "XAGUSD": ["SILVER"]}


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def trade_mode_txt(mode) -> str:
    return TRADE_MODE_TXT.get(str(mode or ""), "operable" if not mode else str(mode))


def q_step(v: float, step: float) -> float:
    if not step or step <= 0:
        return v
    n = round(v / step)
    return round(n * step, 8)


def q_floor(v: float, step: float) -> float:
    if not step or step <= 0:
        return v
    import math
    return round(math.floor(v / step + 1e-9) * step, 8)


class FxLiveBroker:
    def __init__(self, token: str, account_id: str, region: str, base_url: str = "", suffix: str = "", symbol_map: dict = None, suffixes: dict = None):
        self.api = MetaApi(token, region, base_url)
        self.account_id, self.region, self.suffix = account_id, region, suffix or ""
        self.suffixes = dict(suffixes) if suffixes is not None else dict(HFM_SUFFIXES)  # suffix → account type label (broker profile)
        self.symbol_map = dict(symbol_map or {})  # our symbol → broker name when it is not simply symbol+suffix (HFM metals, dotted suffixes…)
        self._info = {"ts": 0.0, "data": None}
        self._positions = {"ts": 0.0, "data": []}
        self._kcache = {}
        self.chart_fail_ts = 0.0  # last failure fetching chart candles from MetaApi (chart falls back to Yahoo for 60 s)
        self.last_error = None

    async def aclose(self):
        await self.api.aclose()

    async def candles(self, symbol: str, tf: str, before_ts: int = None, limit: int = 1000, cache_sec: float = 20.0) -> list:
        """Chart candles from the HFM terminal (same as MetaTrader). `before_ts` (unix s, exclusive) pages backwards; history pages cache 10 min."""
        from datetime import datetime, timezone
        key = (symbol, tf, before_ts, limit)
        hit = self._kcache.get(key)
        if hit and time.time() - hit[0] < (600 if before_ts else cache_sec):
            return hit[1]
        start = datetime.fromtimestamp(before_ts - 1, timezone.utc).isoformat().replace("+00:00", "Z") if before_ts else None
        rows = await self.api.candles(self.account_id, self.broker_symbol(symbol), tf, start, limit, self.region)
        if before_ts:
            rows = [r for r in rows if r[0] // 1000 < before_ts]
        if len(self._kcache) > 300:
            self._kcache = dict(sorted(self._kcache.items(), key=lambda kv: kv[1][0])[-150:])
        self._kcache[key] = (time.time(), rows)
        return rows

    def broker_symbol(self, symbol: str) -> str:
        return self.symbol_map.get(symbol) or f"{symbol}{self.suffix}"

    def our_symbol(self, broker_symbol: str) -> str:
        for ours, theirs in self.symbol_map.items():
            if theirs == broker_symbol:
                return ours
        return broker_symbol[:-len(self.suffix)] if self.suffix and broker_symbol.endswith(self.suffix) else broker_symbol

    async def resolve_symbol(self, symbol: str):
        """`symbol+suffix` is unknown to the server: find the name this account group really uses (HFM: XAUUSDb may be GOLDb, XAUUSD.b…)
        among the account's symbols, preferring tradable variants that carry the suffix. Returns the broker name or None."""
        names = await self.api.symbols(self.account_id, self.region)
        aliases = [symbol] + SYMBOL_ALIASES.get(symbol, [])
        cands = [n for n in names if any(_norm(n).startswith(a) for a in aliases) and len(_norm(n)) <= len(symbol) + 6]
        cands.sort(key=lambda n: (0 if self.suffix and self.suffix.upper() in _norm(n)[len(symbol) - 1:] else 1, len(n), n))
        for n in cands[:6]:
            if n == f"{symbol}{self.suffix}":
                continue
            try:
                spec = await self.api.spec(self.account_id, n, self.region, max_age=0)
            except MetaApiError as e:
                if e.status not in (400, 404):
                    raise
                continue
            if (spec.get("tradeMode") or TRADE_MODE_FULL) == TRADE_MODE_FULL:
                return n
        return None

    def invalidate(self):
        self._info["ts"] = self._positions["ts"] = 0.0

    async def info(self, max_age: float = 10.0) -> dict:
        if self._info["data"] is None or time.time() - self._info["ts"] > max_age:
            raw = await self.api.account_info(self.account_id, self.region, refresh=max_age == 0)
            self._info = {"ts": time.time(), "data": {
                "balance": float(raw.get("balance") or 0), "equity": float(raw.get("equity") or 0), "margin": float(raw.get("margin") or 0),
                "available": float(raw.get("freeMargin") or 0), "leverage": int(raw.get("leverage") or 0), "currency": raw.get("currency") or "",
                "trade_allowed": raw.get("tradeAllowed", True), "broker": raw.get("broker") or "", "server": raw.get("server") or "",
                "unrealised": float(raw.get("equity") or 0) - float(raw.get("balance") or 0)}}
        return self._info["data"]

    @staticmethod
    def _pos(p: dict) -> dict:
        return {"id": str(p.get("id")), "symbol": p.get("symbol"), "side": "long" if str(p.get("type", "")).endswith("BUY") else "short",
                "volume": float(p.get("volume") or 0), "entry": float(p.get("openPrice") or 0), "mark": float(p.get("currentPrice") or 0),
                "sl": p.get("stopLoss"), "tp": p.get("takeProfit"), "profit": float(p.get("profit") or 0), "swap": float(p.get("swap") or 0),
                "commission": float(p.get("commission") or 0), "client_id": p.get("clientId"), "magic": p.get("magic"), "time": p.get("time")}

    async def positions(self, max_age: float = 4.0) -> list:
        if time.time() - self._positions["ts"] > max_age:
            raw = await self.api.positions(self.account_id, self.region, refresh=max_age == 0)
            self._positions = {"ts": time.time(), "data": [self._pos(p) for p in raw]}
        return self._positions["data"]

    async def position(self, position_id: str, max_age: float = 0.0) -> dict | None:
        return next((p for p in await self.positions(max_age) if p["id"] == str(position_id)), None)

    async def lot(self, symbol: str) -> dict:
        return self._lot_from(symbol, await self.api.spec(self.account_id, self.broker_symbol(symbol), self.region))

    def _lot_from(self, symbol: str, s: dict) -> dict:
        return {"min": float(s.get("minVolume") or 0.01), "step": float(s.get("volumeStep") or 0.01), "max": float(s.get("maxVolume") or 100),
                "digits": int(s.get("digits") if s.get("digits") is not None else SPECS[symbol]["digits"]), "tick": float(s.get("tickSize") or 0),
                "contract": float(s.get("contractSize") or SPECS[symbol]["contract"]), "symbol": self.broker_symbol(symbol)}

    async def check_symbol(self, symbol: str, resolve: bool = True) -> dict:
        """Quote AND tradability: HFM lists every account group's symbols, but only the ones with your suffix are tradable (others → ERR_TRADE_DISABLED).
        An unknown name is looked up in the account's symbol list once (`resolve`) and remembered in `symbol_map`."""
        try:
            spec = await self.api.spec(self.account_id, self.broker_symbol(symbol), self.region, max_age=0)
            lot = self._lot_from(symbol, spec)
            tick = await self.api.tick(self.account_id, self.broker_symbol(symbol), self.region)
            mode = spec.get("tradeMode") or TRADE_MODE_FULL
            ok = mode == TRADE_MODE_FULL
            return {"ok": ok, "quoted": True, "broker_symbol": lot["symbol"], "min_lot": lot["min"], "lot_step": lot["step"], "digits": lot["digits"],
                    "contract": lot["contract"], "bid": tick.get("bid"), "ask": tick.get("ask"), "trade_mode": trade_mode_txt(mode), "resolved": symbol in self.symbol_map,
                    "error": None if ok else f"cotiza pero NO es operable en esta cuenta ({trade_mode_txt(mode)}) · sufijo de símbolos incorrecto"}
        except MetaApiError as e:
            if e.status in (400, 404) and resolve:
                try:
                    alt = await self.resolve_symbol(symbol)
                except MetaApiError:
                    alt = None
                if alt:
                    self.symbol_map[symbol] = alt
                    return await self.check_symbol(symbol, resolve=False)
                self.symbol_map.pop(symbol, None)
                return {"ok": False, "quoted": False, "transient": False, "broker_symbol": self.broker_symbol(symbol), "trade_mode": None,
                        "error": f"{e.msg} · no existe en esta cuenta con ningún nombre conocido ({', '.join([symbol] + SYMBOL_ALIASES.get(symbol, []))}) · desactívalo en Pares activos"}
            # 400/404 = the broker does not know the symbol; anything else (timeout, 429, 5xx) is a temporary failure, not a verdict
            return {"ok": False, "quoted": False, "transient": e.status not in (400, 404), "broker_symbol": self.broker_symbol(symbol), "trade_mode": None, "error": e.msg}

    async def detect_suffix(self, probe: str = SUFFIX_PROBE) -> dict:
        """Finds which variant of `probe` (EURUSD, EURUSDb, EURUSDr…) the account can actually trade → the symbol suffix of this account type."""
        try:
            names = await self.api.symbols(self.account_id, self.region)
            cands = sorted({n[len(probe):] for n in names if n.startswith(probe) and len(n) - len(probe) <= 4}, key=lambda s: (len(s), s))
        except MetaApiError:
            cands = []
        if not cands:
            cands = list(self.suffixes)
        rows = []
        for suf in cands[:8]:
            try:
                spec = await self.api.spec(self.account_id, probe + suf, self.region, max_age=0)
                rows.append({"suffix": suf, "symbol": probe + suf, "trade_mode": trade_mode_txt(spec.get("tradeMode") or TRADE_MODE_FULL),
                             "tradable": (spec.get("tradeMode") or TRADE_MODE_FULL) == TRADE_MODE_FULL})
            except MetaApiError as e:
                if e.status not in (404, 400):
                    raise
        tradable = [r for r in rows if r["tradable"]]
        chosen = tradable[0]["suffix"] if tradable else None
        return {"suffix": chosen, "account_type": self.suffixes.get(chosen) if chosen is not None else None, "probe": probe, "candidates": rows,
                "current": self.suffix, "matches": chosen == self.suffix if chosen is not None else None}

    async def _wait_position(self, position_id: str, tries: int = 6) -> dict | None:
        for i in range(tries):
            p = await self.position(position_id, max_age=0 if i == 0 else 0.5)
            if p and p["entry"]:
                return p
            await asyncio.sleep(0.8)
        return None

    async def _reconcile_open(self, link: str) -> dict | None:
        """Trade result unknown (timeout / connection dropped mid-trade): MetaApi may still have executed it. Looks for a position (then a filled
        order) carrying our clientId before deciding whether a retry is safe."""
        self.invalidate()
        pos = await self.api.find_position_by_client_id(self.account_id, link, self.region)
        if pos:
            return {"id": str(pos.get("id"))}
        order = await self.api.order_filled_by_client_id(self.account_id, link, self.region)
        return {"id": str(order.get("positionId") or "")} if order and order.get("positionId") else None

    async def _with_retries(self, send, on_unknown):
        """Runs a trade request: 'unknown' results are verified with `on_unknown` (returns a result when it did execute), 'transient' failures are
        retried with backoff, definitive rejections propagate at once. If the verification itself fails the error stays 'unknown' (never retried:
        the order may exist at the broker). The raised error carries `.attempts`."""
        attempts = 0
        while True:
            try:
                return await send(), attempts + 1, None
            except MetaApiError as e:
                if e.kind == "unknown":
                    try:
                        done = await asyncio.wait_for(on_unknown(), RECONCILE_BUDGET_SEC)
                    except (MetaApiError, asyncio.TimeoutError) as ve:
                        detail = ve.msg[:80] if isinstance(ve, MetaApiError) else f"sin respuesta en {RECONCILE_BUDGET_SEC:.0f} s"
                        e.msg, e.attempts = f"{e.msg} · no se pudo verificar en HFM ({detail})", attempts + 1
                        raise e
                    if done is not None:
                        return done, attempts + 1, f"confirmada en HFM tras «{e.msg[:90]}»"
                    e.kind, e.msg = "transient", f"{e.msg} · verificado en HFM: no se ejecutó"
                if e.kind != "transient" or attempts >= len(RETRY_DELAYS):
                    e.attempts = attempts + 1
                    raise
                self.last_error = e.msg
                logger.warning(f"hfm transient ({attempts + 1}/{len(RETRY_DELAYS) + 1}): {e.msg}")
                await asyncio.sleep(e.retry_after or RETRY_DELAYS[attempts])
                attempts += 1

    async def open(self, symbol: str, side: str, lots: float, sl: float, tp: float) -> dict:
        lot = await self.lot(symbol)
        vol = q_floor(lots, lot["step"])
        if vol < lot["min"]:
            raise ValueError(f"{vol} lotes por debajo del lote mínimo del bróker ({lot['min']})")
        vol = min(vol, lot["max"])
        d = lot["digits"]
        links = []
        tp_r = round(tp, d) if tp else None  # None = no take profit (Test cruce EMA)

        async def send():
            links.append(new_link())
            return await self.api.market_order(self.account_id, lot["symbol"], side, vol, round(sl, d), tp_r, links[-1], self.region)

        async def on_unknown():
            found = await self._reconcile_open(links[-1])
            return {"positionId": found["id"], "orderId": "", "reconciled": True} if found else None

        try:
            res, attempts, note = await self._with_retries(send, on_unknown)
        except MetaApiError as e:
            e.link_id = links[-1] if links else ""
            raise
        link = links[-1]
        self.invalidate()
        pos = await self._wait_position(res.get("positionId")) if res.get("positionId") else None
        return {"position_id": str(res.get("positionId") or ""), "order_id": str(res.get("orderId") or ""), "link_id": link, "lots": pos["volume"] if pos else vol,
                "entry": pos["entry"] if pos else 0.0, "commission": pos["commission"] if pos else 0.0, "filled": bool(pos),
                "sl": float(pos["sl"]) if pos and pos.get("sl") else round(sl, d), "tp": float(pos["tp"]) if pos and pos.get("tp") else tp_r,
                "attempts": attempts, "note": note}

    async def close(self, position_id: str, link_id: str = "") -> dict:
        async def send():
            return await self.api.close_position(self.account_id, position_id, self.region, close_link(link_id))

        async def on_unknown():
            self.invalidate()
            return {"orderId": "", "reconciled": True} if await self.position(position_id, max_age=0) is None else None

        res, attempts, note = await self._with_retries(send, on_unknown)
        self.invalidate()
        return {"order_id": str(res.get("orderId") or ""), "attempts": attempts, "note": note}

    async def close_partial(self, position_id: str, lots: float, link_id: str = "") -> dict:
        """Reduce a live position by `lots` (POSITION_PARTIAL). Unknown result → reconciled when the broker volume already dropped."""
        before = await self.position(position_id, max_age=0)

        async def send():
            return await self.api.close_position_partially(self.account_id, position_id, lots, self.region, close_link(link_id)[:28] + "p")

        async def on_unknown():
            self.invalidate()
            p = await self.position(position_id, max_age=0)
            return {"orderId": "", "reconciled": True} if (p is None or (before and p["volume"] < before["volume"] - 1e-9)) else None

        res, attempts, note = await self._with_retries(send, on_unknown)
        self.invalidate()
        return {"order_id": str(res.get("orderId") or ""), "attempts": attempts, "note": note, "commission": 0.0}

    async def set_stop(self, symbol: str, position_id: str, sl: float, tp: float):
        lot = await self.lot(symbol)
        d = lot["digits"]
        want_sl, want_tp = round(sl, d), round(tp, d)

        async def send():
            return await self.api.modify_position(self.account_id, position_id, want_sl, want_tp, self.region)

        async def on_unknown():
            self.invalidate()
            p = await self.position(position_id, max_age=0)
            ok = p is None or (p.get("sl") is not None and abs(float(p["sl"]) - want_sl) < 10 ** -d)
            return {"positionId": position_id, "reconciled": True} if ok else None

        await self._with_retries(send, on_unknown)
        self.invalidate()

    async def closed_result(self, position_id: str, tries: int = 4) -> dict | None:
        """Realized result of a closed position from its exit deals: net = profit + swap + commission (both legs)."""
        for i in range(tries):
            deals = await self.api.deals_by_position(self.account_id, position_id, self.region)
            outs = [d for d in deals if str(d.get("entryType", "")).endswith("OUT")]
            vol_in = sum(float(d.get("volume") or 0) for d in deals if str(d.get("entryType", "")).endswith("IN"))
            vol_out = sum(float(d.get("volume") or 0) for d in outs)
            if outs and (vol_out >= vol_in - 1e-9 or i == tries - 1):
                commission = sum(float(d.get("commission") or 0) for d in deals)
                swap = sum(float(d.get("swap") or 0) for d in deals)
                profit = sum(float(d.get("profit") or 0) for d in outs)
                last = max(outs, key=lambda d: d.get("time") or "")
                comment = " ".join(str(d.get("comment") or d.get("brokerComment") or "") for d in outs)
                m = _REASON_RE.search(comment)
                return {"exit": float(last.get("price") or 0), "pnl": round(profit + swap + commission, 4), "fees": round(-(commission + swap), 4),
                        "reason": m.group(1).upper() if m else None, "time": last.get("time")}
            if i < tries - 1:
                await asyncio.sleep(1.0)
        return None

    async def cash_flows(self, since_ms: int) -> list:
        """Balance operations of the account (deposits, withdrawals, credits, corrections) since `since_ms`, oldest first."""
        from datetime import datetime, timezone
        start = datetime.fromtimestamp(since_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        end = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        deals = await self.api.deals_by_time(self.account_id, start, end, self.region)
        out = []
        for d in deals:
            if d.get("type") not in BALANCE_DEAL_TYPES:
                continue
            amount = float(d.get("profit") or 0)
            if not amount:
                continue
            out.append({"id": f"hfm:{d.get('id')}", "amount": amount, "ts": str(d.get("time") or end).replace("Z", "+00:00"),
                        "note": (d.get("comment") or d.get("brokerComment") or BALANCE_DEAL_LABELS.get(d.get("type"), "operación de balance"))[:80]})
        return sorted(out, key=lambda x: x["ts"])
