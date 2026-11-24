# Iteration 13: Funding Rate Filter (unit funding.py, config API, engine e2e gate/reduce, AI prompt, dashboard, shadow kinds).
# Run with: pytest -n 0
import os
import sys
import asyncio
from types import SimpleNamespace

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

import funding  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PASSWORD = "BlackCat2233@"
QA_REASON = "QA funding iter13"


def cfg(**kw):
    base = dict(funding_enabled=True, funding_block_pct=0.10, funding_reduce_pct=0.05, funding_reduce=True, funding_size_factor=0.5)
    base.update(kw)
    return SimpleNamespace(**base)


def _db_run(coro_fn):
    async def go():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            return await coro_fn(c[os.environ["DB_NAME"]])
        finally:
            c.close()
    return asyncio.run(go())


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"password": PASSWORD})
    if r.status_code != 200:
        pytest.skip("login failed")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


# ---------- Unit: funding.gate ----------
class TestGate:
    def test_normal(self):
        assert funding.gate(0.0001, "long", cfg()) == (None, 1.0)

    def test_high_against_reduces(self):
        assert funding.gate(0.0006, "long", cfg()) == (None, 0.5)

    def test_extreme_against_long_blocks(self):
        r, f = funding.gate(0.0012, "long", cfg())
        assert r.startswith("funding extremo en contra") and "largos pagan" in r and f == 1.0

    def test_extreme_in_favor_short(self):
        assert funding.gate(0.0012, "short", cfg()) == (None, 1.0)

    def test_extreme_against_short(self):
        r, _ = funding.gate(-0.0012, "short", cfg())
        assert "cortos pagan" in r

    def test_negative_long_in_favor(self):
        assert funding.gate(-0.0006, "long", cfg()) == (None, 1.0)

    def test_reduce_disabled(self):
        assert funding.gate(0.0006, "long", cfg(funding_reduce=False)) == (None, 1.0)

    def test_disabled(self):
        assert funding.gate(0.005, "long", cfg(funding_enabled=False)) == (None, 1.0)

    def test_none_rate_and_cfg(self):
        assert funding.gate(None, "long", cfg()) == (None, 1.0)
        assert funding.gate(0.005, "long", None) == (None, 1.0)


# ---------- Unit: level / assess / prompt_line ----------
class TestLevelAssess:
    def test_levels(self):
        c = cfg()
        assert funding.level(0.0001, c) == "NORMAL"
        assert funding.level(0.0005, c) == "ALTO"
        assert funding.level(0.001, c) == "EXTREMO"
        assert funding.level(None, c) == "NORMAL"
        assert funding.level(-0.001, c) == "EXTREMO"

    def test_assess(self):
        c = cfg()
        assert funding.assess(0.0006, "long", c)["against"] is True
        assert funding.assess(-0.0006, "short", c)["against"] is True
        assert funding.assess(0.0006, "short", c)["against"] is False
        assert funding.assess(-0.0006, "long", c)["against"] is False
        assert funding.assess(0.0, "long", c)["pays"] is None
        a = funding.assess(0.0006, "long", c)
        assert a["rate_pct"] == 0.06 and a["annualized_pct"] == round(0.06 * 3 * 365, 1) and a["pays"] == "largos"
        assert funding.assess(None, "long", c) is None

    def test_prompt_line(self):
        c = cfg()
        assert "EN CONTRA" in funding.prompt_line(funding.assess(0.0006, "long", c))
        assert "A FAVOR" in funding.prompt_line(funding.assess(0.0006, "short", c))
        assert "no disponible" in funding.prompt_line(None)
        assert "neutro" in funding.prompt_line(funding.assess(0.0, "long", c))


# ---------- AI prompt (no AI call) ----------
class TestAiPrompt:
    def test_build_prompt(self):
        import ai_advisor
        sig = {"side": "long", "strategy": "trend_ema", "price": 100.0, "sl": 95.0, "tp": 110.0, "score": 5, "reasons": ["x"],
               "indicators": {}, "atr": 2.0}
        base_ctx = {"htf_tf": "4h", "htf_trend": "ALCISTA", "change_24h": 1.0, "funding_rate": 0.0006, "recent_changes": [0.1],
                    "open_positions": 0, "daily_pnl_pct": 0, "news": None}
        try:
            p = ai_advisor._build_prompt("ETHUSDT", "1h", sig, {**base_ctx, "funding": funding.assess(0.0006, "long", cfg())})
        except Exception as e:
            pytest.fail(f"_build_prompt failed: {e}")
        assert "nivel ALTO" in p and "EN CONTRA de la señal" in p
        p2 = ai_advisor._build_prompt("ETHUSDT", "1h", sig, base_ctx)
        assert "Funding rate: no disponible" in p2
        assert "EXTREMO en contra" in ai_advisor.SYSTEM


