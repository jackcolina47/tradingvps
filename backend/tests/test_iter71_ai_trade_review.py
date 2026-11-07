# Iter71: AI trade post-mortem, price-action facts, periodic AI review of open positions (unit + API)
import os
import sys
import time
import random
import types

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import price_action  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ANALYZED = "6aee745e715a36d120759344"
UNANALYZED = "6aee7451d30c090fe3627972"
POS_ID = "6aee74a630b35597d99f7bf1"
DEFAULTS = {"ai_position_review_enabled": False, "ai_position_review_minutes": 30,
            "ai_position_review_mode": "auto", "ai_position_review_min_confidence": 75}


# ---------- UNIT: price_action.facts ----------
def _base(n=60, seed=1, price=100.0):
    random.seed(seed)
    out = []
    for _ in range(n):
        o = price
        c = o + random.uniform(-0.1, 0.1)
        out.append({"open": o, "high": max(o, c) + 0.05, "low": min(o, c) - 0.05, "close": c})
        price = c
    return out


class TestPriceActionUnit:
    def test_extended_move(self):
        c = _base()
        # prior swing high well above
        mid = len(c) - 20
        c[mid]["high"] = c[mid]["close"] + 50
        atr = price_action._atr(c)
        p = c[-1]["close"]
        for _ in range(3):
            body = 2.5 * atr
            c.append({"open": p, "high": p + body + 0.01, "low": p - 0.01, "close": p + body})
            p += body
        f = price_action.facts(c, "long", p, atr=atr)
        assert f["extended_move"] is True
        assert f["consecutive_candles"] == 3
        assert f["warning"] is True
        assert any("MOVIMIENTO EXTENDIDO" in s for s in f["summary"])

    def test_last_candle_against(self):
        c = _base()
        p = c[-1]["close"]
        c.append({"open": p, "high": p + 0.05, "low": p - 0.6, "close": p - 0.5})
        f = price_action.facts(c, "long", c[-1]["close"])
        assert f["consecutive_candles"] == 0
        assert any("no se persigue" in s for s in f["summary"])

    def test_near_opposing_swing(self):
        c = _base(seed=3)
        atr = price_action._atr(c)
        idx = len(c) - 10
        ref = c[-1]["close"]
        c[idx]["high"] = ref + 0.5 * atr
        for j in range(idx - 3, idx + 4):
            if j != idx:
                c[j]["high"] = min(c[j]["high"], ref + 0.4 * atr)
        f = price_action.facts(c, "long", ref, atr=atr)
        assert f["near_opposing_swing"] is True
        assert any("MUY CERCA" in s for s in f["summary"])

    def test_few_candles(self):
        f = price_action.facts(_base(n=5), "long", 100)
        assert "Sin suficientes velas" in f["summary"][0]

    def test_prompt_lines(self):
        assert price_action.prompt_lines(None) == ""
        f = price_action.facts(_base(), "long", 100)
        assert price_action.prompt_lines(f).startswith("Acción del precio antes de la entrada:")

    def test_build_prompts_include_price_action(self):
        import ai_advisor
        from fx import ai as fx_ai
        f = price_action.facts(_base(), "long", 100)
        line = price_action.prompt_lines(f).strip()
        sig = {"side": "long", "strategy": "ema", "price": 100.0, "sl": 99.0, "tp": 102.0, "score": 3,
               "reasons": ["a", "b"], "indicators": {"rsi": 55}}
        ctx = {"htf_tf": "4h", "htf_trend": "up", "change_24h": 1.0, "recent_changes": [0.1], "open_positions": 0,
               "daily_pnl_pct": 0, "price_action": f}
        assert line in ai_advisor._build_prompt("BTCUSDT", "1h", sig, ctx)
        sig_fx = dict(sig, price=1.3155, sl=1.3135, tp=1.3195)
        assert line in fx_ai._build_prompt("GBPUSD", "1h", sig_fx, ctx)


# ---------- UNIT: position_review.due ----------
class TestDueUnit:
    def _s(self, **kw):
        d = dict(ai_position_review_enabled=True, ai_enabled=True, ai_position_review_minutes=30)
        d.update(kw)
        return types.SimpleNamespace(**d)

    def test_due(self):
        import position_review as pr
        eng = types.SimpleNamespace(running=True, _ai_review_last=0.0)
        assert pr.due(eng, self._s(ai_position_review_enabled=False)) is False
        assert pr.due(types.SimpleNamespace(running=False, _ai_review_last=0.0), self._s()) is False
        assert pr.due(eng, self._s()) is True
        eng2 = types.SimpleNamespace(running=True, _ai_review_last=time.time() - 60)
        assert pr.due(eng2, self._s()) is False
        assert pr.due(types.SimpleNamespace(running=True), self._s()) is True  # no attr → due


