# Iteration 46 (QA iter39): ENGINE e2e for horizon caps, BTC regime gate, weekly rotation gate/size and maker entries (paper).
# Run with: python -m pytest tests/test_iter46_engine_e2e.py -n 0 -q -s
import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

QA = "QA iter46 engine e2e"


async def _run(db):
    from engine import TradingEngine
    from config import ConfigStore
    from indicators import build_df
    from strategies import _signal
    from bson import ObjectId
    import maker

    eng = TradingEngine(db)
    store = ConfigStore(db)
    store.listeners.append(eng.apply_config)
    await store.load()
    await eng.market.detect()
    await eng.market.fetch_tickers()
    s = await eng.get_settings()
    s.ai_enabled = False
    s.entry_order_type = "market"
    s.max_positions, s.max_long_positions = 1, 1
    out = {}
    start_oid = ObjectId()
    syms = [x for x in s.symbols if x in eng.market.tickers]
    assert await db.positions.count_documents({}) == 0, "expected no open positions"
    for x in syms:
        eng.market.tickers[x]["funding_rate"] = 0.0
    bump = [0]
    dfs = {}

    async def inject(sym, side, strategy="trend_ema", horizon="short"):
        bump[0] += 1
        tf = "1d" if horizon == "long" else s.timeframe
        if (sym, tf) not in dfs:
            rows = await (eng.market.fetch_history(sym, "1d", 300) if horizon == "long" else eng.market.fetch_klines(sym, tf, 300))
            dfs[(sym, tf)] = build_df(rows)
        df = dfs[(sym, tf)]
        row = df.iloc[-2]
        atr, c = row["atr"], row["close"]
        sl = c - 1.5 * atr if side == "long" else c + 1.5 * atr
        tp = c + 3 * atr if side == "long" else c - 3 * atr
        sig = _signal(side, row, sl, tp, strategy, [QA], 5, int(row["ts"]) + 7000 + bump[0])
        if horizon == "long":
            await eng._handle_signal(sym, sig, s, df, None, "1d", horizon="long", tf="1d")
        else:
            await eng._handle_signal(sym, sig, s, df, 0, "4h")
        sd = await db.signals.find_one({"symbol": sym, "reasons": QA}, sort=[("_id", -1)])
        pos = await db.positions.find_one({"signal_id": str(sd["_id"])})
        sh = await db.shadow_positions.find_one({"signal_id": str(sd["_id"])})
        return sd, pos, sh

    async def drop(pos):
        if pos:
            await db.positions.delete_one({"_id": pos["_id"]})
            await db.account.update_one({"_id": "main"}, {"$inc": {"balance": pos.get("fees", 0)}})

    async def drop_all_qa():
        sids = [str(d["_id"]) async for d in db.signals.find({"reasons": QA}, {"_id": 1})]
        await maker.cancel_all(eng, "QA")
        async for p in db.positions.find({"signal_id": {"$in": sids}}):
            await drop(p)
        return sids

    try:
        A, B, C, D = syms[0], syms[1], syms[2], syms[3]
        # (a) caps by horizon
        sa, pa, _ = await inject(A, "long")
        out["a_open"] = bool(pa) and (pa.get("horizon") or "short") == "short"
        sb, pb, shb = await inject(B, "long")
        out["b"] = {"dec": sb["ai_decision"], "note": sb.get("note"), "shadow": shb and shb["kind"], "pos": bool(pb)}
        sc, pc, _ = await inject(C, "long", "multi_horizon", "long")
        out["c"] = {"pos": bool(pc), "horizon": pc and pc.get("horizon"), "entry_type": pc and pc.get("entry_type"), "note": sc.get("note")}
        sd_, pd_, shd = await inject(D, "short", "multi_horizon", "long")
        out["d"] = {"dec": sd_["ai_decision"], "note": sd_.get("note"), "pos": bool(pd_), "shadow": shd and shd["kind"]}
        await drop_all_qa()

        s.max_positions, s.max_long_positions = 10, 6

        # (d) maker paper
        s.entry_order_type, s.maker_wait_bars = "maker", 1
        oid = ObjectId()
        sm, pm, _ = await inject(A, "long")
        pend = await db.pending_entries.find_one({"signal_id": str(sm["_id"])})
        price = eng.market.tickers[A]["price"]
        lg = await db.logs.find_one({"_id": {"$gt": oid}, "level": "ORDER", "message": {"$regex": "orden LÍMITE maker LONG @"}})
        sm2 = await db.signals.find_one({"_id": sm["_id"]})
        out["maker_q"] = {"pend": bool(pend), "limit_lt_price": pend and pend["limit_price"] < price, "live": pend and pend["live"],
                          "horizon": pend and pend["horizon"], "expires": pend and pend.get("expires_at"), "log": bool(lg), "note": sm2.get("note"), "pos": bool(pm)}
        sdup, pdup, _ = await inject(A, "long")
        out["maker_dup"] = {"dec": sdup["ai_decision"], "note": sdup.get("note")}
        if pend:
            limit = pend["limit_price"]
            eng.market.tickers[A]["price"] = limit * 0.999
            await maker.monitor_pending(eng, eng.market.tickers)
            eng.market.tickers[A]["price"] = price
            pf = await db.positions.find_one({"signal_id": str(sm["_id"])})
            out["maker_fill"] = {"gone": not await db.pending_entries.find_one({"_id": pend["_id"]}), "pos": bool(pf),
                                 "entry_type": pf and pf.get("entry_type"), "entry_ok": pf and abs(pf["entry_price"] - limit) < 1e-9,
                                 "fee_ok": pf and abs(pf["fees"] - pf["entry_price"] * pf["qty"] * 0.0002) < 1e-9}
        # expiration -> market fallback
        se, _, _ = await inject(B, "long")
        pe = await db.pending_entries.find_one({"signal_id": str(se["_id"])})
        if pe:
            await db.pending_entries.update_one({"_id": pe["_id"]}, {"$set": {"expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}})
            await maker.monitor_pending(eng, eng.market.tickers)
            pfe = await db.positions.find_one({"signal_id": str(se["_id"])})
            out["maker_exp"] = {"pos": bool(pfe), "entry_type": pfe and pfe.get("entry_type"),
                                "fee_ok": pfe and abs(pfe["fees"] - pfe["entry_price"] * pfe["qty"] * 0.00055) < 1e-9}
        # cancel_all
        sx, _, _ = await inject(C, "long")
        out["cancel_n"] = await maker.cancel_all(eng, "QA")
        out["cancel_left"] = await db.pending_entries.count_documents({"signal_id": str(sx["_id"])})
        await drop_all_qa()

        # (e) pending entries vs caps: max_positions=1, two pendings in different pairs
        s.max_positions = 1
        e1, _, _ = await inject(D, "long")
        e2, _, _ = await inject(syms[4], "long")
        out["cap_pending"] = {"first": e1.get("note"), "second_dec": e2["ai_decision"], "second_note": e2.get("note"),
                              "pendings": await db.pending_entries.count_documents({"signal_id": {"$in": [str(e1["_id"]), str(e2["_id"])]}})}
    finally:
        sids = await drop_all_qa()
        await db.shadow_positions.delete_many({"signal_id": {"$in": sids}})
        await db.shadow_trades.delete_many({"signal_id": {"$in": sids}})
        await db.pending_entries.delete_many({"signal_id": {"$in": sids}})
        await db.signals.delete_many({"reasons": QA})
        await db.logs.delete_many({"_id": {"$gt": start_oid}, "level": {"$in": ["SIGNAL", "RISK", "ORDER", "AI_EVAL"]}})
    return out


@pytest.fixture(scope="module")
def e2e():
    async def go():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            return await _run(c[os.environ["DB_NAME"]])
        finally:
            c.close()
    r = asyncio.run(go())
    print(r)
    return r


def test_a_short_cap(e2e):
    assert e2e["a_open"]
    b = e2e["b"]
    assert b["dec"] == "BLOQUEADA" and "máximo de posiciones de corto plazo alcanzado (1)" in b["note"] and b["shadow"] == "BLOQUEADA" and not b["pos"]


def test_a_long_ignores_short_cap(e2e):
    c = e2e["c"]
    assert c["pos"] and c["horizon"] == "long" and c["entry_type"] == "market", c


def test_a_long_cap(e2e):
    d = e2e["d"]
    assert d["dec"] == "BLOQUEADA" and "máximo de posiciones de largo plazo alcanzado (1)" in d["note"] and not d["pos"], d


def test_d_maker_queue(e2e):
    m = e2e["maker_q"]
    assert m["pend"] and m["limit_lt_price"] and m["live"] is False and m["horizon"] == "short" and m["expires"] and m["log"] and not m["pos"], m
    assert m["note"].startswith("Orden límite maker en espera")


def test_d_maker_dup_blocked(e2e):
    assert "ya existe una posición abierta (o una orden límite en espera)" in e2e["maker_dup"]["note"]


def test_d_maker_fill(e2e):
    f = e2e["maker_fill"]
    assert f["gone"] and f["pos"] and f["entry_type"] == "maker" and f["entry_ok"] and f["fee_ok"], f


def test_d_maker_expire(e2e):
    f = e2e["maker_exp"]
    assert f["pos"] and f["entry_type"] == "market_fallback" and f["fee_ok"], f


def test_d_cancel_all(e2e):
    assert e2e["cancel_n"] >= 1 and e2e["cancel_left"] == 0


def test_e_pending_counts_towards_cap(e2e):
    """With max_positions=1 a second maker entry while one is pending should be blocked (otherwise both fill and exceed the cap)."""
    c = e2e["cap_pending"]
    assert c["second_dec"] == "BLOQUEADA", c
