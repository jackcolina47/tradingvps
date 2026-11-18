"""Risk/execution gates for the HotForex bot: net currency exposure, the 17:00 New York rollover window, ATR volatility regime,
intraday seasonality bias (Breedon & Ranaldo) and the FOMC no-continuation rule. Every gate returns (block_reason | None, size_factor)."""
from datetime import datetime, timezone, time as dtime
from fx.symbols import SPECS, currencies
from fx.market import NY

ROLLOVER_START, ROLLOVER_END = dtime(16, 50), dtime(17, 15)   # New York local time
ROLLOVER_SPREAD_MULT = 4.0
VOL_LOOKBACK = 200
VOL_HIGH_FACTOR = 0.5
FOMC_HOURS = 24
FOMC_WORDS = ("fomc", "fed interest rate", "federal funds", "fed funds", "interest rate decision")
# local business hours in UTC (winter-ish; the effect is broad enough for a 1 h shift not to matter)
LOCAL_HOURS = {"EUR": (7, 15), "CHF": (7, 15), "GBP": (7, 15), "USD": (13, 21), "CAD": (13, 21), "JPY": (0, 8), "AUD": (22, 6), "NZD": (22, 6)}


# ---------- rollover ----------
def in_rollover(now: datetime = None) -> bool:
    t = (now or datetime.now(timezone.utc)).astimezone(NY).time()
    return ROLLOVER_START <= t <= ROLLOVER_END


def rollover_block(enabled: bool, now: datetime = None):
    if enabled and in_rollover(now):
        return "ventana de rollover (17:00 Nueva York): spreads ampliados y liquidez mínima"
    return None


# ---------- volatility regime ----------


# ---------- intraday seasonality ----------
def _in_hours(hour: int, span: tuple) -> bool:
    a, b = span
    return a <= hour < b if a < b else (hour >= a or hour < b)


def seasonality_bias(symbol: str, now: datetime = None) -> str:
    """'short' while the base currency is in its local hours (it tends to depreciate), 'long' during the quote currency's hours, else 'both'."""
    base, quote = currencies(symbol)
    if base not in LOCAL_HOURS or quote not in LOCAL_HOURS:
        return "both"
    h = (now or datetime.now(timezone.utc)).hour
    b, q = _in_hours(h, LOCAL_HOURS[base]), _in_hours(h, LOCAL_HOURS[quote])
    if b and not q:
        return "short"
    if q and not b:
        return "long"
    return "both"


# ---------- currency exposure ----------
def exposure_counts(open_docs: list) -> dict:
    """Net directional count per currency: long EURUSD = +1 EUR, −1 USD."""
    out = {}
    for p in open_docs:
        if p["symbol"] not in SPECS:
            continue
        base, quote = currencies(p["symbol"])
        sign = 1 if p["side"] == "long" else -1
        out[base] = out.get(base, 0) + sign
        out[quote] = out.get(quote, 0) - sign
    return out


def exposure_block(open_docs: list, symbol: str, side: str, limit: int):
    """Blocks when the new trade would push any currency's net directional count beyond `limit`."""
    counts = exposure_counts(open_docs)
    base, quote = currencies(symbol)
    sign = 1 if side == "long" else -1
    for cur, delta in ((base, sign), (quote, -sign)):
        new = counts.get(cur, 0) + delta
        if abs(new) > limit and abs(new) > abs(counts.get(cur, 0)):
            return f"exposición neta en {cur} llegaría a {abs(new)} posiciones en la misma dirección (tope {limit})"
    return None


# ---------- FOMC ----------


