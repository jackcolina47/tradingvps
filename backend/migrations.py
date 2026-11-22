from models import now_iso
from profiles import profile_patch
from notify import notify as tg


async def _activo(engine, db):
    s = await engine.get_settings()
    if s.risk_profile != 3:
        return
    await engine.update_settings(profile_patch(2), "system", "Migración inicial: perfil Activo")
    await engine.log("INFO", "Perfil de riesgo bajado de Agresivo a Activo (ajuste acordado tras la actualización)")
    await tg("🎚 <b>Perfil de riesgo</b>: Agresivo → <b>Activo</b> (10 pares · 1h · criterios relajados · 1.5%/op · 5 posiciones · 5x), ajuste acordado tras la actualización.", "review")


async def _hfm_alert_180(engine, db):
    """Default HFM disconnection alert went 60 → 180 s (short demo-server restarts are normal); existing installs still store the old default."""
    await db.fx_config.update_one({"_id": "main", "hfm_alert_after_sec": 60}, {"$set": {"hfm_alert_after_sec": 180}})


async def _ema_cross_off(engine, db):
    """New opt-in strategy ema_cross: keep it switched off in every existing bot (stored lists predate the key)."""
    for coll in ("settings", "fx_settings", "pep_settings"):
        await db[coll].update_one({"_id": "main"}, {"$addToSet": {"disabled_strategies": "ema_cross"}})


async def _pepper_ema_only(engine, db):
    """Pepperstone becomes a single-strategy bot («Cruce EMA 9 y 21 Personalizado»). Nothing is deleted here: the bot is left stopped with the new pair list and
    the user wipes history/positions with the «Reiniciar» button (explicit action). Positions of the old strategies keep their SL/TP until then."""
    from fx.symbols import PEPPER_SYMBOLS
    await db.pep_settings.update_one({"_id": "main"}, {"$set": {"symbols": list(PEPPER_SYMBOLS), "strategy": "all", "bot_running": False, "ema_test_enabled": True,
                                                                "ema_test_symbols": list(PEPPER_SYMBOLS), "ema_test_adx_filter": False, "ai_enabled": False}})
    await db.pep_logs.insert_one({"level": "INFO", "ts": now_iso(), "message": "Pepperstone pasa a una sola estrategia (Cruce EMA 9 y 21 Personalizado) · bot detenido · "
                                  "pulsa «Reiniciar» para empezar de cero (borra posiciones paper, historial y señales)"})


async def _pepper_exits(engine, db):
    """Pepperstone: «parcial 50% + breakeven» becomes three independent exits. An enabled old setting is carried over as partial 50 % + breakeven at the same ATR multiple."""
    s = await db.pep_settings.find_one({"_id": "main"}) or {}
    if s.get("ema_test_partial_enabled"):
        k = s.get("ema_test_partial_atr", 1.5)
        await db.pep_settings.update_one({"_id": "main"}, {"$set": {"ema_partial_on": True, "ema_partial_pct": 50.0, "ema_partial_at_atr": k, "ema_be_on": True, "ema_be_atr": k,
                                                                    "ema_test_partial_enabled": False}})


async def _pepper_crypto_usdt(engine, db):
    """Pepperstone crypto pairs renamed XXXUSD → XXXUSDT everywhere they are stored (settings, positions, history, signals). Nothing is deleted."""
    from fx.symbols import CRYPTO
    ren = {s[:-1]: s for s in CRYPTO}
    s = await db.pep_settings.find_one({"_id": "main"}) or {}
    upd = {k: [ren.get(x, x) for x in s[k]] for k in ("symbols", "ema_test_symbols") if isinstance(s.get(k), list)}
    if isinstance(s.get("symbol_params"), dict):
        upd["symbol_params"] = {ren.get(k, k): v for k, v in s["symbol_params"].items()}
    if upd:
        await db.pep_settings.update_one({"_id": "main"}, {"$set": upd})
    for col in ("positions", "trades", "signals", "shadow_positions", "shadow_trades", "pending_orders"):
        for old, new in ren.items():
            await db[f"pep_{col}"].update_many({"symbol": old}, {"$set": {"symbol": new}})


MIGRATIONS = {"2026-10-20-activo": _activo, "2026-10-22-hfm-alert-180": _hfm_alert_180, "2026-10-27-ema-cross-off": _ema_cross_off, "2026-11-21-pepper-ema-only": _pepper_ema_only, "2026-11-22-pepper-exits": _pepper_exits, "2026-11-22-pepper-crypto-usdt": _pepper_crypto_usdt}


async def run(engine, db):
    done = {d["_id"] async for d in db.migrations.find({}, {"_id": 1})}
    for key, fn in MIGRATIONS.items():
        if key in done:
            continue
        await fn(engine, db)
        await db.migrations.insert_one({"_id": key, "at": now_iso()})
