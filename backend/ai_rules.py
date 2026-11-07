"""Learned rules: short trader-written (or AI-suggested) rules stored per bot and injected, with high priority, into the AI signal-evaluation prompt."""
from datetime import datetime, timezone
from typing import Optional, List
from bson import ObjectId
from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_RULES = 30


class RuleIn(BaseModel):
    text: str = Field(min_length=5, max_length=300)
    symbol: Optional[str] = Field(default=None, max_length=20)
    source_trade_id: Optional[str] = None
    verdict: Optional[str] = None


class RulePatch(BaseModel):
    enabled: Optional[bool] = None
    text: Optional[str] = Field(default=None, min_length=5, max_length=300)
    symbol: Optional[str] = Field(default=None, max_length=20)


def _pub(d: dict) -> dict:
    return {"id": str(d["_id"]), "text": d["text"], "symbol": d.get("symbol") or "", "enabled": d.get("enabled", True),
            "source_trade_id": d.get("source_trade_id"), "verdict": d.get("verdict"), "created_at": d.get("created_at"), "hits": d.get("hits", 0)}


async def list_rules(db) -> List[dict]:
    return [_pub(d) async for d in db.ai_rules.find().sort("_id", -1)]


async def active(db, symbol: str = None) -> List[dict]:
    """Enabled rules that apply to `symbol` (global rules + symbol-specific ones)."""
    q = {"enabled": True}
    if symbol:
        q["$or"] = [{"symbol": ""}, {"symbol": None}, {"symbol": symbol}]
    return [_pub(d) async for d in db.ai_rules.find(q).sort("_id", -1).limit(MAX_RULES)]


async def add(db, body: RuleIn) -> dict:
    if await db.ai_rules.count_documents({}) >= MAX_RULES:
        raise HTTPException(400, f"Máximo {MAX_RULES} reglas: borra alguna antes de añadir otra")
    text = " ".join(body.text.split())
    if await db.ai_rules.find_one({"text": text}):
        raise HTTPException(409, "Esa regla ya existe")
    doc = {"text": text, "symbol": (body.symbol or "").upper().strip(), "enabled": True, "source_trade_id": body.source_trade_id, "verdict": body.verdict,
           "created_at": datetime.now(timezone.utc).isoformat(), "hits": 0}
    res = await db.ai_rules.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _pub(doc)


async def patch(db, rule_id: str, body: RulePatch) -> dict:
    if not ObjectId.is_valid(rule_id):
        raise HTTPException(404, "Regla no encontrada")
    upd = {k: (v.upper().strip() if k == "symbol" else v) for k, v in body.model_dump(exclude_none=True).items()}
    if "text" in upd:
        upd["text"] = " ".join(upd["text"].split())
    doc = await db.ai_rules.find_one_and_update({"_id": ObjectId(rule_id)}, {"$set": upd}, return_document=True) if upd else await db.ai_rules.find_one({"_id": ObjectId(rule_id)})
    if not doc:
        raise HTTPException(404, "Regla no encontrada")
    return _pub(doc)


async def remove(db, rule_id: str) -> None:
    if not ObjectId.is_valid(rule_id) or not (await db.ai_rules.delete_one({"_id": ObjectId(rule_id)})).deleted_count:
        raise HTTPException(404, "Regla no encontrada")


def prompt_lines(rules: Optional[List[dict]]) -> str:
    if not rules:
        return ""
    lines = "\n".join(f"- {r['text']}" + (f" (solo {r['symbol']})" if r.get("symbol") else "") for r in rules)
    return ("REGLAS APRENDIDAS DEL TRADER (prioridad alta: si la señal incumple alguna, RECHAZA o baja mucho la confianza y cita la regla):\n"
            f"{lines}\n")
