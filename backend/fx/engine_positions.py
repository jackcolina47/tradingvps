"""FxEngine mixin · positions: paper tick (SL/TP, time stop, partial, trailing), REAL sync with HFM, adoption of orphan broker positions,
open/close with sizing and all the MetaApi failure paths."""
import time
from datetime import datetime, timezone
from bson import ObjectId
from models import Position, Trade, now_iso
from fx.models import FxSettings
from fx.symbols import SPECS, fmt_price, pips
from fx import risk
from risk import be_allowed
from fx.metaapi import MetaApiError, TRADE_DISABLED_CODES
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
                           link_id=xp["client_id"], opened_at=str(xp.get("time") or now_iso()).replace("Z", "+00:00"))
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

    async def _tg_transient(self, symbol: str, e: MetaApiError):
        """One Telegram notice per 30 min for orders skipped because HFM/MetaApi did not answer in time (not a broker rejection)."""
        now = time.time()
        if now - self._transient_tg_ts < TRANSIENT_TG_SEC:
            return
        self._transient_tg_ts = now
        await self.tg(f"⏳ <b>Orden REAL no enviada · {symbol}</b>\n{self.bk}/MetaApi no respondió a tiempo ({e.attempts} intento(s)): {e.msg[:200]}\n"
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
            reason = (cr or {}).get("reason") or ("TP" if abs(price - p.tp) <= abs(price - p.sl) else ("TRAILING" if p.breakeven else "SL"))
            if reason == "SL" and p.breakeven:
                reason = "TRAILING"
            await self.db.positions.delete_one({"_id": ObjectId(p.id)})
            trade = await self._record_trade(p, price, net, fees, reason)
        await self._announce_close(p, trade, reason, cr is None)

    async def _announce_close(self, p: Position, trade: Trade, reason: str, estimated: bool = False):
        tag = "REAL " if p.live else ""
        est = f" · PnL estimado, pendiente de confirmar con {self.bk}" if estimated else ""
        await self.log("ORDER", f"CERRADA {tag}{p.side.upper()} {p.symbol} @ {fmt_price(p.symbol, trade.exit_price)} · {reason} · PnL {trade.pnl:+.2f} USD{est}")
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
                      sl=p.sl, tp=p.tp, initial_sl=p.initial_sl, atr=p.atr, breakeven=p.breakeven, signal_id=p.signal_id, order_id=p.order_id)
        res = await self.db.trades.insert_one(trade.to_mongo())
        trade.id = str(res.inserted_id)
        return trade

    # ---------- execution ----------
    def _slot_block(self, open_docs: list, settings: FxSettings, horizon: str):
        """Long-horizon positions (fix reversion) have their own cap and do not count towards «Máx. posiciones»."""
        same = [p for p in open_docs if (p.get("horizon") or "short") == horizon]
        cap = settings.max_long_positions if horizon == "long" else settings.max_positions
        if len(same) >= cap:
            return f"máximo de posiciones de {HORIZON_LABEL[horizon]} alcanzado ({cap})"
        return None

    async def open_position(self, symbol: str, sig: dict, settings: FxSettings, signal_id: str, risk_factor: float = 1.0,
                            risk_reason: str = "noticias", expires_at: str = None, horizon: str = "short"):
        """Returns (Position | None, reason) — reason explains why nothing was opened."""
        risk_pct = settings.risk_per_trade * risk_factor
        tickers = self.market.tickers
        async with self.lock:
            open_docs = await self.db.positions.find({}, {"symbol": 1, "side": 1, "horizon": 1}).to_list(100)
            open_syms = [p["symbol"] for p in open_docs]
            slot = self._slot_block(open_docs, settings, horizon)
            if symbol in open_syms or slot:
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
                    await self.log("RISK", f"{symbol}: orden no enviada · cuenta {self.bk} sin conexión con MetaApi ({self.hfm['error']})")
                    return None, f"cuenta {self.bk} sin conexión con MetaApi"
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
                        reason = (f"tamaño por debajo del lote mínimo del bróker ({lot['min']:g}): con este stop arriesga el {need}% de la equidad real "
                                  f"(objetivo {risk_pct:g}%, tope {settings.min_lot_max_risk:g}%)")
                        await self.log("RISK", f"{symbol}: {reason}")
                        return None, reason
                    r = await self.broker.open(symbol, sig["side"], lots, sl, tp)
                except MetaApiError as e:
                    if e.kind == "unknown":
                        link = getattr(e, "link_id", "") or ""
                        if link:
                            await self.db.pending_orders.update_one({"_id": link}, {"$set": {
                                "symbol": symbol, "side": sig["side"], "strategy": sig["strategy"], "signal_id": signal_id, "atr": sig["atr"], "sl": sl, "tp": tp,
                                "lots": lots, "leverage": leverage, "expires_at": expires_at, "created_at": now_iso()}}, upsert=True)
                        reason = f"resultado desconocido: {e.msg[:120]}"
                        await self.log("ERROR", f"{symbol}: orden REAL con resultado desconocido · {e.msg[:160]} · si {self.bk} la ejecutó, el bot la adoptará por su identificador {link}")
                        await self.tg(f"❓ <b>Orden REAL con resultado desconocido · {symbol}</b>\n{e.msg[:250]}\nSi {self.bk} llegó a ejecutarla, el bot la detectará por su identificador "
                                      f"({link}) en cuanto MetaApi responda y la gestionará con normalidad (el SL/TP ya viaja en la orden).", "orders")
                        return None, reason
                    if e.kind == "transient":
                        reason = f"{self.bk}/MetaApi no disponible tras {e.attempts} intento(s): {e.msg[:120]}"
                        await self.log("RISK", f"{symbol}: orden REAL no enviada · {reason} · verificado: no quedó ninguna orden abierta")
                        await self._tg_transient(symbol, e)
                        return None, reason
                    await self.log("ERROR", f"{symbol}: orden REAL rechazada por {self.bk}/MetaApi · {e.msg[:260]}")
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
                    await self.log("RISK", f"{symbol}: orden REAL ejecutada en el intento {r['attempts']} tras fallos temporales de {self.bk}/MetaApi")
                entry, lots, fee = (r["entry"] or entry), r["lots"], abs(r["commission"])
                sl, tp = r["sl"], r["tp"]
                margin = lots * SPECS[symbol]["contract"] * risk.base_usd(symbol, tickers, entry) / leverage
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=lots, initial_qty=lots, leverage=leverage, margin=margin,
                               sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"], fees=fee, mark_price=price, signal_id=signal_id, expires_at=expires_at,
                               live=True, order_id=r["position_id"], link_id=r["link_id"], horizon=horizon)
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
                    reason = (f"tamaño por debajo del lote mínimo (0.01): con este stop el lote mínimo arriesga el {need}% de la equidad "
                              f"(objetivo {risk_pct:g}%, tope con lote mínimo {settings.min_lot_max_risk:g}%) · sube el capital, el tope o usa una temporalidad menor")
                    await self.log("RISK", f"{symbol}: {reason}")
                    return None, reason
                fee = risk.spread_cost(symbol, lots, tickers)
                pos = Position(symbol=symbol, side=sig["side"], strategy=sig["strategy"], entry_price=entry, qty=lots, initial_qty=lots, leverage=settings.leverage,
                               margin=margin, sl=sl, tp=tp, initial_sl=sl, atr=sig["atr"], fees=fee, mark_price=price, signal_id=signal_id, expires_at=expires_at, horizon=horizon)
                res = await self.db.positions.insert_one(pos.to_mongo())
                pos.id = str(res.inserted_id)
                await self.db.account.update_one({"_id": "main"}, {"$inc": {"balance": -fee}})
        tag = "REAL " if self.live else ""
        reduced = f" · tamaño ajustado al {int(risk_factor * 100)}% por {risk_reason}" if risk_factor != 1 else ""
        reduced += " · largo plazo (no cuenta en Máx. posiciones)" if horizon == "long" else ""
        if real_risk > risk_pct + 0.05:
            reduced += f" · lote mínimo: riesgo real {real_risk:g}% (objetivo {risk_pct:g}%)"
        f = lambda v: fmt_price(symbol, v)  # noqa: E731
        await self.log("ORDER", f"ABIERTA {tag}{sig['side'].upper()} {symbol} @ {f(entry)} · {lots:.2f} lotes · 1:{leverage} · SL {f(sl)} ({pips(symbol, abs(entry - sl)):.1f} pips) · TP {f(tp)}{reduced}")
        await self.tg(f"{'🟢' if long else '🔴'} <b>ABIERTA {tag}{sig['side'].upper()} {symbol}</b> @ {f(entry)}\n"
                      f"{lots:.2f} lotes · 1:{leverage} · margen ${margin:,.2f}\nSL {f(sl)} · TP {f(tp)}\n"
                      f"Estrategia: {FX_STRATEGY_INFO.get(sig['strategy'], {}).get('name', sig['strategy'])}{reduced}", "orders")
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
                        await self.log("RISK", f"{p.symbol}: cierre REAL ejecutado en el intento {cr_close['attempts']} tras fallos temporales de {self.bk}/MetaApi")
                except Exception as e:
                    if getattr(e, "code", "") != "TRADE_RETCODE_POSITION_CLOSED":
                        k = getattr(e, "kind", "")
                        if k == "unknown":
                            await self.log("ERROR", f"{p.symbol}: cierre REAL con resultado desconocido · {str(e)[:160]} · se comprobará en {self.bk} en la siguiente sincronización")
                            return None
                        kind = f"{self.bk}/MetaApi no disponible tras varios intentos" if k == "transient" else "cierre REAL fallido"
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
