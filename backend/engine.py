import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from models import Settings, Position, Trade, Signal, LogEntry, now_iso
from market import MarketData, HTF
from indicators import build_df, trend_label, htf_trend
from strategies import evaluate, exit_signal, EXIT_ON_CROSS, EXIT_REASON, STRATEGY_INFO as FAST_INFO
from strategies_slow import SLOW_INFO, SLOW_KEYS, evaluate_slow
from risk import FEE_RATE, MAKER_FEE, SLIPPAGE, position_size, pnl, check_exit, update_trailing, trade_rules, move_in_r, tp_in_r, partial_qty
from ai_advisor import evaluate_signal, label as ai_label
from plan import SCENARIOS, build_checkin, contributions as plan_contributions
from notify import notify as tg
from bybit import BybitClient, BybitError
from live import LiveBroker
from shadow import open_shadow, monitor_shadows
import evolution
import funding
import cashflow
import regime
import maker
import broker_health
from broker_health import fmt_duration

STRATEGY_INFO = {**FAST_INFO, **SLOW_INFO}
BLOCK_BY = {"NOTICIAS": "noticias", "FUNDING": "funding", "BLOQUEADA": "riesgo", "REGIMEN": "régimen de BTC", "ROTACION": "rotación semanal"}
HORIZON_LABEL = {"long": "largo plazo", "short": "corto plazo"}

