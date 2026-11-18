"""/api/cron/* webhooks (Bearer WEBHOOK_CRON_SECRET). They ack 2xx immediately and run the work in background."""
import os
import hmac
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pymongo.errors import DuplicateKeyError

import notify
import weekly
import backup
from review import run_auto_review
from deps import engine, db, store, FX_BOTS
from fx import review as fx_review
from fx import weekly as fx_weekly

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cron")
RUN_ID_TTL_DAYS = 30
CRYPTO = {"engine": engine, "db": db, "store": store, "review": run_auto_review, "weekly": weekly.send_weekly, "label": "", "prefix": ""}
FX_LIST = [{"engine": e, "db": e.db, "store": s, "review": fx_review.run_auto_review, "weekly": fx_weekly.send, "label": f" {e.profile.broker_long}", "prefix": fx_weekly.prefix(e)} for e, s in FX_BOTS]
BOTS = [CRYPTO, *FX_LIST]


async def init():
    """Processed run_ids live in db.cron_runs (survive restarts); Mongo expires them after RUN_ID_TTL_DAYS."""
    await db.cron_runs.create_index("at", expireAfterSeconds=RUN_ID_TTL_DAYS * 86400)


def _auth(request: Request):
    auth = request.headers.get("authorization", "")
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    if not (auth.lower().startswith("bearer ") and secret and hmac.compare_digest(auth[7:], secret)):
        raise HTTPException(401, "No autorizado")


async def _run_id(request: Request) -> str:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Cuerpo inválido")
    if body is not None and not isinstance(body, dict):
        raise HTTPException(400, "Cuerpo inválido")
    return str(request.headers.get("x-webhook-id") or (body or {}).get("run_id") or "")[:200]


async def _accept(request: Request) -> bool:
    """Auth + idempotency. Returns True when this run_id was already processed (persisted in Mongo)."""
    _auth(request)
    run_id = await _run_id(request)
    if not run_id:
        return False
    try:
        await db.cron_runs.insert_one({"_id": run_id, "path": request.url.path, "at": datetime.now(timezone.utc)})
    except DuplicateKeyError:
        return True
    return False


async def _safe_auto_review(bot: dict):
    try:
        await bot["review"](bot["engine"], bot["db"], bot["store"])
    except Exception as e:
        logger.exception("scheduled auto review")
        await bot["engine"].log("ERROR", f"Revisión IA automática falló: {str(e)[:160]}")
        await notify.notify(f"{bot['prefix']}⚠️ <b>Revisión IA automática{bot['label']} falló</b>\n{str(e)[:300]}", "review", bot=notify.bot_of(bot["engine"]))


async def _safe_weekly(bot: dict):
    eng, d = bot["engine"], bot["db"]
    try:
        last = await d.weekly.find_one({"_id": "latest"}, {"_id": 0, "sent_at": 1})
        if last and last.get("sent_at") and (datetime.now(timezone.utc) - datetime.fromisoformat(last["sent_at"])).days < 6:
            await eng.log("INFO", f"Resumen semanal omitido: ya se envió el {last['sent_at'][:10]}")
            return
        await bot["weekly"](eng, d)
    except Exception as e:
        logger.exception("weekly summary")
        await eng.log("ERROR", f"Resumen semanal falló: {str(e)[:160]}")


def _review_due(cfg) -> bool:
    now = datetime.now(timezone.utc)
    due = cfg.auto_review_enabled and (cfg.auto_review_frequency == "daily" or (now.weekday() + 1) % 7 == cfg.auto_review_day)
    if due and cfg.auto_review_last_at:
        due = (now - datetime.fromisoformat(cfg.auto_review_last_at)).total_seconds() > 20 * 3600
    return bool(due)


@router.post("/ai-review")
async def cron_ai_review(request: Request):
    """Daily 08:00 UTC trigger for both bots; each one decides if a review is due (frequency/day, > 20 h since the last)."""
    if await _accept(request):
        return {"accepted": True, "duplicate": True}
    due = [_review_due(b["store"].cfg) for b in BOTS]
    for b, d in zip(BOTS, due):
        if d:
            asyncio.create_task(_safe_auto_review(b))
    return {"accepted": True, "scheduled": due[0], "scheduled_fx": due[1], "scheduled_pepper": due[2]}


