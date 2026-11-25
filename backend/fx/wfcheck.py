"""Weekly walk-forward revalidation kit for the FX bots (shares wfcheck.run_check with the crypto bot); one kit (job state) per engine."""
import asyncio
from types import SimpleNamespace
from fx.optimizer import walk_forward, WF_CANDLES


def new_job() -> dict:
    return {"status": "idle", "step": "", "started_at": None, "finished_at": None, "error": None}


async def _wf(engine, settings, initial: float, sym: str) -> dict:
    klines = await engine.market.fetch_klines(sym, settings.timeframe, WF_CANDLES, cache_sec=600)
    return await asyncio.to_thread(walk_forward, klines, settings, initial, sym, dict(engine.market.tickers), engine.costs, engine.fxr())


async def _notify(engine, msg: str):
    await engine.tg(msg, "review")


def make_kit(engine) -> SimpleNamespace:
    return SimpleNamespace(wf=_wf, job=engine.wf_job, notify=_notify)
