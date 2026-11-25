"""Growth plan for HotForex: the same compounding phases as crypto with forex-realistic scenarios, backtests in lots/pips
(spread, lot-minimum and weekend/session rules of the bot) and Monte Carlo on the resulting R-multiples."""
import time
import asyncio
import plan as core
from fx.risk import trade_rules
from fx.optimizer import kit

CURRENCY = "USD"
SCENARIOS = {
    "conservador": {"name": "Prudente", "rate": 1.5, "desc": "≈20% anual · lo que sostienen los sistemas forex consistentes con riesgo bajo"},
    "base": {"name": "Realista", "rate": 3.0, "desc": "≈43% anual · bot con profit factor > 1.3 arriesgando 1% por operación"},
    "optimista": {"name": "Ambicioso", "rate": 5.0, "desc": "≈80% anual · solo en rachas de tendencia fuerte en oro y mayores, no sostenible"},
}
_cache: dict = {}


def _bt(klines: list, settings, initial: float, symbol: str, params: dict, tickers: dict, costs=None, fxr: float = 1.0) -> dict:
    k = kit(settings, tickers, costs, fxr)
    df = k.build_df(klines, int(params.get("atr_period", 14)))
    sigs = k.scan_signals(df, settings.strategy, None, settings.signal_mode, settings.disabled_strategies)
    return k.simulate(df, sigs, settings.risk_per_trade, settings.leverage, trade_rules(settings), initial, symbol, params.get("sl_atr"), params.get("tp_atr"))


async def backtest_stats(engine, settings, initial: float) -> dict:
    tickers = dict(engine.market.tickers)
    fxr = engine.fxr() if hasattr(engine, 'fxr') else 1.0
    results = []
    for sym in settings.symbols:
        params = settings.symbol_params.get(sym) or {}
        klines = await engine.market.fetch_klines(sym, settings.timeframe, 1000, cache_sec=300)
        results.append((sym, await asyncio.to_thread(_bt, klines, settings, initial, sym, params, tickers, getattr(engine, 'costs', None), fxr)))
    out = core.summarize(results, settings, ("total_pips", "avg_pips", "weekend_closed", "skipped_session"))
    out["total_pips"] = round(sum(p["total_pips"] for p in out["per_symbol"]), 1)
    out["rejected_lot"] = sum(p["rejected_lot"] for p in out["per_symbol"])
    return out


async def plan_stats(engine, settings, initial: float) -> dict:
    key = (round(engine.fxr(), 3) if hasattr(engine, 'fxr') else 1.0, getattr(getattr(engine, 'profile', None), 'key', 'fx'), str(getattr(engine, 'costs', None)), tuple(settings.symbols), settings.timeframe, settings.strategy, settings.risk_per_trade, settings.leverage, str(trade_rules(settings)),
           settings.max_positions, initial, str(settings.symbol_params), settings.signal_mode, tuple(settings.disabled_strategies),
           settings.min_lot_max_risk, settings.close_before_weekend, tuple(settings.sessions_allowed))
    hit = _cache.get(key)
    if not hit or time.time() - hit["ts"] > 300:
        hit = {"ts": time.time(), "data": await backtest_stats(engine, settings, initial)}
        _cache.clear()
        _cache[key] = hit
    return hit["data"]


def build_plan(req: dict, bt: dict, currency: str = CURRENCY) -> dict:
    return {**core.build_plan(req, bt, SCENARIOS), "currency": currency}


def _rate(settings) -> float:
    return SCENARIOS.get(settings.plan_scenario, SCENARIOS["base"])["rate"]


async def contributions(db, acc: dict, settings, currency: str = CURRENCY) -> dict:
    return await core.contributions(db, acc, settings, _rate(settings), currency)


def build_checkin(s: dict, acc: dict, last30: dict, settings, days: int, contrib: dict = None, currency: str = CURRENCY) -> dict:
    return core.build_checkin(s, acc, last30, settings, _rate(settings), days, currency, contrib)
