"""FxEngine mixin · account & statistics: paper account, HFM account summary, re-base on account switch, plan check-in, daily metrics."""
import time
from datetime import datetime, timezone
from models import now_iso
from fx.models import FxSettings
import cashflow
import maintenance
from fx import plan as fx_plan
from plan import last30_stats
from fx.engine_core import logger, INITIAL_BALANCE, today


class AccountMixin:
    async def _new_account(self, initial: float = INITIAL_BALANCE):
        await self.db.account.replace_one({"_id": "main"}, {
            "_id": "main", "initial_balance": initial, "balance": initial, "peak_equity": initial, "max_drawdown": 0.0,
            "day": today(), "day_start_equity": initial, "daily_paused": False, "created_at": now_iso(), "net_deposits": 0.0,
            "account_ref": self.account_ref if self.live else "",
        }, upsert=True)

    @property
    def account_ref(self) -> str:
        """Identity of the HFM account the statistics belong to (login@server); empty in paper."""
        return f"{self.cfg.mt_login}@{self.cfg.mt_server}" if self.cfg and self.cfg.mt_login and self.cfg.mt_server else ""

    def account_kind(self) -> str | None:
        return None if not self.live else ("demo" if "demo" in self.env.lower() else "live")

    async def rebase_account(self) -> dict:
        """Account switch (e.g. demo → real money): the new account's equity becomes the capital base while trades, counters and the
        strategy knowledge are kept. Previous trades get tagged with the old account period and the equity curve is shifted so it stays continuous."""
        info = await self._broker_info(0)
        if not info:
            raise ValueError(f"No se pudo leer la equidad de la cuenta {self.bk}: pulsa «Probar conexión» y vuelve a intentarlo")
        equity = float(info["equity"])
        acc = await self.db.account.find_one({"_id": "main"})
        old_ref = acc.get("account_ref") or "cuenta anterior"
        label = f"Periodo anterior · {old_ref}"
        async with self.lock:
            tagged = await self.db.trades.update_many({"$or": [{"period": {"$exists": False}}, {"period": ""}]}, {"$set": {"period": label}})
            last = await self.db.equity.find_one({}, sort=[("ts", -1)])
            first = await self.db.equity.find_one({}, sort=[("ts", 1)])
            if last:
                nd0 = float((first or {}).get("net_deposits") or 0)
                adjusted_last = float(last["equity"]) - (float(last.get("net_deposits") or 0) - nd0)
                shift = round(equity - adjusted_last, 2)
                await self.db.equity.update_many({}, [{"$set": {"equity": {"$round": [{"$add": ["$equity", shift]}, 2]},
                                                                 "balance": {"$round": [{"$add": [{"$ifNull": ["$balance", "$equity"]}, shift]}, 2]},
                                                                 "net_deposits": 0.0}}])
            await self.db.account.update_one({"_id": "main"}, {"$set": {
                "initial_balance": equity, "balance": equity, "peak_equity": equity, "day": today(), "day_start_equity": equity,
                "daily_paused": False, "net_deposits": 0.0, "account_ref": self.account_ref, "rebased_at": now_iso(), "previous_account_ref": old_ref,
                "cash_checked_at": now_iso()}})
            await self.db.equity.insert_one({"ts": now_iso(), "equity": equity, "balance": float(info["balance"]), "net_deposits": 0.0})
        self._last_equity = equity
        self._cash_checked = time.time()
        cur = info.get("currency", "USD")
        await self.log("INFO", f"Cuenta {self.bk} cambiada a {self.account_ref} · capital inicial re-basado a {equity:,.2f} {cur} · "
                               f"{tagged.modified_count} operaciones anteriores etiquetadas «{label}» · historial y contadores conservados")
        await self.tg(f"🔁 <b>Cuenta {self.bk} cambiada</b> · {self.account_ref}\nCapital inicial re-basado a {equity:,.2f} {cur}. "
                      f"Historial conservado: {tagged.modified_count} operaciones del periodo anterior ({old_ref}).", "orders")
        return {"equity": equity, "currency": cur, "tagged": tagged.modified_count, "account_ref": self.account_ref, "previous_account_ref": old_ref}

    async def reset_account(self, initial: float = None):
        if not initial and self.live:
            info = await self._broker_info(0)
            initial = info["equity"] if info else (self.cfg.hfm_verified_equity if self.cfg else None)
        initial = initial or INITIAL_BALANCE
        async with self.lock:
            for col in ("positions", "trades", "signals", "equity", "news_trades", "shadow_positions", "shadow_trades", "cash_flows", "pending_orders"):
                await self.db[col].delete_many({})
            self._trade_totals = None
            await self._new_account(initial)
        await self.log("INFO", f"Estadísticas reiniciadas · capital inicial {initial:,.2f} {self.ccy} · modo {self.mode_label}")

    async def _broker_info(self, max_age: float = 10.0):
        if not (self.live and self.broker):
            return None
        try:
            info = await self.broker.info(max_age)
            self.broker_error = None
            return info
        except Exception as e:
            self.broker_error = str(e)[:160]
            logger.warning(f"hfm info: {e}")
            return None

    async def account_summary(self) -> dict:
        acc = await self.db.account.find_one({"_id": "main"})
        positions = await self.db.positions.find({}, {"unrealized_pnl": 1, "margin": 1}).to_list(100)
        unreal = sum(p["unrealized_pnl"] for p in positions)
        used = sum(p["margin"] for p in positions)
        balance, equity, available = acc["balance"], acc["balance"] + unreal, acc["balance"] - used
        info = await self._broker_info()
        if info:
            balance, equity, available, unreal, used = info["balance"], info["equity"], info["available"], info["unrealised"], info["margin"]
            if info["currency"] and self.store and info["currency"].upper() != (self.cfg.hfm_currency or "").upper():
                await self.store.update({"hfm_currency": info["currency"].upper()})  # the account currency is whatever the terminal reports
        st = await maintenance.trade_totals(self)
        daily = equity - acc["day_start_equity"]
        return {
            "initial_balance": acc["initial_balance"], "balance": round(balance, 2), "equity": round(equity, 2),
            "unrealized_pnl": round(unreal, 2), "unrealized_pct": round(unreal / balance * 100, 2) if balance else 0,
            "used_margin": round(used, 2), "available": round(available, 2),
            "daily_pnl": round(daily, 2), "daily_pnl_pct": round(daily / acc["day_start_equity"] * 100, 2) if acc["day_start_equity"] else 0,
            "daily_paused": acc["daily_paused"], "total_trades": st["n"], "wins": st["wins"],
            "win_rate": round(st["wins"] / st["n"] * 100, 1) if st["n"] else 0, "realized_pnl": round(st["realized"], 2),
            "profit_factor": round(st["gw"] / abs(st["gl"]), 2) if st["gl"] else None,
            **cashflow.fields(acc, equity),
            "max_drawdown": round(acc["max_drawdown"], 2),
            "open_positions": len(positions), "mode": self.mode_label, "exchange_error": self.broker_error if self.live else None,
            "exchange_synced": info is not None, "currency": (info["currency"] if info and info["currency"] else self.ccy).upper(),
            "account_ref": acc.get("account_ref") or "", "account_kind": self.account_kind(),
            "account_changed": bool(self.live and acc.get("account_ref") and acc.get("account_ref") != self.account_ref),
        }

    async def checkin(self, settings: FxSettings = None, summary: dict = None) -> dict:
        settings = settings or await self.get_settings()
        s = summary or await self.account_summary()
        acc = await self.db.account.find_one({"_id": "main"})
        last30 = await last30_stats(self.db, s["equity"])
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(acc["created_at"])).days
        contrib = await fx_plan.contributions(self.db, acc, settings)
        return fx_plan.build_checkin(s, acc, last30, settings, days, contrib)

    async def _update_account_metrics(self, settings: FxSettings):
        s = await self.account_summary()
        if self.live and self.broker and not self.hfm["down_since"] and \
                await cashflow.poll(self, "hfm", force=cashflow.jumped(self._last_equity, s["equity"]), currency=self.ccy):
            s = await self.account_summary()
        self._last_equity = s["equity"]
        acc = await self.db.account.find_one({"_id": "main"})
        nd = float(acc.get("net_deposits") or 0.0)
        upd = {}
        if acc["day"] != today():
            upd.update(day=today(), day_start_equity=s["equity"], daily_paused=False)
            await self.log("INFO", f"Nuevo día de trading · equidad inicial {s['equity']:,.2f} {self.ccy}")
            c = await self.checkin(settings, s)
            await self.log("PLAN", c["message"] + (" · ".join([""] + c["warnings"]) if c["warnings"] else ""))
            await self.tg(f"📊 <b>Resumen diario</b>\nEquidad {s['equity']:,.2f} {self.ccy} ({s['total_return_pct']:+.2f}% total)\n"
                          f"PnL de ayer {s['daily_pnl']:+.2f} {self.ccy} ({s['daily_pnl_pct']:+.2f}%)\n"
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