# ---------- Config API ----------
class TestConfigApi:
    def test_defaults(self, client):
        d = client.get(f"{BASE_URL}/api/config").json()
        assert d["funding_enabled"] is True and d["funding_block_pct"] == 0.1 and d["funding_reduce_pct"] == 0.05
        assert d["funding_reduce"] is True and d["funding_size_factor"] == 0.5

    def test_validation(self, client):
        r = client.put(f"{BASE_URL}/api/config", json={"funding_reduce_pct": 0.5})
        assert r.status_code == 422 and "umbral" in r.text.lower()
        assert client.put(f"{BASE_URL}/api/config", json={"funding_block_pct": 0.005}).status_code == 422
        assert client.put(f"{BASE_URL}/api/config", json={"funding_size_factor": 1.5}).status_code == 422
        assert client.get(f"{BASE_URL}/api/config").json()["funding_reduce_pct"] == 0.05

    def test_update_and_restore(self, client):
        try:
            r = client.put(f"{BASE_URL}/api/config", json={"funding_block_pct": 0.2, "funding_reduce_pct": 0.08})
            assert r.status_code == 200, r.text
            d = client.get(f"{BASE_URL}/api/config").json()
            assert d["funding_block_pct"] == 0.2 and d["funding_reduce_pct"] == 0.08
            assert client.put(f"{BASE_URL}/api/config", json={"funding_enabled": False}).status_code == 200
            assert client.get(f"{BASE_URL}/api/config").json()["funding_enabled"] is False
        finally:
            client.put(f"{BASE_URL}/api/config", json={"funding_block_pct": 0.1, "funding_reduce_pct": 0.05, "funding_enabled": True})
        d = client.get(f"{BASE_URL}/api/config").json()
        assert d["funding_block_pct"] == 0.1 and d["funding_reduce_pct"] == 0.05 and d["funding_enabled"] is True


# ---------- Dashboard + shadow kinds ----------
class TestDashboardShadow:
    def test_watchlist_funding_level(self, client):
        d = client.get(f"{BASE_URL}/api/dashboard").json()
        c = client.get(f"{BASE_URL}/api/config").json()
        wl = d["watchlist"]
        assert wl
        for w in wl:
            assert "funding_rate" in w and w["funding_level"] in ("NORMAL", "ALTO", "EXTREMO")
            assert w["funding_level"] == funding.level(w["funding_rate"], SimpleNamespace(**c))

    def test_shadow_kind_funding(self, client):
        import shadow
        assert "FUNDING" in shadow.KINDS

        async def ins(db):
            return (await db.shadow_trades.insert_one({
                "signal_id": "qa", "symbol": "QAUSDT", "side": "long", "strategy": "trend_ema", "timeframe": "1h", "kind": "FUNDING",
                "ai_confidence": 0, "ai_risk": "-", "entry_price": 1, "qty": 1, "leverage": 5, "margin": 1, "atr": 0.1, "fees": 0,
                "risk_factor": 1.0, "opened_at": "2026-10-20T00:00:00+00:00", "exit_price": 1, "pnl": -2.0, "pnl_pct": -2,
                "reason": QA_REASON, "closed_at": "2026-10-20T01:00:00+00:00"})).inserted_id
        oid = _db_run(ins)
        try:
            p = client.get(f"{BASE_URL}/api/performance").json()
            keys = {k["key"]: k for k in p["shadow"]["by_kind"]}
            assert "FUNDING" in keys
        finally:
            _db_run(lambda db: db.shadow_trades.delete_one({"_id": oid}))


