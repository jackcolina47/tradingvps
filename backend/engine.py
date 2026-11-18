import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from models import Settings, Position, Trade, LogEntry, now_iso
from market import MarketData, HTF
from indicators import build_df
from legacy import exit_signal, EXIT_ON_CROSS, EXIT_REASON, EMA_TEST, EMA_TEST_TAG, INFO as LEGACY_INFO
from risk import FEE_RATE, MAKER_FEE, SLIPPAGE, position_size, pnl, check_exit, update_trailing, trade_rules, move_in_r, tp_in_r, partial_qty, be_allowed, ema_partial_due, EMA_PARTIAL_PCT, stop_cost
import housekeeping
import position_review
import trade_learning
from plan import SCENARIOS, build_checkin, contributions as plan_contributions
import notify as _notify
from bybit import BybitClient
from live import LiveBroker
from shadow import open_shadow, monitor_shadows
import evolution
import cashflow
import maker
import broker_health
import trade_guard
import funnel
from broker_health import fmt_duration

STRATEGY_INFO = {**LEGACY_INFO, **funnel.INFO}
BLOCK_BY = {"NOTICIAS": "noticias", "FUNDING": "funding", "BLOQUEADA": "riesgo", "REGIMEN": "régimen de BTC", "ROTACION": "rotación semanal",
            "RR_BAJO": "R:R insuficiente", "SPREAD": "spread", "HORARIO": "horario operativo"}
HORIZON_LABEL = {"long": "largo plazo", "short": "corto plazo"}

