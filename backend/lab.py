"""Bybit Lab: paper-only clone of the crypto engine for strategy research. It trades ONLY the «Test cruce EMA 9/21» mode, has its own
collections (lab_*) and shares the market data of the main Bybit bot. No REAL mode, no AI, no news, no regular strategies."""
import asyncio
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from bson import ObjectId

import chart
import notify
from engine import TradingEngine
from fx.engine_core import PrefixedDB
from models import Settings, SettingsUpdate, Position, Trade, Signal, LogEntry, ResetRequest, BacktestRequest, AVAILABLE_SYMBOLS, TIMEFRAMES, now_iso
from market import TF_SECONDS
from indicators import build_df, trend_label
from strategies import ema_test, ema_test_params, EMA_TEST, STRATEGY_INFO
from backtest import run_backtest
from risk import trade_rules
from performance import performance

NAME = "Bybit Lab"
DEFAULT_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


class LabEngine(TradingEngine):
    def __init__(self, db, main: TradingEngine, key: str = "lab", name: str = NAME, source: "LabEngine" = None, timeframe: str = None, capital: float = None):
        """`source`: another lab whose current settings seed this clone on its first start (with `timeframe` and `capital` overriding)."""
        super().__init__(PrefixedDB(db, f"{key}_"))
        self.bot_key, self.name, self.tg_prefix = key, name, f"🧪 <b>{name}</b>\n"
        self.source, self.seed_timeframe, self.seed_capital = source, timeframe, capital
        self.market = main.market  # shared tickers and candle cache: no extra requests for prices

    async def apply_config(self, cfg):
        self.cfg = cfg  # always paper: no broker, no REAL mode

    async def init(self):
        if not await self.db.settings.find_one({"_id": "main"}):
            seed = (await self.source.get_settings()).model_dump() if self.source else Settings(ema_test_enabled=True, ema_test_symbols=DEFAULT_PAIRS, symbols=DEFAULT_PAIRS).model_dump()
            seed.update(bot_running=False, **({"timeframe": self.seed_timeframe} if self.seed_timeframe else {}))
            await self.db.settings.insert_one({"_id": "main", **seed})
        if not await self.db.account.find_one({"_id": "main"}):
            await self._new_account(*([self.seed_capital] if self.seed_capital else []))
        self.running = (await self.get_settings()).bot_running
        async for s in self.db.signals.find({}, {"key": 1}).sort("_id", -1).limit(500):
            if s.get("key"):
                self.signal_keys.add(s["key"])
        await self.log("INFO", f"{self.name} iniciado · solo paper · test cruce EMA · bot {'ACTIVO' if self.running else 'DETENIDO'}")
        self.tasks = [asyncio.create_task(self._monitor_loop()), asyncio.create_task(self._scan_loop())]

    async def shutdown(self):
        for t in self.tasks:
            t.cancel()

    async def update_settings(self, patch: dict, source: str = "manual", summary: str = "", extra: dict = None) -> Settings:
        current = (await self.get_settings()).model_dump()
        current.update({k: v for k, v in patch.items() if v is not None})
        s = Settings.model_validate(current)
        await self.db.settings.update_one({"_id": "main"}, {"$set": s.model_dump()})
        await self.log("INFO", "Configuración actualizada")
        self.scan_event.set()
        return s

    async def stop(self):
        self.running = False
        await self.db.settings.update_one({"_id": "main"}, {"$set": {"bot_running": False}})
        await self.log("INFO", "Bot DETENIDO · las posiciones abiertas siguen protegidas por su SL de seguridad")
        await self.tg("⏹ <b>Bot DETENIDO</b> · paper", "orders")

    def _after_close(self, trade: Trade):
        pass  # research bot: no AI analysis, no learned rules

    async def checkin(self, settings=None, summary=None) -> dict:
        return {"message": "", "warnings": [], "ready_to_advance": False}

    async def _monitor_once(self):
        tickers = self.market.tickers  # refreshed every 5 s by the main Bybit engine
        settings = await self.get_settings()
        rules = trade_rules(settings)
        async for doc in self.db.positions.find():
            p = Position.from_mongo(doc)
            t = tickers.get(p.symbol)
            if t:
                await self._manage_paper_position(p, t["price"], rules)
        await self._update_account_metrics(settings)

    async def _update_account_metrics(self, settings: Settings):
        s = await self.account_summary()
        acc = await self.db.account.find_one({"_id": "main"})
        upd = {}
        if acc["day"] != time.strftime("%Y-%m-%d", time.gmtime()):
            upd.update(day=time.strftime("%Y-%m-%d", time.gmtime()), day_start_equity=s["equity"], daily_paused=False)
        elif not acc["daily_paused"] and s["daily_pnl_pct"] <= -settings.daily_loss_limit:
            upd["daily_paused"] = True
            await self.log("RISK", f"Límite de pérdida diaria ({settings.daily_loss_limit}%) alcanzado · nuevas entradas pausadas hasta mañana (UTC)")
        peak = max(acc["peak_equity"], s["equity"])
        upd.update(peak_equity=peak, max_drawdown=max(acc["max_drawdown"], (peak - s["equity"]) / peak * 100 if peak > 0 else 0))
        await self.db.account.update_one({"_id": "main"}, {"$set": upd})
        if time.time() - self._last_snapshot >= 60:
            self._last_snapshot = time.time()
            await self.db.equity.insert_one({"ts": now_iso(), "equity": s["equity"], "balance": s["balance"], "net_deposits": 0.0})

    candle_sync, ai_gate = False, False  # only the test cruce EMA: fixed-interval scan

    async def scan_once(self, settings: Settings, normal: bool = True):
        self.scanning = True
        found = 0
        test_syms = set(settings.ema_test_symbols) if settings.ema_test_enabled else set()
        open_test = {d["symbol"] for d in await self.db.positions.find({}, {"symbol": 1}).to_list(100)}
        try:
            for symbol in list(dict.fromkeys([*settings.ema_test_symbols, *open_test])):
                try:
                    df = build_df(await self.market.fetch_klines(symbol, settings.timeframe, 300))
                    i = len(df) - 2
                    row = df.iloc[i]
                    if self.running:
                        await self._exit_on_cross(symbol, df, i)
                    tsig = ema_test(df, i, params=ema_test_params(settings)) if symbol in test_syms else None
                    self.analysis[symbol] = {"trend": trend_label(row), "rsi": round(float(row["rsi"]), 1), "macd_hist": float(row["macd_hist"]),
                                             "adx": round(float(row["adx"]), 1), "htf": None, "signal": tsig["side"] if tsig else None,
                                             "signal_strategy": EMA_TEST if tsig else None, "updated_at": now_iso()}
                    if tsig and self.running:
                        found += 1
                        await self._handle_test_signal(symbol, tsig, settings, df)
                except Exception as e:
                    await self.log("ERROR", f"{symbol}: {e}")
            self.last_scan = now_iso()
            if self.running:
                await self.log("SCAN", f"Escaneo {settings.timeframe} completado · {len(test_syms)} pares en test · {found} cruces")
        finally:
            self.scanning = False


