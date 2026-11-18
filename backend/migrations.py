from models import now_iso


async def _hfm_alert_180(engine, db):
    """Default HFM disconnection alert went 60 → 180 s (short demo-server restarts are normal); existing installs still store the old default."""
    await db.fx_config.update_one({"_id": "main", "hfm_alert_after_sec": 60}, {"$set": {"hfm_alert_after_sec": 180}})


async def _one_pct_stop(engine, db):
    """Every stop-out = 1% of equity: 1% risk, fixed ATR stop/target and the minimum lot may not risk more than that (all still editable)."""
    await db.settings.update_many({}, {"$set": {"risk_per_trade": 1.0, "atr_levels_enabled": True}})
    for name in ("fx_settings", "pep_settings"):
        await db[name].update_many({}, {"$set": {"risk_per_trade": 1.0, "atr_levels_enabled": True, "min_lot_max_risk": 1.0}})


MIGRATIONS = {"2026-10-22-hfm-alert-180": _hfm_alert_180, "2026-11-19-one-pct-stop": _one_pct_stop}


async def run(engine, db):
    done = {d["_id"] async for d in db.migrations.find({}, {"_id": 1})}
    for key, fn in MIGRATIONS.items():
        if key in done:
            continue
        await fn(engine, db)
        await db.migrations.insert_one({"_id": key, "at": now_iso()})
