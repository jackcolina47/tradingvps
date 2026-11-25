"""FxEngine mixin · positions: paper tick (SL/TP, time stop, partial, trailing), REAL sync with HFM, adoption of orphan broker positions,
open/close with sizing and all the puente MT failure paths."""
import time
import asyncio
import trade_learning
import maintenance
from datetime import datetime, timezone
from bson import ObjectId
from models import Position, Trade, now_iso
from fx.models import FxSettings
from fx.symbols import SPECS, fmt_price, pips
from fx import risk
from risk import be_allowed, ema_partial_due, EMA_PARTIAL_PCT
from fx.bridge import BridgeError, TRADE_DISABLED_CODES
from strategies import EMA_TEST_TAG, TOUCH_REASON, CLOSE_EMA_REASON
from fx.engine_core import logger, LIVE_GRACE_SEC, TRANSIENT_TG_SEC, PENDING_TTL_SEC, HORIZON_LABEL, FX_STRATEGY_INFO


class PositionsMixin:
    def _excursion(self, p: Position, price: float) -> dict:
        m = risk.move_in_r(p.side, p.entry_price, p.initial_sl, price)
        return {"mfe_r": round(max(p.mfe_r, m), 3), "mae_r": round(max(p.mae_r, -m), 3)}

    async def _manage_paper_position(self, p: Position, price: float, rules: dict, now: datetime, rollover: bool = False):
        """One monitor tick for a PAPER position: MFE/MAE, SL/TP (skipped during the rollover spread spike), time stop, partial take-profit, trailing."""
        tickers = self.market.tickers
        updates = self._excursion(p, price)
        p.mfe_r, p.mae_r = updates["mfe_r"], updates["mae_r"]
        if p.exit_mode == "cross":  # Test cruce EMA: only the safety SL (no TP/partial/BE/trailing); the opposite cross is the normal exit
            if not rollover and ((price <= p.sl) if p.side == "long" else (price >= p.sl)):
                await self.log("RISK", f"{p.symbol}: stop en breakeven del test cruce EMA alcanzado ({fmt_price(p.symbol, p.sl)}) tras el cierre parcial" if p.breakeven else
                               f"{p.symbol}: SL de seguridad del test cruce EMA alcanzado ({fmt_price(p.symbol, p.sl)}) · movimiento violento antes del cierre de vela")
                await self.close_position(p.id, p.sl, self._ema_sl_reason(p))
                return
            updates.update(mark_price=price, unrealized_pnl=round(risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty, tickers), 4))
            if self.profile.ema_only and not rollover:
                await self._ema_exits(p, price, rules, updates)
            if not rollover and ema_partial_due(rules, p.side, p.entry_price, p.atr, price, p.partial_done):
                part = risk.partial_lots(p.qty, EMA_PARTIAL_PCT)
                be = self._ema_be(p)
                net = 0.0
                if part > 0 and not await maintenance.claim_partial(self.db, ObjectId(p.id)):
                    return
                if part > 0:
                    fee = risk.spread_cost(p.symbol, part, tickers)
                    gross = risk.pnl(p.symbol, p.side, p.entry_price, price, part, tickers)
                    net = gross - fee
                    await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": net}})
                    updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), fees=round(p.fees + fee, 6),
                                   partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + fee, 6), initial_qty=p.initial_qty or p.qty,
                                   unrealized_pnl=round(risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty - part, tickers), 4))
                    p.qty = updates["qty"]
                updates.update(partial_done=True, sl=be, breakeven=True)
                await self._announce_ema_partial(p, part, price, net, be, rules)
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            return
        if not rollover:
            hit, reason = risk.check_exit(p.side, p.sl, p.tp, price)
            if hit is not None:
                await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
                if reason == "SL" and p.breakeven:
                    reason = "TRAILING"
                await self.close_position(p.id, hit, reason)
                return
        if p.expires_at and now >= datetime.fromisoformat(p.expires_at):
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            await self.close_position(p.id, price, "TIEMPO")
            return
        updates.update(mark_price=price, unrealized_pnl=round(risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty, tickers), 4))
        if rules["partial_tp_enabled"] and not p.partial_done and p.mfe_r >= rules["partial_tp_r"] and not rollover:
            part = risk.partial_lots(p.qty, rules["partial_tp_pct"])
            if part > 0 and not await maintenance.claim_partial(self.db, ObjectId(p.id)):
                return
            if part > 0:
                fee = risk.spread_cost(p.symbol, part, tickers)
                gross = risk.pnl(p.symbol, p.side, p.entry_price, price, part, tickers)
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - fee}})
                updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), fees=round(p.fees + fee, 6), partial_done=True,
                               partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + fee, 6), initial_qty=p.initial_qty or p.qty,
                               unrealized_pnl=round(risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty - part, tickers), 4))
                p.qty = updates["qty"]
                await self._announce_partial(p, part, price, gross - fee, rules)
            else:
                updates["partial_done"] = True
                await self.log("RISK", f"{p.symbol}: cierre parcial omitido · el lote mínimo (0.01) no permite dividir la posición")
        if rules["trailing_enabled"] or rules["breakeven_enabled"]:
            new_sl, be = risk.update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, price, SPECS[p.symbol]["spread"],
                                              rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"],
                                              be_enabled=be_allowed(rules, p.partial_done or updates.get("partial_done", False)), trail_enabled=rules["trailing_enabled"])
            if new_sl != p.sl:
                updates.update(sl=new_sl, breakeven=be)
                if be and not p.breakeven:
                    await self.log("RISK", f"{p.symbol}: stop movido a breakeven ({fmt_price(p.symbol, new_sl)})")
        await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})

    def _ema_sl_reason(self, p: Position) -> str:
        if not self.profile.ema_only:
            return "BREAKEVEN" if p.breakeven else "SL"
        return "SL" if p.sl == p.initial_sl else "BREAKEVEN" if p.sl == self._ema_be(p) else "TRAILING"

    async def _ema_exits(self, p: Position, price: float, rules: dict, updates: dict):
        """Pepperstone: partial close (N % at +N×ATR), trailing (N×ATR) and breakeven (at +N×ATR), each on its own toggle. Paper and REAL."""
        tickers, f = self.market.tickers, lambda v: fmt_price(p.symbol, v)  # noqa: E731
        ex = risk.ema_exits(rules, p.side, p.entry_price, p.atr, price, p.sl, self._ema_be(p), p.partial_done)
        tag = "REAL " if p.live else ""
        if ex["partial"]:
            pct, k = rules["ema_partial_pct"], rules["ema_partial_at_atr"]
            part = min(risk.partial_lots(p.qty, pct, await self.broker.lot(p.symbol)) if p.live else risk.partial_lots(p.qty, pct), p.qty)
            updates["partial_done"] = True
            if part <= 0 or part >= p.qty:
                await self.log("RISK", f"{p.symbol}: +{k:g}×ATR alcanzado · cierre parcial del {pct:g}% omitido (el lote mínimo no permite dividir la posición)")
            else:
                try:
                    fee = abs((await self.broker.close_partial(p.order_id, part, p.link_id)).get("commission") or 0) if p.live else risk.spread_cost(p.symbol, part, tickers)
                    gross = risk.pnl(p.symbol, p.side, p.entry_price, price, part, tickers)
                    if not p.live:
                        if not await maintenance.claim_partial(self.db, ObjectId(p.id)):
                            return
                        await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - fee}})
                    rest = round(p.qty - part, 8)
                    updates.update(qty=rest, margin=round(p.margin * rest / p.qty, 6), partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + fee, 6),
                                   initial_qty=p.initial_qty or p.qty, **({} if p.live else {"fees": round(p.fees + fee, 6), "unrealized_pnl": round(risk.pnl(p.symbol, p.side, p.entry_price, price, rest, tickers), 4)}))
                    p.qty = rest
                    msg = f"cerrado {pct:g}% ({part:.2f} lotes) @ {f(price)} a +{k:g}×ATR · PnL {gross - fee:+.2f} USD · resto {rest:.2f} lotes"
                    await self.log("ORDER", f"PARCIAL {tag}{p.side.upper()} {p.symbol} · {msg}")
                    await self.tg(f"🎯 <b>PARCIAL {tag}{p.side.upper()} {p.symbol}</b>\n{msg[0].upper() + msg[1:]}", "orders")
                except Exception as e:
                    updates.pop("partial_done")
                    await self.log("ERROR", f"{p.symbol}: cierre parcial REAL fallido · {str(e)[:120]}")
        if ex["sl"] is not None:
            new_sl = round(ex["sl"], SPECS[p.symbol]["digits"])
            if new_sl == p.sl:
                return
            try:
                if p.live:
                    await self.broker.set_stop(p.symbol, p.order_id, new_sl, None)
                updates.update(sl=new_sl, breakeven=True)
                if ex["why"] == "breakeven":
                    await self.log("RISK", f"{p.symbol}: +{rules['ema_be_atr']:g}×ATR alcanzado · stop {tag}movido a breakeven ({f(new_sl)})")
            except Exception as e:
                await self.log("ERROR", f"{p.symbol}: no se pudo mover el stop ({ex['why']}) en {self.bk} · {str(e)[:120]}")

    @staticmethod
    def _ema_be(p: Position) -> float:
        spread = SPECS[p.symbol]["spread"]
        return round(p.entry_price + spread if p.side == "long" else p.entry_price - spread, SPECS[p.symbol]["digits"])

    async def _announce_ema_partial(self, p: Position, part: float, price: float, net: float, be: float, rules: dict):
        tag = "REAL " if p.live else ""
        k, f = rules["ema_test_partial_atr"], lambda v: fmt_price(p.symbol, v)  # noqa: E731
        if part <= 0:
            await self.log("RISK", f"{p.symbol}: test cruce EMA a +{k:g}×ATR · cierre parcial omitido (el lote mínimo no permite dividir) · SL movido a breakeven ({f(be)})")
            return
        await self.log("ORDER", f"PARCIAL {tag}{p.side.upper()} {p.symbol} · {EMA_TEST_TAG}: cerrado {EMA_PARTIAL_PCT}% ({part:.2f} lotes) @ {f(price)} a +{k:g}×ATR · "
                                f"PnL {net:+.2f} USD · resto {p.qty:.2f} lotes con SL en breakeven ({f(be)}) hasta el cruce contrario")
        await self.tg(f"🎯 <b>PARCIAL {tag}{p.side.upper()} {p.symbol}</b> · {EMA_TEST_TAG} @ {f(price)} (+{k:g}×ATR)\n"
                      f"Cerrado {EMA_PARTIAL_PCT}% ({part:.2f} lotes) · PnL {net:+.2f} USD · resto {p.qty:.2f} lotes con SL en breakeven ({f(be)}) hasta el cruce contrario", "orders")

    async def _ema_partial_live(self, p: Position, price: float, rules: dict, updates: dict):
        done = p.partial_done
        if ema_partial_due(rules, p.side, p.entry_price, p.atr, price, done):
            part = risk.partial_lots(p.qty, EMA_PARTIAL_PCT, await self.broker.lot(p.symbol))
            if part > 0:
                try:
                    r = await self.broker.close_partial(p.order_id, part, p.link_id)
                    gross = risk.pnl(p.symbol, p.side, p.entry_price, price, part, self.market.tickers)
                    updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), partial_done=True,
                                   partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + abs(r.get("commission") or 0), 6),
                                   initial_qty=p.initial_qty or p.qty)
                    p.qty = updates["qty"]
                    done = True
                    await self._announce_ema_partial(p, part, price, gross, self._ema_be(p), rules)
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: cierre parcial REAL del test cruce EMA fallido · {str(e)[:120]}")
            else:
                done = updates["partial_done"] = True
                await self._announce_ema_partial(p, 0, price, 0.0, self._ema_be(p), rules)
        if done and not p.breakeven:
            be = self._ema_be(p)
            try:
                await self.broker.set_stop(p.symbol, p.order_id, be, None)
                updates.update(sl=be, breakeven=True)
                await self.log("RISK", f"{p.symbol}: stop REAL del test cruce EMA movido a breakeven ({fmt_price(p.symbol, be)})")
            except Exception as e:
                await self.log("ERROR", f"{p.symbol}: no se pudo mover el stop a breakeven en {self.bk} · {str(e)[:120]}")

    async def _announce_partial(self, p: Position, part: float, price: float, net: float, rules: dict):
        tag = "REAL " if p.live else ""
        await self.log("ORDER", f"PARCIAL {tag}{p.side.upper()} {p.symbol}: cerrado {rules['partial_tp_pct']}% ({part:.2f} lotes) @ {fmt_price(p.symbol, price)} "
                                f"a +{rules['partial_tp_r']:g}R · PnL {net:+.2f} USD · resto {p.qty:.2f} lotes sigue con trailing")
        await self.tg(f"🎯 <b>PARCIAL {tag}{p.side.upper()} {p.symbol}</b> @ {fmt_price(p.symbol, price)} (+{rules['partial_tp_r']:g}R)\n"
                      f"Cerrado {rules['partial_tp_pct']}% ({part:.2f} lotes) · PnL {net:+.2f} USD · resto {p.qty:.2f} lotes con trailing", "orders")

    async def _adopt_live(self, pid: str, xp: dict, settings: FxSettings, tickers: dict):
        """A broker position carries our clientId but has no DB record (order with unknown result, restored backup…): manage it like any other."""
        async with self.lock:
            if await self.db.positions.find_one({"order_id": pid}):
                return
            symbol = self.broker.our_symbol(xp["symbol"])
            if symbol not in SPECS:
                if pid not in self._external_warned:
                    self._external_warned.add(pid)
                    await self.log("ERROR", f"{xp['symbol']}: posición en {self.bk} con identificador del bot pero símbolo desconocido · no se puede gestionar")
                return
            pend = await self.db.pending_orders.find_one_and_delete({"_id": xp["client_id"]}) or {}
            entry = xp["entry"] or (tickers.get(symbol) or {}).get("price") or 0.0
            sl = float(xp["sl"] or pend.get("sl") or 0.0)
            tp = float(xp["tp"] or pend.get("tp") or 0.0)
            leverage = int(pend.get("leverage") or ((await self._broker_info()) or {}).get("leverage") or settings.leverage)
            margin = xp["volume"] * SPECS[symbol]["contract"] * risk.base_usd(symbol, tickers, entry) / max(leverage, 1)
            pos = Position(symbol=symbol, side=xp["side"], strategy=pend.get("strategy") or "adoptada", entry_price=entry, qty=xp["volume"], leverage=leverage,
                           margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=float(pend.get("atr") or abs(entry - sl) or SPECS[symbol]["pip"] * 10), fees=abs(xp["commission"]),
                           mark_price=xp["mark"] or entry, signal_id=pend.get("signal_id"), expires_at=pend.get("expires_at"), live=True, order_id=pid,
                           link_id=xp["client_id"], opened_at=str(xp.get("time") or now_iso()).replace("Z", "+00:00"), exit_mode=pend.get("exit_mode"))
            res = await self.db.positions.insert_one(pos.to_mongo())
            pos.id = str(res.inserted_id)
            if pend.get("signal_id"):
                await self.db.signals.update_one({"_id": ObjectId(pend["signal_id"])}, {"$set": {"executed": True, "note": f"Ejecutada en {self.bk} · confirmada por su identificador (clientId) tras un resultado desconocido"}})
                await self.db.shadow_positions.delete_many({"signal_id": pend["signal_id"]})
        why = f"orden con resultado desconocido confirmada en {self.bk}" if pend else f"posición en {self.bk} con identificador del bot sin registro local"
        f = lambda v: fmt_price(symbol, v)  # noqa: E731
        await self.log("ORDER", f"ADOPTADA REAL {pos.side.upper()} {symbol} @ {f(entry)} · {pos.qty:.2f} lotes · SL {f(sl) if sl else '—'} · TP {f(tp) if tp else '—'} · {why}")
        await self.tg(f"✅ <b>Posición REAL adoptada · {pos.side.upper()} {symbol}</b> @ {f(entry)}\n{pos.qty:.2f} lotes · SL {f(sl) if sl else '—'} · TP {f(tp) if tp else '—'}\n{why.capitalize()}. El bot la gestiona con normalidad.", "orders")
        if not sl:
            await self.log("ERROR", f"{symbol}: la posición adoptada no tiene stop loss en {self.bk} · revísala manualmente")

    async def _expire_pending(self, now: datetime):
        async for d in self.db.pending_orders.find({}):
            if (now - datetime.fromisoformat(d["created_at"])).total_seconds() < PENDING_TTL_SEC:
                continue
            await self.db.pending_orders.delete_one({"_id": d["_id"]})
            await self.log("INFO", f"{d['symbol']}: en 2 h no apareció en {self.bk} ninguna posición con el identificador {d['_id']} · la orden se da por no ejecutada")

    async def _sync_live_position(self, p: Position, ex: dict, ticker: dict, settings: FxSettings, now: datetime, rules: dict = None):
        rules = rules or risk.trade_rules(settings)
        if not ex:
            if (now - datetime.fromisoformat(p.opened_at)).total_seconds() < LIVE_GRACE_SEC:
                return
            try:
                fresh = await self.broker.position(p.order_id, max_age=0)
            except Exception as e:
                logger.warning(f"hfm recheck {p.symbol}: {e}")
                return
            if fresh:
                ex = fresh
            else:
                await self._close_live_from_broker(p)
                return
        price = ex["mark"] or (ticker or {}).get("price") or p.mark_price
        updates = {"mark_price": price, "unrealized_pnl": round(ex["profit"] + ex["swap"] + ex["commission"], 4), **self._excursion(p, price)}
        p.mfe_r, p.mae_r = updates["mfe_r"], updates["mae_r"]
        if abs(ex["volume"] - p.qty) > 1e-9:
            updates["qty"] = ex["volume"]
            p.qty = ex["volume"]
        if p.exit_mode == "cross":  # Test cruce EMA: safety SL travels in the order; only the optional partial + breakeven rule is managed here
            await (self._ema_exits if self.profile.ema_only else self._ema_partial_live)(p, price, rules, updates)
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            return
        if p.expires_at and now >= datetime.fromisoformat(p.expires_at):
            await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})
            await self.close_position(p.id, price, "TIEMPO")
            return
        if rules["partial_tp_enabled"] and not p.partial_done and p.mfe_r >= rules["partial_tp_r"]:
            lot = await self.broker.lot(p.symbol)
            part = risk.partial_lots(p.qty, rules["partial_tp_pct"], lot)
            if part > 0:
                try:
                    r = await self.broker.close_partial(p.order_id, part, p.link_id)
                    gross = risk.pnl(p.symbol, p.side, p.entry_price, price, part, self.market.tickers)
                    updates.update(qty=round(p.qty - part, 8), margin=round(p.margin * (p.qty - part) / p.qty, 6), partial_done=True,
                                   partial_gross=round(p.partial_gross + gross, 6), partial_fees=round(p.partial_fees + abs(r.get("commission") or 0), 6),
                                   initial_qty=p.initial_qty or p.qty)
                    p.qty = updates["qty"]
                    await self._announce_partial(p, part, price, gross, rules)
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: cierre parcial REAL fallido · {str(e)[:120]}")
            else:
                updates["partial_done"] = True
                await self.log("RISK", f"{p.symbol}: cierre parcial omitido · el lote mínimo del bróker no permite dividir la posición")
        if rules["trailing_enabled"] or rules["breakeven_enabled"]:
            new_sl, be = risk.update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, price, SPECS[p.symbol]["spread"],
                                              rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"],
                                              be_enabled=be_allowed(rules, p.partial_done or updates.get("partial_done", False)), trail_enabled=rules["trailing_enabled"])
            new_sl = round(new_sl, SPECS[p.symbol]["digits"])
            if new_sl != p.sl and abs(new_sl - p.sl) >= SPECS[p.symbol]["pip"]:
                try:
                    await self.broker.set_stop(p.symbol, p.order_id, new_sl, p.tp)
                    updates.update(sl=new_sl, breakeven=be)
                    await self.log("RISK", f"{p.symbol}: stop REAL movido a {fmt_price(p.symbol, new_sl)}{' (breakeven)' if be and not p.breakeven else ''}")
                except Exception as e:
                    await self.log("ERROR", f"{p.symbol}: no se pudo mover el stop en {self.bk} · {str(e)[:120]}")
        await self.db.positions.update_one({"_id": ObjectId(p.id)}, {"$set": updates})

    async def _tg_transient(self, symbol: str, e: BridgeError):
        """One Telegram notice per 30 min for orders skipped because HFM/puente MT did not answer in time (not a broker rejection)."""
        now = time.time()
        if now - self._transient_tg_ts < TRANSIENT_TG_SEC:
            return
        self._transient_tg_ts = now
        await self.tg(f"⏳ <b>Orden REAL no enviada · {symbol}</b>\n{self.bk}/puente MT no respondió a tiempo ({e.attempts} intento(s)): {e.msg[:200]}\n"
                      f"Se verificó que no quedó ninguna orden abierta. El bot sigue operando; si se repite, revisa la conexión en Ajustes → {self.bl}.", "orders")

    async def _close_live_from_broker(self, p: Position):
        async with self.lock:
            if not await self.db.positions.find_one({"_id": ObjectId(p.id)}):
                return
            cr = None
            try:
                cr = await self.broker.closed_result(p.order_id)
            except Exception as e:
                logger.warning(f"hfm deals {p.symbol}: {e}")
            price = (cr or {}).get("exit") or p.mark_price
            fees = cr["fees"] if cr else p.fees + p.partial_fees
            net = cr["pnl"] if cr else risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty, self.market.tickers) + p.partial_gross - p.fees - p.partial_fees
            reason = (cr or {}).get("reason") or ("SL" if p.exit_mode == "cross" else "TP" if abs(price - p.tp) <= abs(price - p.sl) else ("TRAILING" if p.breakeven else "SL"))
            if reason == "SL" and p.breakeven:
                reason = self._ema_sl_reason(p) if p.exit_mode == "cross" else "TRAILING"
            await self.db.positions.delete_one({"_id": ObjectId(p.id)})
            trade = await self._record_trade(p, price, net, fees, reason)
        await self._announce_close(p, trade, reason, cr is None)

    async def _announce_close(self, p: Position, trade: Trade, reason: str, estimated: bool = False):
        tag = "REAL " if p.live else ""
        est = f" · PnL estimado, pendiente de confirmar con {self.bk}" if estimated else ""
        await self.log("ORDER", f"CERRADA {tag}{p.side.upper()} {p.symbol} @ {fmt_price(p.symbol, trade.exit_price)} · {reason} · PnL {trade.pnl:+.2f} USD{est}")
        if reason in (TOUCH_REASON, CLOSE_EMA_REASON):
            how = ("toque de la EMA 21", "La vela cerró tocando la EMA 21 con la operación abierta.") if reason == TOUCH_REASON else \
                  ("cierre de vela al otro lado de la EMA 21", f"La vela cerró {'por debajo' if p.side == 'long' else 'por encima'} de la EMA 21 con la operación abierta.")
            await self.tg(f"{'✅' if trade.pnl >= 0 else '❌'} <b>CERRADA {tag}{p.side.upper()} {p.symbol} · {how[0]}</b>\n"
                          f"{how[1]}\n"
                          f"{'Ganó' if trade.pnl >= 0 else 'Perdió'} <b>{abs(trade.pnl):.2f} USD</b> ({trade.pnl_pct:+.2f}%) · entrada {fmt_price(p.symbol, p.entry_price)} → "
                          f"salida {fmt_price(p.symbol, trade.exit_price)}{est}", "orders")
            return
        await self.tg(f"{'✅' if trade.pnl >= 0 else '❌'} <b>CERRADA {tag}{p.side.upper()} {p.symbol}</b> @ {fmt_price(p.symbol, trade.exit_price)} · {reason}\n"
                      f"PnL <b>{trade.pnl:+.2f} USD</b> ({trade.pnl_pct:+.2f}%) · entrada {fmt_price(p.symbol, p.entry_price)}{est}", "orders")

    async def _record_trade(self, p: Position, price: float, net: float, fees: float, reason: str) -> Trade:
        tp_r = risk.tp_in_r(p.side, p.entry_price, p.initial_sl, p.tp)
        mfe = max(p.mfe_r, risk.move_in_r(p.side, p.entry_price, p.initial_sl, price))
        hold = (datetime.now(timezone.utc) - datetime.fromisoformat(p.opened_at)).total_seconds() / 60
        margin = p.margin * (p.initial_qty / p.qty) if p.initial_qty and p.qty else p.margin
        trade = Trade(symbol=p.symbol, side=p.side, strategy=p.strategy, entry_price=p.entry_price, exit_price=price, qty=p.initial_qty or p.qty,
                      leverage=p.leverage, margin=round(margin, 6), pnl=round(net, 4), pnl_pct=round(net / margin * 100, 2) if margin else 0,
                      fees=round(fees, 4), reason=reason, opened_at=p.opened_at, live=p.live, horizon=p.horizon, entry_type=p.entry_type,
                      mfe_r=round(mfe, 2), mae_r=round(p.mae_r, 2), mfe_tp_pct=round(mfe / tp_r * 100, 1) if tp_r else 0.0,
                      partial_tp=p.partial_done and bool(p.partial_gross), hold_min=round(hold, 1),
                      sl=p.sl, tp=p.tp, initial_sl=p.initial_sl, atr=p.atr, breakeven=p.breakeven, signal_id=p.signal_id, order_id=p.order_id,
                      exit_mode=p.exit_mode, tags=[EMA_TEST_TAG] if p.exit_mode == "cross" and not self.profile.ema_only else [])
        res = await self.db.trades.insert_one(trade.to_mongo())
        self._trade_totals = None
        trade.id = str(res.inserted_id)
        if self.profile.rules_only and trade.exit_mode != "cross" and trade.strategy != "manual":
            asyncio.create_task(self._learn(trade))
        return trade

    async def _learn(self, trade: Trade):
        if (await self.get_settings()).rules_auto_learn:
            await trade_learning.learn(self, trade, "USD", "s")

    # ---------- execution ----------
    def _slot_block(self, open_docs: list, settings: FxSettings, horizon: str):
        """Long-horizon positions (fix reversion) have their own cap and do not count towards «Máx. posiciones». Test cruce EMA positions use no slot."""
        same = [p for p in open_docs if (p.get("horizon") or "short") == horizon and p.get("exit_mode") != "cross"]
        cap = settings.max_long_positions if horizon == "long" else settings.max_positions
        if len(same) >= cap:
            return f"máximo de posiciones de {HORIZON_LABEL[horizon]} alcanzado ({cap})"
        return None

    def _pair_taken(self, open_docs: list, symbol: str, cross: bool) -> bool:
        """One position per pair; with `dual_pair` (HotForex) the Test cruce EMA and the normal strategies are counted separately."""
        return any(p["symbol"] == symbol and (not self.profile.dual_pair or (p.get("exit_mode") == "cross") == cross) for p in open_docs)

    async def _log_sizing(self, symbol, sig, lots, equity, real_risk, entry, sl, tp):
        k, f = sig.get("atr_levels"), lambda v: fmt_price(symbol, v)  # noqa: E731
        await self.log("RISK", f"{symbol}: tamaño {lots:.2f} lotes · equidad {equity:.2f} · riesgo real {real_risk:g}% · stop {pips(symbol, abs(entry - sl)):.1f} pips · "
                               f"ATR {f(sig['atr'])}" + (f" · SL {k[0]:g}×ATR · TP {k[1]:g}×ATR" if k else "") + f" · entrada {f(entry)} · SL {f(sl)} · TP {f(tp)}")

    async def open_position(self, symbol: str, sig: dict, settings: FxSettings, signal_id: str, risk_factor: float = 1.0,
                            risk_reason: str = "noticias", expires_at: str = None, horizon: str = "short", exit_mode: str = None):
        """Returns (Position | None, reason) — reason explains why nothing was opened.
        `exit_mode="cross"` (Test cruce EMA): only the 3×ATR safety SL is sent (no TP), the position uses no slot and closes on the opposite cross."""
        risk_factor = 1.0  # fixed-fractional risk: no filter may change the size
        risk_pct = settings.risk_per_trade
        cross = exit_mode == "cross"
        tickers = self.market.tickers
        async with self.lock:
            open_docs = await self.db.positions.find({}, {"symbol": 1, "side": 1, "horizon": 1, "exit_mode": 1}).to_list(100)
            slot = None if cross else self._slot_block(open_docs, settings, horizon)
            if self._pair_taken(open_docs, symbol, cross) or slot:
                await self.log("RISK", f"{symbol}: apertura cancelada · {slot or 'par ya abierto'}")
                return None, slot or "par ya abierto"
            price = tickers.get(symbol, {}).get("price") or sig["price"]
            long = sig["side"] == "long"
            entry = price
            sl = entry - (sig["price"] - sig["sl"])
            tp = entry + (sig["tp"] - sig["price"])
            if self.live:
                if not self.broker:
                    await self.log("ERROR", f"{symbol}: modo REAL sin cuenta {self.bk} vinculada · orden no enviada")
                    return None, f"modo REAL sin cuenta {self.bk} vinculada"
                if self.hfm["down_since"]:
                    await self.log("RISK", f"{symbol}: orden no enviada · cuenta {self.bk} sin conexión con el puente MT ({self.hfm['error']})")
                    return None, f"cuenta {self.bk} sin conexión con el puente MT"
                try:
                    info = await self.broker.info(max_age=0)
                    if not info.get("trade_allowed", True):
                        await self.log("ERROR", f"{symbol}: la cuenta {self.bk} no permite operar (trading deshabilitado o contraseña investor)")
                        return None, f"la cuenta {self.bk} no permite operar"
                    lot = await self.broker.lot(symbol)
                    leverage = info["leverage"] or settings.leverage
                    lots, margin, real_risk = risk.position_size(info["equity"], info["available"], risk_pct, entry, sl, leverage, symbol, tickers,
                                                                 settings.min_lot_max_risk, lot)
                    if lots <= 0 or margin < 1:
                        need = risk.min_lot_risk_pct(info["equity"], entry, sl, symbol, tickers, lot["min"])
                        reason = (f"no se puede arriesgar exactamente el {risk_pct:g}%: el lote mínimo del bróker ({lot['min']:g}) arriesgaría el {need}% de la equidad real, "
                                  f"o falta margen para el tamaño necesario")
                        await self.log("RISK", f"{symbol}: {reason}")
                        return None, reason
                    await self._log_sizing(symbol, sig, lots, info["equity"], real_risk, entry, sl, tp)
                    r = await self.broker.open(symbol, sig["side"], lots, sl, None if cross else tp)
                except BridgeError as e:
                    if e.kind == "unknown":
                        link = getattr(e, "link_id", "") or ""
                        if link:
                            await self.db.pending_orders.update_one({"_id": link}, {"$set": {
                                "symbol": symbol, "side": sig["side"], "strategy": sig["strategy"], "signal_id": signal_id, "atr": sig["atr"], "sl": sl, "tp": tp, "exit_mode": exit_mode,
                                "lots": lots, "leverage": leverage, "expires_at": expires_at, "created_at": now_iso()}}, upsert=True)
                        reason = f"resultado desconocido: {e.msg[:120]}"
                        await self.log("ERROR", f"{symbol}: orden REAL con resultado desconocido · {e.msg[:160]} · si {self.bk} la ejecutó, el bot la adoptará por su identificador {link}")
                        await self.tg(f"❓ <b>Orden REAL con resultado desconocido · {symbol}</b>\n{e.msg[:250]}\nSi {self.bk} llegó a ejecutarla, el bot la detectará por su identificador "
                                      f"({link}) en cuanto el puente MT responda y la gestionará con normalidad (el SL/TP ya viaja en la orden).", "orders")
                        return None, reason
                    if e.kind == "transient":
                        reason = f"{self.bk}/puente MT no disponible tras {e.attempts} intento(s): {e.msg[:120]}"
                        await self.log("RISK", f"{symbol}: orden REAL no enviada · {reason} · verificado: no quedó ninguna orden abierta")
                        await self._tg_transient(symbol, e)
                        return None, reason
                    await self.log("ERROR", f"{symbol}: orden REAL rechazada por {self.bk}/puente MT · {e.msg[:260]}")
                    await self.tg(f"⚠️ <b>Orden REAL rechazada · {symbol}</b>\n{e.msg[:400]}", "orders")
                    if e.code in TRADE_DISABLED_CODES and self.store:
                        bad = sorted(set(self.cfg.hfm_symbols_bad or []) | {symbol})
                        await self.store.update({"hfm_symbols_bad": bad})
                        await self.log("RISK", f"{symbol}: marcado como NO operable en {self.bk} · no se enviarán más órdenes de este par hasta corregir el sufijo y volver a «Comprobar símbolos»")
                    return None, f"orden REAL rechazada: {e.msg[:160]}"
                except Exception as e:
                    await self.log("ERROR", f"{symbol}: orden REAL no enviada · {str(e)[:160]}")
                    await self.tg(f"⚠️ <b>Orden REAL no enviada · {symbol}</b>\n{str(e)[:300]}", "orders")
                    return None, f"orden REAL no enviada: {str(e)[:120]}"
                if r.get("note"):
                    await self.log("RISK", f"{symbol}: orden REAL {r['note']} · posición {r['position_id']} localizada por clientId")
                elif r.get("attempts", 1) > 1:
                    await self.log("RISK", f"{symbol}: orden REAL ejecutada en el intento {r['attempts']} tras fallos temporales de {self.bk}/puente MT")
                entry, lots, fee = (r["entry"] or entry), r["lots"], abs(r["commission"])
                sl, tp = r["sl"], (tp if cross else r["tp"])  # cross: TP stays virtual (R metrics only), nothing at the broker
                margin = lots * SPECS[symbol]["contract"] * risk.base_usd(symbol, tickers, entry) / leverage
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=lots, initial_qty=lots, leverage=leverage, margin=margin,
                               sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"], fees=fee, mark_price=price, signal_id=signal_id, expires_at=expires_at,
                               live=True, order_id=r["position_id"], link_id=r["link_id"], horizon=horizon, exit_mode=exit_mode)
                res = await self.db.positions.insert_one(pos.to_mongo())
                pos.id = str(res.inserted_id)
                if not r["filled"]:
                    await self.log("RISK", f"{symbol}: orden enviada (posición {r['position_id']}) pero sin confirmación de llenado · se sincronizará con {self.bk}")
            else:
                leverage = settings.leverage
                s = await self.account_summary()
                lots, margin, real_risk = risk.position_size(s["equity"], s["available"], risk_pct, entry, sl, settings.leverage, symbol, tickers,
                                                             settings.min_lot_max_risk)
                if lots <= 0 or margin < 1:
                    need = risk.min_lot_risk_pct(s["equity"], entry, sl, symbol, tickers)
                    reason = (f"no se puede arriesgar exactamente el {risk_pct:g}%: el lote mínimo (0.01) arriesgaría el {need}% de la equidad, "
                              f"o falta margen para el tamaño necesario · sube el capital o usa una temporalidad menor")
                    await self.log("RISK", f"{symbol}: {reason}")
                    return None, reason
                await self._log_sizing(symbol, sig, lots, s["equity"], real_risk, entry, sl, tp)
                fee = risk.spread_cost(symbol, lots, tickers)
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=lots, initial_qty=lots, leverage=settings.leverage,
                               margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"], fees=fee, mark_price=price, signal_id=signal_id, expires_at=expires_at, horizon=horizon,
                               exit_mode=exit_mode)
                res = await self.db.positions.insert_one(pos.to_mongo())
                pos.id = str(res.inserted_id)
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": -fee}})
        tag = "REAL " if self.live else ""
        reduced = f" · tamaño ajustado al {int(risk_factor * 100)}% por {risk_reason}" if risk_factor != 1 else ""
        reduced += " · largo plazo (no cuenta en Máx. posiciones)" if horizon == "long" else ""
        if real_risk > risk_pct + 0.05:
            reduced += f" · lote mínimo: riesgo real {real_risk:g}% (objetivo {risk_pct:g}%)"
        f = lambda v: fmt_price(symbol, v)  # noqa: E731
        levels = (f"SL de seguridad {f(sl)} ({pips(symbol, abs(entry - sl)):.1f} pips) · sin TP · cierre en el cruce contrario" + ("" if self.profile.ema_only else f" · ⚠️ {EMA_TEST_TAG}") if cross
                  else f"SL {f(sl)} ({pips(symbol, abs(entry - sl)):.1f} pips) · TP {f(tp)}")
        await self.log("ORDER", f"ABIERTA {tag}{sig['side'].upper()} {symbol} @ {f(entry)} · {lots:.2f} lotes · 1:{leverage} · {levels}{reduced}")
        await self.tg(f"{'🟢' if long else '🔴'} <b>ABIERTA {tag}{sig['side'].upper()} {symbol}</b> @ {f(entry)}\n"
                      f"{lots:.2f} lotes · 1:{leverage} · margen ${margin:,.2f}\n{levels}\n"
                      f"Estrategia: {self.strat_name if cross else FX_STRATEGY_INFO.get(sig['strategy'], {}).get('name', sig['strategy'])}{reduced}", "orders")
        return pos, None

    async def close_position(self, pos_id: str, price: float = None, reason: str = "MANUAL"):
        tickers = self.market.tickers
        async with self.lock:
            doc = await self.db.positions.find_one({"_id": ObjectId(pos_id)})
            if not doc:
                return None
            p = Position.from_mongo(doc)
            if p.live:
                if not self.broker:
                    await self.log("ERROR", f"{p.symbol}: cierre REAL imposible · sin cuenta {self.bk} vinculada")
                    return None
                try:
                    cr_close = await self.broker.close(p.order_id, p.link_id)
                    if cr_close.get("note"):
                        await self.log("RISK", f"{p.symbol}: cierre REAL {cr_close['note']}")
                    elif cr_close.get("attempts", 1) > 1:
                        await self.log("RISK", f"{p.symbol}: cierre REAL ejecutado en el intento {cr_close['attempts']} tras fallos temporales de {self.bk}/puente MT")
                except Exception as e:
                    if getattr(e, "code", "") != "TRADE_RETCODE_POSITION_CLOSED":
                        k = getattr(e, "kind", "")
                        if k == "unknown":
                            await self.log("ERROR", f"{p.symbol}: cierre REAL con resultado desconocido · {str(e)[:160]} · se comprobará en {self.bk} en la siguiente sincronización")
                            return None
                        kind = f"{self.bk}/puente MT no disponible tras varios intentos" if k == "transient" else "cierre REAL fallido"
                        await self.log("ERROR", f"{p.symbol}: {kind} · {str(e)[:160]} · la posición sigue abierta en {self.bk} con su SL/TP; se reintentará")
                        return None
                cr = None
                try:
                    cr = await self.broker.closed_result(p.order_id)
                except Exception as e:
                    logger.warning(f"hfm deals {p.symbol}: {e}")
                price = (cr or {}).get("exit") or tickers.get(p.symbol, {}).get("price") or p.mark_price
                fees = cr["fees"] if cr else p.fees + p.partial_fees
                net = cr["pnl"] if cr else risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty, tickers) + p.partial_gross - p.fees - p.partial_fees
                await self.db.positions.delete_one({"_id": ObjectId(pos_id)})
                trade = await self._record_trade(p, price, net, fees, reason)
                estimated = cr is None
            else:
                await self.db.positions.delete_one({"_id": ObjectId(pos_id)})
                price = price or tickers.get(p.symbol, {}).get("price") or p.mark_price
                exit_fee = risk.spread_cost(p.symbol, p.qty, tickers)
                gross = risk.pnl(p.symbol, p.side, p.entry_price, price, p.qty, tickers)
                net = gross + p.partial_gross - p.fees - exit_fee
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": gross - exit_fee}})
                trade = await self._record_trade(p, price, net, p.fees + exit_fee, reason)
                estimated = False
        await self._announce_close(p, trade, reason, estimated)
        return trade
