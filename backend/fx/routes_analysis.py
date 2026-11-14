"""/api/fx/* · analysis: backtest, optimizer, walk-forward (+ weekly re-validation), AI advisor/optimize-all, growth plan, weekly summary, FX gates context."""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException

from models import OptimizeRequest, PlanRequest, AdviseRequest
from ai_advisor import friendly_error
from fx.models import FxBacktestRequest, FX_TIMEFRAMES, FX_STRATEGY_KEYS
from fx import filters as fx_filters
from fx.risk import trade_rules
from fx.strategies_slow import fix_window, last_business_day
from fx.symbols import SPECS, AVAILABLE, pips
from fx.market import LONDON
from fx.backtest import run_backtest
from strategies import EMA_TEST
from fx.optimizer import optimize, walk_forward, WF_CANDLES
from fx import review as fx_review
from fx import plan as fx_plan
from fx import weekly as fx_weekly
import wfcheck

logger = logging.getLogger(__name__)


def register(router, engine, store, news):
    async def _initial_balance() -> float:
        acc = await engine.db.account.find_one({"_id": "main"}, {"initial_balance": 1})
        return acc["initial_balance"]

    @router.post("/backtest")
    async def backtest(req: FxBacktestRequest):
        if req.symbol not in AVAILABLE or req.timeframe not in FX_TIMEFRAMES or req.strategy not in FX_STRATEGY_KEYS + [EMA_TEST]:
            raise HTTPException(400, "Parámetros inválidos")
        s = await engine.get_settings()
        tf = req.timeframe
        params = dict(s.symbol_params.get(req.symbol) or {})
        if req.strategy == EMA_TEST and s.ema_test_adx_filter:
            params["ema_test_adx_min"] = s.ema_test_adx_min
        if req.strategy == "fix_reversion":
            if SPECS[req.symbol]["group"] == "Metales":
                raise HTTPException(400, "La reversión del fix de fin de mes no opera metales")
            tf = "1h"
            klines = await engine.market.fetch_klines(req.symbol, "1h", 1000, cache_sec=600)
            while klines and len(klines) < 6000:  # ≈ 1 year of hourly candles → ~12 month-ends
                older = await engine.market.fetch_before(req.symbol, "1h", klines[0][0] // 1000, 1000)
                if not older:
                    break
                klines = older + klines
        else:
            klines = await engine.market.fetch_klines(req.symbol, req.timeframe, 1000, cache_sec=60)
        result = await asyncio.to_thread(run_backtest, klines, req.strategy, req.risk_per_trade, req.leverage, trade_rules(s), await _initial_balance(),
                                         req.symbol, s.signal_mode, s.disabled_strategies, dict(engine.market.tickers), s.min_lot_max_risk, s.close_before_weekend,
                                         tuple(s.sessions_allowed), engine.costs, params or None)
        result.pop("r_multiples", None)
        return {"symbol": req.symbol, "strategy": req.strategy, "timeframe": tf, "params": params or None, "spread_pips": round(pips(req.symbol, engine.costs.spread(req.symbol)), 1), "commission_lot_usd": engine.costs.commission_lot_usd,
                "sessions_allowed": s.sessions_allowed, **result}

    @router.post("/optimize")
    async def optimize_symbol(req: OptimizeRequest):
        if req.symbol not in AVAILABLE:
            raise HTTPException(400, "Símbolo inválido")
        s = await engine.get_settings()
        klines = await engine.market.fetch_klines(req.symbol, s.timeframe, 1000, cache_sec=300)
        res = await asyncio.to_thread(optimize, klines, s, await _initial_balance(), req.symbol, dict(engine.market.tickers), engine.costs)
        return {**res, "timeframe": s.timeframe, "applied": s.symbol_params.get(req.symbol)}

    @router.post("/optimize/walk-forward")
    async def optimize_walk_forward(req: OptimizeRequest):
        if req.symbol not in AVAILABLE:
            raise HTTPException(400, "Símbolo inválido")
        s = await engine.get_settings()
        klines = await engine.market.fetch_klines(req.symbol, s.timeframe, WF_CANDLES, cache_sec=600)
        res = await asyncio.to_thread(walk_forward, klines, s, await _initial_balance(), req.symbol, dict(engine.market.tickers), engine.costs)
        return {**res, "timeframe": s.timeframe, "applied_params": s.symbol_params.get(req.symbol)}

    def _check_payload():
        return {"job": wfcheck.job_state(engine.wf_kit), "auto_remove": store.cfg.wf_auto_remove}

    @router.get("/walk-forward/check")
    async def walk_forward_check_status():
        return {**_check_payload(), "latest": await wfcheck.latest(engine.db)}

    @router.post("/walk-forward/check")
    async def walk_forward_check_run():
        if not await wfcheck.start_check(engine, engine.db, store, "manual", engine.wf_kit):
            raise HTTPException(409, "Ya hay una revalidación en curso")
        return {**_check_payload(), "latest": await wfcheck.latest(engine.db)}

    @router.post("/ai/optimize-all")
    async def ai_optimize_all():
        if not await fx_review.start(engine, engine.db):
            raise HTTPException(409, "Ya hay una optimización global en curso")
        return fx_review.job_state(engine)

    @router.get("/ai/optimize-all")
    async def ai_optimize_all_status():
        return fx_review.job_state(engine)

    @router.post("/ai/advise")
    async def ai_advise(req: AdviseRequest):
        if req.scope not in ("optimizer", "plan"):
            raise HTTPException(400, "Ámbito inválido")
        try:
            return await fx_review.advise_scope(engine, req.scope, req.optimizer_results)
        except Exception as e:
            logger.exception("fx advise")
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")

    @router.post("/ai/auto-review")
    async def ai_auto_review_now():
        try:
            return await fx_review.run_auto_review(engine, engine.db, store)
        except Exception as e:
            logger.exception("fx auto review")
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")

    @router.post("/plan")
    async def growth_plan(req: PlanRequest):
        settings = await engine.get_settings()
        if req.scenario not in fx_plan.SCENARIOS and req.scenario != "backtest":
            req.scenario = "base"
        if (settings.plan_scenario, settings.plan_target_monthly, settings.plan_monthly_deposit) != (req.scenario, req.target_monthly, req.monthly_deposit):
            settings = await engine.update_settings({"plan_scenario": req.scenario, "plan_target_monthly": req.target_monthly, "plan_monthly_deposit": req.monthly_deposit})
        return fx_plan.build_plan(req.model_dump(), await fx_plan.plan_stats(engine, settings, req.initial_capital))

    @router.get("/plan/checkin")
    async def plan_checkin():
        return await engine.checkin()

    @router.get("/weekly-summary")
    async def weekly_summary_preview():
        w = await fx_weekly.build(engine, engine.db)
        last = await engine.db.weekly.find_one({"_id": "latest"}, {"_id": 0, "sent_at": 1})
        return {**w, "text": fx_weekly.prefix(engine) + fx_weekly.fmt(w), "last_sent_at": (last or {}).get("sent_at")}

    @router.post("/weekly-summary/send")
    async def weekly_summary_send():
        try:
            return await fx_weekly.send(engine, engine.db, force=True)
        except Exception as e:
            raise HTTPException(424, str(e)[:200])

    @router.get("/market/context")
    async def market_context():
        """FX gates dashboard: USD regime, net exposure, volatility regimes, seasonality bias, rollover window, FOMC and the next month-end fix."""
        s = await engine.get_settings()
        ctx = await fx_review.gates_context(engine, s)
        now = datetime.now(timezone.utc)
        ldn = now.astimezone(LONDON).date()
        d = ldn
        for _ in range(45):
            if last_business_day(d) and (d > ldn or (d == ldn and now.astimezone(LONDON).hour < 16)):
                break
            d += timedelta(days=1)
        ev = fx_filters.fomc_event(news.latest.get("events", []) if news and news.latest else [])
        ctx["fomc"].update(evento_activo=({"title": ev["title"], "time": ev["time"]} if ev else None))
        ctx["fix_reversion"] = {"enabled": "fix_reversion" not in s.disabled_strategies and s.strategy in ("all", "fix_reversion"), "window_open": bool(fix_window(now)),
                                "next_fix_london": f"{d.isoformat()} 16:00", "pairs": [x for x in s.symbols if SPECS[x]["group"] != "Metales"]}
        ctx["watch"] = {sym: {k: engine.analysis.get(sym, {}).get(k) for k in ("vol_pct", "vol_regime", "season_bias")} for sym in s.symbols}
        ctx["open_positions"] = [{"symbol": p["symbol"], "side": p["side"], "horizon": p.get("horizon", "short")} for p in await engine.db.positions.find({}, {"symbol": 1, "side": 1, "horizon": 1}).to_list(50)]
        return ctx