# ---------- API ----------
@pytest.fixture(scope="module")
def s():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"}, timeout=20)
    if r.status_code != 200:
        pytest.skip("login failed")
    ses = requests.Session()
    ses.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return ses


REVIEW_KEYS = {"why_opened", "chart_context", "what_happened", "better_decision", "verdict", "lesson", "confidence",
               "price_action", "model", "at"}


class TestTradeAnalyze:
    def test_cached(self, s):
        r = s.post(f"{BASE_URL}/api/fx/trades/{ANALYZED}/analyze", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["cached"] is True
        assert REVIEW_KEYS <= set(d["review"])
        assert d["review"]["verdict"] in ("BUENA", "ACEPTABLE", "EVITABLE", "MALA")
        assert isinstance(d["review"]["price_action"], list)

    def test_detail(self, s):
        r = s.get(f"{BASE_URL}/api/fx/trades/{ANALYZED}/detail", timeout=30)
        assert r.status_code == 200
        assert r.json()["trade"].get("ai_review")

    def test_404(self, s):
        r = s.post(f"{BASE_URL}/api/fx/trades/000000000000000000000000/analyze", timeout=30)
        assert r.status_code == 404

    def test_fresh_analysis(self, s):
        r = s.post(f"{BASE_URL}/api/fx/trades/{UNANALYZED}/analyze", timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["cached"] is False
        assert REVIEW_KEYS <= set(d["review"])
        assert d["review"]["verdict"] in ("BUENA", "ACEPTABLE", "EVITABLE", "MALA")
        assert d["review"]["why_opened"] and d["review"]["lesson"]
        det = s.get(f"{BASE_URL}/api/fx/trades/{UNANALYZED}/detail", timeout=30).json()
        assert det["trade"]["ai_review"]["verdict"] == d["review"]["verdict"]


class TestSettings:
    @pytest.mark.parametrize("base", ["/api/fx", "/api"])
    def test_roundtrip_and_validation(self, s, base):
        try:
            new = {"ai_position_review_enabled": True, "ai_position_review_minutes": 15,
                   "ai_position_review_mode": "notify", "ai_position_review_min_confidence": 80}
            r = s.put(f"{BASE_URL}{base}/settings", json=new, timeout=30)
            assert r.status_code == 200, r.text
            g = s.get(f"{BASE_URL}{base}/settings", timeout=30).json()
            for k, v in new.items():
                assert g[k] == v
            for bad in ({"ai_position_review_minutes": 3}, {"ai_position_review_mode": "foo"},
                        {"ai_position_review_min_confidence": 40}):
                assert s.put(f"{BASE_URL}{base}/settings", json=bad, timeout=30).status_code == 422, bad
        finally:
            s.put(f"{BASE_URL}{base}/settings", json=DEFAULTS, timeout=30)
        g = s.get(f"{BASE_URL}{base}/settings", timeout=30).json()
        for k, v in DEFAULTS.items():
            assert g[k] == v


class TestPositionsReview:
    def test_fx_review(self, s):
        # use notify mode so the seeded paper position is never closed by the AI
        s.put(f"{BASE_URL}/api/fx/settings", json={"ai_position_review_mode": "notify"}, timeout=30)
        try:
            r = s.post(f"{BASE_URL}/api/fx/positions/ai-review", timeout=180)
        finally:
            s.put(f"{BASE_URL}/api/fx/settings", json={"ai_position_review_mode": "auto"}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True and d["count"] == 1
        rv = d["reviews"][0]
        assert rv["symbol"] == "XAUUSD"
        assert rv["action"] in ("MANTENER", "BREAKEVEN", "CERRAR")
        for k in ("confidence", "reasoning", "r_now", "mode"):
            assert k in rv
        assert rv["mode"] == "notify"
        dash = s.get(f"{BASE_URL}/api/fx/dashboard", timeout=60).json()
        pos = next(p for p in dash["positions"] if p["id"] == POS_ID)
        assert pos["ai_review"]["action"] == rv["action"]

    def test_pepper_empty_and_disabled(self, s):
        r = s.post(f"{BASE_URL}/api/pepper/positions/ai-review", timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 0
        try:
            assert s.put(f"{BASE_URL}/api/pepper/settings", json={"ai_enabled": False}, timeout=30).status_code == 200
            r = s.post(f"{BASE_URL}/api/pepper/positions/ai-review", timeout=60)
            assert r.status_code == 400
            assert "El filtro IA está desactivado" in r.json()["detail"]
        finally:
            s.put(f"{BASE_URL}/api/pepper/settings", json={"ai_enabled": True}, timeout=30)
        assert s.get(f"{BASE_URL}/api/pepper/settings", timeout=30).json()["ai_enabled"] is True
