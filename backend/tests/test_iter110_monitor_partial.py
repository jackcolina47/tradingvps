"""Iter110 — Priority runtime verification: the position-manager must NOT execute partial
close on range (1:1) positions, and must execute it on trend (2:1) positions. Both crypto
and FX paper engines are exercised via `_manage_paper_position`. Also verifies that
Position.rr_target survives a Mongo round-trip and that the maker pipeline keeps rr_target
in the signal payload used by open_position.
"""
import copy
import pytest
from bson import ObjectId

import engine as engine_mod
import maker as maker_mod
import risk as risk_mod
from models import Position


# ---------------- in-memory Mongo doubles ----------------
class _PosColl:
    def __init__(self):
        self.docs = {}
        self._auto = 0

    async def insert_one(self, d):
        self._auto += 1
        oid = ObjectId("%024x" % self._auto)
        d = {**d, "_id": oid}
        self.docs[oid] = d

        class _R:
            pass
        _R.inserted_id = oid
        return _R

    async def find_one(self, q):
        if "_id" in q:
            return copy.deepcopy(self.docs.get(q["_id"]))
        return None

    async def update_one(self, q, u):
        oid = q.get("_id")
        if oid and oid in self.docs:
            self.docs[oid].update(u.get("$set", {}))

    async def delete_one(self, q):
        self.docs.pop(q.get("_id"), None)


class _AccountColl:
    def __init__(self, balance):
        self.doc = {"_id": "main", "balance": balance, "net_deposits": 0.0}

    async def update_one(self, q, u):
        for k, v in (u.get("$inc") or {}).items():
            self.doc[k] = self.doc.get(k, 0.0) + v


class _SimpleDB:
    def __init__(self, balance=1000.0):
        self.positions = _PosColl()
        self.account = _AccountColl(balance)


# ---------------- engine stubs that reuse the real method under test ----------------
class _FakeMarket:
    def __init__(self):
        self.tickers = {}


class StubCryptoEngine(engine_mod.TradingEngine):
    def __init__(self):
        self.db = _SimpleDB()
        self.market = _FakeMarket()
        self.logs = []
        self.closed = []
        self.partial_announces = []

    async def log(self, level, text):
        self.logs.append((level, text))

    async def _announce_partial(self, p, part, exit_price, net, rules):
        self.partial_announces.append((p.symbol, p.id))

    async def close_position(self, pos_id, price=None, reason="MANUAL"):
        # Simplified paper close: just pop the doc and remember the reason.
        self.closed.append((pos_id, reason, price))
        await self.db.positions.delete_one({"_id": ObjectId(pos_id)})


def _make_pos(db_pos, *, symbol, rr_target, qty=0.1, entry=100.0, sl=98.0, tp=104.0):
    pos = Position(symbol=symbol, side="long", strategy="x", rr_target=rr_target,
                   entry_price=entry, qty=qty, initial_qty=qty, leverage=10,
                   margin=entry * qty / 10, sl=sl, tp=tp, initial_sl=sl, atr=1.0,
                   fees=0.0, mark_price=entry, signal_id=None, live=False, horizon="short",
                   entry_type="market")
    return pos


async def _insert(db, pos):
    res = await db.positions.insert_one(pos.to_mongo())
    pos.id = str(res.inserted_id)
    return pos


