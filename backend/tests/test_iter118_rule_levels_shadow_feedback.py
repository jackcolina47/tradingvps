"""Rule-level adjustments (R:R >= 1.5, stop never tighter) and shadow feedback pruning of harmful rules (Bybit + HotForex)."""
import os
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

import ai_funnel as f  # noqa: E402
import ai_rules  # noqa: E402
import shadow  # noqa: E402
from fx import shadow as fx_shadow  # noqa: E402
from models import now_iso  # noqa: E402

import uuid  # noqa: E402

TEST_DB = f"test_iter118_{uuid.uuid4().hex[:8]}"  # fresh database per run: nothing is dropped


class Eng:
    def __init__(self, db):
        self.db, self.logs, self.sent, self.costs = db, [], [], None

    async def log(self, level, msg):
        self.logs.append(msg)

    async def tg(self, text, kind):
        self.sent.append(text)


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[f"{TEST_DB}_{uuid.uuid4().hex[:6]}"]


async def _rule(db):
    return str((await db.ai_rules.insert_one({"text": "No comprar pegado a una resistencia", "symbol": "", "enabled": True, "hits": 0, "misses": 0})).inserted_id)


def test_apply_rule_levels_validation():
    base = {"side": "long", "price": 100.0, "sl": 98.0, "tp": 104.0}
    s = dict(base); assert f.apply_rule_levels(s, 97.0, 105.0) and (s["sl"], s["tp"]) == (97.0, 105.0) and s["default_sl"] == 98.0
    s = dict(base); assert f.apply_rule_levels(s, None, 103.5) and s["tp"] == 103.5 and s["sl"] == 98.0   # R:R 1.75
    s = dict(base); assert f.apply_rule_levels(s, None, 102.5) is None and s == base                       # R:R 1.25 < 1.5 → strategy levels
    s = dict(base); assert f.apply_rule_levels(s, 99.0, None) is None and s == base                        # tighter stop → strategy levels
    s = dict(base); assert f.apply_rule_levels(s, 96.0, 104.0) is None and s == base                       # wider stop, R:R 1.0
    s = dict(base); assert f.apply_rule_levels(s, None, None) is None
    s = {"side": "short", "price": 100.0, "sl": 102.0, "tp": 96.0}
    assert f.apply_rule_levels(s, 102.5, 95.0) and (s["sl"], s["tp"]) == (102.5, 95.0)
    s = {"side": "short", "price": 100.0, "sl": 102.0, "tp": 96.0}
    assert f.apply_rule_levels(s, 101.0, 95.0) is None and s["sl"] == 102.0


def test_high_conviction_flag_and_prompt(monkeypatch):
    assert f.high_conviction({"side": "long", "score": 5}, "ALCISTA") and not f.high_conviction({"side": "long", "score": 4}, "ALCISTA")
    assert f.high_conviction({"side": "short", "score": 6}, "BAJISTA") and not f.high_conviction({"side": "short", "score": 6}, "ALCISTA")
    seen = {}

    async def ask(chat, prompt):
        seen["prompt"] = prompt
        return '{"violates":false,"rule":0,"reason":"objetivo antes del swing","sl":null,"tp":103.5}'
    monkeypatch.setattr(f, "_ask", ask)
    monkeypatch.setattr(f, "_chat", lambda system, *a: SimpleNamespace(system=seen.setdefault("system", system)))
    sig = {"side": "long", "strategy": "trend_ema", "price": 100.0, "sl": 98.0, "tp": 104.0, "score": 5, "reasons": ["a"], "indicators": {}}
    res = asyncio.run(f.rules_check("BTCUSDT", "1h", sig, {"d1_trend": "ALCISTA"}, [{"text": "x"}]))
    assert "ALTA CONVICCIÓN: SÍ" in seen["prompt"] and "PAR PRINCIPAL DE ALTA LIQUIDEZ: SÍ" in seen["prompt"] and res["tp"] == 103.5
    assert "ALTA CONVICCIÓN" in seen["system"] and "BTCUSDT, ETHUSDT" in seen["system"]
    asyncio.run(f.rules_check("DOGEUSDT", "1h", {**sig, "score": 3}, {"d1_trend": "ALCISTA"}, [{"text": "x"}]))
    assert "ALTA CONVICCIÓN: NO" in seen["prompt"] and "PAR PRINCIPAL DE ALTA LIQUIDEZ: NO" in seen["prompt"]