# ---------- Engine e2e (own engine instance with mocked funding) ----------
async def _engine_e2e(db):
    from engine import TradingEngine
    from config import ConfigStore
    from indicators import build_df
    from strategies import _signal
    from bson import ObjectId

    eng = TradingEngine(db)
    store = ConfigStore(db)
    store.listeners.append(eng.apply_config)
    await store.load()
    await eng.market.detect()
    await eng.market.fetch_tickers()
    s = await eng.get_settings()
    s.ai_enabled = False
    s.max_positions = 10
    s.entry_order_type = "market"  # iter45 gates are covered by their own tests
    open_syms = {p["symbol"] for p in await db.positions.find({}, {"symbol": 1}).to_list(100)}
    free = [x for x in s.symbols if x not in open_syms and x in eng.market.tickers]
    assert len(free) >= 2, f"need 2 free symbols, got {free}"
    out = {"sym_a": free[0], "sym_c": free[1]}
    created_pos, start_oid = [], ObjectId()

    async def inject(sym, side, rate, bump):
        eng.market.tickers[sym]["funding_rate"] = rate
        eng.market.tickers[sym]["funding_interval_h"] = 8
        df = build_df(await eng.market.fetch_klines(sym, s.timeframe, 300))
        row = df.iloc[-2]
        atr, c = row["atr"], row["close"]
        sl = c - 1.5 * atr if side == "long" else c + 1.5 * atr
        tp = c + 3 * atr if side == "long" else c - 3 * atr
        sig = _signal(side, row, sl, tp, "trend_ema", [QA_REASON], 5, int(row["ts"]) + 1000 + bump)
        await eng._handle_signal(sym, sig, s, df, 0, "4h")
        return await db.signals.find_one({"symbol": sym, "reasons": QA_REASON}, sort=[("_id", -1)])

    try:
        a = out["sym_a"]
        sa = await inject(a, "long", 0.0015, 1)
        out["a_signal"] = {"ai_decision": sa["ai_decision"], "note": sa["note"]}
        sh = await db.shadow_positions.find_one({"signal_id": str(sa["_id"])})
        out["a_shadow"] = {"kind": sh["kind"], "qty": sh["qty"]} if sh else None
        out["a_pos"] = await db.positions.count_documents({"symbol": a})
        out["a_log"] = bool(await db.logs.find_one({"_id": {"$gt": start_oid}, "level": "RISK",
                                                     "message": {"$regex": f"^{a}: señal descartada · funding extremo en contra"}}))
        mid_oid = ObjectId()
        sb = await inject(a, "long", 0.0007, 2)
        pb = await db.positions.find_one({"symbol": a, "signal_id": str(sb["_id"])})
        if pb:
            created_pos.append(pb)
        out["b_signal"] = sb["ai_decision"]
        out["b_qty"] = pb["qty"] if pb else None
        lb = await db.logs.find_one({"_id": {"$gt": mid_oid}, "level": "ORDER", "message": {"$regex": f"ABIERTA LONG {a} "}})
        out["b_log"] = lb["message"] if lb else None
        c_sym = out["sym_c"]
        c_oid = ObjectId()
        sc = await inject(c_sym, "short", 0.0015, 3)
        pc = await db.positions.find_one({"symbol": c_sym, "signal_id": str(sc["_id"])})
        if pc:
            created_pos.append(pc)
        out["c_signal"] = sc["ai_decision"]
        out["c_pos"] = bool(pc)
        lc = await db.logs.find_one({"_id": {"$gt": c_oid}, "level": "ORDER", "message": {"$regex": f"ABIERTA SHORT {c_sym} "}})
        out["c_log"] = lc["message"] if lc else None
    finally:
        for p in created_pos:
            await db.positions.delete_one({"_id": p["_id"]})
            await db.account.update_one({"_id": "main"}, {"$inc": {"balance": p.get("fees", 0)}})
        sigs = [d["_id"] async for d in db.signals.find({"reasons": QA_REASON}, {"_id": 1})]
        await db.shadow_positions.delete_many({"signal_id": {"$in": [str(x) for x in sigs]}})
        await db.signals.delete_many({"reasons": QA_REASON})
        try:
            await eng.market.close()
        except Exception:
            pass
    return out


@pytest.fixture(scope="module")
def e2e():
    return _db_run(_engine_e2e)


class TestEngineE2E:
    def test_a_extreme_blocks(self, e2e):
        print(e2e)
        assert e2e["a_signal"]["ai_decision"] == "FUNDING"
        assert e2e["a_signal"]["note"] == "Bloqueada por funding: funding extremo en contra (+0.1500%/8h · largos pagan)"
        assert e2e["a_shadow"] and e2e["a_shadow"]["kind"] == "FUNDING"
        assert e2e["a_pos"] == 0
        assert e2e["a_log"]

    def test_b_high_reduces(self, e2e):
        assert e2e["b_qty"], f"no position opened: {e2e}"
        ratio = e2e["b_qty"] / e2e["a_shadow"]["qty"]
        assert 0.4 <= ratio <= 0.6, ratio
        assert "tamaño reducido al 50% por funding alto en contra" in e2e["b_log"]

    def test_c_short_in_favor(self, e2e):
        assert e2e["c_pos"]
        assert e2e["c_log"] and "reducido" not in e2e["c_log"]
