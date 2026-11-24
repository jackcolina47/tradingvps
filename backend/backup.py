"""Native backup/restore of the whole database (both bots) as a gzip JSON-lines archive: a header line followed by one line per
document in MongoDB Extended JSON (ObjectId/datetime survive). Created from the panel, the nightly cron or deploy/backup.sh; restored
from the panel (upload) or deploy/restore.sh. No mongodump/mongorestore needed."""
import io
import asyncio
import os
import gzip
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from bson import json_util

logger = logging.getLogger("backup")
FORMAT = "aitrader-backup"
VERSION = 1
DIR = Path(os.environ.get("BACKUP_DIR") or Path(__file__).parent / "backups")
UPLOADS = "backup_uploads"
KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "14"))
KEEP_MIN = 3
MAX_UPLOAD_MB = 200
CHUNK_MAX = 1048576
SKIP = {"cron_runs", UPLOADS}
AUTH_KEYS = ("password_hash", "token_version")
BATCH = 500
JSON_OPTS = json_util.RELAXED_JSON_OPTIONS


def _mb(n: int) -> float:
    return round(n / 1048576, 2)


def _entry(p: Path) -> dict:
    st = p.stat()
    return {"filename": p.name, "size_mb": _mb(st.st_size), "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}


def list_backups() -> list:
    DIR.mkdir(parents=True, exist_ok=True)
    files = sorted((p for p in DIR.glob("*.json.gz") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return [_entry(p) for p in files]


def safe_path(filename: str) -> Path:
    name = Path(filename or "").name
    if not name or name != filename or not name.endswith(".json.gz"):
        raise ValueError("Nombre de archivo inválido")
    p = DIR / name
    if not p.is_file():
        raise FileNotFoundError("Copia no encontrada")
    return p


def prune() -> list:
    """Deletes archives older than KEEP_DAYS but always keeps the newest KEEP_MIN."""
    removed = []
    files = sorted((p for p in DIR.glob("*.json.gz") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[KEEP_MIN:]:
        if time.time() - p.stat().st_mtime > KEEP_DAYS * 86400:
            p.unlink(missing_ok=True)
            removed.append(p.name)
    return removed


async def _collections(db) -> list:
    return sorted(n for n in await db.list_collection_names() if not n.startswith("system.") and n not in SKIP)


async def dump(db) -> bytes:
    names = await _collections(db)
    counts = {n: await db[n].count_documents({}) for n in names}
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        header = {"format": FORMAT, "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "db": db.name, "collections": counts}
        gz.write((json.dumps(header, ensure_ascii=False) + "\n").encode())
        for n in names:
            i = 0
            async for doc in db[n].find({}):
                gz.write((json_util.dumps({"c": n, "d": doc}, json_options=JSON_OPTS, ensure_ascii=False) + "\n").encode())
                i += 1
                if i % 200 == 0:
                    await asyncio.sleep(0)  # lets the bots' loops run while a large collection is being compressed
    return buf.getvalue()


def _write(path, data: bytes) -> list:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return prune()


async def create(db, source: str = "manual") -> dict:
    data = await dump(db)
    name = f"{db.name}-{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}.json.gz"
    path = DIR / name
    removed = await asyncio.to_thread(_write, path, data)
    rec = {**_entry(path), "source": source}
    await db.meta.update_one({"_id": "backup"}, {"$set": {"last_at": rec["created_at"], "filename": name, "size_mb": rec["size_mb"], "source": source}}, upsert=True)
    return {**rec, "removed": removed}


def parse(data: bytes) -> tuple:
    """Returns (header, {collection: [docs]}); raises ValueError on anything that is not one of our archives."""
    try:
        raw = gzip.decompress(data)
    except Exception:
        raise ValueError("El archivo no es una copia válida (.json.gz)")
    lines = raw.split(b"\n")
    try:
        header = json.loads(lines[0])
    except Exception:
        raise ValueError("El archivo no es una copia válida (cabecera ilegible)")
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise ValueError("El archivo no es una copia de este bot")
    if int(header.get("version", 0)) > VERSION:
        raise ValueError("La copia fue creada por una versión más reciente del bot")
    docs: dict = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        try:
            row = json_util.loads(line, json_options=JSON_OPTS)
            docs.setdefault(row["c"], []).append(row["d"])
        except Exception:
            raise ValueError("La copia está dañada (línea ilegible)")
    return header, docs


async def restore(db, data: bytes) -> dict:
    """Replaces every collection with the archive contents. Keeps the current panel password/session version and leaves both bots stopped."""
    header, docs = parse(data)
    cfg = await db.config.find_one({"_id": "main"}, {k: 1 for k in AUTH_KEYS}) or {}
    auth = {k: cfg[k] for k in AUTH_KEYS if k in cfg}
    existing = set(await _collections(db))
    restored = {}
    for name in sorted(existing | set(docs)):
        await db[name].drop()
        rows = docs.get(name, [])
        for i in range(0, len(rows), BATCH):
            await db[name].insert_many(rows[i:i + BATCH], ordered=False)
        restored[name] = len(rows)
    if auth:
        await db.config.update_one({"_id": "main"}, {"$set": auth}, upsert=True)
    for col in ("settings", "fx_settings", "pep_settings"):
        await db[col].update_one({"_id": "main"}, {"$set": {"bot_running": False}})
    return {"created_at": header.get("created_at"), "collections": restored, "documents": sum(restored.values())}


def check_upload_id(upload_id: str):
    if not upload_id or not 8 <= len(upload_id) <= 64 or not all(c in "0123456789abcdef-" for c in upload_id):
        raise ValueError("Identificador de subida inválido")