# ---------------- 1) crypto monitor: 2:1 does partial, 1:1 does NOT ----------------
@pytest.mark.asyncio
async def test_crypto_monitor_partial_only_on_two_to_one():
    eng = StubCryptoEngine()
    rules = {**risk_mod.DEFAULT_RULES, "partial_tp_enabled": True, "partial_tp_r": 1.0,
             "partial_tp_pct": 50, "breakeven_enabled": True, "trailing_enabled": True}

    # 2:1 trend position (rr_target=2.0): entry 100, SL 98 → partial at +1R = 102 (below TP 104)
    trend = await _insert(eng.db, _make_pos(eng.db, symbol="BTCUSDT", rr_target=2.0,
                                            qty=0.1, entry=100.0, sl=98.0, tp=104.0))
    # 1:1 range position (rr_target=1.0): entry 50, SL 49 → at +1R=51 it hits TP
    rng = await _insert(eng.db, _make_pos(eng.db, symbol="ETHUSDT", rr_target=1.0,
                                          qty=1.0, entry=50.0, sl=49.0, tp=51.0))

    # Push trend to +1R without crossing TP
    await eng._manage_paper_position(trend, 102.0, rules)
    trend_doc = eng.db.positions.docs[ObjectId(trend.id)]
    assert trend_doc["partial_done"] is True, "2:1 trend must trigger partial close at +1R"
    assert trend_doc["qty"] < 0.1, "trend qty must be reduced after partial"
    assert (eng.closed, eng.partial_announces[0][0]) == ([], "BTCUSDT")

    # Push range to +0.9R (below its TP 51) → no partial, qty intact
    await eng._manage_paper_position(rng, 50.9, rules)
    rng_doc = eng.db.positions.docs[ObjectId(rng.id)]
    assert rng_doc["partial_done"] is False, "1:1 range must NOT partial"
    assert rng_doc["qty"] == 1.0, "1:1 range qty must stay intact"

    # Now move the range price to TP (51) → closes full with reason TP
    rng_live = Position.from_mongo(eng.db.positions.docs[ObjectId(rng.id)])
    await eng._manage_paper_position(rng_live, 51.0, rules)
    assert any(c[1] == "TP" for c in eng.closed), f"range should close with TP, got {eng.closed}"


# ---------------- 2) Mongo round-trip of rr_target ----------------
@pytest.mark.asyncio
async def test_rr_target_persists_in_mongo():
    db = _SimpleDB()
    pos = _make_pos(db, symbol="BTCUSDT", rr_target=1.0)
    await _insert(db, pos)
    doc = await db.positions.find_one({"_id": ObjectId(pos.id)})
    assert doc["rr_target"] == 1.0
    reloaded = Position.from_mongo(doc)
    assert reloaded.rr_target == 1.0

    pos2 = _make_pos(db, symbol="ETHUSDT", rr_target=2.0)
    await _insert(db, pos2)
    doc2 = await db.positions.find_one({"_id": ObjectId(pos2.id)})
    assert Position.from_mongo(doc2).rr_target == 2.0


# ---------------- 3) FX monitor: same guarantee on paper ----------------
try:
    import fx.engine_positions as fx_pos_mod
    import fx.risk as fx_risk
    from fx.symbols import SPECS as FX_SPECS
    _FX_AVAILABLE = True
except Exception:
    _FX_AVAILABLE = False


class StubFxEngine(fx_pos_mod.PositionsMixin if _FX_AVAILABLE else object):
    def __init__(self):
        self.db = _SimpleDB()
        self.market = _FakeMarket()
        self.logs = []
        self.closed = []
        self.partial_announces = []
        self.live = False

    async def log(self, level, text):
        self.logs.append((level, text))

    async def tg(self, text, kind):
        return None

    async def _announce_partial(self, p, part, price, net, rules):
        self.partial_announces.append((p.symbol, p.id))

    async def close_position(self, pos_id, price=None, reason="MANUAL"):
        self.closed.append((pos_id, reason, price))
        await self.db.positions.delete_one({"_id": ObjectId(pos_id)})


