"""HotForex AI Trading engine: 24/7 paper trading of forex & metals with the same pipeline as the crypto bot
(strategies → risk gates → news gate → Claude validation → paper execution), plus forex hours, weekend flatting and news trades.

Core = lifecycle, config, settings and the monitor loop; the rest lives in mixins:
  engine_account.py   account summary, re-base, plan check-in, daily metrics
  engine_positions.py paper tick, REAL sync/adoption, open/close
  engine_signals.py   scan loop, gates, AI validation, fix scan, news trades
"""
import time
import asyncio
from datetime import datetime, timezone
from models import Position, LogEntry
import notify
from fx.models import FxSettings
from fx.market import FxMarket, market_open, closing_soon
from fx import risk
from fx import filters
from fx.live import FxLiveBroker, is_our_link
from fx.shadow import monitor_shadows
import evolution
import position_review
import broker_health
from broker_health import fmt_duration, fresh_health as _fresh_health  # noqa: F401 (re-exported for tests)
from fx.engine_core import (logger, INITIAL_BALANCE, LIVE_GRACE_SEC, HFM_HEALTH_SEC, HFM_HEALTH_RETRY_SEC, TRANSIENT_TG_SEC, PENDING_TTL_SEC,  # noqa: F401
                            HFM_OK_STATES, HFM_OK_CONN, BLOCK_BY, HORIZON_LABEL, NEWS_STRATEGY, FX_STRATEGY_INFO, today, PrefixedDB)
from fx.engine_account import AccountMixin
from fx.engine_positions import PositionsMixin
from fx.engine_signals import SignalsMixin
from fx.broker import HFM, BrokerProfile
from fx import review as fx_review
from fx import wfcheck as fx_wfcheck


