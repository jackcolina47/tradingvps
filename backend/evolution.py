"""Evolution log: every settings change (AI reviews, optimizer, walk-forward, risk profile, manual) is recorded with the account state at that
moment; the timeline then measures how the bot performed between consecutive changes (after vs. before)."""
from datetime import datetime, timedelta
from bson import ObjectId
from models import now_iso

IGNORE = {"bot_running", "plan_scenario", "plan_target_monthly", "plan_monthly_deposit"}
SOURCES = {"auto_review": "Revisión IA automática", "optimize_all": "Optimizar todo con IA", "optimizer": "IA sobre el optimizador", "plan": "IA sobre el plan",
           "walk_forward": "Revalidación walk-forward", "profile": "Perfil de riesgo", "manual": "Ajuste manual", "system": "Sistema", "revert": "Deshecho manualmente",
           "chat": "Chat IA expert (confirmado por ti)", "lab": "Modo laboratorio", "reopt": "Re-optimización semanal", "auto_prune": "Poda automática de pares"}
AI_SOURCES = ("auto_review", "optimize_all", "optimizer", "plan", "chat")
MIN_TRADES = 5


def diff(before: dict, after: dict) -> dict:
    return {k: {"antes": before.get(k), "despues": after[k]} for k in after if k not in IGNORE and before.get(k) != after.get(k)}


async def record(engine, source: str, changes: dict, summary: str = "", extra: dict = None):
    if not changes:
        return None
    acc = await engine.account_summary()
    doc = {"ts": now_iso(), "source": source if source in SOURCES else "manual", "summary": (summary or "")[:600], "changes": changes,
           "equity": acc["equity"], "net_deposits": acc.get("net_deposits", 0.0), "total_trades": acc["total_trades"], "mode": acc["mode"], **(extra or {})}
    await engine.db.changes.insert_one(doc)
    return doc


def _fmt_day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m %H:%M")


async def revert(engine, event_id: str, source: str = "revert", summary: str = "") -> dict:
    """Puts back the 'antes' values of an event for the keys that still hold its 'despues' value; records the revert as a new event."""
    try:
        oid = ObjectId(event_id)
    except Exception:
        raise ValueError("Cambio no encontrado")
    ev = await engine.db.changes.find_one({"_id": oid})
    if not ev:
        raise ValueError("Cambio no encontrado")
    if ev.get("reverted_at"):
        raise ValueError("Ese cambio ya fue deshecho")
    current = (await engine.get_settings()).model_dump()
    patch = {k: c["antes"] for k, c in ev["changes"].items() if c.get("antes") is not None and current.get(k) == c["despues"]}
    skipped = [k for k in ev["changes"] if k not in patch]
    if not patch:
        raise ValueError("Ese cambio ya no está vigente: los ajustes afectados se modificaron después")
    label = SOURCES.get(ev["source"], ev["source"])
    text = f"Deshecho el cambio «{label}» del {_fmt_day(ev['ts'])} ({', '.join(patch)})" + (f" · {summary}" if summary else "")
    await engine.update_settings(patch, source, text, {"revert_of": event_id, "revert_of_ts": ev["ts"], "revert_of_source": ev["source"]})
    await engine.db.changes.update_one({"_id": oid}, {"$set": {"reverted_at": now_iso(), "reverted_by": source}})
    return {"reverted": list(patch), "skipped": skipped, "event": {"id": event_id, "source": ev["source"], "ts": ev["ts"]}}


def _agg(rows: list) -> dict:
    n = len(rows)
    pnl = sum(r["pnl"] for r in rows)
    wins = [r for r in rows if r["pnl"] > 0]
    gw = sum(r["pnl"] for r in wins)
    gl = sum(r["pnl"] for r in rows if r["pnl"] <= 0)
    return {"trades": n, "pnl": round(pnl, 2), "win_rate": round(len(wins) / n * 100, 1) if n else 0,
            "avg_pnl": round(pnl / n, 2) if n else 0, "profit_factor": round(gw / abs(gl), 2) if gl else None}


def verdict(before: dict, after: dict) -> str:
    if after["trades"] < MIN_TRADES:
        return "PENDIENTE"
    if before["trades"] < MIN_TRADES:
        return "MEJOR" if after["pnl"] > 0 else "PEOR" if after["pnl"] < 0 else "IGUAL"
    delta = after["avg_pnl"] - before["avg_pnl"]
    tol = max(0.02 * max(abs(before["avg_pnl"]), abs(after["avg_pnl"])), 0.01)
    return "MEJOR" if delta > tol else "PEOR" if delta < -tol else "IGUAL"