@pytest.mark.skipif(not _FX_AVAILABLE, reason="fx module unavailable")
@pytest.mark.asyncio
async def test_fx_monitor_partial_only_on_two_to_one():
    from datetime import datetime, timezone
    eng = StubFxEngine()
    rules = {**fx_risk.DEFAULT_RULES, "partial_tp_enabled": True, "partial_tp_r": 1.0,
             "partial_tp_pct": 50, "breakeven_enabled": True, "trailing_enabled": True}
    now = datetime.now(timezone.utc)

    sym = "EURUSD"
    spec = FX_SPECS[sym]
    eng.market.tickers[sym] = {"bid": 1.1, "ask": 1.1 + spec["spread"], "price": 1.1}

    entry, sl, tp_trend = 1.1000, 1.0950, 1.1100  # 2:1
    tp_range = 1.1050  # 1:1

    trend = Position(symbol=sym, side="long", strategy="x", rr_target=2.0,
                     entry_price=entry, qty=0.10, initial_qty=0.10, leverage=10,
                     margin=1000.0, sl=sl, tp=tp_trend, initial_sl=sl, atr=0.001,
                     fees=0.0, mark_price=entry, horizon="short", entry_type="market")
    rng = Position(symbol=sym, side="long", strategy="x", rr_target=1.0,
                   entry_price=entry, qty=0.10, initial_qty=0.10, leverage=10,
                   margin=1000.0, sl=sl, tp=tp_range, initial_sl=sl, atr=0.001,
                   fees=0.0, mark_price=entry, horizon="short", entry_type="market")
    for p in (trend, rng):
        res = await eng.db.positions.insert_one(p.to_mongo())
        p.id = str(res.inserted_id)

    # trend at +1R = 1.1050 → below its TP 1.1100 → should partial
    await eng._manage_paper_position(trend, 1.1050, rules, now)
    doc = eng.db.positions.docs[ObjectId(trend.id)]
    assert doc["partial_done"] is True, "2:1 FX trade must partial at +1R"
    assert doc["qty"] < 0.10

    # range at +0.9R=1.1045 → no partial
    await eng._manage_paper_position(rng, 1.1045, rules, now)
    doc_r = eng.db.positions.docs[ObjectId(rng.id)]
    assert doc_r["partial_done"] is False
    assert doc_r["qty"] == 0.10

    # range at its TP 1.1050 → full close with reason TP
    rng_live = Position.from_mongo(eng.db.positions.docs[ObjectId(rng.id)])
    await eng._manage_paper_position(rng_live, 1.1050, rules, now)
    assert any(c[1] == "TP" for c in eng.closed)


# ---------------- 4) Maker pipeline keeps rr_target intact ----------------
@pytest.mark.asyncio
async def test_maker_queue_preserves_rr_target(monkeypatch):
    """`sig` stored in pending_entries keeps rr_target; when the paper limit fills, _finish
    passes the same sig to engine.open_position (so Position gets rr_target)."""
    # Minimal engine for queue_entry: just db + market + log + live=False + get_settings
    class _Doc:
        def __init__(self):
            self.docs = []
            self._auto = 0

        async def insert_one(self, d):
            self._auto += 1
            self.docs.append({**d, "_id": self._auto})

        async def update_one(self, q, u):
            pass

        async def delete_one(self, q):
            self.docs = [d for d in self.docs if d["_id"] != q["_id"]]

        def find(self, q=None):
            rows = list(self.docs)

            class _C:
                def __aiter__(s):
                    s.i = 0
                    return s

                async def __anext__(s):
                    if s.i >= len(rows):
                        raise StopAsyncIteration
                    v = rows[s.i]
                    s.i += 1
                    return v
            return _C()

    class _Eng:
        def __init__(self):
            self.db = type("D", (), {})()
            self.db.pending_entries = _Doc()
            self.db.signals = _Doc()
            self.market = _FakeMarket()
            self.live = False
            self.open_position_calls = []

        async def log(self, *a):
            pass

        async def open_position(self, symbol, sig, settings, signal_id, risk_factor, risk_reason,
                                horizon="short", entry_type="market", fill=None):
            self.open_position_calls.append({"sig": sig, "entry_type": entry_type})
            return object()

        async def get_settings(self):
            return settings

        async def account_summary(self):
            return {"equity": 1000.0}

    from models import Settings
    settings = Settings(maker_wait_bars=2, timeframe="15m", risk_per_trade=1.0, leverage=10)
    eng = _Eng()
    eng.market.tickers["BTCUSDT"] = {"price": 100.0}
    # Pre-register a signal with an ObjectId-shaped id so update_one won't crash
    from bson import ObjectId as _OID
    signal_id = str(_OID())

    sig = {"side": "long", "strategy": "funnel", "price": 100.0, "sl": 98.0, "tp": 104.0,
           "atr": 1.0, "rr_target": 1.0}

    # Patch _finish's engine.db.signals.update_one to accept the ObjectId gracefully (it uses str id)
    # queue_entry's update_one uses ObjectId(signal_id) — our stub ignores filters.
    await maker_mod.queue_entry(eng, "BTCUSDT", sig, settings, signal_id, 1.0, "test", "short", "15m")
    pending = eng.db.pending_entries.docs
    assert len(pending) == 1
    assert pending[0]["sig"]["rr_target"] == 1.0, "maker.sig must keep rr_target"

    # Now simulate price crossing the limit → _finish triggers engine.open_position with sig
    tickers = {"BTCUSDT": {"price": pending[0]["limit_price"] - 0.01}}
    # monkeypatch shadow.open_shadow which _finish imports
    import shadow as sh
    monkeypatch.setattr(sh, "open_shadow", lambda *a, **k: None)
    await maker_mod.monitor_pending(eng, tickers)
    assert len(eng.open_position_calls) == 1
    assert eng.open_position_calls[0]["sig"]["rr_target"] == 1.0
    assert eng.open_position_calls[0]["entry_type"] == "maker"