class FxEngine(AccountMixin, PositionsMixin, SignalsMixin):
    def __init__(self, db, profile: BrokerProfile = HFM, market: FxMarket = None):
        self.profile = profile
        self.db = PrefixedDB(db, profile.prefix)
        self.market = market or FxMarket()
        self.costs = profile.costs()
        self.review_job = fx_review.new_job()
        self.wf_job = fx_wfcheck.new_job()
        self.wf_kit = fx_wfcheck.make_kit(self)
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
        self._closed_logged = False
        self._weekend_flat_day = None
        self.news = None
        self.cfg = None
        self.live = False
        self.broker = None
        self._broker_key = None
        self.broker_error = None
        self._transient_tg_ts = 0.0
        self._external_warned = set()
        self.store = None
        self.hfm = _fresh_health()
        self._last_equity = None
        self._cash_checked = 0.0

    async def apply_config(self, cfg):
        self.cfg = cfg
        self.live = cfg.trading_mode == "live"
        self.costs = self.profile.costs(cfg.account_type)
        key = (cfg.metaapi_token, cfg.metaapi_account_id, cfg.metaapi_region, cfg.metaapi_base_url, cfg.mt_symbol_suffix)
        if key != self._broker_key:
            if self.broker:
                await self.broker.aclose()
            self.broker = FxLiveBroker(*key, symbol_map=cfg.mt_symbol_map, suffixes=self.profile.suffixes) if cfg.metaapi_token and cfg.metaapi_account_id else None
            self._broker_key = key
            self.broker_error = None
            self.hfm = _fresh_health()
        elif self.broker and self.broker.symbol_map != cfg.mt_symbol_map:
            self.broker.symbol_map = dict(cfg.mt_symbol_map)
        if not self.live:
            self.hfm = _fresh_health()

    def broker_health(self) -> dict | None:
        if not (self.live and self.broker):
            return None
        return broker_health.summary(self.hfm, self.cfg.hfm_alert_after_sec if self.cfg else None)

    @property
    def mode_label(self) -> str:
        return "REAL" if self.live else "PAPER"

    @property
    def env(self) -> str:
        return (self.cfg.mt_server if self.cfg and self.cfg.mt_server else self.bk)

    @property
    def bk(self) -> str:
        """Broker short name for user-facing messages (HFM / Pepperstone)."""
        return getattr(self, "profile", HFM).broker

    @property
    def bl(self) -> str:
        """Settings section label (Ajustes → HotForex / Pepperstone)."""
        return getattr(self, "profile", HFM).broker_long

    def new_broker(self) -> FxLiveBroker:
        c = self.cfg
        return FxLiveBroker(c.metaapi_token, c.metaapi_account_id, c.metaapi_region, c.metaapi_base_url, c.mt_symbol_suffix, c.mt_symbol_map, self.profile.suffixes)

    async def tg(self, text: str, kind: str):
        await notify.notify(f"{self.profile.tg_icon} <b>{self.profile.tg_name}</b>\n{text}", kind)

    async def init(self):
        await self.market.detect()
        if not await self.db.settings.find_one({"_id": "main"}):
            await self.db.settings.insert_one({"_id": "main", **FxSettings().model_dump()})
        if not await self.db.account.find_one({"_id": "main"}):
            await self._new_account()
        self.running = (await self.get_settings()).bot_running
        async for s in self.db.signals.find({}, {"key": 1}).sort("_id", -1).limit(500):
            if s.get("key"):
                self.signal_keys.add(s["key"])
        await self.log("INFO", f"Motor {self.bl} iniciado · datos Yahoo Finance · mercado {'ABIERTO' if market_open() else 'CERRADO'} · bot {'ACTIVO' if self.running else 'DETENIDO'}")
        self.tasks = [asyncio.create_task(self._monitor_loop()), asyncio.create_task(self._scan_loop())]

    async def shutdown(self):
        for t in self.tasks:
            t.cancel()
        if getattr(self.market, "_shared_owner", self) is self:
            await self.market.aclose()
        if self.broker:
            await self.broker.aclose()

    async def get_settings(self) -> FxSettings:
        return FxSettings.model_validate(await self.db.settings.find_one({"_id": "main"}))

    async def update_settings(self, patch: dict, source: str = "manual", summary: str = "", extra: dict = None) -> FxSettings:
        current = (await self.get_settings()).model_dump()
        before = dict(current)
        current.update({k: v for k, v in patch.items() if v is not None})
        s = FxSettings.model_validate(current)
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
        if self.live and self.account_ref:
            await self.db.account.update_one({"_id": "main", "$or": [{"account_ref": {"$exists": False}}, {"account_ref": ""}]}, {"$set": {"account_ref": self.account_ref}})
        mode = f"REAL en {self.bk} ({self.env})" if self.live else "paper trading"
        await self.log("INFO", f"Bot ACTIVADO · forex y metales en {mode}" + ("" if market_open() else " · el mercado está cerrado, operará al abrir"))
        await self.tg(f"▶️ <b>Bot ACTIVADO</b> · {mode} forex/metales", "orders")
        self.scan_event.set()

    async def stop(self):
        self.running = False
        await self.db.settings.update_one({"_id": "main"}, {"$set": {"bot_running": False}})
        await self.log("INFO", "Bot DETENIDO · las posiciones abiertas siguen protegidas por SL/TP")
        await self.tg("⏹ <b>Bot DETENIDO</b> · las posiciones abiertas siguen protegidas por SL/TP", "orders")

    # ---------- monitor ----------
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
        settings = await self.get_settings()
        tickers = await self.market.fetch_tickers(settings.symbols)
        now = datetime.now(timezone.utc)
        if settings.close_before_weekend and closing_soon(30) and self._weekend_flat_day != today():
            self._weekend_flat_day = today()
            ids = [str(d["_id"]) async for d in self.db.positions.find({}, {"_id": 1})]
            if ids:
                await self.log("RISK", f"Cierre de fin de semana · {len(ids)} posiciones cerradas antes del cierre del mercado")
                for pid in ids:
                    await self.close_position(pid, reason="FIN_SEMANA")
        ex = None
        if self.live and self.broker:
            try:
                ex = {p["id"]: p for p in await self.broker.positions()}
                self.broker_error = None
            except Exception as e:
                self.broker_error = str(e)[:160]
                logger.warning(f"hfm positions sync: {e}")
            await self._hfm_health(ex is not None)
        local_ids = set()
        rules = risk.trade_rules(settings)
        rollover = settings.rollover_guard and filters.in_rollover(now)
        async for doc in self.db.positions.find():
            p = Position.from_mongo(doc)
            if p.live:
                local_ids.add(p.order_id)
                if ex is not None:
                    await self._sync_live_position(p, ex.get(p.order_id), tickers.get(p.symbol), settings, now, rules)
                continue
            t = tickers.get(p.symbol)
            if not t:
                continue
            await self._manage_paper_position(p, t["price"], rules, now, rollover)
        if ex:
            for pid, xp in ex.items():
                if pid in local_ids:
                    continue
                if is_our_link(xp.get("client_id")):
                    await self._adopt_live(pid, xp, settings, tickers)
                elif pid not in self._external_warned:
                    self._external_warned.add(pid)
                    await self.log("RISK", f"{self.broker.our_symbol(xp['symbol'])}: posición externa detectada en {self.bk} (no gestionada por el bot) · {xp['volume']:.2f} lotes")
        if ex is not None:
            await self._expire_pending(now)
        await monitor_shadows(self, tickers, settings, now)
        await self._update_account_metrics(settings)
        if market_open(now) and position_review.due(self, settings):
            await position_review.review_open_positions(self, settings, "USD", self.profile.bot_name)

    async def _hfm_health(self, synced: bool):
        """Watches the MetaApi account (terminal state + client API) in REAL mode; alerts once per outage, reminds every 6 h, confirms recovery."""
        h, now = self.hfm, time.time()
        if now - h["checked_at"] >= (HFM_HEALTH_SEC if h["ok"] is not False else HFM_HEALTH_RETRY_SEC):
            h["checked_at"] = now
            try:
                acc = await self.broker.api.account(self.broker.account_id)
                h.update(state=acc.get("state") or "", connection=acc.get("connectionStatus") or "", api_error=None)
                if h["connection"] == "CONNECTED":
                    self.broker.chart_fail_ts = 0.0
            except Exception as e:
                h["api_error"] = str(e)[:160]
                logger.warning(f"hfm account state: {e}")
            if self.store and h["state"] and (h["state"], h["connection"]) != (self.cfg.hfm_state, self.cfg.hfm_connection):
                await self.store.update({"hfm_state": h["state"], "hfm_connection": h["connection"]})
        ok = synced and h["state"] in HFM_OK_STATES and h["connection"] in HFM_OK_CONN
        error = (self.broker_error if not synced else None) or h["api_error"] or f"MetaApi: cuenta {h['state'] or '?'} · conexión {h['connection'] or '?'}"
        event, dur = broker_health.transition(h, ok, error, self.cfg.hfm_alert_after_sec, now)
        if event == "up":
            await self.log("INFO", f"Conexión con {self.bk} restablecida · estuvo sin conexión {fmt_duration(dur)}")
            await self.tg(f"✅ <b>Conexión con {self.bk} restablecida</b> · estuvo sin conexión {fmt_duration(dur)}. El bot vuelve a operar con normalidad.", "system")
        elif event:
            remind = event == "remind"
            n = await self.db.positions.count_documents({"live": True})
            await self.log("ERROR", f"Cuenta {self.bk} sin conexión con MetaApi desde hace {fmt_duration(dur)} · {h['error']}")
            hint = f"\nSi persiste, revisa la cuenta en Ajustes → {self.bl} (Probar conexión / Redesplegar)." if (h["state"] not in HFM_OK_STATES or "DISCONNECTED" in h["connection"]) else ""
            await self.tg(f"🔌 <b>Cuenta {self.bk} {'sigue ' if remind else ''}sin conexión con MetaApi</b> · {fmt_duration(dur)}\n{h['error']}\n"
                          f"{n} posición(es) real(es) abiertas: siguen protegidas por su SL/TP en el bróker. El bot no abrirá nuevas operaciones hasta reconectar.{hint}", "system")
