"""Chart payload shared by the crypto and FX `/klines` endpoints: candles + indicator lines computed over a warm-up window (so EMA200/RSI are
exact from the first visible candle), trade/position markers and infinite history (`before` = unix seconds, exclusive → older candles)."""
from datetime import datetime
from indicators import build_df
from models import Trade, Position

WARMUP = 220
MAX_LIMIT = 700
DEFAULT_LIMIT = 300
LINE_KEYS = ("ema9", "ema10", "ema20", "ema21", "ema50", "ema200", "bb_up", "bb_mid", "bb_low", "vwap", "rsi", "macd", "macd_signal", "adx", "gap", "atr")
EMPTY_KEYS = ("candles", "volume", "macd_hist", *LINE_KEYS, "markers", "lines")


def clamp(limit: int) -> int:
    return min(max(int(limit), 100), MAX_LIMIT)


CANDLE_CLOSE_REASONS = ("CRUCE", "CIERRE_EMA21", "TOQUE_EMA")  # exits decided by a candle that just closed: the order goes out seconds into the NEXT candle


def _snap(iso: str, step: int, back: int = 0) -> int:
    ts = int(datetime.fromisoformat(iso).timestamp()) - back
    return ts - ts % step


def price_digits(symbol: str, last_close: float) -> int:
    """Decimals for the price axis/crosshair: broker digits for FX/metals (EURUSD 5, USDJPY 3, XAUUSD 2), by magnitude for crypto."""
    try:
        from fx.symbols import SPECS
        if symbol in SPECS:
            return int(SPECS[symbol]["digits"])
    except ImportError:
        pass
    p = abs(float(last_close or 0))
    return 2 if p >= 1000 else 3 if p >= 10 else 4 if p >= 1 else 5 if p >= 0.01 else 6


async def build(db, symbol: str, tf: str, rows: list, step: int, limit: int, before: int = None, source: str = "") -> dict:
    """`rows` are ascending klines (ms) ending at `before` (or now) and should include ~WARMUP extra candles; only the last `limit` are returned."""
    if not rows:
        return {"symbol": symbol, "tf": tf, "source": source, "has_more": False, "digits": price_digits(symbol, 0), **{k: [] for k in EMPTY_KEYS}}
    df = build_df(rows)
    # daily-anchored VWAP (UTC); spot FX feeds report no volume → equal weight per candle
    w = df["volume"].where(df["volume"] > 0, 1.0) if df["volume"].fillna(0).sum() > 0 else df["volume"] * 0 + 1.0
    day = df["ts"] // 86_400_000
    df["vwap"] = ((df["high"] + df["low"] + df["close"]) / 3 * w).groupby(day).cumsum() / w.groupby(day).cumsum()
    if before is not None:
        df = df[(df["ts"] // 1000) < before]
    has_more = len(df) > limit
    df = df.iloc[-limit:]
    times = (df["ts"] // 1000).astype(int).tolist()
    if not times:
        return {"symbol": symbol, "tf": tf, "source": source, "has_more": False, **{k: [] for k in EMPTY_KEYS}}
    first, end = times[0], times[-1] + step

    def line(col):  # NaN → whitespace point, so every series stays index-aligned with the candles
        return [{"time": t, "value": round(float(v), 8)} if v == v else {"time": t} for t, v in zip(times, df[col].tolist())]

    df = df.assign(gap=((df["ema9"] - df["ema21"]) / df["atr"].where(df["atr"] > 0)).fillna(0.0), adx=df["adx"].fillna(0.0))  # EMA9−EMA21 in ATR units (test cruce EMA)
    candles = [{"time": t, "open": o, "high": h, "low": lo, "close": c} for t, o, h, lo, c in zip(times, df["open"], df["high"], df["low"], df["close"])]
    volume = [{"time": t, "value": v, "color": "rgba(16,185,129,0.35)" if c >= o else "rgba(239,68,68,0.35)"}
              for t, v, o, c in zip(times, df["volume"], df["open"], df["close"])]
    markers = []

    def mark(iso, position, color, shape, text, back=0):
        t = _snap(iso, step, back)
        if first <= t < end:
            markers.append({"time": t, "position": position, "color": color, "shape": shape, "text": text})

    async for d in db.trades.find({"symbol": symbol}).sort("_id", -1).limit(300):
        t = Trade.from_mongo(d)
        long = t.side == "long"
        mark(t.opened_at, "belowBar" if long else "aboveBar", "#10B981" if long else "#EF4444", "arrowUp" if long else "arrowDown", "LONG" if long else "SHORT")
        mark(t.closed_at, "aboveBar" if long else "belowBar", "#F7A600", "circle", f"{t.reason} {t.pnl:+.1f}",
             back=120 if t.reason in CANDLE_CLOSE_REASONS else 0)  # drawn on the candle whose close triggered the exit, not on the one that had just opened
    lines = []
    async for d in db.positions.find({"symbol": symbol}):
        p = Position.from_mongo(d)
        long = p.side == "long"
        mark(p.opened_at, "belowBar" if long else "aboveBar", "#10B981" if long else "#EF4444", "arrowUp" if long else "arrowDown", "LONG" if long else "SHORT")
        if before is None:
            lines = [{"price": p.entry_price, "title": "ENTRADA", "color": "#F8FAFC"}, {"price": p.sl, "title": "SL", "color": "#EF4444"},
                     {"price": p.tp, "title": "TP", "color": "#10B981"}]
    markers.sort(key=lambda m: m["time"])
    return {"symbol": symbol, "tf": tf, "source": source, "has_more": has_more, "digits": price_digits(symbol, candles[-1]["close"] if candles else 0),
            "candles": candles, "volume": volume, **{k: line(k) for k in LINE_KEYS},
            "macd_hist": [{"time": t, "value": round(float(v), 8), "color": "rgba(16,185,129,0.55)" if v >= 0 else "rgba(239,68,68,0.55)"} for t, v in zip(times, df["macd_hist"].fillna(0).tolist())],
            "markers": markers, "lines": lines}
