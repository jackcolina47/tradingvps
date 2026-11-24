"""Learned rules: short trader-written (or AI-suggested) rules stored per bot and injected, with high priority, into the AI signal-evaluation prompt."""
import unicodedata
from datetime import datetime, timezone
from typing import Optional, List
from bson import ObjectId
from fastapi import HTTPException
from pydantic import BaseModel, Field

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
            "side": d.get("side") or "", "strategy": d.get("strategy") or "",
            "misses": d.get("misses", 0), "auto": bool(d.get("auto")), "ai_edited_at": d.get("ai_edited_at"), "ai_note": d.get("ai_note")}


async def list_rules(db) -> List[dict]:
    """Every rule + what its blocked signals would have done (shadow trades of signals it rejected)."""
    stats = {s["_id"]: s async for s in db.shadow_trades.aggregate([{"$match": {"rule_id": {"$ne": None}}}, {"$group": {
        "_id": "$rule_id", "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"}, "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
        "won": {"$sum": {"$max": ["$pnl", 0]}}, "lost": {"$sum": {"$min": ["$pnl", 0]}}}}])}
    out = []
    async for d in db.ai_rules.find().sort("_id", -1):
        s = stats.get(str(d["_id"]))
        out.append({**_pub(d), "blocked_closed": s["n"] if s else 0, "blocked_wins": s["wins"] if s else 0, "blocked_pnl": round(s["pnl"], 2) if s else 0.0,
                    "blocked_won": round(s["won"], 2) if s else 0.0, "blocked_lost": round(-s["lost"], 2) if s else 0.0,
                    "misses_left": max(0, MAX_MISSES - d.get("misses", 0)), "max_misses": MAX_MISSES})
    return out


async def auto_add(db, text: str, trade_id: str, verdict: str, symbol: str = "", side: str = "", strategy: str = "") -> Optional[dict]:
    """Rule learned from a closed trade; returns None when the same text already exists."""
    text = " ".join(text.split())[:300]
    if await db.ai_rules.find_one({"text": text}):
        return None
    doc = {"text": text, "symbol": symbol, "side": side, "strategy": strategy, "enabled": True, "source_trade_id": trade_id, "verdict": verdict, "auto": True,
           "created_at": datetime.now(timezone.utc).isoformat(), "hits": 0, "misses": 0}
    res = await db.ai_rules.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _pub(doc)


async def hit(db, rule_id: str) -> None:
    await db.ai_rules.update_one({"_id": ObjectId(rule_id)}, {"$inc": {"hits": 1}})


async def miss(db, rule_id: str) -> Optional[dict]:
    """Counts one winner the rule blocked. Switched off only with MAX_MISSES misses AND a losing shadow balance (its blocked signals won more than they lost)."""
    if not ObjectId.is_valid(rule_id):
        return None
    doc = await db.ai_rules.find_one_and_update({"_id": ObjectId(rule_id)}, {"$inc": {"misses": 1}}, return_document=True)
    if not doc:
        return None
    agg = await db.shadow_trades.aggregate([{"$match": {"rule_id": rule_id}}, {"$group": {"_id": None, "pnl": {"$sum": "$pnl"}}}]).to_list(1)
    shadow_pnl = agg[0]["pnl"] if agg else 0.0
    auto_off = doc.get("misses", 0) >= MAX_MISSES and shadow_pnl > 0 and doc.get("enabled", True)
    if auto_off:
        doc = await db.ai_rules.find_one_and_update({"_id": doc["_id"]}, {"$set": {"enabled": False}}, return_document=True)
    return {**_pub(doc), "auto_off": auto_off, "shadow_pnl": round(shadow_pnl, 2)}


TREND_STRATEGIES = {"ema_pullback", "supertrend_momentum", "trend_ema"}
MEAN_REVERSION_WORDS = ("reversion a la media", "bollinger")
TREND_ONLY_WORDS = ("pullback", "momentum", "ruptura", "breakout", "supertrend")


def relevant(rule: dict, strategy: str = None) -> bool:
    """Trend-following signals never get the rules written about mean reversion / Bollinger: they only add noise to the AI check."""
    text = unicodedata.normalize("NFKD", rule.get("text") or "").encode("ascii", "ignore").decode().lower()
    about_mr = any(w in text for w in MEAN_REVERSION_WORDS)
    if strategy in TREND_STRATEGIES:
        return not about_mr
    if strategy == "mean_reversion":  # the inverse: no pullback / momentum-breakout rules, unless the rule also talks about mean reversion
        return about_mr or not any(w in text for w in TREND_ONLY_WORDS)
    return True


async def active(db, symbol: str = None, side: str = None, strategy: str = None) -> List[dict]:
    """Enabled rules that apply to this signal: a rule scoped to a pair / side / strategy is only sent to the AI for matching signals."""
    q = {"enabled": True}
    if symbol:
        q["$or"] = [{"symbol": ""}, {"symbol": None}, {"symbol": symbol}]
    rules = [_pub(d) async for d in db.ai_rules.find(q).sort("_id", -1)]
    return [r for r in rules if (not side or r["side"] in ("", side)) and (not strategy or r["strategy"] in ("", strategy)) and relevant(r, strategy)]