# ---------------- 5) performance.by_setup treats range_trade=True as 'rango' even when range_edge is None ----------------
@pytest.mark.asyncio
async def test_by_setup_classifies_range_trade_as_rango():
    import performance
    from bson import ObjectId as _OID

    sig_id_a = _OID()
    sig_id_b = _OID()

    class _Sig:
        docs = [
            {"_id": sig_id_a, "ai_stage": 2, "executed": True,
             "context": {"funnel": {"range_edge": None, "range_trade": True, "counter_trend": False}}},
            {"_id": sig_id_b, "ai_stage": 2, "executed": True,
             "context": {"funnel": {"range_edge": None, "range_trade": False, "counter_trend": False}}},
        ]

        def find(self, q=None, proj=None):
            rows = list(self.docs)

            class _C:
                def __aiter__(s):
                    s.i = 0
                    return s

                async def __anext__(s):
                    if s.i >= len(rows):
                        raise StopAsyncIteration
                    v = rows[s.i]
                    s.i += 1
                    return v
            return _C()

    class _Trades:
        docs = [
            {"signal_id": str(sig_id_a), "pnl": 5.0, "fees": 0.1},
            {"signal_id": str(sig_id_b), "pnl": -2.0, "fees": 0.1},
        ]

        def find(self, q=None, proj=None):
            ids = q.get("signal_id", {}).get("$in", []) if q else []
            rows = [d for d in self.docs if d["signal_id"] in ids] if ids else list(self.docs)

            class _C:
                def __aiter__(s):
                    s.i = 0
                    return s

                async def __anext__(s):
                    if s.i >= len(rows):
                        raise StopAsyncIteration
                    v = rows[s.i]
                    s.i += 1
                    return v
            return _C()

    class _DB:
        signals = _Sig()
        trades = _Trades()

    rows = await performance.by_setup(_DB())
    keys = {r["key"]: r for r in rows}
    assert "rango" in keys, f"expected 'rango' in {keys}"
    assert keys["rango"]["pnl"] == 5.0
    assert "tendencia" in keys
    assert keys["tendencia"]["pnl"] == -2.0


# ---------------- 6) DEEP_SYSTEM.format doesn't raise KeyError ----------------
def test_deep_system_format_has_all_keys():
    import funnel
    out = funnel.DEEP_SYSTEM.format(market="cripto", tf="15m", levels="x")
    assert "cripto" in out and "15m" in out
