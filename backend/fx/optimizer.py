"""Grid optimizer + walk-forward for forex/metals: the shared optimizer core with the FX simulation kit (lots, spread, weekend flat)."""
from functools import partial
from types import SimpleNamespace
import optimizer as core
from backtest import scan_signals
from fx.risk import trade_rules
from fx.backtest import build_fx_df, simulate
from fx.models import FxSettings

WF_CANDLES = core.WF_CANDLES


def kit(settings: FxSettings, tickers: dict, costs=None, fxr: float = 1.0):
    sim = partial(simulate, tickers=tickers, min_lot_max_risk=settings.min_lot_max_risk, weekend_flat=settings.close_before_weekend,
                  sessions_allowed=tuple(settings.sessions_allowed), costs=costs, fxr=fxr)
    return SimpleNamespace(build_df=build_fx_df, scan_signals=scan_signals, simulate=sim)


def optimize(klines: list, settings: FxSettings, initial: float, symbol: str, tickers: dict, costs=None, fxr: float = 1.0) -> dict:
    return core.optimize(klines, settings.strategy, settings.risk_per_trade, settings.leverage, trade_rules(settings), initial, symbol,
                         settings.signal_mode, settings.disabled_strategies, kit=kit(settings, tickers, costs, fxr))


def walk_forward(klines: list, settings: FxSettings, initial: float, symbol: str, tickers: dict, costs=None, fxr: float = 1.0) -> dict:
    return core.walk_forward(klines, settings.strategy, settings.risk_per_trade, settings.leverage, trade_rules(settings), initial, symbol,
                             settings.signal_mode, settings.disabled_strategies, settings.symbol_params.get(symbol), kit=kit(settings, tickers, costs, fxr))
