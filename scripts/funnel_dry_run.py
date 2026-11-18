"""QA helper: one real AI-funnel cycle per bot in PAPER, forcing the operating-hours gate open. Everything it creates is removed afterwards.
Usage: python scripts/funnel_dry_run.py [bybit|hfm|pepperstone] [threshold] [SYMBOL:long|short to skip stage 1 and force a stage-2 analysis]"""
import asyncio, sys, logging
sys.path.insert(0, "/app/backend")
logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
import deps, trade_guard as tg, funnel

BOTS = {"bybit": (deps.engine, deps.store), "hfm": (deps.fx_engine, deps.fx_store), "pepperstone": (deps.pep_engine, deps.pep_store)}


async def main(bot: str, threshold: int | None, force: str | None):
    eng, store = BOTS[bot]
    if store is not deps.store:
        await deps.store.load()   # global AI config
    await store.load()
    await eng.apply_config(store.cfg)
    eng.live, eng.running = False, True
    real = tg.hours_status
    tg.hours_status = lambda kind, now=None: {**real(kind, now), "in_hours": True, "window": "PRUEBA"}
    tg.in_hours = lambda kind, now=None: True
    if force:
        sym, side, *conf = force.split(":")
        if conf:   # SYMBOL:side:confidence → stage 2 mocked too (tests the threshold / shadow / execution paths without AI)
            async def forced2(kind, symbol, tf, cand, m, *a, **k):
                return {"decision": "BUY" if side == "long" else "SELL", "confidence": int(conf[0]), "sl": None, "tp": None, "rule": None, "reasoning": "veredicto simulado por la prueba", "model": "prueba", "ms": 0}
            funnel.stage2 = forced2

        async def forced(kind, tf, rows, limit, usage_kind):
            return {"candidates": [{"symbol": sym, "side": side, "reason": "candidato forzado por la prueba"}] if sym in rows else [], "model": "prueba", "ms": 0}
        funnel.stage1 = forced
    settings = await eng.get_settings()
    if threshold is not None:
        settings.ai_min_confidence = threshold
    await (eng.market.fetch_tickers() if eng.guard_kind == "crypto" else eng.market.fetch_tickers(settings.symbols))
    acc = await eng.db.account.find_one({"_id": "main"})
    before = {c: {d["_id"] async for d in eng.db[c].find({}, {"_id": 1})} for c in ("signals", "positions", "shadow_positions", "pending_entries", "logs")}
    n = await funnel.run(eng, settings)
    print(f"\n=== {bot}: {n} decisión(es) BUY/SELL · TF {settings.timeframe} · umbral {settings.ai_min_confidence}% ===")
    for s, a in eng.analysis.items():
        f = a.get("funnel")
        print(f"  {s:10} {a['trend']:8} RSI {a['rsi']:5} ADX {a['adx']:5} {a['activity_kind']} {a['activity']}× → " + (f"etapa {f['stage']} {f['state']} {f['confidence'] or ''} · {f['text'][:110]}" if f else "sin nota (pasa a etapa 1)"))
    for c, ids in before.items():
        async for d in eng.db[c].find({"_id": {"$nin": list(ids)}}):
            if c == "logs":
                print("  LOG", d["level"], d["message"][:260])
            elif c != "logs":
                print(f"  NEW {c}:", {k: d.get(k) for k in ("symbol", "side", "ai_decision", "ai_confidence", "kind", "entry_price", "limit_price", "price", "qty", "sl", "tp", "executed", "note") if d.get(k) is not None})
        await eng.db[c].delete_many({"_id": {"$nin": list(ids)}})
    await eng.db.account.replace_one({"_id": "main"}, acc)

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "bybit", int(sys.argv[2]) if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else None))