logger = logging.getLogger("engine")
INITIAL_BALANCE = 500.0


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TradingEngine:
    bot_key, tg_prefix = "bybit", ""
    guard_kind = "crypto"
    usage_kind = "signal"

    async def tg(self, text: str, kind: str):
        return await _notify.notify(self.tg_prefix + text, kind, bot=self.bot_key)

    def _after_close(self, trade: Trade):
        """Automatic learning: every closed trade (except the EMA test, immune to the rules) is analysed in the background."""
        if trade.exit_mode != "cross" and trade.strategy != "manual" and trade.reason != "NOTICIA":
            asyncio.create_task(self._learn(trade))

    async def _learn(self, trade: Trade):
        if (await self.get_settings()).rules_auto_learn:
            await trade_learning.learn(self, trade)

    def __init__(self, db):
        self.db = db
        self.market = MarketData()
        self.lock = asyncio.Lock()
        self.running = False
        self.scanning = False
        self.last_scan = None
        self.next_scan_ts = None
        self.analysis = {}
        self.signal_keys = set()
        self.scan_event = asyncio.Event()
        self.tasks = []
        self._last_snapshot = 0.0
        self.live = False
        self.env = "mainnet"
        self.broker = None
        self._broker_key = None
        self.exchange_error = None
        self._open_fail = None
        self._external_warned = set()
        self.news = None
        self.cfg = None
        self._last_equity = None
        self._cash_checked = 0.0
        self.bybit = broker_health.fresh_health()
        self.funnel_said, self.funnel_last = {}, {}

    async def apply_config(self, cfg):
        self.cfg = cfg
        self.live = cfg.trading_mode == "live"
        self.env = cfg.bybit_env
        key = (cfg.bybit_api_key, cfg.bybit_api_secret, cfg.bybit_env, cfg.bybit_base_url)
        if key != self._broker_key:
            if self.broker:
                await self.broker.aclose()
            self.broker = LiveBroker(BybitClient(*key)) if cfg.bybit_api_key and cfg.bybit_api_secret else None
            self._broker_key = key
            self.exchange_error = None
            self.bybit = broker_health.fresh_health()
        if not self.live:
            self.bybit = broker_health.fresh_health()

    def broker_health(self) -> dict | None:
        if not (self.live and self.broker):
            return None
        return broker_health.summary(self.bybit, self.cfg.bybit_alert_after_sec if self.cfg else None)

    @property
    def mode_label(self) -> str:
        return "REAL" if self.live else "PAPER"

    async def init(self):
        await self.market.detect()
        if not await self.db.settings.find_one({"_id": "main"}):
            await self.db.settings.insert_one({"_id": "main", **Settings().model_dump()})
        if not await self.db.account.find_one({"_id": "main"}):
            await self._new_account()
        self.running = (await self.get_settings()).bot_running
        async for s in self.db.signals.find({}, {"key": 1}).sort("_id", -1).limit(500):
            if s.get("key"):
                self.signal_keys.add(s["key"])
        await self.log("INFO", f"Motor iniciado · fuente de datos {self.market.provider.upper()} · bot {'ACTIVO' if self.running else 'DETENIDO'}")
        self.tasks = [asyncio.create_task(self._monitor_loop()), asyncio.create_task(self._scan_loop())]

    async def shutdown(self):
        for t in self.tasks:
            t.cancel()
        await self.market.client.aclose()

    async def _new_account(self, initial: float = INITIAL_BALANCE):
        await self.db.account.replace_one({"_id": "main"}, {
            "_id": "main", "initial_balance": initial, "balance": initial,
            "peak_equity": initial, "max_drawdown": 0.0, "day": today(),
            "day_start_equity": initial, "daily_paused": False, "created_at": now_iso(), "net_deposits": 0.0,
        }, upsert=True)

    async def get_settings(self) -> Settings:
        return Settings.model_validate(await self.db.settings.find_one({"_id": "main"}))

    async def update_settings(self, patch: dict, source: str = "manual", summary: str = "", extra: dict = None) -> Settings:
        current = (await self.get_settings()).model_dump()
        before = dict(current)
        current.update({k: v for k, v in patch.items() if v is not None})
        s = Settings.model_validate(current)
        await self.db.settings.update_one({"_id": "main"}, {"$set": s.model_dump()})
        await self.log("INFO", "Configuración actualizada")
        self.scan_event.set()
        await evolution.record(self, source, evolution.diff(before, s.model_dump()), summary, extra)
        return s

    async def log(self, level: str, message: str):
        await self.db.logs.insert_one(LogEntry(level=level, message=message).to_mongo())
        logger.info(f"[{level}] {message}")

    async def start(self):
        self.running = True
        await self.db.settings.update_one({"_id": "main"}, {"$set": {"bot_running": True}})
        await self.log("INFO", f"Bot ACTIVADO · embudo IA dentro del horario operativo · modo {'REAL en Bybit ' + self.env.upper() if self.live else 'paper trading'}")
        await self.tg(f"▶️ <b>Bot ACTIVADO</b> · modo {self.mode_label}" + (f" · Bybit {self.env}" if self.live else ""), "orders")
        self.scan_event.set()

    async def stop(self):
        self.running = False
        await self.db.settings.update_one({"_id": "main"}, {"$set": {"bot_running": False}})
        await self.log("INFO", "Bot DETENIDO · las posiciones abiertas siguen protegidas por SL/TP")
        await self.tg(f"⏹ <b>Bot DETENIDO</b> · modo {self.mode_label} · las posiciones abiertas siguen protegidas por SL/TP", "orders")
        await maker.cancel_all(self, "bot detenido")

    async def reset_account(self, initial: float = None):
        initial = initial or INITIAL_BALANCE
        async with self.lock:
            for col in ("positions", "trades", "signals", "equity", "shadow_positions", "shadow_trades", "cash_flows", "pending_entries"):
                await self.db[col].delete_many({})
            await self._new_account(initial)
        await self.log("INFO", f"Estadísticas reiniciadas · capital inicial ${initial:,.2f} USDT · modo {self.mode_label}")

    async def _exchange_wallet(self):
        if not (self.live and self.broker):
            return None
        try:
            return await self.broker.wallet()  # cached 10 s: the monitor's positions sync owns clearing exchange_error
        except Exception as e:
            self.exchange_error = str(e)[:160]
            logger.warning(f"wallet: {e}")
            return None

    async def account_summary(self) -> dict:
        acc = await self.db.account.find_one({"_id": "main"})
        positions = await self.db.positions.find({}, {"unrealized_pnl": 1, "margin": 1}).to_list(100)
        unreal = sum(p["unrealized_pnl"] for p in positions)
        used = sum(p["margin"] for p in positions)
        balance, equity, available = acc["balance"], acc["balance"] + unreal, acc["balance"] - used
        w = await self._exchange_wallet()
        if w:
            balance, equity, available, unreal = w["wallet_balance"], w["equity"], w["available"], w["unrealised"]
        agg = await self.db.trades.aggregate([{"$group": {
            "_id": None, "n": {"$sum": 1}, "realized": {"$sum": "$pnl"},
            "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
            "gw": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
            "gl": {"$sum": {"$cond": [{"$lte": ["$pnl", 0]}, "$pnl", 0]}},
        }}]).to_list(1)
        st = agg[0] if agg else {"n": 0, "realized": 0, "wins": 0, "gw": 0, "gl": 0}
        daily = equity - acc["day_start_equity"]
        return {
            "initial_balance": acc["initial_balance"], "balance": round(balance, 2),
            "equity": round(equity, 2), "unrealized_pnl": round(unreal, 2),
            "unrealized_pct": round(unreal / balance * 100, 2) if balance else 0,
            "used_margin": round(used, 2), "available": round(available, 2),
            "daily_pnl": round(daily, 2), "daily_pnl_pct": round(daily / acc["day_start_equity"] * 100, 2) if acc["day_start_equity"] else 0,
            "daily_paused": acc["daily_paused"], "total_trades": st["n"], "wins": st["wins"],
            "win_rate": round(st["wins"] / st["n"] * 100, 1) if st["n"] else 0,
            "realized_pnl": round(st["realized"], 2),
            "profit_factor": round(st["gw"] / abs(st["gl"]), 2) if st["gl"] else None,
            **cashflow.fields(acc, equity),
            "max_drawdown": round(acc["max_drawdown"], 2),
            "open_positions": len(positions), "mode": self.mode_label, "exchange_error": self.exchange_error,
            "exchange_synced": bool(w),
        }

    async def checkin(self, settings: Settings = None, summary: dict = None) -> dict:
        settings = settings or await self.get_settings()
        s = summary or await self.account_summary()
        acc = await self.db.account.find_one({"_id": "main"})
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        agg = await self.db.trades.aggregate([{"$match": {"closed_at": {"$gte": since}}}, {"$group": {
            "_id": None, "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"},
            "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
            "gw": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
            "gl": {"$sum": {"$cond": [{"$lte": ["$pnl", 0]}, "$pnl", 0]}},
        }}]).to_list(1)
        a = agg[0] if agg else {"n": 0, "pnl": 0.0, "wins": 0, "gw": 0.0, "gl": 0.0}
        last30 = {"trades": a["n"], "pnl": round(a["pnl"], 2), "win_rate": round(a["wins"] / a["n"] * 100, 1) if a["n"] else 0,
                  "profit_factor": round(a["gw"] / abs(a["gl"]), 2) if a["gl"] else None,
                  "return_pct": round(a["pnl"] / (s["equity"] - a["pnl"]) * 100, 2) if s["equity"] - a["pnl"] > 0 else 0}
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(acc["created_at"])).days
        rate = SCENARIOS.get(settings.plan_scenario, SCENARIOS["base"])["rate"]
        contrib = await plan_contributions(self.db, acc, settings, rate)
        return build_checkin(s, acc, last30, settings, rate, days, contributions=contrib)

    async def _monitor_loop(self):
        while True:
            try:
                await self._monitor_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"monitor error: {e}")
            await asyncio.sleep(5)

    async def _monitor_once(self):
        tickers = await self.market.fetch_tickers()
        settings = await self.get_settings()
        ex = None
        if self.live and self.broker:
            try:
                ex = {x["symbol"]: x for x in await self.broker.positions()}
                self.exchange_error = None
            except Exception as e:
                self.exchange_error = str(e)[:160]
                logger.warning(f"positions sync: {e}")
            await self._bybit_health(ex is not None)
        local_syms = set()
        rules = trade_rules(settings)
        async for doc in self.db.positions.find():
            p = Position.from_mongo(doc)
            local_syms.add(p.symbol)
            if p.live:
                if ex is not None:
                    await self._sync_live_position(p, ex.get(p.symbol), tickers.get(p.symbol), settings, rules)
                continue
            t = tickers.get(p.symbol)
            if not t:
                continue
            await self._manage_paper_position(p, t["price"], rules)
        if ex:
            for sym in set(ex) - local_syms - self._external_warned:
                self._external_warned.add(sym)
                await self.log("RISK", f"{sym}: posición externa detectada en Bybit (no gestionada por el bot) · tamaño {ex[sym]['size']}")
        await trade_guard.protect_positions(self)
        hit = [d["symbol"] for d in await self.db.pending_entries.find({}, {"symbol": 1}).to_list(50) if trade_guard.calendar.blocking(self.guard_kind, d["symbol"])]
        if hit:
            await maker.cancel_all(self, f"evento rojo · bloqueo ±{trade_guard.BEFORE_MIN} min", hit)
        await monitor_shadows(self, tickers, rules)
        await maker.monitor_pending(self, tickers)
        await self._update_account_metrics(settings)
        if position_review.due(self, settings):
            await position_review.review_open_positions(self, settings, "USDT", "Bybit")
        elif shock := await position_review.btc_shock(self, settings):
            await position_review.review_open_positions(self, settings, "USDT", "Bybit", trigger=shock)
        await housekeeping.tick(self, settings)

    def _excursion(self, p: Position, price: float) -> dict:
        m = move_in_r(p.side, p.entry_price, p.initial_sl, price)
        return {"mfe_r": round(max(p.mfe_r, m), 3), "mae_r": round(max(p.mae_r, -m), 3)}

    async def _bybit_health(self, synced: bool):
        """REAL mode: one Telegram alert per Bybit outage (positions not answering for > bybit_alert_after_sec), reminder every 6 h, recovery notice."""
        event, dur = broker_health.transition(self.bybit, synced, self.exchange_error or "Bybit no responde", self.cfg.bybit_alert_after_sec)
        if event == "up":
            await self.log("INFO", f"Conexión con Bybit restablecida · estuvo sin respuesta {fmt_duration(dur)}")
            await self.tg(f"✅ <b>Conexión con Bybit restablecida</b> · estuvo sin respuesta {fmt_duration(dur)}. El bot vuelve a operar con normalidad.", "system")
        elif event:
            n = await self.db.positions.count_documents({"live": True})
            await self.log("ERROR", f"Bybit sin respuesta desde hace {fmt_duration(dur)} · {self.bybit['error']}")
            await self.tg(f"🔌 <b>Bybit {'sigue ' if event == 'remind' else ''}sin respuesta</b> · {fmt_duration(dur)} · {self.env}\n{self.bybit['error']}\n"
                     f"{n} posición(es) real(es) abiertas: siguen protegidas por su SL/TP en el exchange. El bot no abrirá nuevas operaciones hasta reconectar.\n"
                     "Si persiste, revisa el estado de Bybit y las credenciales en Ajustes → Bybit (Probar conexión).", "system")

    async def _manage_paper_position(self, p: Position, price: float, rules: dict):
        """One monitor tick for a PAPER position: exits, partial take-profit, trailing and MFE/MAE tracking."""
        updates = self._excursion(p, price)
        p.mfe_r, p.mae_r = updates["mfe_r"], updates["mae_r"]
        if p.exit_mode == "cross":  # Test cruce EMA: only the safety SL (no TP/partial/BE/trailing); the opposite cross is the normal exit
            if (price <= p.sl) if p.side == "long" else (price >= p.sl):
                await self.log("RISK", f"{p.symbol}: stop en breakeven del test cruce EMA alcanzado ({p.sl:.6g}) tras el cierre parcial" if p.breakeven else
                               f"{p.symbol}: SL de seguridad del test cruce EMA alcanzado ({p.sl:.6g}) · movimiento violento antes del cierre de vela")
                await self.close_position(p.id, p.sl, "BREAKEVEN" if p.breakeven else "SL")
                return
            updates.update(mark_price=price, unrealized_pnl=round(pnl(p.side, p.entry_price, price, p.qty), 4))
            if ema_partial_due(rules, p.side, p.entry_price, p.atr, price, p.partial_done):
                part = partial_qty(p.symbol, p.qty, EMA_PARTIAL_PCT)
                be = self._ema_be(p)
                net = 0.0
                if part > 0:
                    exit_price = price * (1 - SLIPPAGE) if p.side == "long" else price * (1 + SLIPPAGE)
                    fee = exit_price * part * FEE_RATE
                    gross = pnl(p.side, p.entry_price, exit_price, part)
                    net = gross - fee
                    await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": net}})
                    updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), fees=round(p.fees + fee, 6),
                                   partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + fee, 6), initial_qty=p.initial_qty or p.qty,
                                   unrealized_pnl=round(pnl(p.side, p.entry_price, price, p.qty - part), 4))
                    p.qty = updates["qty"]
                updates.update(partial_done=True, sl=be, breakeven=True)
                await self._announce_ema_partial(p, part, price, net, be, rules)
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            return
        hit, reason = check_exit(p.side, p.sl, p.tp, price, price)
        if hit is not None:
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            if reason == "SL" and p.breakeven:
                reason = "TRAILING"
            await self.close_position(p.id, price, reason)
            return
        updates.update(mark_price=price, unrealized_pnl=round(pnl(p.side, p.entry_price, price, p.qty), 4))
        if rules["partial_tp_enabled"] and not p.partial_done and p.mfe_r >= rules["partial_tp_r"]:
            part = partial_qty(p.symbol, p.qty, rules["partial_tp_pct"])
            if part > 0:
                exit_price = price * (1 - SLIPPAGE) if p.side == "long" else price * (1 + SLIPPAGE)
                fee = exit_price * part * FEE_RATE
                gross = pnl(p.side, p.entry_price, exit_price, part)
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - fee}})
                updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), fees=round(p.fees + fee, 6),
                               partial_done=True, partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + fee, 6), initial_qty=p.initial_qty or p.qty,
                               unrealized_pnl=round(pnl(p.side, p.entry_price, price, p.qty - part), 4))
                p.qty = updates["qty"]
                await self._announce_partial(p, part, exit_price, gross - fee, rules)
            else:
                updates["partial_done"] = True
                await self.log("RISK", f"{p.symbol}: cierre parcial omitido · el lote mínimo de Bybit no permite dividir la posición")
        if rules["trailing_enabled"] or rules["breakeven_enabled"]:
            new_sl, be = update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, price, rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"],
                                         be_enabled=be_allowed(rules, p.partial_done or updates.get("partial_done", False)), trail_enabled=rules["trailing_enabled"])
            if new_sl != p.sl:
                updates.update(sl=new_sl, breakeven=be)
                if be and not p.breakeven:
                    await self.log("RISK", f"{p.symbol}: stop movido a breakeven ({new_sl:.6g})")
        await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})

    @staticmethod
    def _ema_be(p: Position) -> float:
        return p.entry_price * (1 + 2 * FEE_RATE) if p.side == "long" else p.entry_price * (1 - 2 * FEE_RATE)

    async def news_protect(self, p: Position, event: dict) -> bool:
        """Red event in ≤15 min: in profit → stop to breakeven; in loss → close now. Returns True once the position is handled."""
        long = p.side == "long"
        be = self._ema_be(p)
        label = trade_guard.event_label(event)
        if (p.sl >= be) if long else (p.sl <= be):
            return True
        price = self.market.tickers.get(p.symbol, {}).get("price") or p.mark_price
        if (price > be) if long else (price < be):
            if p.live:
                try:
                    await self.broker.set_stop(p.symbol, be)
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: no se pudo mover el stop a breakeven antes del evento rojo · {str(e)[:120]}")
                    return False
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": {"sl": be, "breakeven": True}})
            await self.log("RISK", f"{p.symbol}: evento rojo en menos de {trade_guard.BEFORE_MIN} min ({label}) · posición en ganancia → stop a breakeven ({be:.6g})")
            await self.tg(f"🛡 <b>{p.symbol} {p.side.upper()} a breakeven</b> ({be:.6g})\nEvento rojo: {label}", "orders")
            return True
        await self.log("RISK", f"{p.symbol}: evento rojo en menos de {trade_guard.BEFORE_MIN} min ({label}) · posición en pérdida → cierre inmediato")
        return await self.close_position(p.id, None, "NOTICIA") is not None

    async def _announce_ema_partial(self, p: Position, part: float, price: float, net: float, be: float, rules: dict):
        tag = "REAL " if p.live else ""
        k = rules["ema_test_partial_atr"]
        if part <= 0:
            await self.log("RISK", f"{p.symbol}: test cruce EMA a +{k:g}×ATR · cierre parcial omitido (el lote mínimo de Bybit no permite dividir) · SL movido a breakeven ({be:.6g})")
            return
        await self.log("ORDER", f"PARCIAL {tag}{p.side.upper()} {p.symbol} · {EMA_TEST_TAG}: cerrado {EMA_PARTIAL_PCT}% ({part:.6g}) @ {price:.6g} a +{k:g}×ATR · "
                                f"PnL {net:+.2f} USDT · resto {p.qty:.6g} con SL en breakeven ({be:.6g}) hasta el cruce contrario")
        await self.tg(f"🎯 <b>PARCIAL {tag}{p.side.upper()} {p.symbol}</b> · {EMA_TEST_TAG} @ {price:.6g} (+{k:g}×ATR)\n"
                 f"Cerrado {EMA_PARTIAL_PCT}% · PnL {net:+.2f} USDT · resto {p.qty:.6g} con SL en breakeven ({be:.6g}) hasta el cruce contrario", "orders")

    async def _ema_partial_live(self, p: Position, price: float, rules: dict, updates: dict):
        done = p.partial_done
        if ema_partial_due(rules, p.side, p.entry_price, p.atr, price, done):
            part = partial_qty(p.symbol, p.qty, EMA_PARTIAL_PCT)
            if part > 0:
                try:
                    r = await self.broker.close(p.symbol, p.side, part)
                    exit_price = r["exit"] or price
                    gross = pnl(p.side, p.entry_price, exit_price, part)
                    updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), fees=round(p.fees + r["fee"], 6),
                                   partial_done=True, partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + r["fee"], 6), initial_qty=p.initial_qty or p.qty)
                    p.qty = updates["qty"]
                    done = True
                    await self._announce_ema_partial(p, part, exit_price, gross - r["fee"], self._ema_be(p), rules)
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: cierre parcial REAL del test cruce EMA fallido · {str(e)[:120]}")
            else:
                done = updates["partial_done"] = True
                await self._announce_ema_partial(p, 0, price, 0.0, self._ema_be(p), rules)
        if done and not p.breakeven:
            be = self._ema_be(p)
            try:
                await self.broker.set_stop(p.symbol, be)
                updates.update(sl=be, breakeven=True)
                await self.log("RISK", f"{p.symbol}: stop REAL del test cruce EMA movido a breakeven ({be:.6g})")
            except Exception as e:
                await self.log("ERROR", f"{p.symbol}: no se pudo mover el stop a breakeven en Bybit · {str(e)[:120]}")

    async def _announce_partial(self, p: Position, part: float, exit_price: float, net: float, rules: dict):
        tag = "REAL " if p.live else ""
        await self.log("ORDER", f"PARCIAL {tag}{p.side.upper()} {p.symbol}: cerrado {rules['partial_tp_pct']}% ({part:.6g}) @ {exit_price:.6g} a +{rules['partial_tp_r']:g}R · "
                                f"PnL {net:+.2f} USDT · resto {p.qty:.6g} sigue con trailing")
        await self.tg(f"🎯 <b>PARCIAL {tag}{p.side.upper()} {p.symbol}</b> @ {exit_price:.6g} (+{rules['partial_tp_r']:g}R)\n"
                 f"Cerrado {rules['partial_tp_pct']}% · PnL {net:+.2f} USDT · resto {p.qty:.6g} con trailing", "orders")

    async def _sync_live_position(self, p: Position, ex: dict, ticker: dict, settings: Settings, rules: dict = None):
        rules = rules or trade_rules(settings)
        if not ex:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(p.opened_at)).total_seconds()
            if age < 20:
                return
            try:
                fresh = await self.broker.client.positions(p.symbol)
            except Exception as e:
                logger.warning(f"recheck {p.symbol}: {e}")
                return
            if fresh:
                ex = fresh[0]
            else:
                await self._close_live_from_exchange(p)
                return
        price = ex["mark"] or (ticker or {}).get("price") or p.mark_price
        updates = {"mark_price": price, "unrealized_pnl": round(ex["unrealised"], 4), **self._excursion(p, price)}
        p.mfe_r, p.mae_r = updates["mfe_r"], updates["mae_r"]
        if abs(ex["size"] - p.qty) > 1e-9:
            updates["qty"] = ex["size"]
        if p.exit_mode == "cross":  # Test cruce EMA: only the optional partial + breakeven rule on the exchange
            await self._ema_partial_live(p, price, rules, updates)
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            return
        if rules["partial_tp_enabled"] and not p.partial_done and p.mfe_r >= rules["partial_tp_r"]:
            part = partial_qty(p.symbol, p.qty, rules["partial_tp_pct"])
            if part > 0:
                try:
                    r = await self.broker.close(p.symbol, p.side, part)
                    exit_price = r["exit"] or price
                    gross = pnl(p.side, p.entry_price, exit_price, part)
                    updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), fees=round(p.fees + r["fee"], 6),
                                   partial_done=True, partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + r["fee"], 6), initial_qty=p.initial_qty or p.qty)
                    p.qty = updates["qty"]
                    await self._announce_partial(p, part, exit_price, gross - r["fee"], rules)
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: cierre parcial REAL fallido · {str(e)[:120]}")
            else:
                updates["partial_done"] = True
                await self.log("RISK", f"{p.symbol}: cierre parcial omitido · el lote mínimo de Bybit no permite dividir la posición")
        if rules["trailing_enabled"] or rules["breakeven_enabled"]:
            new_sl, be = update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, price, rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"],
                                         be_enabled=be_allowed(rules, p.partial_done or updates.get("partial_done", False)), trail_enabled=rules["trailing_enabled"])
            if new_sl != p.sl and abs(new_sl - p.sl) / p.sl > 0.0005:
                try:
                    await self.broker.set_stop(p.symbol, new_sl)
                    updates.update(sl=new_sl, breakeven=be)
                    await self.log("RISK", f"{p.symbol}: stop REAL movido a {new_sl:.6g}{' (breakeven)' if be and not p.breakeven else ''}")
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: no se pudo mover el stop en Bybit · {str(e)[:120]}")
        await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})

    async def _close_live_from_exchange(self, p: Position):
        async with self.lock:
            if not await self.db.positions.find_one({"_id": ObjectId(p.id)}):
                return
            cr = None
            try:
                cr = await self.broker.closed_result(p.symbol, p.opened_at, p.side, p.qty)
            except Exception as e:
                logger.warning(f"closed_pnl {p.symbol}: {e}")
            price = (cr or {}).get("exit") or p.mark_price
            fees = ((cr or {}).get("fee") or p.fees) + (p.partial_fees if cr else 0.0)
            net = (cr["pnl"] + p.partial_gross - p.partial_fees) if cr else pnl(p.side, p.entry_price, price, p.qty) + p.partial_gross - p.fees
            reason = "TP" if abs(price - p.tp) <= abs(price - p.sl) else ("TRAILING" if p.breakeven else "SL")
            if p.exit_mode == "cross":
                reason = "BREAKEVEN" if p.breakeven else "SL"
            await self.db.positions.delete_one({"_id": ObjectId(p.id)})
            trade = await self._record_trade(p, price, net, fees, reason)
        await self._announce_close(p, trade, reason)

    async def _record_trade(self, p: Position, price: float, net: float, fees: float, reason: str) -> Trade:
        tp_r = tp_in_r(p.side, p.entry_price, p.initial_sl, p.tp)
        mfe = max(p.mfe_r, move_in_r(p.side, p.entry_price, p.initial_sl, price))
        hold = (datetime.now(timezone.utc) - datetime.fromisoformat(p.opened_at)).total_seconds() / 60
        margin = p.margin * (p.initial_qty / p.qty) if p.initial_qty and p.qty else p.margin
        trade = Trade(symbol=p.symbol, side=p.side, strategy=p.strategy, entry_price=p.entry_price, exit_price=price,
                      qty=p.initial_qty or p.qty, leverage=p.leverage, margin=round(margin, 6), pnl=round(net, 4),
                      pnl_pct=round(net / margin * 100, 2) if margin else 0, fees=round(fees, 4),
                      reason=reason, opened_at=p.opened_at, live=p.live, horizon=p.horizon, entry_type=p.entry_type,
                      mfe_r=round(mfe, 2), mae_r=round(p.mae_r, 2), mfe_tp_pct=round(mfe / tp_r * 100, 1) if tp_r else 0.0,
                      partial_tp=p.partial_done and bool(p.partial_gross), hold_min=round(hold, 1),
                      sl=p.sl, tp=p.tp, initial_sl=p.initial_sl, atr=p.atr, breakeven=p.breakeven, signal_id=p.signal_id, order_id=p.order_id,
                      exit_mode=p.exit_mode, tags=[EMA_TEST_TAG] if p.exit_mode == "cross" else [])
        res = await self.db.trades.insert_one(trade.to_mongo())
        trade.id = str(res.inserted_id)
        return trade

    async def _announce_close(self, p: Position, trade: Trade, reason: str):
        tag = "REAL " if p.live else ""
        await self.log("ORDER", f"CERRADA {tag}{p.side.upper()} {p.symbol} @ {trade.exit_price:.6g} · {reason} · PnL {trade.pnl:+.2f} USDT")
        await self.tg(f"{'✅' if trade.pnl >= 0 else '❌'} <b>CERRADA {tag}{p.side.upper()} {p.symbol}</b> @ {trade.exit_price:.6g} · {reason}\n"
                 f"PnL <b>{trade.pnl:+.2f} USDT</b> ({trade.pnl_pct:+.2f}%) · entrada {p.entry_price:.6g}", "orders")
        self._after_close(trade)

    async def _update_account_metrics(self, settings: Settings):
        s = await self.account_summary()
        if self.live and self.broker and not self.bybit["down_since"] and await cashflow.poll(self, "bybit", force=cashflow.jumped(self._last_equity, s["equity"])):
            s = await self.account_summary()
        self._last_equity = s["equity"]
        acc = await self.db.account.find_one({"_id": "main"})
        nd = float(acc.get("net_deposits") or 0.0)
        upd = {}
        if acc["day"] != today():
            upd.update(day=today(), day_start_equity=s["equity"], daily_paused=False)
            await self.log("INFO", f"Nuevo día de trading · equidad inicial ${s['equity']:,.2f}")
            c = await self.checkin(settings, s)
            await self.log("PLAN", c["message"] + (" · ".join([""] + c["warnings"]) if c["warnings"] else ""))
            await self.tg(f"📊 <b>Resumen diario</b>\nEquidad ${s['equity']:,.2f} ({s['total_return_pct']:+.2f}% total)\n"
                     f"PnL de ayer {s['daily_pnl']:+.2f} USDT ({s['daily_pnl_pct']:+.2f}%)\n"
                     f"{s['total_trades']} ops · acierto {s['win_rate']}% · PF {s['profit_factor'] or '—'} · DD máx {s['max_drawdown']}%\n"
                     f"{s['open_positions']} posiciones abiertas\n\n📋 {c['message']}", "daily")
            if c["ready_to_advance"] or c["warnings"]:
                await self.tg(f"📋 <b>Plan</b>: {c['message']}" + ("\n⚠️ " + "\n⚠️ ".join(c["warnings"]) if c["warnings"] else ""), "plan")
        elif not acc["daily_paused"] and s["daily_pnl_pct"] <= -settings.daily_loss_limit:
            upd["daily_paused"] = True
            await self.log("RISK", f"Límite de pérdida diaria ({settings.daily_loss_limit}%) alcanzado · nuevas entradas pausadas hasta mañana (UTC)")
            await self.tg(f"🛑 <b>Límite de pérdida diaria alcanzado</b> ({s['daily_pnl_pct']:+.2f}%). Nuevas entradas pausadas hasta mañana (UTC).", "orders")
        adj = s["equity"] - nd
        peak = max(acc["peak_equity"], adj)
        dd = (peak - adj) / peak * 100 if peak > 0 else 0
        upd.update(peak_equity=peak, max_drawdown=max(acc["max_drawdown"], dd))
        await self.db.account.update_one({"_id": "main"}, {"$set": upd})
        if time.time() - self._last_snapshot >= 60:
            self._last_snapshot = time.time()
            await self.db.equity.insert_one({"ts": now_iso(), "equity": s["equity"], "balance": s["balance"], "net_deposits": round(nd, 2)})

    async def live_size(self, symbol: str, entry: float, sl: float, risk_pct: float, leverage: int):
        """Order size on the real wallet; None (and _open_fail set) when below Bybit's minimum lot or margin."""
        w = await self.broker.wallet(max_age=0)
        lot = await self.broker.lot(symbol)
        inst = await self.broker.client.instrument(symbol)
        qty, margin = position_size(w["equity"], w["available"], risk_pct, entry, sl, leverage, lot=lot, min_notional=float(inst.get("min_notional") or 5))
        if qty <= 0 or margin < 1:
            await self.log("RISK", f"{symbol}: tamaño por debajo del lote mínimo de Bybit ({lot[0]}) o margen insuficiente")
            self._open_fail = f"tamaño por debajo del lote mínimo de Bybit ({lot[0]}) o margen insuficiente"
            return None
        return {"qty": qty, "margin": margin, "equity": w["equity"]}

    async def _log_sizing(self, symbol, sig, qty, equity, risk_pct, entry, sl, tp):
        k = sig.get("atr_levels")
        loss = (abs(entry - sl) + stop_cost(entry, sl)) * qty
        real = loss / equity * 100 if equity else 0
        capped = " · REDUCIDO por margen/apalancamiento o lote" if real < risk_pct * 0.9 else ""
        await self.log("RISK", f"{symbol}: tamaño {qty:.6g} · pérdida en el SL {loss:.2f} USDT = {real:.2f}% de la equidad {equity:.2f} (objetivo {risk_pct:g}%, comisiones incluidas){capped} · "
                               f"ATR {sig['atr']:.6g}" + (f" · SL {k[0]:g}×ATR · TP {k[1]:g}×ATR" if k else "") + f" · entrada {entry:.6g} · SL {sl:.6g} · TP {tp:.6g}")

    async def open_position(self, symbol: str, sig: dict, settings: Settings, signal_id: str, risk_factor: float = 1.0, risk_reason: str = "noticias",
                            horizon: str = "short", entry_type: str = "market", fill: dict = None, exit_mode: str = None):
        """`fill` = an already executed maker order ({entry, qty, fee, order_id, link_id} in REAL; {entry} in PAPER): no new order is sent.
        `exit_mode="cross"` (Test cruce EMA): the SL/TP are virtual (sizing only), nothing is sent to Bybit and the position doesn't use a slot."""
        risk_pct = settings.risk_per_trade * risk_factor
        cross = exit_mode == "cross"
        async with self.lock:
            open_docs = await self._open_and_pending(exclude_symbol=symbol)
            slot = None if cross else self._slot_block(open_docs, settings, horizon)
            # a REAL maker fill is already a live position: it must be recorded even if the cap was reached meanwhile
            if symbol in [p["symbol"] for p in open_docs if not p.get("pending")] or (slot and not (fill and self.live)):
                await self.log("RISK", f"{symbol}: apertura cancelada · {slot or 'par ya abierto'}")
                self._open_fail = slot or "par ya abierto"
                return None
            price = self.market.tickers.get(symbol, {}).get("price") or sig["price"]
            long = sig["side"] == "long"
            maker_fill = bool(fill) and entry_type == "maker"
            entry = fill["entry"] if maker_fill else (price * (1 + SLIPPAGE) if long else price * (1 - SLIPPAGE))
            sl = entry - (sig["price"] - sig["sl"])
            tp = entry + (sig["tp"] - sig["price"])
            fee_rate = MAKER_FEE if maker_fill else FEE_RATE
            if self.live:
                if not self.broker:
                    await self.log("ERROR", f"{symbol}: modo REAL sin credenciales de Bybit · orden no enviada")
                    self._open_fail = "modo REAL sin credenciales de Bybit"
                    return None
                if fill and fill.get("qty"):
                    r = {"entry": fill["entry"], "qty": fill["qty"], "fee": fill.get("fee") or 0.0, "order_id": fill.get("order_id"), "link_id": fill.get("link_id"), "filled": True}
                else:
                    if self.bybit["down_since"]:
                        await self.log("RISK", f"{symbol}: orden no enviada · Bybit sin respuesta ({self.bybit['error']})")
                        self._open_fail = "Bybit sin respuesta"
                        return None
                    try:
                        size = await self.live_size(symbol, entry, sl, risk_pct, settings.leverage)
                        if not size:
                            return None
                        await self._log_sizing(symbol, sig, size["qty"], size["equity"], risk_pct, entry, sl, tp)
                        r = await self.broker.open(symbol, sig["side"], size["qty"], sl, None if cross else tp, settings.leverage)
                    except Exception as e:
                        await self.log("ERROR", f"{symbol}: orden REAL rechazada por Bybit · {str(e)[:160]}")
                        await self.tg(f"⚠️ <b>Orden REAL rechazada · {symbol}</b>\n{str(e)[:300]}", "orders")
                        self._open_fail = f"orden REAL rechazada: {str(e)[:120]}"
                        return None
                entry, qty, fee = (r["entry"] or entry), r["qty"], r["fee"]
                margin = qty * entry / settings.leverage
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=qty, initial_qty=qty,
                               leverage=settings.leverage, margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"],
                               fees=fee, mark_price=price, signal_id=signal_id, live=True, order_id=r["order_id"], link_id=r["link_id"],
                               horizon=horizon, entry_type=entry_type, exit_mode=exit_mode)
                res = await self.db.positions.insert_one(pos.to_mongo())
                pos.id = str(res.inserted_id)
                if not r["filled"]:
                    await self.log("RISK", f"{symbol}: orden enviada (id {r['order_id']}) pero sin confirmación de llenado · se sincronizará con Bybit")
            else:
                s = await self.account_summary()
                qty, margin = position_size(s["equity"], s["available"], risk_pct, entry, sl, settings.leverage, symbol)
                if qty <= 0 or margin < 1:
                    await self.log("RISK", f"{symbol}: tamaño por debajo del lote mínimo de Bybit o margen insuficiente · aumenta capital o riesgo")
                    self._open_fail = "tamaño por debajo del lote mínimo de Bybit o margen insuficiente"
                    return None
                await self._log_sizing(symbol, sig, qty, s["equity"], risk_pct, entry, sl, tp)
                fee = entry * qty * fee_rate
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=qty, initial_qty=qty,
                               leverage=settings.leverage, margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"],
                               fees=fee, mark_price=price, signal_id=signal_id, horizon=horizon, entry_type=entry_type, exit_mode=exit_mode)
                res = await self.db.positions.insert_one(pos.to_mongo())
                pos.id = str(res.inserted_id)
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": -fee}})
        if signal_id:
            try:
                await self.db.signals.update_one({"_id": ObjectId(signal_id)}, {"$set": {"executed": True}})
            except Exception:
                pass
        tag = "REAL " if self.live else ""
        reduced = f" · tamaño reducido al {int(risk_factor * 100)}% por {risk_reason}" if risk_factor < 1 else ""
        how = {"maker": " · entrada límite maker (comisión 0,02 %)", "market_fallback": " · a mercado tras no llenarse la orden límite"}.get(entry_type, "")
        lp = " · LARGO PLAZO" if horizon == "long" else ""
        levels = f"SL de seguridad {sl:.6g} · sin TP · cierre en el cruce contrario · ⚠️ {EMA_TEST_TAG}" if cross else f"SL {sl:.6g} · TP {tp:.6g}"
        await self.log("ORDER", f"ABIERTA {tag}{sig['side'].upper()} {symbol} @ {entry:.6g} · qty {qty:.6g} · {settings.leverage}x · {levels}{reduced}{how}{lp}")
        await self.tg(f"{'🟢' if long else '🔴'} <b>ABIERTA {tag}{sig['side'].upper()} {symbol}</b> @ {entry:.6g}{lp}\n"
                 f"Cantidad {qty:.6g} · {settings.leverage}x · margen ${margin:,.2f}\n{levels}\n"
                 f"Estrategia: {STRATEGY_INFO.get(sig['strategy'], {}).get('name', sig['strategy'])}{reduced}{how}", "orders")
        return pos

    async def close_position(self, pos_id: str, price: float = None, reason: str = "MANUAL"):
        async with self.lock:
            doc = await self.db.positions.find_one({"_id": ObjectId(pos_id)})
            if not doc:
                return None
            p = Position.from_mongo(doc)
            if p.live:
                if not self.broker:
                    await self.log("ERROR", f"{p.symbol}: cierre REAL imposible · sin credenciales de Bybit")
                    return None
                try:
                    r = await self.broker.close(p.symbol, p.side, p.qty)
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: cierre REAL fallido · {str(e)[:160]}")
                    return None
                await asyncio.sleep(0.8)
                cr = None
                try:
                    cr = await self.broker.closed_result(p.symbol, p.opened_at, p.side, p.qty)
                except Exception as e:
                    logger.warning(f"closed_pnl {p.symbol}: {e}")
                price = (cr or {}).get("exit") or r["exit"] or self.market.tickers.get(p.symbol, {}).get("price") or p.mark_price
                fees = ((cr or {}).get("fee") or (p.fees + r["fee"])) + (p.partial_fees if cr else 0.0)
                net = (cr["pnl"] + p.partial_gross - p.partial_fees) if cr else pnl(p.side, p.entry_price, price, p.qty) + p.partial_gross - p.fees - r["fee"]
                await self.db.positions.delete_one({"_id": ObjectId(pos_id)})
            else:
                await self.db.positions.delete_one({"_id": ObjectId(pos_id)})
                price = price or self.market.tickers.get(p.symbol, {}).get("price") or p.mark_price
                if reason != "TP":
                    price = price * (1 - SLIPPAGE) if p.side == "long" else price * (1 + SLIPPAGE)
                exit_fee = price * p.qty * FEE_RATE
                gross = pnl(p.side, p.entry_price, price, p.qty)
                net = gross + p.partial_gross - p.fees - exit_fee
                fees = p.fees + exit_fee
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - exit_fee}})
            trade = await self._record_trade(p, price, net, fees, reason)
        await self._announce_close(p, trade, reason)
        return trade

    async def _scan_loop(self):
        await asyncio.sleep(2)
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

    async def scan_once(self, settings: Settings):
        """One AI-funnel cycle (see funnel.py). Legacy positions of the old EMA-cross strategies keep their exit on the opposite cross."""
        self.scanning = True
        try:
            if self.running:
                for symbol in {d["symbol"] for d in await self.db.positions.find({"strategy": {"$in": list(EXIT_ON_CROSS)}}, {"symbol": 1}).to_list(100)}:
                    df = build_df(await self.market.fetch_klines(symbol, settings.timeframe, 300))
                    await self._exit_on_cross(symbol, df, len(df) - 2)
            await funnel.run(self, settings)
            self.last_scan = now_iso()
        finally:
            self.scanning = False

    # ---------- AI funnel adapter ----------
    async def funnel_frame(self, symbol: str, tf: str, limit: int = 300):
        return build_df(await self.market.fetch_klines(symbol, tf, limit, cache_sec=30)), True

    funnel_mtf = ("4h", "1h")   # 4H structure + 1H confirmation before the entry timeframe

    @staticmethod
    def funnel_htf(tf: str) -> str:
        return HTF[tf]

    @staticmethod
    def funnel_digits(symbol: str):
        return None

    def funnel_analysis(self, symbol: str, m: dict) -> dict:
        return {"htf": None}

    def funnel_ctx(self, symbol: str) -> dict:
        t = self.market.tickers.get(symbol, {})
        return {"modo": self.mode_label, "cambio_24h_pct": t.get("change_pct"), "funding_rate": t.get("funding_rate"), "funding_cada_h": t.get("funding_interval_h", 8)}

    async def funnel_state(self, settings: Settings) -> dict:
        acc = await self.account_summary()
        docs = await self._open_and_pending()
        used = len([p for p in docs if (p.get("horizon") or "short") == "short" and p.get("exit_mode") != "cross"])
        block = ("límite de pérdida diaria alcanzado" if acc["daily_paused"] else "Bybit sin respuesta · sin conexión con el exchange" if self.live and self.bybit["down_since"]
                 else f"máximo de posiciones alcanzado ({settings.max_positions})" if used >= settings.max_positions else None)
        return {"acc": acc, "open": {p["symbol"] for p in docs}, "free": settings.max_positions - used, "block": block}

    async def funnel_open(self, symbol: str, sig: dict, settings: Settings, signal_id: str, acc: dict):
        self._open_fail = None
        if settings.entry_order_type == "maker":
            pos = await maker.queue_entry(self, symbol, sig, settings, signal_id, 1.0, "", "short", settings.timeframe)
        else:
            pos = await self.open_position(symbol, sig, settings, signal_id)
        return bool(pos), self._open_fail or "apertura cancelada"

    async def funnel_shadow(self, signal_id: str, symbol: str, sig: dict, settings: Settings, kind: str, acc: dict, confidence: int):
        await open_shadow(self, signal_id, symbol, sig, settings, kind, acc["equity"], ai_confidence=confidence)

    async def _exit_on_cross(self, symbol: str, df, i: int):
        """Strategy exits decided at candle close (ema_cross / ema_test: the EMAs crossed back against the position)."""
        row = df.iloc[i]
        async for d in self.db.positions.find({"symbol": symbol, "strategy": {"$in": list(EXIT_ON_CROSS)}}, {"_id": 1, "side": 1, "strategy": 1}):
            if exit_signal(d["strategy"], d["side"], float(row["ema10"]), float(row["ema20"])):
                await self.log("INFO", f"{symbol}: la EMA10 volvió a cruzar la EMA20 en contra de la posición {d['side'].upper()} · cierre por regla de la estrategia"
                               + (" (test cruce EMA: se invierte si el test sigue activo)" if d["strategy"] == EMA_TEST else ""))
                await self.close_position(str(d["_id"]), None, EXIT_REASON)

    def _slot_block(self, open_docs: list, settings: Settings, horizon: str):
        """Long-horizon positions have their own cap and do not count towards «Máx. posiciones». `open_docs` includes pending maker entries."""
        same = [p for p in open_docs if (p.get("horizon") or "short") == horizon and p.get("exit_mode") != "cross"]  # test positions use no slot
        cap = settings.max_long_positions if horizon == "long" else settings.max_positions
        if len(same) >= cap:
            return f"máximo de posiciones de {HORIZON_LABEL[horizon]} alcanzado ({cap})"
        return None

    async def _open_and_pending(self, exclude_symbol: str = None) -> list:
        docs = await self.db.positions.find({}, {"symbol": 1, "horizon": 1, "exit_mode": 1}).to_list(100)
        q = {"symbol": {"$ne": exclude_symbol}} if exclude_symbol else {}
        docs += [{**d, "pending": True} for d in await self.db.pending_entries.find(q, {"symbol": 1, "horizon": 1}).to_list(100)]
        return docs