async def add(db, body: RuleIn) -> dict:
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
        if await db.ai_rules.find_one({"text": upd["text"], "_id": {"$ne": ObjectId(rule_id)}}):
            raise HTTPException(409, "Ya existe otra regla con ese texto")
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
    return ("REGLAS APRENDIDAS DEL TRADER (prioridad alta: si la señal incumple alguna, RECHAZA o baja mucho la confianza y cita la regla; "
            "si apruebas y alguna regla habla de dónde colocar el stop o el objetivo y procede aplicarla, devuelve adjusted_sl/adjusted_tp acordes, "
            "que se aplicarán a la orden; si no, déjalos en null):\n"
            f"{lines}\n")


ACTIONS = ("DESACTIVAR", "MODIFICAR", "ELIMINAR")


async def event(db, action: str, text: str, reason: str = "", trade: dict = None, prev: str = None) -> None:
    """Audit trail of what the automatic learning did to the rules (shown in the settings panel)."""
    await db.ai_rule_events.insert_one({"at": datetime.now(timezone.utc).isoformat(), "action": action, "text": text, "prev": prev, "reason": reason[:300], **(trade or {})})


async def events(db, limit: int = 30) -> List[dict]:
    return [{k: v for k, v in d.items() if k != "_id"} async for d in db.ai_rule_events.find().sort("_id", -1).limit(limit)]


async def apply_action(db, rule: dict, action: str, text: str, reason: str, trade: dict) -> Optional[str]:
    """Applies one maintenance action decided by the trade post-mortem. Returns a log line, or None when nothing changed."""
    oid = ObjectId(rule["id"])
    if action == "DESACTIVAR" and rule["enabled"]:
        await db.ai_rules.update_one({"_id": oid}, {"$set": {"enabled": False, "ai_edited_at": datetime.now(timezone.utc).isoformat(), "ai_note": f"Apagada por la IA: {reason}"[:300]}})
    elif action == "ELIMINAR":
        await db.ai_rules.delete_one({"_id": oid})
    elif action == "MODIFICAR":
        text = " ".join((text or "").split())[:300]
        if len(text) < 5 or text == rule["text"] or await db.ai_rules.find_one({"text": text}):
            return None
        await db.ai_rules.update_one({"_id": oid}, {"$set": {"text": text, "ai_edited_at": datetime.now(timezone.utc).isoformat(), "ai_note": f"Reescrita por la IA: {reason}"[:300], "misses": 0}})
    else:
        return None
    await event(db, action, text if action == "MODIFICAR" else rule["text"], reason, trade, rule["text"] if action == "MODIFICAR" else None)
    verb = {"DESACTIVAR": "apagada", "ELIMINAR": "eliminada", "MODIFICAR": "reescrita"}[action]
    return f"Regla {verb}: «{rule['text']}»" + (f" → «{text}»" if action == "MODIFICAR" else "") + (f" · {reason}" if reason else "")


class MergeIn(BaseModel):
    ids: List[str] = Field(min_length=2, max_length=20)
    merged: str = Field(default="", max_length=300)
    keep_id: Optional[str] = None
    reason: str = Field(default="", max_length=300)


async def merge(db, body: MergeIn) -> dict:
    """Applies one accepted clean-up proposal: the group becomes one merged rule (or only `keep_id` survives)."""
    if not all(ObjectId.is_valid(i) for i in body.ids):
        raise HTTPException(404, "Regla no encontrada")
    docs = [d async for d in db.ai_rules.find({"_id": {"$in": [ObjectId(i) for i in body.ids]}})]
    if len(docs) < 2:
        raise HTTPException(409, "Esas reglas ya no existen: vuelve a analizar")
    text = " ".join(body.merged.split())
    if len(text) < 5 and body.keep_id not in [str(d["_id"]) for d in docs]:
        raise HTTPException(400, "Falta el texto de la regla fusionada")
    old = "; ".join(f"«{d['text']}»" for d in docs)
    if len(text) >= 5:
        await db.ai_rules.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
        doc = {"text": text, "symbol": docs[0].get("symbol") or "", "enabled": any(d.get("enabled", True) for d in docs), "auto": all(d.get("auto") for d in docs),
               **{k: (docs[0].get(k) or "") if len({d.get(k) or "" for d in docs}) == 1 else "" for k in ("side", "strategy")},
               "created_at": datetime.now(timezone.utc).isoformat(), "hits": sum(d.get("hits", 0) for d in docs), "misses": 0,
               "ai_edited_at": datetime.now(timezone.utc).isoformat(), "ai_note": f"Fusión de {len(docs)} reglas"}
        await db.ai_rules.delete_many({"text": text})
        doc["_id"] = (await db.ai_rules.insert_one(doc)).inserted_id
        await event(db, "FUSION", text, body.reason, prev=old)
        return _pub(doc)
    await db.ai_rules.delete_many({"_id": {"$in": [d["_id"] for d in docs if str(d["_id"]) != body.keep_id]}})
    kept = next(d for d in docs if str(d["_id"]) == body.keep_id)
    await event(db, "FUSION", kept["text"], body.reason, prev=old)
    return _pub(kept)
