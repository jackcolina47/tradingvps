import numpy as np
import pandas as pd


def build_df(klines: list, atr_period: int = 14) -> pd.DataFrame:
    df = pd.DataFrame(klines, columns=["ts", "open", "high", "low", "close", "volume"])
    return add_indicators(df, atr_period)


def _rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    c, h, low, v = df["close"], df["high"], df["low"], df["volume"]
    for n in (9, 10, 20, 21, 27, 50, 200):
        df[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()

    delta = c.diff()
    gain = _rma(delta.clip(lower=0), 14)
    loss = _rma(-delta.clip(upper=0), 14)
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50)

    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    df["macd"] = macd
    df["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = macd - df["macd_signal"]

    prev_c = c.shift(1)
    tr = pd.concat([h - low, (h - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    df["atr"] = _rma(tr, atr_period)
    atr14 = _rma(tr, 14).replace(0, np.nan)

    df["bb_mid"] = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    df["bb_up"] = df["bb_mid"] + 2 * std
    df["bb_low"] = df["bb_mid"] - 2 * std

    df["vol_ratio"] = (v / v.rolling(20).mean()).fillna(1)

    up, down = h.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr = atr14
    plus_di = 100 * _rma(plus_dm, 14) / atr
    minus_di = 100 * _rma(minus_dm, 14) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = _rma(dx.fillna(0), 14)

    df["don_high"] = h.rolling(20).max().shift(1)
    df["don_low"] = low.rolling(20).min().shift(1)
    df["st"], df["st_dir"] = _supertrend(df, _rma(tr, 10), 3.0)
    return df


def _supertrend(df: pd.DataFrame, atr: pd.Series, mult: float):
    hl2 = (df["high"] + df["low"]) / 2
    up, lo, c = (hl2 + mult * atr).values.copy(), (hl2 - mult * atr).values.copy(), df["close"].values
    n = len(df)
    st, d = np.full(n, np.nan), np.zeros(n, dtype=int)
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(up[i - 1]):
            continue
        if not (up[i] < up[i - 1] or c[i - 1] > up[i - 1]):
            up[i] = up[i - 1]
        if not (lo[i] > lo[i - 1] or c[i - 1] < lo[i - 1]):
            lo[i] = lo[i - 1]
        if d[i - 1] >= 0:
            d[i] = -1 if c[i] < lo[i] else 1
        else:
            d[i] = 1 if c[i] > up[i] else -1
        st[i] = lo[i] if d[i] == 1 else up[i]
    return st, d


def snapshot(row) -> dict:
    keys = ["close", "ema9", "ema10", "ema20", "ema21", "ema50", "ema200", "rsi", "macd_hist", "adx", "atr", "vol_ratio", "bb_up", "bb_mid", "bb_low"]
    return {k: round(float(row[k]), 6) for k in keys if pd.notna(row[k])}


def trend_label(row) -> str:
    if row["ema9"] > row["ema21"] and row["close"] > row["ema200"]:
        return "ALCISTA"
    if row["ema9"] < row["ema21"] and row["close"] < row["ema200"]:
        return "BAJISTA"
    return "NEUTRAL"


def htf_trend(df: pd.DataFrame) -> int:
    row = df.iloc[-2]
    if row["close"] > row["ema50"] and row["ema21"] > row["ema50"]:
        return 1
    if row["close"] < row["ema50"] and row["ema21"] < row["ema50"]:
        return -1
    return 0
