from datetime import datetime, timezone
import numpy as np
from strategies import evaluate, exit_signal, EXIT_REASON, CROSS_ONLY
from risk import FEE_RATE, position_size, pnl, check_exit, update_trailing, trade_rules, partial_qty, tp_in_r


def _iso(ts_ms) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def scan_signals(df, strategy: str, params: dict = None, mode: str = "strict", disabled=()) -> dict:
    """Signal detection is independent of SL/TP → scan once, simulate many times."""
    out, used = {}, set()
    for i in range(210, len(df)):
        sig = evaluate(df, i, strategy, None, params, mode, disabled)
        if sig and (sig["strategy"], sig["key_ts"]) not in used:
            used.add((sig["strategy"], sig["key_ts"]))
            out[i] = sig
    return out


def _levels(sig, sl_atr, tp_atr):
    if sl_atr is None:
        return sig["sl"], sig["tp"]
    long, p, atr = sig["side"] == "long", sig["price"], sig["atr"]
    sl = p - sl_atr * atr if long else p + sl_atr * atr
    if sig.get("tp_mode") == "fixed":
        tp = sig["tp"]
        if abs(tp - p) < abs(p - sl):
            return None, None
    else:
        tp = p + tp_atr * atr if long else p - tp_atr * atr
    return sl, tp


