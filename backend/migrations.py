from models import now_iso


async def _hfm_alert_180(engine, db):
    """Default HFM disconnection alert went 60 → 180 s (short demo-server restarts are normal); existing installs still store the old default."""
    await db.fx_config.update_one({"_id": "main", "hfm_alert_after_sec": 60}, {"$set": {"hfm_alert_after_sec": 180}})


MIGRATIONS = {"2026-10-22-hfm-alert-180": _hfm_alert_180}


async def run(engine, db):
    done = {d["_id"] async for d in db.migrations.find({}, {"_id": 1})}
    for key, fn in MIGRATIONS.items():
        if key in done:
            continue
        await fn(engine, db)
        await db.migrations.insert_one({"_id": key, "at": now_iso()})
