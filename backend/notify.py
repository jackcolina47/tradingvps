import asyncio
import logging
import httpx

logger = logging.getLogger("notify")
TG = "https://api.telegram.org/bot{token}/{method}"
_cfg = {"enabled": False, "token": "", "chat_id": "", "chat_ids": [], "alerts": {}}


def configure(enabled: bool, token: str, chat_id: str, alerts: dict, chat_id_2: str = ""):
    ids = [str(c).strip() for c in (chat_id, chat_id_2) if c and str(c).strip()]
    ids = list(dict.fromkeys(ids))  # the extra chat is ignored when it repeats the main one
    _cfg.update(enabled=enabled, token=token or "", chat_id=ids[0] if ids else "", chat_ids=ids, alerts=alerts or {})


def chat_ids() -> list:
    return list(_cfg["chat_ids"])


def is_on(kind: str = None) -> bool:
    return bool(_cfg["enabled"] and _cfg["token"] and _cfg["chat_ids"] and (kind is None or _cfg["alerts"].get(kind, True)))


async def _send_one(token: str, chat_id: str, text: str) -> bool:
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.post(TG.format(token=token, method="sendMessage"),
                         json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Error de Telegram"))
    return True


async def _fan_out(targets: list, coro_factory) -> list:
    """Runs one delivery per chat concurrently; returns [{chat_id, ok, error}]. Raises only when every chat failed."""
    results = await asyncio.gather(*(coro_factory(cid) for cid in targets), return_exceptions=True)
    out = [{"chat_id": cid, "ok": not isinstance(r, Exception), "error": str(r)[:160] if isinstance(r, Exception) else None} for cid, r in zip(targets, results)]
    if not any(o["ok"] for o in out):
        raise RuntimeError("; ".join(f"{o['chat_id']}: {o['error']}" for o in out))
    for o in out:
        if not o["ok"]:
            logger.warning(f"telegram chat {o['chat_id']}: {o['error']}")
    return out


async def send(text: str, token: str = None, chat_id: str = None) -> bool:
    """Sends to every configured chat (or only to `chat_id` when given). True if at least one delivery succeeded."""
    token = token or _cfg["token"]
    targets = [str(chat_id)] if chat_id else _cfg["chat_ids"]
    if not token or not targets:
        raise RuntimeError("Telegram no configurado: faltan token o chat ID")
    await _fan_out(targets, lambda cid: _send_one(token, cid, text))
    return True


async def send_detailed(text: str) -> list:
    """Like send() but returns the per-chat outcome (used by the test button)."""
    if not _cfg["token"] or not _cfg["chat_ids"]:
        raise RuntimeError("Telegram no configurado: faltan token o chat ID")
    return await _fan_out(_cfg["chat_ids"], lambda cid: _send_one(_cfg["token"], cid, text))


async def notify(text: str, kind: str):
    """Fire-and-forget alert honouring the per-kind toggles; never raises."""
    if not is_on(kind):
        return False
    try:
        return await send(text)
    except Exception as e:
        logger.warning(f"telegram {kind}: {e}")
        return False


async def _send_document_one(token: str, chat_id: str, data: bytes, filename: str, caption: str) -> bool:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(TG.format(token=token, method="sendDocument"), data={"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                         files={"document": (filename, data, "application/octet-stream")})
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(out.get("description", "Error de Telegram"))
    return True


async def send_document(data: bytes, filename: str, caption: str = "") -> bool:
    """Uploads a file to every configured chat (Telegram bots accept up to 50 MB)."""
    token = _cfg["token"]
    if not token or not _cfg["chat_ids"]:
        raise RuntimeError("Telegram no configurado: faltan token o chat ID")
    await _fan_out(_cfg["chat_ids"], lambda cid: _send_document_one(token, cid, data, filename, caption))
    return True
async def get_chats(token: str) -> list:
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(TG.format(token=token, method="getUpdates"), params={"limit": 100})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Token de Telegram inválido"))
    chats = {}
    for u in data.get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member", {}).get("chat") and {"chat": u["my_chat_member"]["chat"]}
        chat = (msg or {}).get("chat")
        if chat:
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or str(chat["id"])
            chats[chat["id"]] = {"id": str(chat["id"]), "name": name, "type": chat.get("type", "private")}
    return list(chats.values())


async def get_me(token: str) -> dict:
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(TG.format(token=token, method="getMe"))
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Token de Telegram inválido"))
    return {"username": data["result"].get("username"), "name": data["result"].get("first_name")}
