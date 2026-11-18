"""Proposals made by the AI expert chat: validated dry-run (antes → después) first, applied only after the user confirms in the UI."""
import logging
from datetime import datetime, timezone
from bson import ObjectId
from pydantic import ValidationError
from fastapi import HTTPException
from models import SettingsUpdate, now_iso
from fx.models import FxSettingsUpdate
from config import ConfigUpdate
from fx.config import FxConfigUpdate
from review import LABELS, ai_policy
from deps import db, engine, store, FX_BOTS

logger = logging.getLogger("chat.proposals")
FXB = {e.profile.panel: (e, s) for e, s in FX_BOTS}  # "fx" → HotForex, "pepper" → Pepperstone
BOTS = ("crypto", *FXB)
BOT_ERR = "bot debe ser 'crypto' (Bybit), 'fx' (HotForex) o 'pepper' (Pepperstone)"
ACTIONS = {"start_bot": "Iniciar el bot", "stop_bot": "Detener el bot", "scan": "Escanear el mercado ahora", "close_position": "Cerrar una posición",
           "close_all_positions": "Cerrar todas las posiciones"}
GLOBAL_CONFIG_KEYS = {"telegram_enabled", "telegram_alerts", "auto_review_enabled", "auto_review_frequency", "auto_review_day", "auto_review_apply", "bybit_alert_after_sec",
                      "chat_summary_every", "chat_keep_last"}
FX_CONFIG_KEYS = {"hfm_alert_after_sec", "account_type", "auto_review_enabled", "auto_review_frequency", "auto_review_day", "auto_review_apply"}
CAP_KEYS = {"risk_per_trade": "riesgo por operación", "leverage": "apalancamiento", "max_positions": "máximo de posiciones"}


def _eng(bot: str):
    if bot not in BOTS:
        raise ValueError(BOT_ERR)
    return FXB[bot][0] if bot in FXB else engine


def _fx_store(bot: str):
    return FXB[bot][1]


def _bot_label(bot: str) -> str:
    return FXB[bot][0].profile.broker_long if bot in FXB else "Bybit"


def _pyd_error(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])


async def _store_proposal(doc: dict) -> dict:
    doc.update(status="pending", created_at=now_iso(), resolved_at=None, result=None)
    res = await db.chat_proposals.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


def view(doc: dict) -> dict:
    d = {k: v for k, v in doc.items() if k != "_id"}
    d["id"] = str(doc["_id"]) if "_id" in doc else doc.get("id")
    return d


# ---------- settings ----------
async def propose_settings(bot: str, changes: dict, reason: str, session_id: str) -> dict:
    eng = _eng(bot)
    model = FxSettingsUpdate if bot in FXB else SettingsUpdate
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Indica los ajustes a cambiar como objeto {clave: valor}")
    unknown = [k for k in changes if k not in model.model_fields]
    if unknown:
        raise ValueError(f"Claves desconocidas para este bot: {', '.join(unknown)}. Claves válidas: {', '.join(model.model_fields)}")
    try:
        patch = model.model_validate(changes).model_dump(exclude_none=True)
    except ValidationError as e:
        raise ValueError(f"Valores fuera de rango: {_pyd_error(e)}")
    settings = await eng.get_settings()
    before = settings.model_dump()
    try:
        after = type(settings).model_validate({**before, **patch}).model_dump()
    except (ValidationError, ValueError) as e:
        raise ValueError(f"Combinación no válida: {_pyd_error(e) if isinstance(e, ValidationError) else e}")
    diff = {k: {"antes": before.get(k), "despues": after.get(k)} for k in patch if before.get(k) != after.get(k)}
    if not diff:
        raise ValueError("Los valores propuestos son iguales a los actuales: no hay nada que cambiar")
    warnings = []
    caps = ai_policy()["caps"]
    for k, cap in caps.items():
        if k in diff and isinstance(diff[k]["despues"], (int, float)) and diff[k]["despues"] > cap:
            warnings.append(f"El {CAP_KEYS.get(k, k)} propuesto ({diff[k]['despues']}) supera el tope prudente que usa la revisión IA automática para este perfil ({cap}).")
    if eng.live:
        warnings.append(f"El bot {_bot_label(bot)} está en modo REAL: el cambio afectará a las próximas órdenes con dinero real.")
    return await _store_proposal({"session_id": session_id, "kind": "settings", "bot": bot, "changes": patch, "diff": diff, "reason": reason or "", "warnings": warnings,
                                  "labels": {k: LABELS.get(k, k) for k in diff}})


