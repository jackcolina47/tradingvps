"""FxEngine mixin · signals: scan loop, AI-funnel adapter and risk/market gates."""
import time
import asyncio
from models import now_iso
from legacy import exit_signal, EXIT_ON_CROSS, EXIT_REASON, EMA_TEST
from fx.models import FxSettings
from fx.market import HTF, market_open, market_status
from fx.symbols import SPECS, pips
from fx import filters
import trade_guard
import funnel
from bson import ObjectId
from indicators import build_df
from fx.shadow import open_shadow
from fx.engine_core import logger


class SignalsMixin:
    async def _scan_loop(self):
        await asyncio.sleep(3)
        while True:
            settings = await self.get_settings()
            try:
                await self.scan_once(settings)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("scan error")
                await self.log("ERROR", f"Error en escaneo: {e}")
            self.next_scan_ts = funnel.next_trigger()
            try:
                await asyncio.wait_for(self.scan_event.wait(), timeout=max(0.5, self.next_scan_ts - time.time()))
            except asyncio.TimeoutError:
                pass
            self.scan_event.clear()

    @staticmethod
    def _df(klines: list, atr_period: int = 14):
        return build_df(klines, atr_period)

    async def scan_once(self, settings: FxSettings):
        """One AI-funnel cycle (see funnel.py). Legacy positions of the old EMA-cross strategies keep their exit on the opposite cross."""
        if not market_open():
            if not self._closed_logged:
                self._closed_logged = True
                nxt = market_status()["next_change"]
                await self.log("SCAN", f"Mercado forex cerrado · sin análisis hasta {nxt[:16].replace('T', ' ')} UTC")
            return
        self._closed_logged = False
        self.scanning = True
        try:
            if self.running:
                for symbol in {d["symbol"] for d in await self.db.positions.find({"strategy": {"$in": list(EXIT_ON_CROSS)}}, {"symbol": 1}).to_list(100)}:
                    df = self._df(await self.market.fetch_klines(symbol, settings.timeframe, 300, cache_sec=60))
                    await self._exit_on_cross(symbol, df, len(df) - 2)
            await funnel.run(self, settings)
            self.last_scan = now_iso()
        finally:
            self.scanning = False

    # ---------- AI funnel adapter ----------
    funnel_mtf = ("4h", "1h")   # 4H structure + 1H confirmation before the entry timeframe

    funnel_rich = True          # fast EMAs, ADX slope, swings, range regime, over-extension filter and AI-stop buffer
    funnel_late_atr = 0.35      # a market entry is dropped when the price already ran this many ATR past the signal candle's close

    async def funnel_frame(self, symbol: str, tf: str, limit: int = 300):
        """Candles with the broker's tick volume while the MetaApi account is deployed; Yahoo (no volume → candle range) otherwise."""
        b = self.broker
        if b and self.cfg and self.cfg.hfm_state == "DEPLOYED" and time.time() - b.chart_fail_ts > 60:
            try:
                rows = await b.candles(symbol, tf, None, limit, cache_sec=45)
                if rows and len(rows) >= 220:
                    return self._df(rows), sum(r[5] for r in rows[-50:]) > 0
            except Exception as e:
                b.chart_fail_ts = time.time()
                logger.warning(f"velas {self.bk} {symbol} {tf}: {e} → usando Yahoo")
        return self._df(await self.market.fetch_klines(symbol, tf, limit, cache_sec=60)), False

    @staticmethod
    def funnel_htf(tf: str) -> str:
        return HTF[tf]

    @staticmethod
    def funnel_digits(symbol: str):
        return SPECS[symbol]["digits"]

    def funnel_analysis(self, symbol: str, m: dict) -> dict:
        return {"htf": None, "atr_pips": round(pips(symbol, m["atr"]), 1)}

    def funnel_ctx(self, symbol: str) -> dict:
        t = self.market.tickers.get(symbol, {})
        return {"modo": self.mode_label, "cambio_dia_pct": t.get("change_pct"), "spread_pips": round(pips(symbol, t.get("spread") or SPECS[symbol]["spread"]), 1),
                "sesiones_abiertas": [x["name"] for x in market_status()["sessions"] if x["open"]]}

    async def funnel_state(self, settings: FxSettings) -> dict:
        acc = await self.account_summary()
        docs = await self.db.positions.find({}, {"symbol": 1, "horizon": 1, "exit_mode": 1}).to_list(100)
        used = len([p for p in docs if (p.get("horizon") or "short") == "short" and p.get("exit_mode") != "cross"])
        block = ("límite de pérdida diaria alcanzado" if acc["daily_paused"] else filters.rollover_block(settings.rollover_guard)
                 or (f"cuenta {self.bk} sin conexión con MetaApi" if self.live and self.hfm["down_since"] else None)
                 or ("regla del viernes · sin entradas nuevas desde las 12:00 NY" if settings.close_before_weekend and trade_guard.friday()["block"] else None)
                 or (f"máximo de posiciones alcanzado ({settings.max_positions})" if used >= settings.max_positions else None))
        return {"acc": acc, "open": {p["symbol"] for p in docs}, "free": settings.max_positions - used, "block": block}

    async def funnel_open(self, symbol: str, sig: dict, settings: FxSettings, signal_id: str, acc: dict):
        block, _, _ = await self._blocks(symbol, settings, acc, sig["side"])
        if block:
            await self.log("RISK", f"{symbol}: entrada del embudo no ejecutada · {block}")
            return False, block
        pos, reason = await self.open_position(symbol, sig, settings, signal_id)
        if pos:
            await self.db.signals.update_one({"_id": ObjectId(signal_id)}, {"$set": {"executed": True}})
        return bool(pos), reason

    async def funnel_shadow(self, signal_id: str, symbol: str, sig: dict, settings: FxSettings, kind: str, acc: dict, confidence: int):
        await open_shadow(self, signal_id, symbol, sig, settings, kind, acc, ai_confidence=confidence)

    async def _exit_on_cross(self, symbol: str, df, i: int):
        """Strategy exits decided at candle close (ema_cross: the EMAs crossed back against the position)."""
        row = df.iloc[i]
        async for d in self.db.positions.find({"symbol": symbol, "strategy": {"$in": list(EXIT_ON_CROSS)}}, {"_id": 1, "side": 1, "strategy": 1}):
            if exit_signal(d["strategy"], d["side"], float(row["ema10"]), float(row["ema20"])):
                await self.log("INFO", f"{symbol}: la EMA10 volvió a cruzar la EMA20 en contra de la posición {d['side'].upper()} · cierre por regla de la estrategia"
                               + (" (test cruce EMA: se invierte si el test sigue activo)" if d["strategy"] == EMA_TEST else ""))
                await self.close_position(str(d["_id"]), None, EXIT_REASON)

    async def _blocks(self, symbol: str, settings: FxSettings, acc: dict, side: str = None, horizon: str = "short"):
        open_docs = await self.db.positions.find({}, {"symbol": 1, "side": 1, "horizon": 1, "exit_mode": 1}).to_list(100)
        open_syms = [p["symbol"] for p in open_docs]
        if not market_open():
            return "mercado cerrado", "MERCADO", open_syms
        g_reason, g_code = trade_guard.entry_block(self.guard_kind, symbol)
        if g_reason:
            return g_reason, g_code, open_syms
        if self._pair_taken(open_docs, symbol, False):
            return "ya existe una posición abierta en el par", "BLOQUEADA", open_syms
        slot = self._slot_block(open_docs, settings, horizon)
        if slot:
            return slot, "BLOQUEADA", open_syms
        if acc["daily_paused"]:
            return "límite de pérdida diaria alcanzado", "BLOQUEADA", open_syms
        roll = filters.rollover_block(settings.rollover_guard)
        if roll:
            return roll, "ROLLOVER", open_syms
        if side:
            exp = filters.exposure_block(open_docs, symbol, side, settings.currency_exposure_limit)
            if exp:
                return exp, "EXPOSICION", open_syms
        if self.live and self.hfm["down_since"]:
            return f"cuenta {self.bk} sin conexión con MetaApi", "BLOQUEADA", open_syms
        if self.live and self.cfg and symbol in (self.cfg.hfm_symbols_bad or []):
            return f"par no operable en {self.bk} con el sufijo de símbolos configurado (Ajustes → {self.bl} → Comprobar símbolos)", "BLOQUEADA", open_syms
        return None, None, open_syms
