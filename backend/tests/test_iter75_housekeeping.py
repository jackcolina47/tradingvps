# Iter75: weekly re-optimisation + automatic pruning (housekeeping.py), /api/housekeeping endpoints, settings keys/validation
import os
import sys
import time
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv("/app/frontend/.env")

import housekeeping  # noqa: E402
from models import Settings  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
SYM = "ONDOUSDT"
TAG = "TEST_iter75"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    r = sess.post(f"{BASE_URL}/api/auth/login", json={"password": "BlackCat2233@"})
    if r.status_code != 200:
        pytest.skip("login failed")
    sess.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return sess


@pytest.fixture(scope="module")
def mdb():
    c = MongoClient(os.environ["MONGO_URL"])
    yield c[os.environ["DB_NAME"]]
    c.close()


# ---------- settings ----------
class TestSettings:
    def test_defaults(self, s):
        d = s.get(f"{BASE_URL}/api/settings").json()
        assert d["reopt_enabled"] is True and d["reopt_symbols"] == ["ZECUSDT", "HYPEUSDT"]
        assert d["auto_prune_enabled"] is True and d["auto_prune_pf"] == 0.8 and d["auto_prune_min_trades"] == 10

    def test_validation(self, s):
        try:
            r = s.put(f"{BASE_URL}/api/settings", json={"reopt_symbols": ["ZECUSDT", "FOOUSDT"]})
            assert r.status_code == 200, r.text
            assert s.get(f"{BASE_URL}/api/settings").json()["reopt_symbols"] == ["ZECUSDT"]
            assert s.put(f"{BASE_URL}/api/settings", json={"auto_prune_pf": 2}).status_code == 422
            assert s.put(f"{BASE_URL}/api/settings", json={"auto_prune_min_trades": 1}).status_code == 422
        finally:
            s.put(f"{BASE_URL}/api/settings", json={"reopt_symbols": ["ZECUSDT", "HYPEUSDT"]})
        assert s.get(f"{BASE_URL}/api/settings").json()["reopt_symbols"] == ["ZECUSDT", "HYPEUSDT"]

    def test_fx_absent(self, s):
        for fx in ("/api/fx", "/api/pepperstone"):
            r = s.get(f"{BASE_URL}{fx}/settings")
            if r.status_code == 200:
                d = r.json()
                for k in ("reopt_enabled", "reopt_symbols", "auto_prune_enabled", "auto_prune_pf", "auto_prune_min_trades"):
                    assert k not in d, f"{fx} has {k}"
            assert s.get(f"{BASE_URL}{fx}/housekeeping").status_code == 404

    def test_status_shape(self, s):
        d = s.get(f"{BASE_URL}/api/housekeeping").json()
        assert "reopt" in d and "prune" in d
        syms = {r["symbol"]: r for r in d["reopt"].get("results", [])}
        assert {"ZECUSDT", "HYPEUSDT"} <= set(syms), syms.keys()
        for r in syms.values():
            for k in ("best", "baseline", "profitable", "active"):
                assert k in r


# ---------- prune flow (live API + seeded trades) ----------
def _seed(mdb, n_loss, n_win, loss=-5.0, win=3.0):
    now = datetime.now(timezone.utc)
    docs = []
    for i in range(n_loss + n_win):
        pnl = loss if i < n_loss else win
        docs.append({"id": f"{TAG}_{uuid.uuid4().hex[:8]}", "symbol": SYM, "side": "long", "pnl": pnl, "fees": 0.1, "strategy": TAG,
                     "opened_at": (now - timedelta(days=2, hours=i + 1)).isoformat(), "closed_at": (now - timedelta(days=2, hours=i)).isoformat()})
    mdb.trades.insert_many(docs)
    return [d["id"] for d in docs]


class TestPruneFlow:
    def test_flow(self, s, mdb):
        ids = []
        try:
            assert SYM in s.get(f"{BASE_URL}/api/settings").json()["symbols"]
            mdb.meta.update_one({"_id": "prune"}, {"$set": {"warned": {}}}, upsert=True)
            # <= min_trades never warned
            ids += _seed(mdb, 10, 0)
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert SYM not in r["warned"], r
            mdb.trades.delete_many({"id": {"$in": ids}}); ids = []
            # 12 trades PF<0.8 -> warned
            ids += _seed(mdb, 9, 3)
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert SYM in r["warned"], r
            assert SYM in s.get(f"{BASE_URL}/api/housekeeping").json()["prune"]["warned"]
            # immediately again -> nothing new
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert r["warned"] == [] and r["switched_off"] == [], r
            # 21 h ago -> switched off
            mdb.meta.update_one({"_id": "prune"}, {"$set": {f"warned.{SYM}": (datetime.now(timezone.utc) - timedelta(hours=21)).isoformat()}})
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert r["switched_off"] == [SYM], r
            assert SYM not in s.get(f"{BASE_URL}/api/settings").json()["symbols"]
            ev = s.get(f"{BASE_URL}/api/evolution").json()
            events = ev.get("events", ev) if isinstance(ev, dict) else ev
            assert any(e.get("source") == "auto_prune" for e in events), str(ev)[:300]
            assert SYM not in s.get(f"{BASE_URL}/api/housekeeping").json()["prune"]["warned"]
            # inactive -> untouched
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert SYM not in r["warned"] + r["switched_off"] + r["cleared"], r
            # cleared
            syms = s.get(f"{BASE_URL}/api/settings").json()["symbols"]
            assert s.put(f"{BASE_URL}/api/settings", json={"symbols": syms + [SYM]}).status_code == 200
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert SYM in r["warned"], r
            mdb.trades.delete_many({"id": {"$in": ids}}); ids = []
            ids += _seed(mdb, 0, 12)
            r = s.post(f"{BASE_URL}/api/housekeeping/prune").json()
            assert SYM in r["cleared"], r
            assert SYM not in s.get(f"{BASE_URL}/api/housekeeping").json()["prune"]["warned"]
        finally:
            if ids:
                mdb.trades.delete_many({"id": {"$in": ids}})
            mdb.meta.update_one({"_id": "prune"}, {"$set": {"warned": {}}})
            syms = s.get(f"{BASE_URL}/api/settings").json()["symbols"]
            if SYM not in syms:
                s.put(f"{BASE_URL}/api/settings", json={"symbols": syms + [SYM]})
        assert mdb.trades.count_documents({"strategy": TAG}) == 0