@router.post("/weekly-summary")
async def cron_weekly_summary(request: Request):
    """Sunday 20:00 UTC: weekly Telegram summary of both bots."""
    if await _accept(request):
        return {"accepted": True, "duplicate": True}
    for b in BOTS:
        asyncio.create_task(_safe_weekly(b))
    return {"accepted": True, "scheduled": True, "scheduled_fx": True, "scheduled_pepper": True}



TELEGRAM_MAX_MB = 49


async def _send_backup(rec: dict) -> dict:
    """Forwards a stored archive to Telegram as a document (≤ 49 MB) and records the outcome in db.meta.backup."""
    name, size_mb = rec["filename"], rec["size_mb"]
    upd = {"sent": False, "error": None, "sent_at": None}
    if not notify.is_on("system"):
        upd["error"] = "Telegram desactivado o aviso 'system' apagado"
    elif size_mb > TELEGRAM_MAX_MB:
        upd["error"] = f"{size_mb} MB supera el límite de Telegram ({TELEGRAM_MAX_MB} MB)"
        await notify.notify(f"🗄 <b>Copia de seguridad</b> {name} generada ({size_mb} MB) pero supera el límite de Telegram; queda guardada en el servidor.", "system")
    else:
        caption = (f"🗄 <b>Copia de seguridad semanal</b> · {name} · {size_mb} MB\n"
                   "Restaurar: Ajustes → Copias de seguridad → Restaurar desde archivo (o <code>bash deploy/restore.sh archivo</code>)")
        try:
            data = await asyncio.to_thread((backup.DIR / name).read_bytes)
            await notify.send_document(data, name, caption)
            upd.update(sent=True, sent_at=datetime.now(timezone.utc).isoformat())
            for bot in BOTS:
                await bot["engine"].log("INFO", f"Copia de seguridad {name} ({size_mb} MB) enviada por Telegram")
        except Exception as e:
            upd["error"] = str(e)[:200]
    await db.meta.update_one({"_id": "backup"}, {"$set": upd}, upsert=True)
    return upd


@router.post("/backup")
async def cron_backup(request: Request):
    """Nightly 03:00 UTC (deploy/backup.sh or the platform cron): creates an archive in BACKUP_DIR, prunes old ones and, on Sundays or with
    {"telegram": true}, sends it to Telegram as a document."""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        rec = await backup.create(db, "auto")
    except Exception as e:
        logger.exception("cron backup")
        for bot in BOTS:
            await bot["engine"].log("ERROR", f"Copia de seguridad nocturna falló: {str(e)[:160]}")
        raise HTTPException(500, f"No se pudo crear la copia: {str(e)[:160]}")
    for bot in BOTS:
        await bot["engine"].log("INFO", f"Copia de seguridad nocturna creada · {rec['filename']} · {rec['size_mb']} MB"
                                        + (f" · borradas {len(rec['removed'])} antiguas" if rec["removed"] else ""))
    out = {"filename": rec["filename"], "size_mb": rec["size_mb"], "removed": rec["removed"], "sent": False, "reason": None}
    if body.get("telegram") or datetime.now(timezone.utc).isoweekday() == 7:
        res = await _send_backup(rec)
        out.update(sent=res["sent"], reason=res["error"])
    return out


@router.post("/restore")
async def cron_restore(request: Request, file: UploadFile = File(...)):
    """deploy/restore.sh uploads an archive here; both bots are stopped, the database replaced and the state reloaded."""
    _auth(request)
    from routers.backup_routes import perform_restore
    data = await file.read()
    if not data:
        raise HTTPException(400, "Archivo vacío")
    try:
        backup.parse(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        out = await perform_restore(data, f"restore.sh · {(file.filename or 'archivo')[:80]}")
    except Exception as e:
        logger.exception("cron restore")
        raise HTTPException(500, f"La restauración falló: {str(e)[:160]}")
    return {**out, "bots_stopped": True}
