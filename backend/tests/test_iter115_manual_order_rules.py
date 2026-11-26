"""Iter115 — manual order with its own exit rules (breakeven, trailing, partial close) and its own AI review interval."""
import time
from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError
import position_review
import risk
from fx import risk as fx_risk
from models import ManualOrder, Position, Settings

BASE = dict(symbol="BTCUSDT", side="long", sl=99.0, tp=102.0, atr=1.0, risk_pct=1.0)


class TestManualOrderModel:
    def test_plain_order_has_no_overrides(self):
        o = ManualOrder(**BASE)
        assert o.rules() == {} and o.ai_review_minutes is None

    def test_overrides_only_the_given_fields(self):
        o = ManualOrder(**BASE, breakeven_enabled=False, trailing_enabled=True, trail_start_r=2.0, trail_atr_mult=1.5, partial_tp_enabled=True, partial_tp_r=1.0, partial_tp_pct=30)
        assert o.rules() == {"breakeven_enabled": False, "trailing_enabled": True, "trail_start_r": 2.0, "trail_atr_mult": 1.5,
                             "partial_tp_enabled": True, "partial_tp_r": 1.0, "partial_tp_pct": 30}

    @pytest.mark.parametrize("field,value", [("be_start_r", 0.4), ("be_start_r", 3.1), ("trail_start_r", 0.2), ("trail_atr_mult", 3.5), ("partial_tp_r", 4),
                                             ("partial_tp_pct", 20), ("partial_tp_pct", 80), ("ai_review_minutes", 3), ("ai_review_minutes", 35), ("ai_review_minutes", 12)])
    def test_ranges(self, field, value):
        with pytest.raises(ValidationError):
            ManualOrder(**BASE, **{field: value})

    @pytest.mark.parametrize("m", [5, 10, 15, 20, 25, 30])
    def test_review_minutes_choices(self, m):
        assert ManualOrder(**BASE, ai_review_minutes=m).ai_review_minutes == m


def _pos(**kw):
    base = dict(symbol="BTCUSDT", side="long", strategy="manual", entry_price=100.0, qty=1.0, leverage=3, margin=33.0, sl=99.0, tp=102.0, initial_sl=99.0, atr=1.0, fees=0.0, mark_price=100.0)
    return Position(**{**base, **kw})


class TestRulesFor:
    def test_bot_rules_when_the_position_has_none(self):
        bot = risk.trade_rules(Settings())
        assert risk.rules_for(bot, _pos()) is bot

    def test_position_rules_win_and_the_rest_follows_the_bot(self):
        bot = risk.trade_rules(Settings(breakeven_enabled=True, be_start_r=1.0, partial_tp_enabled=True, partial_tp_pct=50))
        own = risk.rules_for(bot, _pos(rules={"breakeven_enabled": False, "partial_tp_pct": 30, "trail_atr_mult": 2.0}))
        assert own["breakeven_enabled"] is False and own["partial_tp_pct"] == 30 and own["trail_atr_mult"] == 2.0
        assert own["partial_tp_enabled"] is True and own["be_start_r"] == 1.0 and bot["breakeven_enabled"] is True

    def test_works_with_fx_rules_too(self):
        bot = fx_risk.trade_rules({"trailing_enabled": True})
        assert risk.rules_for(bot, _pos(rules={"trailing_enabled": False}))["trailing_enabled"] is False

    def test_trailing_uses_the_own_distance(self):
        p = _pos(rules={"trail_start_r": 1.0, "trail_atr_mult": 0.5, "trailing_enabled": True, "breakeven_enabled": False})
        r = risk.rules_for(risk.trade_rules(Settings()), p)
        sl, be = risk.update_trailing(p.side, p.entry_price, p.sl, p.initial_sl, p.atr, 102.0, r["be_start_r"], r["trail_start_r"], r["trail_atr_mult"],
                                      be_enabled=risk.be_allowed(r, False), trail_enabled=r["trailing_enabled"])
        assert sl == 101.5

    def test_both_engines_apply_the_own_rules(self):
        import inspect
        import engine
        from fx import engine as fxe
        assert inspect.getsource(engine.TradingEngine._monitor_once).count("rules_for(rules, p)") == 2
        assert inspect.getsource(fxe.FxEngine._monitor_once).count("rules_for(rules, p)") == 2


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield d
        return gen()


class _Eng:
    def __init__(self, docs):
        self.docs = docs
        self.db = type("DB", (), {"positions": type("C", (), {"find": lambda s, q=None: _Cursor([d for d in docs if d.get("ai_review_minutes")])})()})()


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


S = type("S", (), {"ai_enabled": True, "ai_position_review_enabled": False})()


class TestOwnReview:
    @pytest.mark.asyncio
    async def test_due_after_its_interval_from_the_open(self):
        eng = _Eng([{"_id": "a", "opened_at": _ago(11), "ai_review_minutes": 10}, {"_id": "b", "opened_at": _ago(9), "ai_review_minutes": 10},
                    {"_id": "c", "opened_at": _ago(500)}])
        assert await position_review.due_own(eng, S) == ["a"]

    @pytest.mark.asyncio
    async def test_counts_from_the_last_review(self):
        eng = _Eng([{"_id": "a", "opened_at": _ago(60), "ai_review_minutes": 5, "ai_review": {"at": _ago(4)}},
                    {"_id": "b", "opened_at": _ago(60), "ai_review_minutes": 5, "ai_review": {"at": _ago(6)}}])
        assert await position_review.due_own(eng, S) == ["b"]

    @pytest.mark.asyncio
    async def test_a_failed_review_is_not_retried_every_tick(self):
        eng = _Eng([{"_id": "a", "opened_at": _ago(30), "ai_review_minutes": 5}])
        assert await position_review.due_own(eng, S) == ["a"]
        assert await position_review.due_own(eng, S) == []
        eng._own_review_last["a"] = time.time() - 301
        assert await position_review.due_own(eng, S) == ["a"]

    @pytest.mark.asyncio
    async def test_needs_the_ai_enabled_but_not_the_global_review(self):
        eng = _Eng([{"_id": "a", "opened_at": _ago(30), "ai_review_minutes": 5}])
        off = type("S", (), {"ai_enabled": False})()
        assert await position_review.due_own(eng, off) == [] and await position_review.due_own(eng, S) == ["a"]

    def test_own_review_does_not_reset_the_global_clock(self):
        import inspect
        src = inspect.getsource(position_review.review_open_positions)
        assert src.count("if only is None:") == 2 and '{"_id": {"$in": only}} if only else {}' in src
