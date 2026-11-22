"""HotForex: late-entry filter (0.35 ATR past the signal candle's close) and funnel_rich only for the 'fx' profile."""
import pytest
from bson import ObjectId
import funnel as funnel_mod
from tests.test_iter102_funnel_decisions import FakeEngine, _settings, _force_in_hours


class _OidColl:
    def __init__(self):
        self.docs = []

    async def insert_one(self, d):
        d = {**d, "_id": ObjectId()}
        self.docs.append(d)
        return type("R", (), {"inserted_id": d["_id"]})

    async def update_one(self, q, u):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(u.get("$set", {}))


async def _run(monkeypatch, ran_atr, late=0.35, decision="BUY"):
    await _force_in_hours(monkeypatch)
    eng = FakeEngine()
    eng.db.signals = _OidColl()
    if late:
        eng.funnel_late_atr = late
    d = 1 if decision == "BUY" else -1

    async def fake_stage1(kind, tf, rows, limit, usage_kind):
        return {"candidates": [{"symbol": "BTCUSDT", "side": "long" if d == 1 else "short", "reason": "x"}], "model": "fast", "ms": 1}

    async def fake_stage2(kind, symbol, tf, cand, m, *a, **kw):
        eng.market.tickers[symbol] = {**eng.market.tickers.get(symbol, {}), "price": m["close"] + d * ran_atr * m["atr"], "spread": 0.0}
        return {"decision": decision, "confidence": 80, "sl": None, "tp": None, "rule": None, "reasoning": "ok", "model": "deep", "ms": 1}
    monkeypatch.setattr(funnel_mod, "stage1", fake_stage1)
    monkeypatch.setattr(funnel_mod, "stage2", fake_stage2)
    await funnel_mod.run(eng, _settings())
    return eng


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["BUY", "SELL"])
async def test_late_entry_discarded(monkeypatch, decision):
    eng = await _run(monkeypatch, 0.5, decision=decision)
    assert eng.opened == [] and len(eng.shadows) == 1 and "TARDIA" in str(eng.shadows[0])
    doc = eng.db.signals.docs[0]
    assert doc["ai_decision"] == "TARDIA" and "tardía" in doc["note"]


@pytest.mark.asyncio
async def test_within_limit_opens(monkeypatch):
    eng = await _run(monkeypatch, 0.3)
    assert len(eng.opened) == 1 and eng.db.signals.docs[0]["ai_decision"] == "APROBADA"


@pytest.mark.asyncio
async def test_move_against_is_not_late(monkeypatch):
    assert len((await _run(monkeypatch, -1.0)).opened) == 1


@pytest.mark.asyncio
async def test_engine_without_filter_opens(monkeypatch):
    assert len((await _run(monkeypatch, 0.9, late=None)).opened) == 1


def test_both_fx_bots_are_rich():
    from deps import FX_BOTS
    flags = {e.profile.panel: (e.funnel_rich, e.funnel_late_atr) for e, _ in FX_BOTS}
    assert flags == {"fx": (True, 0.35), "pepper": (True, 0.35)}