def build_router(lab: LabEngine, require_auth) -> APIRouter:
    r = APIRouter(prefix=f"/api/{lab.bot_key}", dependencies=[Depends(require_auth)])
    db = lab.db

    def status() -> dict:
        return {"running": lab.running, "scanning": lab.scanning, "provider": lab.market.provider, "last_scan": lab.last_scan,
                "next_scan_in": max(0, int(lab.next_scan_ts - time.time())) if lab.next_scan_ts else None,
                "mode": "PAPER", "env": "paper", "market": "USDT Perpetual", "telegram": notify.is_on(), "ai": None, "ai_usage": None,
                "exchange_error": None, "broker_health": None, "paper_only": True}

    async def docs(col, model, limit):
        return [model.from_mongo(d).model_dump() async for d in db[col].find().sort("_id", -1).limit(limit)]

    @r.get("/meta")
    async def meta():
        return {"symbols": AVAILABLE_SYMBOLS, "timeframes": TIMEFRAMES, "strategies": [EMA_TEST], "strategy_keys": [], "strategy_info": STRATEGY_INFO,
                "long_horizon_keys": [], "signal_modes": [], "mode_info": {}, "profiles": []}

    @r.get("/overview")
    async def overview():
        acc = await lab.account_summary()
        return {"running": lab.running, "mode": "PAPER", "equity": acc["equity"], "total_return_pct": acc["total_return_pct"], "open_positions": acc["open_positions"]}

    @r.get("/dashboard")
    async def dashboard():
        settings = await lab.get_settings()
        watch = [{"symbol": s, **{k: lab.market.tickers.get(s, {}).get(k) for k in ("price", "change_pct", "volume_usdt", "funding_rate")}, **lab.analysis.get(s, {})}
                 for s in settings.ema_test_symbols]
        rows = await db.equity.find({}, {"_id": 0}).sort("ts", -1).limit(1440).to_list(1440)
        rows.reverse()
        return {"status": status(), "settings": settings.model_dump(), "account": await lab.account_summary(), "positions": await docs("positions", Position, 100),
                "trades": await docs("trades", Trade, 50), "signals": await docs("signals", Signal, 30), "logs": await docs("logs", LogEntry, 100),
                "watchlist": watch, "equity": rows[::max(1, len(rows) // 300)]}

    @r.get("/klines")
    async def klines(symbol: str, tf: str = "15m", limit: int = chart.DEFAULT_LIMIT, before: int | None = Query(None, ge=1)):
        if symbol not in AVAILABLE_SYMBOLS or tf not in [*TIMEFRAMES, "1d"]:
            raise HTTPException(400, "Símbolo o temporalidad inválida")
        limit = chart.clamp(limit)
        n = limit + chart.WARMUP
        rows = await lab.market.fetch_before(symbol, tf, before * 1000, n) if before else await lab.market.fetch_klines(symbol, tf, n)
        return await chart.build(db, symbol, tf, rows, TF_SECONDS[tf], limit, before, lab.market.provider or "")

    @r.post("/bot/start")
    async def bot_start():
        await lab.start()
        return status()

    @r.post("/bot/stop")
    async def bot_stop():
        await lab.stop()
        return status()

    @r.post("/bot/scan")
    async def bot_scan():
        lab.scan_event.set()
        return {"ok": True}

    @r.get("/settings")
    async def get_settings():
        return (await lab.get_settings()).model_dump()

    @r.put("/settings")
    async def put_settings(body: SettingsUpdate):
        try:
            return (await lab.update_settings(body.model_dump())).model_dump()
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.post("/positions/{pos_id}/close")
    async def close_pos(pos_id: str):
        trade = await lab.close_position(pos_id, reason="MANUAL") if ObjectId.is_valid(pos_id) else None
        if not trade:
            raise HTTPException(404, "Posición no encontrada")
        return trade.model_dump()

    @r.post("/positions/close-all")
    async def close_all():
        ids = [str(d["_id"]) async for d in db.positions.find({}, {"_id": 1})]
        closed = [pid for pid in ids if await lab.close_position(pid, reason="MANUAL")]
        return {"closed": len(closed), "failed": len(ids) - len(closed)}

    @r.get("/trades")
    async def trades(limit: int = 100):
        return await docs("trades", Trade, min(limit, 500))

    @r.post("/account/reset")
    async def reset(body: ResetRequest = None):
        await lab.reset_account(body.initial_balance if body else None)
        return await lab.account_summary()

    @r.get("/performance")
    async def perf(tz: int = 0):
        return await performance(db, tz_min=tz)

    @r.delete("/logs")
    async def clear_logs():
        await db.logs.delete_many({})
        return {"ok": True}

    @r.post("/backtest")
    async def backtest(req: BacktestRequest):
        if req.symbol not in AVAILABLE_SYMBOLS or req.timeframe not in TIMEFRAMES:
            raise HTTPException(400, "Parámetros inválidos")
        acc = await db.account.find_one({"_id": "main"}, {"initial_balance": 1})
        s = await lab.get_settings()
        params = ema_test_params(s)
        df = build_df(await lab.market.fetch_klines(req.symbol, req.timeframe, 1000, cache_sec=60))
        result = run_backtest(df, EMA_TEST, req.risk_per_trade, req.leverage, trade_rules(s), initial=acc["initial_balance"], symbol=req.symbol, params=params or None)
        result.pop("r_multiples", None)
        return {"symbol": req.symbol, "strategy": EMA_TEST, "timeframe": req.timeframe, "params": params or None, **result}

    return r
