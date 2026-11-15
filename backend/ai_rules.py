"""Learned rules: short trader-written (or AI-suggested) rules stored per bot and injected, with high priority, into the AI signal-evaluation prompt."""
from datetime import datetime, timezone
from typing import Optional, List
from bson import ObjectId
from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_RULES = 30
MAX_MISSES = 3  # winners a rule would have blocked before it is switched off automatically


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
            "source_trade_id": d.get("source_trade_id"), "verdict": d.get("verdict"), "created_at": d.get("created_at"), "hits": d.get("hits", 0),
            "misses": d.get("misses", 0), "auto": bool(d.get("auto"))}


async def list_rules(db) -> List[dict]:
    """Every rule + what its blocked signals would have done (shadow trades of signals it rejected)."""
    stats = {s["_id"]: s async for s in db.shadow_trades.aggregate([{"$match": {"rule_id": {"$ne": None}}}, {"$group": {
        "_id": "$rule_id", "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"}, "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}}}}])}
    out = []
    async for d in db.ai_rules.find().sort("_id", -1):
        s = stats.get(str(d["_id"]))
        out.append({**_pub(d), "blocked_closed": s["n"] if s else 0, "blocked_wins": s["wins"] if s else 0, "blocked_pnl": round(s["pnl"], 2) if s else 0.0})
    return out


async def auto_add(db, text: str, trade_id: str, verdict: str) -> Optional[dict]:
    """Rule learned from a closed trade. When the list is full the oldest switched-off rule makes room; returns None if nothing can be added."""
    text = " ".join(text.split())[:300]
    if await db.ai_rules.find_one({"text": text}):
        return None
    if await db.ai_rules.count_documents({}) >= MAX_RULES:
        old = await db.ai_rules.find_one_and_delete({"enabled": False}, sort=[("_id", 1)])
        if not old:
            return None
    doc = {"text": text, "symbol": "", "enabled": True, "source_trade_id": trade_id, "verdict": verdict, "auto": True,
           "created_at": datetime.now(timezone.utc).isoformat(), "hits": 0, "misses": 0}
    res = await db.ai_rules.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _pub(doc)


async def hit(db, rule_id: str) -> None:
    await db.ai_rules.update_one({"_id": ObjectId(rule_id)}, {"$inc": {"hits": 1}})


async def miss(db, rule_id: str) -> dict:
    doc = await db.ai_rules.find_one_and_update({"_id": ObjectId(rule_id)}, {"$inc": {"misses": 1}}, return_document=True)
    if doc.get("misses", 0) >= MAX_MISSES and doc.get("enabled", True):
        doc = await db.ai_rules.find_one_and_update({"_id": doc["_id"]}, {"$set": {"enabled": False}}, return_document=True)
    return _pub(doc)


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
    if upd.get("enabled"):
        upd["misses"] = 0  # switched back on by hand: the miss counter starts again
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
