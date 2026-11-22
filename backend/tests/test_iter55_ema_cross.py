"""Iteration 55 — strategy «Cruce EMA 10/20» (ema_cross) in the three bots: off by default (and in every profile), exit on the opposite cross."""
import os
import numpy as np
import pytest
import requests

from indicators import build_df
from strategies import evaluate, exit_signal, STRATEGIES, STRATEGY_INFO, MODES, EXIT_REASON
from backtest import run_backtest
from models import STRATEGY_KEYS, Settings
from fx.models import FxSettings
from profiles import PROFILES as CRYPTO_PROFILES
from fx.profiles import PROFILES as FX_PROFILES

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
BOTS = {"crypto": f"{BASE_URL}/api", "fx": f"{BASE_URL}/api/fx", "pepper": f"{BASE_URL}/api/pepper"}


@pytest.fixture(scope="module")
def s():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"}, timeout=30)
    assert r.status_code == 200, r.text
    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return sess


def _df(seed=3, n=1500):
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 3, 0.0008), np.full(n // 3, -0.0008), np.full(n - 2 * (n // 3), 0.0006)])
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    op = np.roll(close, 1)
    op[0] = close[0]
    ts = np.arange(n) * 3600_000 + 1_700_000_000_000
    return build_df(list(zip(ts.tolist(), op.tolist(), high.tolist(), low.tolist(), close.tolist(), rng.uniform(100, 200, n).tolist())))


def test_registry_and_defaults():
    assert "ema_cross" in STRATEGIES and "ema_cross" in STRATEGY_INFO and STRATEGY_KEYS[-1] == "ema_cross"
    assert all(k in MODES[m] for m in MODES for k in ("xc_adx", "xc_ema200", "xc_stack"))
    assert "ema_cross" in Settings().disabled_strategies and "ema_cross" in FxSettings().disabled_strategies
    for p in CRYPTO_PROFILES + FX_PROFILES:  # the user switches it on by hand; no profile enables it
        assert "ema_cross" in p["settings"]["disabled_strategies"], p["name"]


def test_indicators_and_signal_rules():
    df = _df()
    assert {"ema10", "ema20"} <= set(df.columns)
    sigs = [evaluate(df, i, "ema_cross", None, None, m) for m in ("strict", "relaxed", "minimal") for i in range(210, len(df))]
    sigs = [x for x in sigs if x]
    assert sigs, "no ema_cross signals on trending synthetic data"
    for x in sigs:
        r = df.loc[df["ts"] == x["key_ts"]].iloc[0]
        long = x["side"] == "long"
        assert (r["ema10"] > r["ema20"]) if long else (r["ema10"] < r["ema20"])
        assert x["strategy"] == "ema_cross" and x["tp_mode"] == "atr"
        assert abs(abs(x["sl"] - x["price"]) - 2 * x["atr"]) < 1e-6 and abs(abs(x["tp"] - x["price"]) - 6 * x["atr"]) < 1e-6
        assert any("EMA10 cruza" in t for t in x["reasons"]) and "ema10" in x["indicators"] and "ema20" in x["indicators"]
    strict = sum(1 for i in range(210, len(df)) if evaluate(df, i, "ema_cross", None, None, "strict"))
    minimal = sum(1 for i in range(210, len(df)) if evaluate(df, i, "ema_cross", None, None, "minimal"))
    assert strict < minimal
    # disabled → never evaluated; htf against → filtered
    assert not any(evaluate(df, i, "all", None, None, "minimal", ("ema_cross",)) and evaluate(df, i, "all", None, None, "minimal", ("ema_cross",))["strategy"] == "ema_cross" for i in range(210, len(df)))


def test_exit_rule_and_backtest():
    assert exit_signal("ema_cross", "long", {"ema10": 1.0, "ema20": 2.0}) and not exit_signal("ema_cross", "long", {"ema10": 2.0, "ema20": 1.0})
    assert exit_signal("ema_cross", "short", {"ema10": 2.0, "ema20": 1.0}) and not exit_signal("ema_cross", "short", {"ema10": 1.0, "ema20": 2.0})
    assert not exit_signal("trend_ema", "long", {"ema10": 1.0, "ema20": 2.0})
    res = run_backtest(_df(), "ema_cross", 1.0, 3, False, 10000.0, "BTCUSDT", None, "minimal", ())
    reasons = {t["reason"] for t in res["trades"]}
    assert res["total_trades"] > 5 and EXIT_REASON in reasons and set(res["strategy_stats"]) == {"ema_cross"}


@pytest.mark.parametrize("bot", list(BOTS))
def test_api_meta_default_off_and_toggle(s, bot):
    base = BOTS[bot]
    m = s.get(f"{base}/meta", timeout=30).json()
    assert "ema_cross" in m["strategy_keys"] and m["strategy_info"]["ema_cross"]["name"] == "Cruce EMA 10/20"
    st = s.get(f"{base}/settings", timeout=30).json()
    assert "ema_cross" in st["disabled_strategies"], "must be off by default / after migration"
    on = [k for k in st["disabled_strategies"] if k != "ema_cross"]
    r = s.put(f"{base}/settings", json={"disabled_strategies": on}, timeout=30)
    assert r.status_code == 200 and "ema_cross" not in r.json()["disabled_strategies"]
    r = s.put(f"{base}/settings", json={"disabled_strategies": st["disabled_strategies"]}, timeout=30)
    assert r.status_code == 200 and "ema_cross" in r.json()["disabled_strategies"]


@pytest.mark.parametrize("bot,symbol", [("crypto", "BTCUSDT"), ("fx", "EURUSD"), ("pepper", "XAUUSD")])
def test_api_backtest_ema_cross(s, bot, symbol):
    r = s.post(f"{BOTS[bot]}/backtest", json={"symbol": symbol, "strategy": "ema_cross", "timeframe": "1h"}, timeout=180)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_trades"] >= 0 and set(d["strategy_stats"]) <= {"ema_cross"}
    assert all(t["reason"] in ("TP", "SL", "TRAILING", "CRUCE", "FIN", "FIN_SEMANA", "TIEMPO") for t in d["trades"])


def test_profile_keeps_it_off(s):
    base = BOTS["pepper"]
    st = s.get(f"{base}/settings", timeout=30).json()
    r = s.post(f"{base}/profile", json={"level": 3}, timeout=30)
    assert r.status_code == 200, r.text
    assert "ema_cross" in s.get(f"{base}/settings", timeout=30).json()["disabled_strategies"]
    keep = {k: st[k] for k in ("symbols", "timeframe", "signal_mode", "htf_filter", "ai_enabled", "ai_min_confidence", "risk_per_trade",
                                "max_positions", "leverage", "daily_loss_limit", "disabled_strategies", "risk_profile") if k in st}
    if st.get("risk_profile") is not None and st["risk_profile"] in range(5):
        assert s.post(f"{base}/profile", json={"level": st["risk_profile"]}, timeout=30).status_code == 200
    r = s.put(f"{base}/settings", json={k: v for k, v in keep.items() if k != "risk_profile"}, timeout=30)
    assert r.status_code == 200, r.text