# ---------- unit: reoptimize / _profitable / tick ----------
def _best(**kw):
    b = {"sl_atr": 1.5, "tp_atr": 6.0, "atr_period": 20, "robust": 1.0, "trades": 20, "return_pct": 5.0, "profit_factor": 1.5, "max_drawdown": 3.0}
    b.update(kw)
    return b


def _fake_engine():
    db = SimpleNamespace(account=SimpleNamespace(find_one=AsyncMock(return_value={"initial_balance": 1000})),
                         meta=SimpleNamespace(update_one=AsyncMock(), find_one=AsyncMock(return_value=None)))
    return SimpleNamespace(db=db, market=SimpleNamespace(fetch_klines=AsyncMock(return_value=[])), update_settings=AsyncMock(), log=AsyncMock())


def _run_reopt(settings, bests):
    eng = _fake_engine()
    def fake_opt(klines, *a):
        sym = a[5]
        return {"best": bests[sym], "baseline": {"return_pct": -1.0}, "period_start": "a", "period_end": "b", "candles": 1000}
    with patch.object(housekeeping, "optimize", side_effect=fake_opt), patch.object(housekeeping.notify, "notify", new=AsyncMock()) as nt:
        out = asyncio.run(housekeeping.reoptimize(eng, settings, "manual"))
    return out, eng, nt


class TestUnit:
    def test_profitable_thresholds(self):
        p = housekeeping._profitable
        assert p(_best())
        assert not p(_best(trades=14)) and p(_best(trades=15))
        assert not p(_best(return_pct=0)) and not p(_best(profit_factor=1.09)) and p(_best(profit_factor=1.1))
        assert not p(_best(robust=0)) and not p(None)

    def test_no_update_when_same_float(self):
        st = Settings(reopt_symbols=["ZECUSDT"], symbols=["ZECUSDT"], symbol_params={"ZECUSDT": {"sl_atr": 1.5, "tp_atr": 6, "atr_period": 20.0}})
        out, eng, nt = _run_reopt(st, {"ZECUSDT": _best(atr_period=20)})
        assert out["applied"] == {} and eng.update_settings.await_count == 0
        assert nt.await_count == 1 and "RENTABLE" not in nt.await_args[0][0]

    def test_apply_only_when_robust(self):
        st = Settings(reopt_symbols=["ZECUSDT", "HYPEUSDT"], symbols=["ZECUSDT"], symbol_params={})
        out, eng, nt = _run_reopt(st, {"ZECUSDT": _best(robust=0), "HYPEUSDT": _best(sl_atr=2.0)})
        assert list(out["applied"]) == ["HYPEUSDT"]
        args = eng.update_settings.await_args[0]
        assert args[1] == "reopt" and args[0]["symbol_params"]["HYPEUSDT"] == {"sl_atr": 2.0, "tp_atr": 6.0, "atr_period": 20.0}
        txt = nt.await_args[0][0]
        assert "HYPE ha pasado a ser RENTABLE" in txt and "ZEC ha pasado" not in txt
        assert out["turned_profitable"] == ["HYPEUSDT"]

    def test_rentable_not_for_active_or_unprofitable(self):
        st = Settings(reopt_symbols=["ZECUSDT", "HYPEUSDT"], symbols=["ZECUSDT"], symbol_params={})
        out, eng, nt = _run_reopt(st, {"ZECUSDT": _best(), "HYPEUSDT": _best(profit_factor=1.0)})
        assert "RENTABLE" not in nt.await_args[0][0] and out["turned_profitable"] == []

    def test_tick_throttle_and_not_due(self):
        st = Settings()
        eng = _fake_engine()
        eng._hk_checked = time.time() - 100
        with patch.object(housekeeping, "reoptimize", new=AsyncMock()) as ro, patch.object(housekeeping, "prune", new=AsyncMock()) as pr:
            asyncio.run(housekeeping.tick(eng, st))
            assert ro.await_count == 0 and pr.await_count == 0 and eng.db.meta.find_one.await_count == 0
            recent = datetime.now(timezone.utc).isoformat()
            eng._hk_checked = 0.0
            eng.db.meta.find_one = AsyncMock(return_value={"last_at": recent})
            asyncio.run(housekeeping.tick(eng, st))
            assert ro.await_count == 0 and pr.await_count == 0 and eng._hk_checked > 0
            # due -> both run
            eng._hk_checked = 0.0
            eng.db.meta.find_one = AsyncMock(return_value=None)
            asyncio.run(housekeeping.tick(eng, st))
            assert ro.await_count == 1 and pr.await_count == 1