async def _apply_settings(p: dict) -> str:
    eng = _eng(p["bot"])
    s = await eng.update_settings(p["changes"], "chat", p.get("reason") or "cambio pedido en el chat")
    return f"Ajustes de {_bot_label(p['bot'])} actualizados: {', '.join(LABELS.get(k, k) for k in p['diff'])}" + ("" if s else "")


# ---------- config ----------
async def propose_config(bot: str, changes: dict, reason: str, session_id: str) -> dict:
    if bot not in BOTS:
        raise ValueError(BOT_ERR)
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Indica los ajustes generales a cambiar como objeto {clave: valor}")
    allowed = FX_CONFIG_KEYS | GLOBAL_CONFIG_KEYS if bot in FXB else GLOBAL_CONFIG_KEYS
    bad = [k for k in changes if k not in allowed]
    if bad:
        raise ValueError(f"Claves no permitidas desde el chat: {', '.join(bad)} (credenciales, claves de IA, modo REAL y cuentas solo se cambian a mano en Ajustes). Permitidas: {', '.join(sorted(allowed))}")
    f = {k: v for k, v in changes.items() if bot in FXB and k in FX_CONFIG_KEYS}
    g = {k: v for k, v in changes.items() if k not in f}
    fx_store = _fx_store(bot) if bot in FXB else None
    try:
        fp = fx_store.patch_from(FxConfigUpdate(**f)) if f else {}
        gp = store.patch_from(ConfigUpdate(**g)) if g else {}
    except (ValidationError, ValueError) as e:
        raise ValueError(f"Valores no válidos: {_pyd_error(e) if isinstance(e, ValidationError) else e}")
    diff = {}
    for k, v in fp.items():
        if getattr(fx_store.cfg, k, None) != v:
            diff[k] = {"antes": getattr(fx_store.cfg, k, None), "despues": v}
    for k, v in gp.items():
        if getattr(store.cfg, k, None) != v:
            diff[k] = {"antes": getattr(store.cfg, k, None), "despues": v}
    if not diff:
        raise ValueError("Los valores propuestos son iguales a los actuales")
    warnings = []
    if gp.get("telegram_enabled") and not store.cfg.telegram_bot_token:
        warnings.append("Telegram no tiene token configurado: activarlo no enviará mensajes hasta que lo añadas en Ajustes → Telegram.")
    return await _store_proposal({"session_id": session_id, "kind": "config", "bot": bot, "changes": {"fx": fp, "global": gp}, "diff": diff, "reason": reason or "",
                                  "warnings": warnings, "labels": {k: k for k in diff}})


async def _apply_config(p: dict) -> str:
    fp, gp = p["changes"].get("fx") or {}, p["changes"].get("global") or {}
    if fp:
        await _fx_store(p["bot"]).update(fp)
    if gp:
        await store.update(gp)
    eng = _eng(p["bot"])
    keys = list(fp) + list(gp)
    await eng.log("INFO", f"Ajustes generales actualizados desde el chat IA: {', '.join(keys)}")
    return f"Ajustes generales actualizados: {', '.join(keys)}"


# ---------- actions ----------
async def propose_action(bot: str, action: str, args: dict, reason: str, session_id: str) -> dict:
    eng = _eng(bot)
    args = args or {}
    if action not in ACTIONS:
        raise ValueError(f"Acción desconocida: {action}. Acciones: {', '.join(ACTIONS)}")
    detail, diff = ACTIONS[action], {}
    if action == "start_bot" and eng.running:
        raise ValueError(f"El bot {_bot_label(bot)} ya está en marcha")
    if action == "stop_bot" and not eng.running:
        raise ValueError(f"El bot {_bot_label(bot)} ya está detenido")
    if action == "close_position":
        pid = str(args.get("position_id") or "")
        doc = await eng.db.positions.find_one({"_id": ObjectId(pid)}) if ObjectId.is_valid(pid) else None
        if not doc:
            raise ValueError("position_id no válido o la posición ya no existe (consulta posiciones_abiertas del snapshot)")
        detail = f"Cerrar {doc['side'].upper()} {doc['symbol']} ({doc['qty']}) · PnL actual {doc.get('unrealized_pnl', 0):+.2f}"
    if action == "close_all_positions":
        n = await eng.db.positions.count_documents({})
        if not n:
            raise ValueError("No hay posiciones abiertas que cerrar")
        detail = f"Cerrar las {n} posiciones abiertas de {_bot_label(bot)} a mercado"
    warnings = [f"El bot {_bot_label(bot)} está en modo REAL."] if eng.live and action in ("start_bot", "close_position", "close_all_positions") else []
    return await _store_proposal({"session_id": session_id, "kind": "action", "bot": bot, "action": action, "args": args, "detail": detail, "diff": diff,
                                  "reason": reason or "", "warnings": warnings, "labels": {k: LABELS.get(k, k) for k in diff}})