def test_miss_needs_three_misses_and_losing_shadow_balance():
    async def run():
        db = _db()
        rid = await _rule(db)
        await db.shadow_trades.insert_many([{"rule_id": rid, "pnl": -10.0}, {"rule_id": rid, "pnl": 4.0}])  # the rule saved more than it cost
        for _ in range(4):
            r = await ai_rules.miss(db, rid)
        assert r["misses"] == 4 and r["enabled"] and not r["auto_off"] and r["shadow_pnl"] == -6.0
        await db.shadow_trades.insert_one({"rule_id": rid, "pnl": 9.0})  # now its blocked signals won more than they lost
        r = await ai_rules.miss(db, rid)
        assert r["auto_off"] and not r["enabled"] and r["shadow_pnl"] == 3.0
        rid2 = await _rule_text(db, "Otra regla distinta")
        await db.shadow_trades.insert_one({"rule_id": rid2, "pnl": 50.0})
        assert (await ai_rules.miss(db, rid2))["enabled"] and (await ai_rules.miss(db, rid2))["enabled"]  # 2 misses: still on
        assert await ai_rules.miss(db, "ffffffffffffffffffffffff") is None
        row = next(x for x in await ai_rules.list_rules(db) if x["id"] == rid)
        assert (row["blocked_won"], row["blocked_lost"], row["blocked_pnl"], row["misses_left"], row["max_misses"]) == (13.0, 10.0, 3.0, 0, 3)
        assert next(x for x in await ai_rules.list_rules(db) if x["id"] == rid2)["misses_left"] == 1
        assert f.MAJORS == ("BTCUSDT", "ETHUSDT", "EURUSD", "GBPUSD", "XAUUSD")
    asyncio.run(run())


async def _rule_text(db, text):
    return str((await db.ai_rules.insert_one({"text": text, "symbol": "", "enabled": True, "hits": 0, "misses": 0})).inserted_id)


def _pos(rid, tp=104.0, sl=98.0, symbol="BTCUSDT"):
    return {"signal_id": "s", "symbol": symbol, "side": "long", "strategy": "trend_ema", "timeframe": "1h", "kind": "REGLA", "ai_confidence": 0, "ai_risk": "-",
            "entry_price": 100.0, "qty": 1.0, "leverage": 3, "margin": 33.0, "sl": sl, "tp": tp, "initial_sl": sl, "atr": 1.0, "fees": 0.05, "breakeven": False,
            "risk_factor": 1.0, "mark_price": 100.0, "unrealized_pnl": 0.0, "opened_at": now_iso(), "horizon": "short", "rule_id": rid}


def test_bybit_shadow_tp_counts_miss_and_prunes():
    async def run():
        db = _db()
        e, rid = Eng(db), await _rule(db)
        s = SimpleNamespace(trailing_enabled=False, breakeven_enabled=False)
        import risk
        rules = {**risk.trade_rules(risk_settings()), "trailing_enabled": False, "breakeven_enabled": False}
        shadow_rules = lambda r: rules  # noqa: E731
        orig, shadow.trade_rules = shadow.trade_rules, shadow_rules
        try:
            await db.shadow_positions.insert_one(_pos(rid))
            await shadow.monitor_shadows(e, {"BTCUSDT": {"price": 97.0}}, s)  # SL: no feedback
            assert (await db.ai_rules.find_one())["misses"] == 0 and not e.logs
            for i in range(3):
                await db.shadow_positions.insert_one(_pos(rid))
                await shadow.monitor_shadows(e, {"BTCUSDT": {"price": 104.5}}, s)
                doc = await db.ai_rules.find_one()
                assert doc["misses"] == i + 1 and "[SHADOW FEEDBACK]" in e.logs[-1]
            assert doc["enabled"] is False and "APAGADA" in e.logs[-1] and any("Regla apagada" in x for x in e.sent)  # 3 TP (+13) vs 1 SL (−3)
            assert (await db.ai_rule_events.find_one())["action"] == "DESACTIVAR"
            await db.shadow_positions.insert_one({**_pos(None), "kind": "BLOQUEADA"})
            n = len(e.logs)
            await shadow.monitor_shadows(e, {"BTCUSDT": {"price": 104.5}}, s)  # no rule behind it: nothing logged
            assert len(e.logs) == n
        finally:
            shadow.trade_rules = orig
    asyncio.run(run())


def risk_settings():
    from models import Settings
    return Settings()


def test_fx_shadow_tp_counts_miss_and_keeps_profitable_rule(monkeypatch):
    async def run():
        db = _db()
        e, rid = Eng(db), await _rule(db)
        await db.shadow_trades.insert_one({"rule_id": rid, "pnl": -500.0, "kind": "REGLA", "strategy": "trend_ema", "symbol": "EURUSD"})  # it avoided a big loss
        from fx.models import FxSettings
        s = FxSettings(trailing_enabled=False, breakeven_enabled=False, close_before_weekend=False)
        tickers = {"EURUSD": {"price": 1.1050}}
        for i in range(3):
            await db.shadow_positions.insert_one({**_pos(rid, tp=1.1040, sl=1.0980, symbol="EURUSD"), "entry_price": 1.1000, "qty": 0.1, "fees": 0.1, "atr": 0.001})
            await fx_shadow.monitor_shadows(e, tickers, s, datetime.now(timezone.utc))
        doc = await db.ai_rules.find_one()
        assert doc["misses"] == 3 and doc["enabled"] is True and "[SHADOW FEEDBACK]" in e.logs[-1] and "APAGADA" not in e.logs[-1] and not e.sent
    asyncio.run(run())
