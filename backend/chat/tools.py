"""Tools the AI expert can call: read-only consultations run at once; changes/actions only create a proposal the user confirms in the UI."""
import json
import logging
from fastapi import HTTPException
from models import AVAILABLE_SYMBOLS, TIMEFRAMES, ENTRY_ORDER_TYPES, SettingsUpdate, Trade, Signal
from fx.models import FX_TIMEFRAMES, FxSettingsUpdate
import funnel
import trade_guard
from fx.symbols import AVAILABLE as FX_AVAILABLE
from fx.market import SESSIONS
from performance import performance
from fx.shadow import shadow_stats as fx_shadow_stats
import evolution
import ai_rules
from deps import db, engine, FX_BOTS

FXB = {e.profile.panel: (e, s) for e, s in FX_BOTS}
from chat import code_reader, proposals

logger = logging.getLogger("chat.tools")
BOT_PARAM = {"type": "string", "enum": ["crypto", "fx", "pepper"], "description": "Bot: 'crypto' (Bybit), 'fx' (HotForex/HFM) o 'pepper' (Pepperstone)"}


def _t(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


TOOLS = [
    _t("get_performance", "Rendimiento real completo del bot: por par, lado, motivo de salida, horizonte, tipo de entrada, decisiones IA, operaciones sombra (valor del filtro) y recorrido MFE/MAE.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_trades", "Últimas operaciones cerradas del bot (historial).", {"bot": BOT_PARAM, "limit": {"type": "integer", "description": "1-50, por defecto 20"}}, ["bot"]),
    _t("get_signals", "Últimas señales detectadas (ejecutadas, rechazadas por la IA o bloqueadas) con su razonamiento y contexto.", {"bot": BOT_PARAM, "limit": {"type": "integer"}}, ["bot"]),
    _t("get_logs", "Últimas líneas del registro del bot (opcionalmente filtradas por nivel: INFO, SCAN, SIGNAL, AI_EVAL, ORDER, RISK, PLAN, NEWS, ERROR).", {"bot": BOT_PARAM, "limit": {"type": "integer"}, "level": {"type": "string"}}, ["bot"]),
    _t("get_options", "Valores válidos y rangos de cada ajuste del bot (pares disponibles, temporalidades, niveles de confianza IA, sesiones, claves de configuración general permitidas). Úsalo antes de proponer cambios.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_guard", "Horario operativo (Nueva York), ventana activa o próxima, y calendario de eventos rojos con los pares que bloquea cada uno (±15 min).", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_rules", "TODAS las reglas aprendidas del bot, completas y sin recortar: texto íntegro, par al que aplica (vacío = todos), si está activa, si la creó la IA (auto) o el usuario, "
                    "operación de origen y veredicto, aciertos (hits) y fallos (misses), y qué habrían hecho las señales que bloqueó (cerradas, ganadoras, PnL). "
                    "Incluye también el historial de cambios automáticos sobre las reglas (desactivadas, modificadas, eliminadas, con motivo). Úsalo siempre que el usuario pregunte por reglas.",
       {"bot": BOT_PARAM, "only_enabled": {"type": "boolean", "description": "true = solo las activas (las que el modelo potente recibe hoy)"},
        "symbol": {"type": "string", "description": "opcional: reglas que aplican a ese par (globales + específicas)"}}, ["bot"]),
    _t("get_evolution", "Línea de tiempo de cambios de ajustes (fuente, antes→después, rendimiento antes/después y veredicto MEJOR/PEOR/PENDIENTE, ids para deshacer).", {"bot": BOT_PARAM, "limit": {"type": "integer"}}, ["bot"]),
    _t("get_plan_checkin", "Chequeo del plan de crecimiento: fase actual, criterios cumplidos, avisos y si toca avanzar.", {"bot": BOT_PARAM}, ["bot"]),
    _t("list_code", "Lista archivos y carpetas del proyecto (solo lectura). Raíces: /app/backend, /app/frontend/src, /app/deploy.", {"path": {"type": "string"}}),
    _t("read_code", "Lee un archivo de código del proyecto con números de línea (máx. 300 líneas por llamada; usa start/end para paginar). Nunca modifica nada.",
       {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
    _t("search_code", "Busca un texto o expresión regular en el código del proyecto y devuelve archivo:línea.", {"query": {"type": "string"}, "path": {"type": "string"}, "regex": {"type": "boolean"}}, ["query"]),
    _t("propose_settings_change", "Propone cambiar ajustes de riesgo/embudo IA de un bot. NO se aplica: crea una tarjeta que el usuario debe confirmar. Devuelve el diff validado (antes→después) y avisos.",
       {"bot": BOT_PARAM, "changes": {"type": "object", "description": "{clave: valor} con claves de ajustes del bot"}, "reason": {"type": "string", "description": "motivo breve para el usuario"}}, ["bot", "changes", "reason"]),
    _t("propose_config_change", "Propone cambiar ajustes generales (Telegram on/off y tipos de alerta, revisión IA automática, alertas de desconexión, chat). Nunca credenciales ni modo REAL. Requiere confirmación del usuario.",
       {"bot": BOT_PARAM, "changes": {"type": "object"}, "reason": {"type": "string"}}, ["bot", "changes", "reason"]),
    _t("propose_action", "Propone una acción operativa que el usuario debe confirmar: start_bot, stop_bot, scan, close_position (args.position_id), close_all_positions.",
       {"bot": BOT_PARAM, "action": {"type": "string", "enum": list(proposals.ACTIONS)}, "args": {"type": "object"}, "reason": {"type": "string"}}, ["bot", "action", "reason"]),
    _t("remember", "Guarda una nota en la memoria duradera del asistente (preferencias, objetivos, decisiones del usuario). Úsalo cuando el usuario pida recordar algo o exprese una preferencia estable.",
       {"note": {"type": "string", "description": "frase breve (máx. 160 caracteres)"}}, ["note"]),
]


def _compact(obj, depth: int = 0):
    """Trim long lists/curves so tool results stay small enough for the model."""
    if isinstance(obj, dict):
        return {k: _compact(v, depth + 1) for k, v in obj.items() if k not in ("equity_curve", "curve", "oos_curve", "baseline_curve", "r_multiples", "markers", "heatmap", "grid", "trades_list")}
    if isinstance(obj, list):
        return [_compact(x, depth + 1) for x in obj[:25]] + ([f"… {len(obj) - 25} más"] if len(obj) > 25 else [])
    if isinstance(obj, float):
        return round(obj, 4)
    return obj


def _eng(bot):
    if bot not in ("crypto", *FXB):
        raise ValueError("bot debe ser 'crypto', 'fx' o 'pepper'")
    return FXB[bot][0] if bot in FXB else engine


async def _options(bot: str) -> dict:
    model = FxSettingsUpdate if bot in FXB else SettingsUpdate
    ranges = {}
    for k, f in model.model_fields.items():
        meta = {"tipo": getattr(f.annotation, "__name__", str(f.annotation)).replace("Optional[", "").replace("]", "")}
        for m in f.metadata:
            for attr in ("ge", "le", "pattern"):
                if getattr(m, attr, None) is not None:
                    meta[attr] = getattr(m, attr)
        ranges[k] = meta
    if bot in FXB:
        return {"settings_keys": ranges, "symbols": FX_AVAILABLE, "timeframes": FX_TIMEFRAMES, "ai_min_confidence": list(funnel.CONFIDENCE_LEVELS), "sessions": [s[0] for s in SESSIONS],
                "config_keys": sorted(proposals.FX_CONFIG_KEYS | proposals.GLOBAL_CONFIG_KEYS), "actions": proposals.ACTIONS}
    return {"settings_keys": ranges, "symbols": AVAILABLE_SYMBOLS, "timeframes": TIMEFRAMES, "ai_min_confidence": list(funnel.CONFIDENCE_LEVELS), "entry_order_type": ENTRY_ORDER_TYPES,
            "config_keys": sorted(proposals.GLOBAL_CONFIG_KEYS), "actions": proposals.ACTIONS}


async def run_tool(name: str, args: dict, session_id: str) -> dict:
    args = args or {}
    try:
        bot = args.get("bot")
        if name == "get_performance":
            eng = _eng(bot)
            return _compact(await (performance(eng.db, fx_shadow_stats) if bot in FXB else performance(eng.db)))
        if name == "get_trades":
            eng = _eng(bot)
            n = max(1, min(int(args.get("limit") or 20), 50))
            return {"trades": [Trade.from_mongo(d).model_dump(exclude={"id"}) async for d in eng.db.trades.find().sort("_id", -1).limit(n)]}
        if name == "get_signals":
            eng = _eng(bot)
            n = max(1, min(int(args.get("limit") or 15), 40))
            return {"signals": [Signal.from_mongo(d).model_dump(exclude={"id", "indicators"}) async for d in eng.db.signals.find().sort("_id", -1).limit(n)]}
        if name == "get_logs":
            eng = _eng(bot)
            n = max(1, min(int(args.get("limit") or 40), 150))
            q = {"level": str(args["level"]).upper()} if args.get("level") else {}
            return {"logs": [f"{d.get('ts', '')[:19]} [{d['level']}] {d['message']}" async for d in eng.db.logs.find(q).sort("_id", -1).limit(n)]}
        if name == "get_options":
            return await _options(bot)
        if name == "get_guard":
            eng = _eng(bot)
            return _compact(trade_guard.status(eng.guard_kind, (await eng.get_settings()).symbols))
        if name == "get_rules":
            eng = _eng(bot)
            rules = await ai_rules.list_rules(eng.db)
            sym = str(args.get("symbol") or "").upper().strip()
            if sym:
                rules = [r for r in rules if not r["symbol"] or r["symbol"].upper() == sym]
            if args.get("only_enabled"):
                rules = [r for r in rules if r["enabled"]]
            return {"total": len(rules), "activas": sum(1 for r in rules if r["enabled"]), "desactivadas": sum(1 for r in rules if not r["enabled"]),
                    "creadas_por_ia": sum(1 for r in rules if r["auto"]), "creadas_por_usuario": sum(1 for r in rules if not r["auto"]),
                    "auto_desactivacion": f"una regla se apaga sola al acumular {ai_rules.MAX_MISSES} fallos (ganadoras que habría bloqueado)",
                    "rules": rules, "historial_cambios": await ai_rules.events(eng.db, 60)}
        if name == "get_evolution":
            eng = _eng(bot)
            tl = await evolution.timeline(eng, max(1, min(int(args.get("limit") or 15), 40)))
            return _compact(tl)
        if name == "get_plan_checkin":
            return await _eng(bot).checkin()
        if name == "list_code":
            return code_reader.list_dir(args.get("path"))
        if name == "read_code":
            return code_reader.read_file(args.get("path"), args.get("start") or 1, args.get("end"))
        if name == "search_code":
            return code_reader.search(args.get("query"), args.get("path"), bool(args.get("regex")))
        if name == "propose_settings_change":
            p = await proposals.propose_settings(bot, args.get("changes"), args.get("reason", ""), session_id)
            return {"proposal_id": p["id"], "status": "pendiente_de_confirmacion", "diff": p["diff"], "warnings": p["warnings"],
                    "instrucciones": "El usuario verá una tarjeta con Aplicar/Descartar. Explica brevemente el cambio y espera su decisión; no digas que ya está aplicado."}
        if name == "propose_config_change":
            p = await proposals.propose_config(bot, args.get("changes"), args.get("reason", ""), session_id)
            return {"proposal_id": p["id"], "status": "pendiente_de_confirmacion", "diff": p["diff"], "warnings": p["warnings"],
                    "instrucciones": "El usuario verá una tarjeta con Aplicar/Descartar; no digas que ya está aplicado."}
        if name == "propose_action":
            p = await proposals.propose_action(bot, args.get("action"), args.get("args") or {}, args.get("reason", ""), session_id)
            return {"proposal_id": p["id"], "status": "pendiente_de_confirmacion", "detail": p["detail"], "diff": p["diff"], "warnings": p["warnings"],
                    "instrucciones": "El usuario verá una tarjeta con Aplicar/Descartar; no digas que ya se ejecutó."}
        if name == "remember":
            note = str(args.get("note", "")).strip()[:160]
            if not note:
                raise ValueError("Nota vacía")
            await db.chat_memory.update_one({"_id": "main"}, {"$push": {"notes": {"$each": [{"text": note, "ts": proposals.utcnow()}], "$slice": -40}}}, upsert=True)
            return {"ok": True, "guardado": note}
        raise ValueError(f"Herramienta desconocida: {name}")
    except HTTPException as e:
        return {"error": str(e.detail)}
    except (ValueError, KeyError, TypeError) as e:
        return {"error": str(e)[:400]}
    except Exception as e:
        logger.exception(f"tool {name}")
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
