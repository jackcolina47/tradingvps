import asyncio
import pytest
import ai_funnel


def run(fails, exc, monkeypatch):
    calls, sleeps = [], []

    async def check(*a, **k):
        calls.append(1)
        if len(calls) <= fails:
            raise exc
        return {"violates": False}

    async def sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(ai_funnel, "rules_check", check)
    monkeypatch.setattr(ai_funnel.asyncio, "sleep", sleep)
    try:
        return asyncio.run(ai_funnel.rules_check_retry("BTCUSDT", "4h", {}, {}, [])), len(calls), sleeps
    except Exception as e:
        return e, len(calls), sleeps


def test_recovers_on_retry(monkeypatch):
    assert run(1, TimeoutError(), monkeypatch) == ({"violates": False}, 2, [2])
    assert run(2, RuntimeError("503"), monkeypatch) == ({"violates": False}, 3, [2, 5])


def test_blocks_after_three_attempts(monkeypatch):
    res, n, sleeps = run(9, TimeoutError(), monkeypatch)
    assert isinstance(res, TimeoutError) and n == 3 and sleeps == [2, 5]


@pytest.mark.parametrize("msg", ["insufficient_quota", "Budget has been exceeded", "invalid api key"])
def test_no_retry_on_balance_or_config(monkeypatch, msg):
    res, n, sleeps = run(9, RuntimeError(msg), monkeypatch)
    assert isinstance(res, RuntimeError) and n == 1 and sleeps == []
