import math

FEE_RATE = 0.00055
MAKER_FEE = 0.0002
SLIPPAGE = 0.0002
MIN_NOTIONAL = 5.0
# Bybit USDT perpetuals: (min order qty, qty step)
LOT_SIZES = {
    "BTCUSDT": (0.001, 0.001), "ETHUSDT": (0.01, 0.01), "SOLUSDT": (0.1, 0.1), "XRPUSDT": (1, 1),
    "BNBUSDT": (0.01, 0.01), "DOGEUSDT": (1, 1), "AVAXUSDT": (0.1, 0.1), "LINKUSDT": (0.1, 0.1),
    "ADAUSDT": (1, 1), "NEARUSDT": (0.1, 0.1), "SUIUSDT": (10, 10), "LTCUSDT": (0.1, 0.1),
}


def lot_qty(symbol: str, qty: float, lot: tuple = None) -> float:
    min_qty, step = lot or LOT_SIZES.get(symbol, (0.001, 0.001))
    qty = math.floor(qty / step + 1e-9) * step
    return round(qty, 8) if qty >= min_qty else 0.0


def position_size(equity: float, available: float, risk_pct: float, entry: float, sl: float, leverage: int,
                  symbol: str = None, lot: tuple = None):
    dist = abs(entry - sl)
    if dist <= 0 or entry <= 0:
        return 0.0, 0.0
    qty = (equity * risk_pct / 100) / dist
    max_notional = available * leverage * 0.95
    qty = min(qty, max_notional / entry)
    if symbol or lot:
        qty = lot_qty(symbol, qty, lot)
    if qty * entry < MIN_NOTIONAL:
        return 0.0, 0.0
    return qty, qty * entry / leverage


def pnl(side: str, entry: float, price: float, qty: float) -> float:
    return (price - entry) * qty if side == "long" else (entry - price) * qty


def check_exit(side: str, sl: float, tp: float, high: float, low: float):
    if side == "long":
        if low <= sl:
            return sl, "SL"
        if high >= tp:
            return tp, "TP"
    else:
        if high >= sl:
            return sl, "SL"
        if low <= tp:
            return tp, "TP"
    return None, None


def update_trailing(side: str, entry: float, sl: float, initial_sl: float, atr: float, price: float,
                    be_r: float = 1.0, trail_r: float = 1.5, trail_atr: float = 1.0, be_enabled: bool = True, trail_enabled: bool = True):
    """Breakeven at +be_r R (only if `be_enabled`), ATR trailing (trail_atr × ATR) from +trail_r R (only if `trail_enabled`). Returns (new_sl, breakeven_flag)."""
    r = abs(entry - initial_sl)
    if r <= 0:
        return sl, False
    move = (price - entry) if side == "long" else (entry - price)
    new_sl = sl
    if side == "long":
        if be_enabled and move >= be_r * r:
            new_sl = max(new_sl, entry * (1 + 2 * FEE_RATE))
        if trail_enabled and move >= trail_r * r:
            new_sl = max(new_sl, price - trail_atr * atr)
    else:
        if be_enabled and move >= be_r * r:
            new_sl = min(new_sl, entry * (1 - 2 * FEE_RATE))
        if trail_enabled and move >= trail_r * r:
            new_sl = min(new_sl, price + trail_atr * atr)
    be = (new_sl >= entry) if side == "long" else (new_sl <= entry)
    return new_sl, be


DEFAULT_RULES = {"trailing_enabled": True, "breakeven_enabled": True, "be_start_r": 1.0, "trail_start_r": 1.5, "trail_atr_mult": 1.0,
                 "partial_tp_enabled": False, "partial_tp_r": 1.5, "partial_tp_pct": 50}


def be_allowed(rules: dict, partial_done: bool) -> bool:
    """Breakeven is a separate toggle and only kicks in once the position has banked its partial take-profit."""
    return bool(rules["breakeven_enabled"] and partial_done)


def trade_rules(settings) -> dict:
    """Exit-management rules (breakeven + trailing + partial take-profit) from a Settings object; missing fields fall back to the classic constants."""
    if isinstance(settings, dict):
        return {**DEFAULT_RULES, **settings}
    if isinstance(settings, bool):
        return {**DEFAULT_RULES, "trailing_enabled": settings}
    return {k: getattr(settings, k, v) for k, v in DEFAULT_RULES.items()}


def move_in_r(side: str, entry: float, initial_sl: float, price: float) -> float:
    r = abs(entry - initial_sl)
    if r <= 0:
        return 0.0
    return ((price - entry) if side == "long" else (entry - price)) / r


def tp_in_r(side: str, entry: float, initial_sl: float, tp: float) -> float:
    r = abs(entry - initial_sl)
    return abs(tp - entry) / r if r > 0 else 0.0


def partial_qty(symbol: str, qty: float, pct: int, lot: tuple = None) -> float:
    """Quantity to close at the partial take-profit; 0 when either leg would fall below the exchange minimum lot."""
    min_qty, step = lot or LOT_SIZES.get(symbol, (0.001, 0.001))
    part = math.floor(qty * pct / 100 / step + 1e-9) * step
    part = round(part, 8)
    if part < min_qty or round(qty - part, 8) < min_qty:
        return 0.0
    return part