async def _apply_action(p: dict) -> str:
    bot, action, args, eng = p["bot"], p["action"], p.get("args") or {}, _eng(p["bot"])
    if action == "start_bot":
        if bot == "crypto" and eng.live and (not eng.broker or not store.cfg.bybit_verified_at):
            raise ValueError("Modo REAL sin credenciales de Bybit verificadas: pruébalas en Ajustes o vuelve a modo paper")
        if bot in FXB and eng.live:
            c = _fx_store(bot).cfg
            if not (c.metaapi_token and c.metaapi_account_id and c.hfm_verified_at):
                raise ValueError(f"Modo REAL: vincula y verifica la cuenta {eng.bk} (Ajustes → {eng.bl}) antes de iniciar el bot")
            if c.hfm_trade_allowed is False:
                raise ValueError(f"Modo REAL: la cuenta {eng.bk} no permite operar (trading deshabilitado o contraseña investor)")
        await eng.start()
        return f"Bot {_bot_label(bot)} iniciado"
    if action == "stop_bot":
        await eng.stop()
        return f"Bot {_bot_label(bot)} detenido"
    if action == "scan":
        eng.scan_event.set()
        return f"Escaneo solicitado en {_bot_label(bot)}"
    if action == "close_position":
        trade = await eng.close_position(str(args["position_id"]), reason="MANUAL")
        if not trade:
            raise ValueError("La posición ya no existe o el bróker no confirmó el cierre")
        return f"Posición cerrada: {trade.side.upper()} {trade.symbol} · PnL {trade.pnl:+.2f}"
    if action == "close_all_positions":
        ids = [str(d["_id"]) async for d in eng.db.positions.find({}, {"_id": 1})]
        closed = [pid for pid in ids if await eng.close_position(pid, reason="MANUAL")]
        await eng.log("RISK", f"Cierre de emergencia desde el chat IA · {len(closed)} posiciones cerradas")
        return f"{len(closed)} de {len(ids)} posiciones cerradas"
    raise ValueError("Acción no soportada")


# ---------- resolve ----------
async def resolve(proposal_id: str, apply: bool) -> dict:
    if not ObjectId.is_valid(proposal_id):
        raise HTTPException(400, "ID inválido")
    doc = await db.chat_proposals.find_one({"_id": ObjectId(proposal_id)})
    if not doc:
        raise HTTPException(404, "Propuesta no encontrada")
    if doc["status"] != "pending":
        raise HTTPException(409, f"La propuesta ya fue {'aplicada' if doc['status'] == 'applied' else 'descartada' if doc['status'] == 'discarded' else 'procesada'}")
    status, result = "discarded", "Descartada por el usuario"
    if apply:
        try:
            result = await {"settings": _apply_settings, "config": _apply_config, "action": _apply_action}[doc["kind"]](doc)
            status = "applied"
        except (ValueError, HTTPException) as e:
            status, result = "failed", f"No se pudo aplicar: {getattr(e, 'detail', None) or e}"
        except Exception as e:
            logger.exception("apply proposal")
            status, result = "failed", f"No se pudo aplicar: {str(e)[:200]}"
    await db.chat_proposals.update_one({"_id": doc["_id"]}, {"$set": {"status": status, "result": result, "resolved_at": now_iso()}})
    doc.update(status=status, result=result, resolved_at=now_iso())
    return view(doc)


async def pending(session_id: str) -> list:
    return [view(d) async for d in db.chat_proposals.find({"session_id": session_id, "status": "pending"}).sort("_id", 1)]


def describe(p: dict) -> str:
    """One-line description used in the conversation events."""
    if p["kind"] == "action":
        return p.get("detail") or p.get("action")
    return ", ".join(f"{p['labels'].get(k, k)}: {v['antes']} → {v['despues']}" for k, v in p["diff"].items())


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
