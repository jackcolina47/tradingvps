import asyncio
import logging
from types import SimpleNamespace
from models import now_iso
from optimizer import walk_forward, WF_CANDLES
import notify

logger = logging.getLogger("wfcheck")
BAD = ("SOBREAJUSTADO", "NO_RENTABLE")
ICON = {"ROBUSTO": "✅", "DUDOSO": "🟡", "SOBREAJUSTADO": "⚠️", "NO_RENTABLE": "⚠️", "INSUFICIENTE": "⏳"}
job = {"status": "idle", "step": "", "started_at": None, "finished_at": None, "error": None}


async def _crypto_wf(engine, settings, initial: float, sym: str) -> dict:
    klines = await engine.market.fetch_history(sym, settings.timeframe, WF_CANDLES)
    return await asyncio.to_thread(walk_forward, klines, settings.strategy, settings.risk_per_trade, settings.leverage,
                                   settings.trailing_enabled, initial, sym, settings.signal_mode, settings.disabled_strategies,
                                   settings.symbol_params[sym])


async def _crypto_notify(engine, msg: str):
    await notify.notify(msg, "review", bot="bybit")


# A kit binds the weekly revalidation to one bot: how to run the walk-forward, its job state and how to notify.
CRYPTO = SimpleNamespace(wf=_crypto_wf, job=job, notify=_crypto_notify)


def job_state(kit=CRYPTO) -> dict:
    return dict(kit.job)


def _p(p: dict) -> str:
    return f"SL {p.get('sl_atr')}× TP {p.get('tp_atr')}× ATR {int(p.get('atr_period') or 14)}"


def _compact(wf: dict, params: dict, previous: str | None) -> dict:
    pick = ("trades", "expectancy_r", "profit_factor", "return_pct", "win_rate")
    a = wf["applied"] or wf
    return {"verdict": a["verdict"], "verdict_text": a["verdict_text"], "wfe": a["wfe"], "is_expectancy_r": a["is_expectancy_r"],
            "oos": {k: a["oos"][k] for k in pick}, "baseline_oos": {k: wf["baseline_oos"][k] for k in pick}, "process_verdict": wf["verdict"],
            "folds": len(wf["folds"]), "params": params, "previous_verdict": previous, "changed": previous is not None and previous != a["verdict"],
            "removed": False}


def _line(sym: str, r: dict) -> str:
    o = r["oos"]
    prev = f" (antes {r['previous_verdict']})" if r["changed"] else ""
    wfe = f" · WFE {r['wfe']:.2f}" if r["wfe"] is not None else ""
    return (f"{ICON.get(r['verdict'], '•')} <b>{sym.replace('USDT', '')}</b> {_p(r['params'])} · "
            f"{r['verdict']}{prev}{wfe} · OOS {o['expectancy_r']:+.2f}R en {o['trades']} ops (base {r['baseline_oos']['expectancy_r']:+.2f}R)")


async def run_check(engine, db, store, source: str = "auto", kit=CRYPTO) -> dict:
    settings = await engine.get_settings()
    acc = await db.account.find_one({"_id": "main"}, {"initial_balance": 1})
    prev = (await db.wf_checks.find_one({"_id": "latest"}) or {}).get("results", {})
    symbols = [s for s in settings.symbols if s in settings.symbol_params]
    results = {}
    for sym in symbols:
        kit.job["step"] = f"Validando {sym}"
        wf = await kit.wf(engine, settings, acc["initial_balance"], sym)
        results[sym] = _compact(wf, settings.symbol_params[sym], prev.get(sym, {}).get("verdict"))
    bad = [s for s, r in results.items() if r["verdict"] in BAD]
    removed = {}
    if bad and store.cfg.wf_auto_remove:
        current = (await engine.get_settings()).symbol_params
        removed = {s: settings.symbol_params[s] for s in bad if current.get(s) == settings.symbol_params[s]}
        if removed:
            await engine.update_settings({"symbol_params": {k: v for k, v in current.items() if k not in removed}}, "walk_forward",
                                         "Walk-forward: parámetros SL/TP sobreajustados o no rentables retirados de " + ", ".join(removed))
            for s in removed:
                results[s]["removed"] = True
            await engine.log("PLAN", "Walk-forward: SL/TP optimizados quitados por sobreajuste → valores por defecto en "
                                     + ", ".join(f"{s.replace('USDT', '')} ({_p(removed[s])})" for s in removed))
    doc = {"ran_at": now_iso(), "source": source, "timeframe": settings.timeframe, "checked": symbols, "results": results,
           "removed": removed, "auto_remove": bool(store.cfg.wf_auto_remove)}
    await db.wf_checks.update_one({"_id": "latest"}, {"$set": doc}, upsert=True)
    if not symbols:
        await engine.log("PLAN", "Walk-forward semanal: ningún par tiene SL/TP optimizados aplicados · nada que revalidar")
        return doc
    await engine.log("PLAN", f"Walk-forward {'semanal' if source == 'auto' else 'manual'} · {len(symbols)} pares con SL/TP aplicados · "
                             + ", ".join(f"{s.replace('USDT', '')} {results[s]['verdict']}" for s in symbols))
    if removed:
        n = len(removed)
        head = f"✂️ <b>Walk-forward: SL/TP quitados en {n} par{'es' if n > 1 else ''}</b>"
    elif bad:
        n = len(bad)
        head = f"⚠️ <b>Walk-forward: {n} par{'es' if n > 1 else ''} sobreajustado{'s' if n > 1 else ''}</b>"
    else:
        head = "🧪 <b>Walk-forward semanal · todo en orden</b>"
    msg = head + f"\nRevalidación de {len(symbols)} pares con SL/TP optimizados · {settings.timeframe}\n\n" + "\n".join(_line(s, results[s]) for s in symbols)
    pending = [s for s in bad if s not in removed]
    if removed:
        msg += "\n\n<b>Cambios aplicados</b>\n" + "\n".join(f"– {s.replace('USDT', '')}: {_p(p)} → valores por defecto de la estrategia" for s, p in removed.items())
        msg += "\nPuedes volver a optimizar el par cuando acumule más historial."
    if pending:
        msg += ("\n\n<b>Recomendación</b>: los parámetros optimizados de " + ", ".join(s.replace("USDT", "") for s in pending)
                + " ya no se sostienen fuera de muestra. Quítalos en Optimizador → «Volver a valores por defecto» o activa el borrado automático.")
    await kit.notify(engine, msg)
    return doc


async def start_check(engine, db, store, source: str = "manual", kit=CRYPTO) -> bool:
    job = kit.job
    if job["status"] == "running":
        return False
    job.update(status="running", started_at=now_iso(), finished_at=None, error=None, step="Preparando")

    async def _run():
        try:
            await run_check(engine, db, store, source, kit)
            job["status"] = "done"
        except Exception as e:
            logger.exception("walk-forward check")
            job.update(status="error", error=str(e)[:300])
            await engine.log("ERROR", f"Revalidación walk-forward falló: {str(e)[:160]}")
            if source == "auto":
                await kit.notify(engine, f"⚠️ <b>Revalidación walk-forward falló</b>\n{str(e)[:300]}")
        finally:
            job["finished_at"] = now_iso()
            job["step"] = ""

    asyncio.create_task(_run())
    return True


async def latest(db):
    return await db.wf_checks.find_one({"_id": "latest"}, {"_id": 0})
