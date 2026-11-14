import time
import uuid
import asyncio
import logging
from bybit import BybitClient, BybitError, round_step

logger = logging.getLogger("live")
CASH_TYPES = {"TRANSFER_IN", "TRANSFER_OUT", "BONUS_TRANSFER_IN", "BONUS_TRANSFER_OUT", "AIRDROP", "BONUS", "BONUS_RECOLLECT"}
CASH_LABELS = {"TRANSFER_IN": "transferencia entrante", "TRANSFER_OUT": "transferencia saliente", "BONUS_TRANSFER_IN": "bono recibido",
               "BONUS_TRANSFER_OUT": "bono retirado", "AIRDROP": "airdrop", "BONUS": "bono", "BONUS_RECOLLECT": "bono expirado"}


class LiveBroker:
    """Thin stateful wrapper around BybitClient used by the engine in REAL mode."""

    def __init__(self, client: BybitClient):
        self.client = client
        self._wallet = {"ts": 0.0, "data": None}
        self._positions = {"ts": 0.0, "data": []}
        self.last_error = None

    async def aclose(self):
        await self.client.aclose()

    async def wallet(self, max_age: float = 10.0) -> dict:
        if self._wallet["data"] is None or time.time() - self._wallet["ts"] > max_age:
            self._wallet = {"ts": time.time(), "data": await self.client.wallet()}
        return self._wallet["data"]

    async def positions(self, max_age: float = 4.0) -> list:
        if time.time() - self._positions["ts"] > max_age:
            self._positions = {"ts": time.time(), "data": await self.client.positions()}
        return self._positions["data"]

    def invalidate(self):
        self._wallet["ts"] = self._positions["ts"] = 0.0

    async def lot(self, symbol: str) -> tuple:
        inst = await self.client.instrument(symbol)
        return float(inst["min_qty"]), float(inst["qty_step"])

    async def _wait_fill(self, symbol: str, order_id: str, tries: int = 8) -> dict:
        for _ in range(tries):
            o = await self.client.order(symbol, order_id)
            if o.get("status") in ("Filled", "PartiallyFilledCanceled") and o.get("exec_qty"):
                return o
            if o.get("status") in ("Cancelled", "Rejected", "Deactivated"):
                raise BybitError(0, f"Orden {o.get('status')}")
            await asyncio.sleep(0.5)
        return o or {}

    async def open(self, symbol: str, side: str, qty: float, sl: float, tp: float, leverage: int) -> dict:
        inst = await self.client.instrument(symbol)
        q = round_step(qty, inst["qty_step"])
        if float(q) < float(inst["min_qty"]):
            raise ValueError(f"cantidad {q} por debajo del lote mínimo {inst['min_qty']}")
        await self.client.set_leverage(symbol, leverage)
        link = f"bot-{uuid.uuid4().hex[:18]}"
        res = await self.client.market_order(symbol, "Buy" if side == "long" else "Sell", q,
                                             sl=round_step(sl, inst["tick_size"]) if sl else None, tp=round_step(tp, inst["tick_size"]) if tp else None, link_id=link)
        order_id = res.get("orderId")
        fill = await self._wait_fill(symbol, order_id)
        self.invalidate()
        return {"order_id": order_id, "link_id": link, "qty": fill.get("exec_qty") or float(q),
                "entry": fill.get("avg_price") or 0.0, "fee": fill.get("fee") or 0.0, "filled": bool(fill.get("avg_price"))}

    async def open_limit(self, symbol: str, side: str, qty: float, price: float, sl: float, tp: float, leverage: int) -> dict:
        inst = await self.client.instrument(symbol)
        q = round_step(qty, inst["qty_step"])
        if float(q) < float(inst["min_qty"]):
            raise ValueError(f"cantidad {q} por debajo del lote mínimo {inst['min_qty']}")
        await self.client.set_leverage(symbol, leverage)
        link = f"bot-{uuid.uuid4().hex[:18]}"
        res = await self.client.limit_order(symbol, "Buy" if side == "long" else "Sell", q, round_step(price, inst["tick_size"]),
                                            sl=round_step(sl, inst["tick_size"]), tp=round_step(tp, inst["tick_size"]), link_id=link)
        self.invalidate()
        return {"order_id": res.get("orderId"), "link_id": link, "qty": float(q)}

    async def cancel(self, symbol: str, order_id: str):
        await self.client.cancel_order(symbol, order_id)
        self.invalidate()

    async def close(self, symbol: str, side: str, qty: float) -> dict:
        inst = await self.client.instrument(symbol)
        q = round_step(qty, inst["qty_step"])
        res = await self.client.market_order(symbol, "Sell" if side == "long" else "Buy", q, reduce_only=True)
        fill = await self._wait_fill(symbol, res.get("orderId"))
        self.invalidate()
        return {"order_id": res.get("orderId"), "exit": fill.get("avg_price") or 0.0, "fee": fill.get("fee") or 0.0}

    async def set_stop(self, symbol: str, sl: float):
        inst = await self.client.instrument(symbol)
        await self.client.set_stop(symbol, round_step(sl, inst["tick_size"]))

    async def closed_result(self, symbol: str, since_iso: str, side: str = None, qty: float = None):
        from datetime import datetime
        opened_ms = int(datetime.fromisoformat(since_iso).timestamp() * 1000)
        rows = await self.client.closed_pnl(symbol, opened_ms - 60_000)
        rows = [r for r in rows if r["time"] >= opened_ms - 5_000]
        if side:
            close_side = "Sell" if side == "long" else "Buy"
            match = [r for r in rows if r["side"] == close_side and (qty is None or abs(r["qty"] - qty) < 1e-9)]
            rows = match or rows
        return max(rows, key=lambda r: r["time"]) if rows else None

    async def cash_flows(self, since_ms: int, pages: int = 5) -> list:
        """Transfers in/out of the unified wallet (deposits, withdrawals, bonuses, airdrops) since `since_ms`, oldest first."""
        from datetime import datetime, timezone
        end_ms = int(time.time() * 1000)
        start_ms = max(int(since_ms), end_ms - 7 * 86_400_000 + 60_000)
        out, cursor = [], None
        for _ in range(pages):
            page = await self.client.transaction_log(start_ms, end_ms, cursor)
            for r in page["list"]:
                if r.get("type") not in CASH_TYPES or (r.get("currency") or "USDT") != "USDT":
                    continue
                amount = float(r.get("change") or r.get("cashFlow") or 0)
                if not amount:
                    continue
                ts_ms = int(r.get("transactionTime") or end_ms)
                out.append({"id": f"bybit:{r.get('id')}:{r.get('type')}:{ts_ms}:{amount}", "amount": amount,
                            "ts": datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat(), "note": CASH_LABELS.get(r.get("type"), r.get("type"))})
            cursor = page["cursor"]
            if not cursor:
                break
        return sorted(out, key=lambda x: x["ts"])