logger = logging.getLogger("engine")
INITIAL_BALANCE = 500.0


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TradingEngine:
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
        await self.log("INFO", f"Bot ACTIVADO · operando 24/7 en modo {'REAL en Bybit ' + self.env.upper() if self.live else 'paper trading'}")
        await tg(f"▶️ <b>Bot ACTIVADO</b> · modo {self.mode_label}" + (f" · Bybit {self.env}" if self.live else ""), "orders")
        self.scan_event.set()

    async def stop(self):
        self.running = False
        await self.db.settings.update_one({"_id": "main"}, {"$set": {"bot_running": False}})
        await self.log("INFO", "Bot DETENIDO · las posiciones abiertas siguen protegidas por SL/TP")
        await tg(f"⏹ <b>Bot DETENIDO</b> · modo {self.mode_label} · las posiciones abiertas siguen protegidas por SL/TP", "orders")
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
        await monitor_shadows(self, tickers, rules)
        await maker.monitor_pending(self, tickers)
        await self._update_account_metrics(settings)

    def _excursion(self, p: Position, price: float) -> dict:
        m = move_in_r(p.side, p.entry_price, p.initial_sl, price)
        return {"mfe_r": round(max(p.mfe_r, m), 3), "mae_r": round(max(p.mae_r, -m), 3)}

    async def _bybit_health(self, synced: bool):
        """REAL mode: one Telegram alert per Bybit outage (positions not answering for > bybit_alert_after_sec), reminder every 6 h, recovery notice."""
        event, dur = broker_health.transition(self.bybit, synced, self.exchange_error or "Bybit no responde", self.cfg.bybit_alert_after_sec)
        if event == "up":
            await self.log("INFO", f"Conexión con Bybit restablecida · estuvo sin respuesta {fmt_duration(dur)}")
            await tg(f"✅ <b>Conexión con Bybit restablecida</b> · estuvo sin respuesta {fmt_duration(dur)}. El bot vuelve a operar con normalidad.", "system")
        elif event:
            n = await self.db.positions.count_documents({"live": True})
            await self.log("ERROR", f"Bybit sin respuesta desde hace {fmt_duration(dur)} · {self.bybit['error']}")
            await tg(f"🔌 <b>Bybit {'sigue ' if event == 'remind' else ''}sin respuesta</b> · {fmt_duration(dur)} · {self.env}\n{self.bybit['error']}\n"
                     f"{n} posición(es) real(es) abiertas: siguen protegidas por su SL/TP en el exchange. El bot no abrirá nuevas operaciones hasta reconectar.\n"
                     "Si persiste, revisa el estado de Bybit y las credenciales en Ajustes → Bybit (Probar conexión).", "system")

    async def _manage_paper_position(self, p: Position, price: float, rules: dict):
        """One monitor tick for a PAPER position: exits, partial take-profit, trailing and MFE/MAE tracking."""
        updates = self._excursion(p, price)
        p.mfe_r, p.mae_r = updates["mfe_r"], updates["mae_r"]
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
        if rules["trailing_enabled"]:
            new_sl, be = update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, price, rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"])
            if new_sl != p.sl:
                updates.update(sl=new_sl, breakeven=be)
                if be and not p.breakeven:
                    await self.log("RISK", f"{p.symbol}: stop movido a breakeven ({new_sl:.6g})")
        await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})

    async def _announce_partial(self, p: Position, part: float, exit_price: float, net: float, rules: dict):
        tag = "REAL " if p.live else ""
        await self.log("ORDER", f"PARCIAL {tag}{p.side.upper()} {p.symbol}: cerrado {rules['partial_tp_pct']}% ({part:.6g}) @ {exit_price:.6g} a +{rules['partial_tp_r']:g}R · "
                                f"PnL {net:+.2f} USDT · resto {p.qty:.6g} sigue con trailing")
        await tg(f"🎯 <b>PARCIAL {tag}{p.side.upper()} {p.symbol}</b> @ {exit_price:.6g} (+{rules['partial_tp_r']:g}R)\n"
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
        if rules["trailing_enabled"]:
            new_sl, be = update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, price, rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"])
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
                      sl=p.sl, tp=p.tp, initial_sl=p.initial_sl, atr=p.atr, breakeven=p.breakeven, signal_id=p.signal_id, order_id=p.order_id)
        res = await self.db.trades.insert_one(trade.to_mongo())
        trade.id = str(res.inserted_id)
        return trade

    async def _announce_close(self, p: Position, trade: Trade, reason: str):
        tag = "REAL " if p.live else ""
        await self.log("ORDER", f"CERRADA {tag}{p.side.upper()} {p.symbol} @ {trade.exit_price:.6g} · {reason} · PnL {trade.pnl:+.2f} USDT")
        await tg(f"{'✅' if trade.pnl >= 0 else '❌'} <b>CERRADA {tag}{p.side.upper()} {p.symbol}</b> @ {trade.exit_price:.6g} · {reason}\n"
                 f"PnL <b>{trade.pnl:+.2f} USDT</b> ({trade.pnl_pct:+.2f}%) · entrada {p.entry_price:.6g}", "orders")

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
            await tg(f"📊 <b>Resumen diario</b>\nEquidad ${s['equity']:,.2f} ({s['total_return_pct']:+.2f}% total)\n"
                     f"PnL de ayer {s['daily_pnl']:+.2f} USDT ({s['daily_pnl_pct']:+.2f}%)\n"
                     f"{s['total_trades']} ops · acierto {s['win_rate']}% · PF {s['profit_factor'] or '—'} · DD máx {s['max_drawdown']}%\n"
                     f"{s['open_positions']} posiciones abiertas\n\n📋 {c['message']}", "daily")
            if c["ready_to_advance"] or c["warnings"]:
                await tg(f"📋 <b>Plan</b>: {c['message']}" + ("\n⚠️ " + "\n⚠️ ".join(c["warnings"]) if c["warnings"] else ""), "plan")
        elif not acc["daily_paused"] and s["daily_pnl_pct"] <= -settings.daily_loss_limit:
            upd["daily_paused"] = True
            await self.log("RISK", f"Límite de pérdida diaria ({settings.daily_loss_limit}%) alcanzado · nuevas entradas pausadas hasta mañana (UTC)")
            await tg(f"🛑 <b>Límite de pérdida diaria alcanzado</b> ({s['daily_pnl_pct']:+.2f}%). Nuevas entradas pausadas hasta mañana (UTC).", "orders")
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
        qty, margin = position_size(w["equity"], w["available"], risk_pct, entry, sl, leverage, lot=lot)
        if qty <= 0 or margin < 1:
            await self.log("RISK", f"{symbol}: tamaño por debajo del lote mínimo de Bybit ({lot[0]}) o margen insuficiente")
            self._open_fail = f"tamaño por debajo del lote mínimo de Bybit ({lot[0]}) o margen insuficiente"
            return None
        return {"qty": qty, "margin": margin}

    async def open_position(self, symbol: str, sig: dict, settings: Settings, signal_id: str, risk_factor: float = 1.0, risk_reason: str = "noticias",
                            horizon: str = "short", entry_type: str = "market", fill: dict = None):
        """`fill` = an already executed maker order ({entry, qty, fee, order_id, link_id} in REAL; {entry} in PAPER): no new order is sent."""
        risk_pct = settings.risk_per_trade * risk_factor
        async with self.lock:
            open_docs = await self._open_and_pending(exclude_symbol=symbol)
            slot = self._slot_block(open_docs, settings, horizon)
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
                        r = await self.broker.open(symbol, sig["side"], size["qty"], sl, tp, settings.leverage)
                    except Exception as e:
                        await self.log("ERROR", f"{symbol}: orden REAL rechazada por Bybit · {str(e)[:160]}")
                        await tg(f"⚠️ <b>Orden REAL rechazada · {symbol}</b>\n{str(e)[:300]}", "orders")
                        self._open_fail = f"orden REAL rechazada: {str(e)[:120]}"
                        return None
                entry, qty, fee = (r["entry"] or entry), r["qty"], r["fee"]
                margin = qty * entry / settings.leverage
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=qty, initial_qty=qty,
                               leverage=settings.leverage, margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"],
                               fees=fee, mark_price=price, signal_id=signal_id, live=True, order_id=r["order_id"], link_id=r["link_id"],
                               horizon=horizon, entry_type=entry_type)
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
                fee = entry * qty * fee_rate
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=qty, initial_qty=qty,
                               leverage=settings.leverage, margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"],
                               fees=fee, mark_price=price, signal_id=signal_id, horizon=horizon, entry_type=entry_type)
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
        await self.log("ORDER", f"ABIERTA {tag}{sig['side'].upper()} {symbol} @ {entry:.6g} · qty {qty:.6g} · {settings.leverage}x · SL {sl:.6g} · TP {tp:.6g}{reduced}{how}{lp}")
        await tg(f"{'🟢' if long else '🔴'} <b>ABIERTA {tag}{sig['side'].upper()} {symbol}</b> @ {entry:.6g}{lp}\n"
                 f"Cantidad {qty:.6g} · {settings.leverage}x · margen ${margin:,.2f}\nSL {sl:.6g} · TP {tp:.6g}\n"
                 f"Estrategia: {STRATEGY_INFO[sig['strategy']]['name']}{reduced}{how}", "orders")
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
            self.next_scan_ts = time.time() + settings.scan_interval_sec
            try:
                await asyncio.wait_for(self.scan_event.wait(), timeout=settings.scan_interval_sec)
            except asyncio.TimeoutError:
                pass
            self.scan_event.clear()

    async def scan_once(self, settings: Settings):
        self.scanning = True
        found = 0
        htf_tf = HTF[settings.timeframe]
        try:
            for symbol in settings.symbols:
                try:
                    params = settings.symbol_params.get(symbol) or {}
                    df = build_df(await self.market.fetch_klines(symbol, settings.timeframe, 300), int(params.get("atr_period", 14)))
                    i = len(df) - 2
                    row = df.iloc[i]
                    htf = None
                    if settings.htf_filter:
                        htf = htf_trend(build_df(await self.market.fetch_klines(symbol, htf_tf, 200, cache_sec=120)))
                    if self.running:
                        await self._exit_on_cross(symbol, df, i)
                    sig = evaluate(df, i, settings.strategy, htf, params, settings.signal_mode, settings.disabled_strategies)
                    self.analysis[symbol] = {
                        "trend": trend_label(row), "rsi": round(float(row["rsi"]), 1),
                        "macd_hist": float(row["macd_hist"]), "adx": round(float(row["adx"]), 1),
                        "htf": htf, "signal": sig["side"] if sig else None,
                        "signal_strategy": sig["strategy"] if sig else None, "updated_at": now_iso(),
                    }
                    if sig and self.running:
                        found += 1
                        await self._handle_signal(symbol, sig, settings, df, htf, htf_tf)
                except Exception as e:
                    logger.exception(f"scan {symbol}")
                    await self.log("ERROR", f"{symbol}: {e}")
            found += await self._scan_slow(settings)
            self.last_scan = now_iso()
            if self.running:
                await self.log("SCAN", f"Escaneo {settings.timeframe} completado · {len(settings.symbols)} pares · {found} señales")
        finally:
            self.scanning = False

    async def _exit_on_cross(self, symbol: str, df, i: int):
        """Strategy exits decided at candle close (ema_cross: the EMAs crossed back against the position)."""
        row = df.iloc[i]
        async for d in self.db.positions.find({"symbol": symbol, "strategy": {"$in": list(EXIT_ON_CROSS)}}, {"_id": 1, "side": 1, "strategy": 1}):
            if exit_signal(d["strategy"], d["side"], float(row["ema10"]), float(row["ema20"])):
                await self.log("INFO", f"{symbol}: la EMA10 volvió a cruzar la EMA20 en contra de la posición {d['side'].upper()} · cierre por regla de la estrategia")
                await self.close_position(str(d["_id"]), None, EXIT_REASON)

    def slow_active(self, settings: Settings) -> list:
        if settings.strategy != "all":
            return [settings.strategy] if settings.strategy in SLOW_KEYS else []
        return [k for k in SLOW_KEYS if k not in settings.disabled_strategies]

    async def _scan_slow(self, settings: Settings) -> int:
        """Long-horizon strategies run on their own timeframe (1d / 4h) with candles cached 10 min; one signal per closed candle."""
        found = 0
        for key in self.slow_active(settings):
            info = SLOW_INFO[key]
            for symbol in settings.symbols:
                try:
                    params = settings.symbol_params.get(symbol) or {}
                    rows = await self.market.fetch_history(symbol, info["tf"], info["bars"], cache_sec=600)
                    if len(rows) < 215:
                        continue
                    df = build_df(rows, int(params.get("atr_period", 14)))
                    sig = evaluate_slow(df, len(df) - 2, key, params)
                    if sig:
                        self.analysis.setdefault(symbol, {})["slow_signal"] = f"{key}:{sig['side']}"
                        if self.running:
                            found += 1
                            await self._handle_signal(symbol, sig, settings, df, None, info["tf"], horizon="long", tf=info["tf"])
                except Exception as e:
                    logger.exception(f"slow scan {symbol} {key}")
                    await self.log("ERROR", f"{symbol} ({info['name']}): {e}")
        return found

    def _slot_block(self, open_docs: list, settings: Settings, horizon: str):
        """Long-horizon positions have their own cap and do not count towards «Máx. posiciones». `open_docs` includes pending maker entries."""
        same = [p for p in open_docs if (p.get("horizon") or "short") == horizon]
        cap = settings.max_long_positions if horizon == "long" else settings.max_positions
        if len(same) >= cap:
            return f"máximo de posiciones de {HORIZON_LABEL[horizon]} alcanzado ({cap})"
        return None

    async def _open_and_pending(self, exclude_symbol: str = None) -> list:
        docs = await self.db.positions.find({}, {"symbol": 1, "horizon": 1}).to_list(100)
        q = {"symbol": {"$ne": exclude_symbol}} if exclude_symbol else {}
        docs += [{**d, "pending": True} for d in await self.db.pending_entries.find(q, {"symbol": 1, "horizon": 1}).to_list(100)]
        return docs

    async def _handle_signal(self, symbol, sig, settings: Settings, df, htf, htf_tf, horizon: str = "short", tf: str = None):
        key = f"{symbol}:{sig['strategy']}:{sig['side']}:{sig['key_ts']}"
        if key in self.signal_keys:
            return
        self.signal_keys.add(key)
        tf = tf or settings.timeframe
        name = STRATEGY_INFO[sig["strategy"]]["name"]
        await self.log("SIGNAL", f"{symbol} {sig['side'].upper()} · {name} · {sig['score']} confirmaciones" + (" · largo plazo" if horizon == "long" else ""))
        acc = await self.account_summary()
        open_docs = await self._open_and_pending()
        open_syms = [p["symbol"] for p in open_docs]
        block, kind = None, "BLOQUEADA"
        if symbol in open_syms:
            block = "ya existe una posición abierta (o una orden límite en espera) en el par"
        elif self._slot_block(open_docs, settings, horizon):
            block = self._slot_block(open_docs, settings, horizon)
        elif acc["daily_paused"]:
            block = "límite de pérdida diaria alcanzado"
        elif self.live and self.bybit["down_since"]:
            block = "Bybit sin respuesta · sin conexión con el exchange"
        # BTC regime and weekly rotation only gate short-horizon strategies; long-horizon ones carry their own daily filters
        regime_now = await regime.btc_regime(self.market, settings.btc_regime_sma) if settings.btc_regime_filter else None
        r_block = regime.regime_block(regime_now, sig["side"]) if regime_now and horizon == "short" else None
        if not block and r_block:
            block, kind = r_block, "REGIMEN"
        rotation = await regime.current_rotation(self, settings) if settings.rotation_enabled else None
        rot_block, rot_factor = regime.rotation_gate(rotation, symbol, sig["side"]) if rotation and horizon == "short" else (None, 1.0)
        if not block and rot_block:
            block, kind = rot_block, "ROTACION"
        news_block, risk_factor = self.news.gate(symbol) if self.news else (None, 1.0)
        if not block and news_block:
            block, kind = news_block, "NOTICIAS"
        t0 = self.market.tickers.get(symbol, {})
        funding_rate, funding_h = t0.get("funding_rate"), t0.get("funding_interval_h", 8)
        f_block, f_factor = funding.gate(funding_rate, sig["side"], self.cfg, funding_h)
        if not block and f_block:
            block, kind = f_block, "FUNDING"
        risk_reason = " y ".join(r for r, f in (("riesgo de noticias", risk_factor), ("funding alto en contra", f_factor), ("menor liquidez relativa del par", rot_factor)) if f < 1)
        risk_factor = min(risk_factor, f_factor) * rot_factor
        news_ctx = self.news.context(symbol) if self.news else None
        context = {"mode": "REAL" if self.live else "PAPER", "htf_tf": htf_tf, "htf_trend": {1: "ALCISTA", -1: "BAJISTA", 0: "NEUTRAL"}.get(htf, "N/D"),
                   "horizon": horizon, "signal_tf": tf, "btc_regime": regime_now, "rotation_bias": next((r["bias"] for r in (rotation or {}).get("ranking", []) if r["symbol"] == symbol), None),
                   "entry_order_type": settings.entry_order_type,
                   "price_now": t0.get("price"), "change_24h": t0.get("change_pct"), "funding_rate": funding_rate, "funding_interval_h": funding_h,
                   "funding_size_factor": f_factor, "news_size_factor": risk_factor, "open_positions": len(open_syms), "max_positions": settings.max_positions,
                   "daily_pnl_pct": acc["daily_pnl_pct"], "equity": acc["equity"], "news_risk": (news_ctx or {}).get("mercado", {}).get("risk") if news_ctx else None,
                   "news_pair": (news_ctx or {}).get("par") if news_ctx else None, "ai_enabled": settings.ai_enabled, "ai_min_confidence": settings.ai_min_confidence}
        if block:
            await self.log("RISK", f"{symbol}: señal descartada · {block}")
            res = await self.db.signals.insert_one(Signal(
                symbol=symbol, side=sig["side"], strategy=sig["strategy"], timeframe=tf,
                price=sig["price"], sl=sig["sl"], tp=sig["tp"], score=sig["score"], reasons=sig["reasons"],
                indicators=sig["indicators"], ai_decision=kind, note=f"Bloqueada por {BLOCK_BY.get(kind, 'riesgo')}: {block}", key=key, context=context,
            ).to_mongo())
            await open_shadow(self, str(res.inserted_id), symbol, sig, settings, kind, acc["equity"], risk_factor=risk_factor, horizon=horizon)
            return

        signal = Signal(symbol=symbol, side=sig["side"], strategy=sig["strategy"], timeframe=tf,
                        price=sig["price"], sl=sig["sl"], tp=sig["tp"], score=sig["score"], reasons=sig["reasons"],
                        indicators=sig["indicators"], ai_decision="SIN_IA", key=key, context=context)
        approve, shadow_kind = True, None
        if settings.ai_enabled:
            await self.log("AI_EVAL", f"{ai_label()} evaluando {symbol} {sig['side'].upper()}...")
            closes = df["close"].iloc[-22:-1]
            t = self.market.tickers.get(symbol, {})
            ctx = {
                "htf_tf": htf_tf, "htf_trend": {1: "ALCISTA", -1: "BAJISTA", 0: "NEUTRAL"}.get(htf, "N/D"),
                "horizon": horizon, "btc_regime": regime_now, "rotation_bias": context["rotation_bias"],
                "change_24h": t.get("change_pct", 0), "funding_rate": t.get("funding_rate", 0),
                "funding": funding.assess(funding_rate, sig["side"], self.cfg, funding_h),
                "recent_changes": [round(x, 3) for x in (closes.pct_change().dropna() * 100).tolist()],
                "open_positions": len(open_syms), "daily_pnl_pct": acc["daily_pnl_pct"],
                "news": self.news.context(symbol) if self.news else None,
            }
            try:
                res = await evaluate_signal(symbol, tf, sig, ctx)
                signal.ai_decision, signal.ai_confidence = res["decision"], res["confidence"]
                signal.ai_risk, signal.ai_reasoning = res["risk"], res["reasoning"]
                signal.ai_min_confidence = settings.ai_min_confidence
                approve = res["decision"] == "APROBADA" and res["confidence"] >= settings.ai_min_confidence  # risk is informative only
                if res["decision"] == "APROBADA" and not approve:
                    signal.note = f"Confianza {res['confidence']}% menor al mínimo ({settings.ai_min_confidence}%)"
                    shadow_kind = "BAJA_CONFIANZA"
                elif not approve:
                    shadow_kind = "RECHAZADA"
            except Exception as e:
                logger.exception("ai error")
                signal.ai_decision, signal.note, approve, shadow_kind = "ERROR", f"Error IA: {str(e)[:180]}", False, "ERROR"
            await self.log("AI_EVAL", f"{symbol}: {signal.ai_decision} · confianza {signal.ai_confidence}% · riesgo {signal.ai_risk}")
            await tg(f"🤖 <b>{ai_label()} · {symbol} {sig['side'].upper()}</b>: {signal.ai_decision} ({signal.ai_confidence}% · riesgo {signal.ai_risk})\n"
                     f"{signal.ai_reasoning[:300] or signal.note}", "ai")
        res = await self.db.signals.insert_one(signal.to_mongo())
        sid = str(res.inserted_id)
        if approve:
            self._open_fail = None
            if settings.entry_order_type == "maker":
                pos = await maker.queue_entry(self, symbol, sig, settings, sid, risk_factor, risk_reason, horizon, tf)
            else:
                pos = await self.open_position(symbol, sig, settings, sid, risk_factor, risk_reason, horizon=horizon)
            if not pos:
                reason = self._open_fail or "apertura cancelada"
                await self.db.signals.update_one({"_id": res.inserted_id}, {"$set": {"note": f"No ejecutada: {reason}"}})
                await open_shadow(self, sid, symbol, sig, settings, "NO_EJECUTADA", acc["equity"], signal.ai_confidence, signal.ai_risk, risk_factor, horizon)
        elif shadow_kind:
            await open_shadow(self, sid, symbol, sig, settings, shadow_kind, acc["equity"], signal.ai_confidence, signal.ai_risk, risk_factor, horizon)