def simulate(df, signals: dict, risk_pct: float, leverage: int, trailing, initial: float,
             symbol: str = None, sl_atr: float = None, tp_atr: float = None, start: int = 210, end: int = None) -> dict:
    """`trailing` is either a bool (classic rules) or a rules dict from risk.trade_rules (trailing + partial take-profit)."""
    rules = trade_rules(trailing)
    use_trailing, use_partial = rules["trailing_enabled"], rules["partial_tp_enabled"]
    highs, lows, closes, ts = df["high"].values, df["low"].values, df["close"].values, df["ts"].values
    e10, e20 = df["ema10"].values, df["ema20"].values
    balance, peak, max_dd = initial, initial, 0.0
    trades, curve, pos, rejected = [], [], None, 0
    n = len(df)
    end = end or n

    def close(price, reason, i):
        nonlocal balance, pos
        if reason == "SL" and pos["be"]:
            reason = "TRAILING"
        fee = price * pos["qty"] * FEE_RATE
        gross = pnl(pos["side"], pos["entry"], price, pos["qty"])
        net = gross + pos["partial_gross"] - pos["fee"] - fee
        balance += gross - fee
        tp_r = tp_in_r(pos["side"], pos["entry"], pos["init_sl"], pos["tp"])
        trades.append({"side": pos["side"], "strategy": pos["strategy"], "entry": pos["entry"], "exit": float(price),
                       "pnl": round(float(net), 2), "r": round(float(net / pos["risk_amt"]), 3), "reason": reason,
                       "mfe_r": round(pos["mfe"], 2), "mae_r": round(pos["mae"], 2), "mfe_tp_pct": round(pos["mfe"] / tp_r * 100, 1) if tp_r else 0.0,
                       "partial": pos["partial"], "opened_at": pos["opened_at"], "closed_at": _iso(ts[i])})
        pos = None

    for i in range(start, end):
        if pos:
            r_dist = abs(pos["entry"] - pos["init_sl"])
            fav = (highs[i] - pos["entry"]) if pos["side"] == "long" else (pos["entry"] - lows[i])
            adv = (pos["entry"] - lows[i]) if pos["side"] == "long" else (highs[i] - pos["entry"])
            pos["mfe"], pos["mae"] = max(pos["mfe"], fav / r_dist), max(pos["mae"], adv / r_dist)
            cross_only = pos["strategy"] in CROSS_ONLY
            price, reason = (None, None) if cross_only else check_exit(pos["side"], pos["sl"], pos["tp"], highs[i], lows[i])
            if price is not None:
                close(price, reason, i)
            elif exit_signal(pos["strategy"], pos["side"], e10[i], e20[i]):
                close(closes[i], EXIT_REASON, i)
            elif cross_only:
                pass
            else:
                if use_partial and not pos["partial"] and fav >= rules["partial_tp_r"] * r_dist:
                    part = partial_qty(symbol, pos["qty"], rules["partial_tp_pct"]) if symbol else round(pos["qty"] * rules["partial_tp_pct"] / 100, 8)
                    if part > 0:
                        level = pos["entry"] + rules["partial_tp_r"] * r_dist * (1 if pos["side"] == "long" else -1)
                        fee = level * part * FEE_RATE
                        gross = pnl(pos["side"], pos["entry"], level, part)
                        balance += gross - fee
                        pos.update(qty=round(pos["qty"] - part, 8), fee=pos["fee"] + fee, partial_gross=pos["partial_gross"] + gross, partial=True)
                if use_trailing or rules["breakeven_enabled"]:
                    pos["sl"], pos["be"] = update_trailing(pos["side"], pos["entry"], pos["sl"], pos["init_sl"], pos["atr"], closes[i],
                                                           rules["be_start_r"], rules["trail_start_r"], rules["trail_atr_mult"],
                                                           be_enabled=rules["breakeven_enabled"] and pos["partial"], trail_enabled=use_trailing)
        if not pos and i in signals:
            sig = signals[i]
            sl, tp = _levels(sig, sl_atr, tp_atr)
            if sl is not None:
                qty, _ = position_size(balance, balance, risk_pct, sig["price"], sl, leverage, symbol)
                if qty > 0:
                    fee = sig["price"] * qty * FEE_RATE
                    balance -= fee
                    pos = {"side": sig["side"], "strategy": sig["strategy"], "entry": sig["price"], "sl": sl, "init_sl": sl,
                           "tp": tp, "atr": sig["atr"], "qty": qty, "fee": fee, "risk_amt": qty * abs(sig["price"] - sl),
                           "be": False, "mfe": 0.0, "mae": 0.0, "partial": False, "partial_gross": 0.0, "opened_at": _iso(ts[i])}
                else:
                    rejected += 1
        if pos and i == end - 1 and end < n:
            close(closes[i], "FIN", i)
        equity = balance + (pnl(pos["side"], pos["entry"], closes[i], pos["qty"]) if pos else 0)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0)
        if i % 5 == 0 or i == end - 1:
            curve.append({"time": _iso(ts[i]), "equity": round(float(equity), 2)})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    final = curve[-1]["equity"] if curve else initial
    R = np.array([t["r"] for t in trades]) if trades else np.zeros(0)
    sqn = float(R.mean() / R.std() * np.sqrt(len(R))) if len(R) > 1 and R.std() > 0 else 0.0
    near_miss = [t for t in trades if t["reason"] != "TP" and t["mfe_tp_pct"] >= 70]
    by_strategy = {}
    for t in trades:
        s = by_strategy.setdefault(t["strategy"], {"trades": 0, "wins": 0, "pnl": 0.0, "r_sum": 0.0, "gw": 0.0, "gl": 0.0})
        s["trades"] += 1
        s["wins"] += int(t["pnl"] > 0)
        s["pnl"] += t["pnl"]
        s["r_sum"] += t["r"]
        s["gw" if t["pnl"] > 0 else "gl"] += abs(t["pnl"])
    return {
        "initial": initial,
        "final_equity": round(final, 2),
        "return_pct": round((final - initial) / initial * 100, 2),
        "total_trades": len(trades),
        "rejected_lot": rejected,
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else 0),
        "max_drawdown": round(max_dd, 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
        "expectancy_r": round(float(R.mean()), 3) if len(R) else 0,
        "sqn": round(sqn, 2),
        "excursion": {"avg_mfe_r": round(float(np.mean([t["mfe_r"] for t in trades])), 2) if trades else 0,
                      "avg_mae_r": round(float(np.mean([t["mae_r"] for t in trades])), 2) if trades else 0,
                      "near_miss": len(near_miss), "near_miss_pct": round(len(near_miss) / len(trades) * 100, 1) if trades else 0,
                      "partial_closes": sum(1 for t in trades if t["partial"])},
        "period_start": _iso(ts[start]) if n > start else None,
        "period_end": _iso(ts[end - 1]),
        "candles": n,
        "equity_curve": curve,
        "r_multiples": [t["r"] for t in trades],
        "strategy_stats": {k: {f: round(v, 3) for f, v in s.items()} for k, s in by_strategy.items()},
        "trades": trades[-50:][::-1],
    }


def run_backtest(df, strategy: str, risk_pct: float, leverage: int, trailing=True,
                 initial: float = 10000.0, symbol: str = None, params: dict = None, mode: str = "strict", disabled=()) -> dict:
    return simulate(df, scan_signals(df, strategy, params, mode, disabled), risk_pct, leverage, trailing, initial, symbol)
