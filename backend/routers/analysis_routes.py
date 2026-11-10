"""Analysis routes for the crypto bot: growth plan + check-in, per-pair optimizer and backtest."""
from fastapi import HTTPException

from models import BacktestRequest, PlanRequest, OptimizeRequest, AVAILABLE_SYMBOLS, TIMEFRAMES, CRYPTO_STRATEGY_KEYS
from indicators import build_df
from backtest import run_backtest, simulate
from risk import trade_rules
from strategies_slow import SLOW_INFO, scan_slow
from plan import build_plan, SCENARIOS
from optimizer import optimize
from review import plan_stats
import housekeeping
from deps import db, engine, protected_router

router = protected_router()


@router.get("/housekeeping")
async def housekeeping_status():
    """Last weekly re-optimisation and pruning state (crypto bot)."""
    reopt = await db.meta.find_one({"_id": "reopt"}, {"_id": 0}) or {}
    prune = await db.meta.find_one({"_id": "prune"}, {"_id": 0}) or {}
    return {"reopt": reopt, "prune": prune}


@router.post("/housekeeping/reopt")
async def housekeeping_reopt():
    """Runs the weekly re-optimisation now for the configured pairs."""
    settings = await engine.get_settings()
    if not settings.reopt_symbols:
        raise HTTPException(400, "No hay pares seleccionados para re-optimizar")
    if housekeeping._lock.locked():
        raise HTTPException(409, "Ya hay una re-optimización en curso")
    async with housekeeping._lock:
        return await housekeeping.reoptimize(engine, settings, "manual")


@router.post("/housekeeping/prune")
async def housekeeping_prune():
    """Runs the pruning check now (warns / switches off according to the 30-day stats)."""
    return await housekeeping.prune(engine, await engine.get_settings())


@router.post("/plan")
async def growth_plan(req: PlanRequest):
    settings = await engine.get_settings()
    if req.scenario not in SCENARIOS and req.scenario != "backtest":
        req.scenario = "base"
    if (settings.plan_scenario, settings.plan_target_monthly, settings.plan_monthly_deposit) != (req.scenario, req.target_monthly, req.monthly_deposit):
        await engine.update_settings({"plan_scenario": req.scenario, "plan_target_monthly": req.target_monthly,
                                      "plan_monthly_deposit": req.monthly_deposit})
    return build_plan(req.model_dump(), await plan_stats(engine, settings, req.initial_capital))


@router.get("/plan/checkin")
async def plan_checkin():
    return await engine.checkin()


@router.post("/optimize")
async def optimize_symbol(req: OptimizeRequest):
    if req.symbol not in AVAILABLE_SYMBOLS:
        raise HTTPException(400, "Símbolo inválido")
    s = await engine.get_settings()
    acc = await db.account.find_one({"_id": "main"}, {"initial_balance": 1})
    klines = await engine.market.fetch_klines(req.symbol, s.timeframe, 1000, cache_sec=300)
    res = optimize(klines, s.strategy, s.risk_per_trade, s.leverage, trade_rules(s), acc["initial_balance"], req.symbol, s.signal_mode, s.disabled_strategies)
    return {**res, "timeframe": s.timeframe, "applied": s.symbol_params.get(req.symbol)}


@router.post("/backtest")
async def backtest(req: BacktestRequest):
    if req.symbol not in AVAILABLE_SYMBOLS or req.strategy not in CRYPTO_STRATEGY_KEYS or (req.strategy not in SLOW_INFO and req.timeframe not in TIMEFRAMES):
        raise HTTPException(400, "Parámetros inválidos")
    acc = await db.account.find_one({"_id": "main"}, {"initial_balance": 1})
    s = await engine.get_settings()
    if req.strategy in SLOW_INFO:
        # long-horizon strategies run on their own timeframe (1d / 4h) with as much history as the exchange serves
        tf = SLOW_INFO[req.strategy]["tf"]
        df = build_df(await engine.market.fetch_history(req.symbol, tf, 1500 if tf == "1d" else 1000, cache_sec=300))
        result = simulate(df, scan_slow(df, req.strategy, s.symbol_params.get(req.symbol)), req.risk_per_trade, req.leverage, trade_rules(s),
                          acc["initial_balance"], req.symbol)
        result.pop("r_multiples", None)
        return {"symbol": req.symbol, "strategy": req.strategy, "timeframe": tf, **result}
    params = s.symbol_params.get(req.symbol) or {}
    df = build_df(await engine.market.fetch_klines(req.symbol, req.timeframe, 1000, cache_sec=60), int(params.get("atr_period", 14)))
    result = run_backtest(df, req.strategy, req.risk_per_trade, req.leverage, trade_rules(s), initial=acc["initial_balance"], symbol=req.symbol,
                          params=params or None, mode=s.signal_mode, disabled=s.disabled_strategies)
    result.pop("r_multiples", None)
    return {"symbol": req.symbol, "strategy": req.strategy, "timeframe": req.timeframe, "params": params or None, **result}
