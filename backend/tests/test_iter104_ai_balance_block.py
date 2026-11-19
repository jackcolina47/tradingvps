import asyncio
import ai_advisor as a


def test_balance_block(monkeypatch):
    async def run():
        old = dict(a._health)
        calls = []

        async def bad():
            calls.append(1)
            raise RuntimeError("insufficient balance")

        async def good():
            a._health.update(a._fresh_health())
            return "ok"
        try:
            a._health.update(a._fresh_health())
            assert await a.balance_block() is None
            a._health.update(ok=False, code="network")
            assert await a.balance_block() is None
            a._health.update(ok=False, code="balance")
            a._balance_probe_at = 0.0
            monkeypatch.setattr(a, "ping", bad)
            assert await a.balance_block() == a.BALANCE_BLOCK and await a.balance_block() == a.BALANCE_BLOCK
            assert len(calls) == 1                      # probe at most every 5 min
            a._balance_probe_at = 0.0
            monkeypatch.setattr(a, "ping", good)
            assert await a.balance_block() is None      # recovered
        finally:
            a._health.clear()
            a._health.update(old)
    asyncio.run(run())
    assert a.classify(RuntimeError("Budget has been exceeded"))[0] == "balance"
