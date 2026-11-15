import asyncio
import notify
from risk import ema_partial_due, trade_rules, DEFAULT_RULES
from fx import risk as fxrisk
from models import Settings
from fx.models import FxSettings

R = {**DEFAULT_RULES, "ema_test_partial_enabled": True, "ema_test_partial_atr": 1.5}


def test_due():
    assert ema_partial_due(R, "long", 100, 2, 103, False)
    assert not ema_partial_due(R, "long", 100, 2, 102.9, False)
    assert ema_partial_due(R, "short", 100, 2, 97, False)
    assert not ema_partial_due(R, "long", 100, 2, 110, True)
    assert not ema_partial_due(DEFAULT_RULES, "long", 100, 2, 110, False)


def test_rules_from_settings():
    assert trade_rules(Settings(ema_test_partial_enabled=True, ema_test_partial_atr=2))["ema_test_partial_atr"] == 2
    assert fxrisk.trade_rules(FxSettings())["ema_test_partial_enabled"] is False


def test_tg_routing(monkeypatch):
    sent = []

    async def fake(token, cid, text):
        sent.append(cid)
        return True
    monkeypatch.setattr(notify, "_send_one", fake)
    old = dict(notify._cfg)
    try:
        notify.configure(True, "t", "A", {}, "B", ["bybit"], ["hfm", "pepperstone"])
        asyncio.run(notify.notify("x", "orders", bot="bybit"))
        assert sent == ["A"]
        sent.clear()
        asyncio.run(notify.notify("x", "orders", bot="hfm"))
        assert sent == ["B"]
        sent.clear()
        asyncio.run(notify.notify("x", "system"))
        assert sent == ["A", "B"]
        notify.configure(True, "t", "A", {}, "", ["bybit"], None)
        sent.clear()
        assert asyncio.run(notify.notify("x", "orders", bot="pepperstone")) is False and sent == []
    finally:
        notify._cfg.update(old)
