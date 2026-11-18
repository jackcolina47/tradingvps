"""Panel backup routes (/api/backup/*): list/create/download archives, chunked upload from the user's disk (staged in Mongo) and restore."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import Binary
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import backup
import notify
import migrations
from deps import engine, db, store, fx_engine, pep_engine, FX_BOTS, ALL_ENGINES, ai_usage, protected_router
from routers import cron_routes

logger = logging.getLogger(__name__)
router = protected_router()
CONFIRM = "RESTAURAR"
UPLOAD_TTL_SEC = 3600


class RestoreRequest(BaseModel):
    confirm: str = ""
    upload_id: Optional[str] = None
    filename: Optional[str] = None


async def init():
    await db[backup.UPLOADS].create_index("at", expireAfterSeconds=UPLOAD_TTL_SEC)


async def _log_both(level: str, text: str):
    for eng in ALL_ENGINES:
        await eng.log(level, text)


async def reload_state():
    """Re-reads everything the processes keep in memory after the collections were replaced."""
    await store.load()
    for _e, st in FX_BOTS:
        await st.load()
    await ai_usage.load()
    await cron_routes.init()
    await init()
    for eng in ALL_ENGINES:
        eng.running = False
        eng.signal_keys = set()
        async for s in eng.db.signals.find({}, {"key": 1}).sort("_id", -1).limit(500):
            if s.get("key"):
                eng.signal_keys.add(s["key"])
        eng._external_warned = set()
    await migrations.run(engine, db)


async def perform_restore(data: bytes, origin: str) -> dict:
    """Stops both bots, replaces the database and reloads the in-memory state. Used by the panel and by deploy/restore.sh."""
    for eng in ALL_ENGINES:
        eng.running = False
    out = await backup.restore(db, data)
    await reload_state()
    when = str(out["created_at"] or "")[:16].replace("T", " ")
    await _log_both("INFO", f"Copia de seguridad restaurada ({origin}) · datos del {when} · {out['documents']} documentos · "
                            "los tres bots quedan DETENIDOS: revisa posiciones y ajustes antes de iniciar")
    await notify.notify(f"🗄 <b>Copia de seguridad restaurada</b> · datos del {when} · {out['documents']} documentos\n"
                        "Los tres bots quedan detenidos: revisa posiciones y ajustes antes de iniciarlos.", "system")
    return out


@router.get("/backup")
async def backup_status():
    meta = await db.meta.find_one({"_id": "backup"}, {"_id": 0}) or {}
    try:
        backups, error = backup.list_backups(), None
    except Exception as e:  # unreadable/unwritable BACKUP_DIR must not hide the panel (restoring from a file still works)
        logger.exception("backup list")
        backups, error = [], f"No se pudo leer la carpeta de copias ({backup.DIR}): {str(e)[:120]}"
    return {"backups": backups, "last": meta, "keep_days": backup.KEEP_DAYS, "max_upload_mb": backup.MAX_UPLOAD_MB, "error": error,
            "bots_running": {"crypto": engine.running, "fx": fx_engine.running, "pepper": pep_engine.running}, "telegram": notify.is_on("system"), "confirm_word": CONFIRM}


@router.post("/backup/create")
async def backup_create():
    try:
        rec = await backup.create(db, "manual")
    except Exception as e:
        logger.exception("backup create")
        raise HTTPException(500, f"No se pudo crear la copia: {str(e)[:160]}")
    await _log_both("INFO", f"Copia de seguridad creada desde el panel · {rec['filename']} · {rec['size_mb']} MB")
    return rec


@router.get("/backup/download/{filename}")
async def backup_download(filename: str):
    try:
        path = backup.safe_path(filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@router.post("/backup/upload")
async def backup_upload(request: Request):
    """One chunk of a backup picked on the user's disk (headers X-Upload-Id, X-Chunk-Index, X-Chunk-Total; raw bytes body). Chunks are
    staged in Mongo (TTL 1 h) so the restore does not depend on pod-local files."""
    upload_id = request.headers.get("x-upload-id", "")
    try:
        backup.check_upload_id(upload_id)
        index, total = int(request.headers.get("x-chunk-index", "")), int(request.headers.get("x-chunk-total", ""))
    except ValueError as e:
        raise HTTPException(400, str(e) if str(e).startswith("Identificador") else "Cabeceras de subida inválidas")
    if total < 1 or not 0 <= index < total:
        raise HTTPException(400, "Índice de fragmento inválido")
    chunk = await request.body()
    if not chunk or len(chunk) > backup.CHUNK_MAX:
        raise HTTPException(400, "Fragmento vacío o demasiado grande")
    col = db[backup.UPLOADS]
    if index == 0:
        await col.delete_many({"upload_id": upload_id})
    received = await col.count_documents({"upload_id": upload_id})
    if received != index:
        raise HTTPException(409, "Fragmento fuera de orden · vuelve a iniciar la subida")
    if (index + 1) * backup.CHUNK_MAX > backup.MAX_UPLOAD_MB * 1048576:
        await col.delete_many({"upload_id": upload_id})
        raise HTTPException(413, f"El archivo supera {backup.MAX_UPLOAD_MB} MB")
    await col.insert_one({"_id": f"{upload_id}:{index}", "upload_id": upload_id, "index": index, "total": total, "data": Binary(chunk),
                          "at": datetime.now(timezone.utc)})
    return {"received": index + 1, "total": total, "complete": index + 1 == total}


async def _uploaded_bytes(upload_id: str) -> bytes:
    col = db[backup.UPLOADS]
    parts = await col.find({"upload_id": upload_id}, {"index": 1, "total": 1, "data": 1}).sort("index", 1).to_list(100000)
    if not parts or len(parts) != parts[0]["total"] or [p["index"] for p in parts] != list(range(len(parts))):
        raise HTTPException(400, "La subida no está completa · vuelve a seleccionar el archivo")
    return b"".join(bytes(p["data"]) for p in parts)


@router.post("/backup/restore")
async def backup_restore(req: RestoreRequest):
    if req.confirm.strip().upper() != CONFIRM:
        raise HTTPException(400, f"Escribe {CONFIRM} para confirmar")
    if any(e.running for e in ALL_ENGINES):
        raise HTTPException(409, "Detén los tres bots (Bybit, HotForex y Pepperstone) antes de restaurar una copia")
    if req.upload_id:
        try:
            backup.check_upload_id(req.upload_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        data = await _uploaded_bytes(req.upload_id)
        origin = "archivo subido desde el panel"
    elif req.filename:
        try:
            path = backup.safe_path(req.filename)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        data = await asyncio.to_thread(path.read_bytes)
        origin = f"copia {path.name}"
    else:
        raise HTTPException(400, "Indica una copia guardada o un archivo subido")
    try:
        backup.parse(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        out = await perform_restore(data, origin)
    except Exception as e:
        logger.exception("restore")
        raise HTTPException(500, f"La restauración falló: {str(e)[:160]}")
    finally:
        if req.upload_id:
            await db[backup.UPLOADS].delete_many({"upload_id": req.upload_id})
    return {**out, "origin": origin, "bots_stopped": True}