async def timeline(engine, limit: int = 40) -> dict:
    db = engine.db
    events = await db.changes.find({}).sort("ts", -1).limit(limit).to_list(limit)
    for ev in events:
        ev["id"] = str(ev.pop("_id"))
    trades = await db.trades.find({}, {"_id": 0, "closed_at": 1, "pnl": 1}).to_list(20000)
    acc = await engine.account_summary()
    now = now_iso()
    out = []
    for i, ev in enumerate(events):
        end = events[i - 1]["ts"] if i > 0 else now
        prev_ts = events[i + 1]["ts"] if i + 1 < len(events) else None
        dur = datetime.fromisoformat(end) - datetime.fromisoformat(ev["ts"])
        before_start = prev_ts or (datetime.fromisoformat(ev["ts"]) - max(dur, timedelta(days=7))).isoformat()
        after = _agg([t for t in trades if ev["ts"] <= t["closed_at"] < end])
        before = _agg([t for t in trades if before_start <= t["closed_at"] < ev["ts"]])
        equity_end = events[i - 1]["equity"] if i > 0 else acc["equity"]
        nd_end = (events[i - 1].get("net_deposits") or 0.0) if i > 0 else (acc.get("net_deposits") or 0.0)
        delta = (equity_end - nd_end) - (ev["equity"] - (ev.get("net_deposits") or 0.0))
        out.append({**ev, "source_label": SOURCES.get(ev["source"], ev["source"]), "ai": ev["source"] in AI_SOURCES, "window_end": end,
                    "window_days": round(dur.total_seconds() / 86400, 1), "ongoing": i == 0, "after": after, "before": before,
                    "equity_end": equity_end, "equity_delta_pct": round(delta / ev["equity"] * 100, 2) if ev["equity"] else 0,
                    "verdict": verdict(before, after), "reverted_at": ev.get("reverted_at"), "revert_of": ev.get("revert_of"),
                    "revertible": ev["source"] != "lab" and not ev.get("reverted_at") and any(c.get("antes") is not None for c in ev["changes"].values())})
    verdicts = [e["verdict"] for e in out]
    ai_verdicts = [e["verdict"] for e in out if e["ai"]]
    return {"events": out, "total": await db.changes.count_documents({}), "ai_changes": len(ai_verdicts),
            "better": verdicts.count("MEJOR"), "worse": verdicts.count("PEOR"), "pending": verdicts.count("PENDIENTE"),
            "ai_better": ai_verdicts.count("MEJOR"), "ai_worse": ai_verdicts.count("PEOR"), "ai_pending": ai_verdicts.count("PENDIENTE"),
            "first_at": events[-1]["ts"] if events else None, "equity": acc["equity"], "min_trades": MIN_TRADES}


async def week_summary(engine, since: str) -> dict:
    """Changes of the last week for the Telegram weekly summary: what improved, what got worse and what the AI undid."""
    tl = await timeline(engine, 60)
    items = []
    for e in tl["events"]:
        if e["ts"] < since:
            continue
        items.append({"id": e["id"], "ts": e["ts"], "source": e["source"], "source_label": e["source_label"], "ai": e["ai"], "keys": list(e["changes"]),
                      "verdict": e["verdict"], "after": e["after"], "before": e["before"], "reverted_at": e.get("reverted_at"), "reverted_by": e.get("reverted_by"),
                      "revert_of_ts": e.get("revert_of_ts"), "revert_of_source": e.get("revert_of_source"), "is_revert": bool(e.get("revert_of"))})
    changes = [i for i in items if not i["is_revert"]]
    reverts = [i for i in items if i["is_revert"]]
    return {"changes": items, "total": len(changes), "ai_changes": sum(1 for i in changes if i["ai"]),
            "better": sum(1 for i in changes if i["verdict"] == "MEJOR"), "worse": sum(1 for i in changes if i["verdict"] == "PEOR"),
            "pending": sum(1 for i in changes if i["verdict"] == "PENDIENTE"),
            "reverted_by_ai": sum(1 for i in reverts if i["source"] in AI_SOURCES), "reverted_manual": sum(1 for i in reverts if i["source"] not in AI_SOURCES)}


async def ai_context(engine, limit: int = 12) -> dict:
    """Compact timeline for the AI advisor: lets it judge (and undo) changes that made results worse."""
    tl = await timeline(engine, limit)
    items = []
    for e in tl["events"]:
        items.append({"id": e["id"], "fecha": e["ts"][:16], "fuente": e["source_label"], "resumen": (e.get("summary") or "")[:160],
                      "cambios": {k: {"antes": c["antes"], "despues": c["despues"]} for k, c in e["changes"].items()},
                      "dias_vigente": e["window_days"], "ops_despues": e["after"]["trades"], "pnl_despues": e["after"]["pnl"], "pnl_medio_despues": e["after"]["avg_pnl"],
                      "ops_antes": e["before"]["trades"], "pnl_antes": e["before"]["pnl"], "pnl_medio_antes": e["before"]["avg_pnl"],
                      "veredicto": e["verdict"], "deshecho": bool(e["reverted_at"]), "deshace_a": e.get("revert_of"), "se_puede_deshacer": e["revertible"]})
    return {"cambios": items, "_explicacion": (
        "Historial de cambios de configuración con el rendimiento REAL desde cada cambio hasta el siguiente (despues) frente al tramo anterior (antes). "
        f"Veredicto PEOR con ops_despues ≥ {MIN_TRADES} = evidencia de que el cambio perjudicó; PENDIENTE = aún faltan operaciones, no lo juzgues. "
        "Puedes deshacer un cambio PEOR devolviendo su id en la lista revert (restaura los valores 'antes' que sigan vigentes). "
        "No deshagas cambios PENDIENTE ni los ya deshechos; si el empeoramiento se explica por el mercado (noticias, racha general), dilo en notes en vez de deshacer.")}
