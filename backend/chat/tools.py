"""Tools the AI expert can call: read-only consultations run at once; changes/actions only create a proposal the user confirms in the UI."""
import json
import logging
from fastapi import HTTPException
from models import BacktestRequest, OptimizeRequest, AVAILABLE_SYMBOLS, TIMEFRAMES, CRYPTO_STRATEGY_KEYS, SIGNAL_MODES, ENTRY_ORDER_TYPES, REGIME_SMAS, SettingsUpdate, Trade, Signal
from fx.models import FxBacktestRequest, FX_TIMEFRAMES, FX_STRATEGY_KEYS, FxSettingsUpdate
from fx.symbols import AVAILABLE as FX_AVAILABLE
from fx.market import SESSIONS
from profiles import PROFILES
from fx.profiles import PROFILES as FX_PROFILES
from performance import performance
from fx.shadow import shadow_stats as fx_shadow_stats
import evolution
import wfcheck
from deps import db, engine, news, FX_BOTS, LABS
import ai_rules

FXB = {e.profile.panel: (e, s, n) for e, s, n in FX_BOTS}
from chat import code_reader, proposals

logger = logging.getLogger("chat.tools")
BOT_PARAM = {"type": "string", "enum": ["crypto", "fx", "pepper"], "description": "Bot: 'crypto' (Bybit), 'fx' (HotForex/HFM) o 'pepper' (Pepperstone)"}


def _t(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


TOOLS = [
    _t("get_performance", "Rendimiento real completo del bot: por estrategia, par, lado, motivo de salida, horizonte, tipo de entrada, decisiones IA, operaciones sombra (valor del filtro) y recorrido MFE/MAE.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_learned_rules", "TODAS las reglas aprendidas de un bot, sin recortar: texto completo, activa/apagada, automática o manual, ámbito (par, lado, estrategia), fecha, operación y veredicto de origen, "
       "señales bloqueadas y su resultado, ganadoras que habría bloqueado, nota de la IA, y la actividad reciente del aprendizaje (nuevas, reescritas, apagadas, eliminadas, fusiones). "
       "Cada bot tiene su propio banco de reglas. Úsala siempre que pregunten por reglas aprendidas.",
       {"bot": {"type": "string", "enum": ["crypto", "fx", "pepper", "lab", "lab4h"], "description": "'crypto' (Bybit), 'fx' (HotForex), 'pepper' (Pepperstone), 'lab' (Bybit Lab) o 'lab4h' (Bybit Lab 4H)"},
        "only_active": {"type": "boolean", "description": "true = solo las activas; por defecto todas"},
        "search": {"type": "string", "description": "texto a buscar en la regla o el par (opcional)"}}, ["bot"]),
    _t("get_trades", "Últimas operaciones cerradas del bot (historial).", {"bot": BOT_PARAM, "limit": {"type": "integer", "description": "1-50, por defecto 20"}}, ["bot"]),
    _t("get_signals", "Últimas señales detectadas (ejecutadas, rechazadas por la IA o bloqueadas) con su razonamiento y contexto.", {"bot": BOT_PARAM, "limit": {"type": "integer"}}, ["bot"]),
    _t("get_logs", "Últimas líneas del registro del bot (opcionalmente filtradas por nivel: INFO, SCAN, SIGNAL, AI_EVAL, ORDER, RISK, PLAN, NEWS, ERROR).", {"bot": BOT_PARAM, "limit": {"type": "integer"}, "level": {"type": "string"}}, ["bot"]),
    _t("get_options", "Valores válidos y rangos de cada ajuste del bot (pares disponibles, estrategias, temporalidades, perfiles, sesiones, claves de configuración general permitidas). Úsalo antes de proponer cambios.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_market_context", "Contexto de mercado y filtros: cripto → régimen de BTC, rotación semanal y entradas maker pendientes; fx → régimen USD, exposición por divisa, volatilidad, estacionalidad, rollover, FOMC y próximo fix de fin de mes.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_news", "Último análisis del agente de noticias: sentimiento, riesgo, resumen, alertas y próximos eventos macro de alto impacto.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_evolution", "Línea de tiempo de cambios de ajustes (fuente, antes→después, rendimiento antes/después y veredicto MEJOR/PEOR/PENDIENTE, ids para deshacer).", {"bot": BOT_PARAM, "limit": {"type": "integer"}}, ["bot"]),
    _t("get_plan_checkin", "Chequeo del plan de crecimiento: fase actual, criterios cumplidos, avisos y si toca avanzar.", {"bot": BOT_PARAM}, ["bot"]),
    _t("get_walk_forward_status", "Resultado de la última revalidación walk-forward semanal de los pares con SL/TP aplicados.", {"bot": BOT_PARAM}, ["bot"]),
    _t("run_backtest", "Ejecuta un backtest (≈1000 velas reales) de un par con los ajustes actuales del bot. Tarda unos segundos.",
       {"bot": BOT_PARAM, "symbol": {"type": "string"}, "timeframe": {"type": "string", "description": "por defecto la del bot"}, "strategy": {"type": "string", "description": "'all' o una estrategia; por defecto la del bot"}}, ["bot", "symbol"]),
    _t("run_optimizer", "Optimizador SL/TP (grid de 54 combinaciones, SQN robusto) de un par. Tarda 10-40 s.", {"bot": BOT_PARAM, "symbol": {"type": "string"}}, ["bot", "symbol"]),
    _t("run_walk_forward", "Validación walk-forward (2000 velas, optimización in-sample y prueba a ciegas) de un par; devuelve veredicto ROBUSTO/DUDOSO/SOBREAJUSTADO/NO_RENTABLE. Tarda 20-60 s.", {"bot": BOT_PARAM, "symbol": {"type": "string"}}, ["bot", "symbol"]),
    _t("list_code", "Lista archivos y carpetas del proyecto (solo lectura). Raíces: /app/backend, /app/frontend/src, /app/deploy.", {"path": {"type": "string"}}),
    _t("read_code", "Lee un archivo de código del proyecto con números de línea (máx. 300 líneas por llamada; usa start/end para paginar). Nunca modifica nada.",
       {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
    _t("search_code", "Busca un texto o expresión regular en el código del proyecto y devuelve archivo:línea.", {"query": {"type": "string"}, "path": {"type": "string"}, "regex": {"type": "boolean"}}, ["query"]),
    _t("propose_settings_change", "Propone cambiar ajustes de estrategia/riesgo de un bot. NO se aplica: crea una tarjeta que el usuario debe confirmar. Devuelve el diff validado (antes→después) y avisos.",
       {"bot": BOT_PARAM, "changes": {"type": "object", "description": "{clave: valor} con claves de ajustes del bot"}, "reason": {"type": "string", "description": "motivo breve para el usuario"}}, ["bot", "changes", "reason"]),
    _t("propose_config_change", "Propone cambiar ajustes generales (Telegram on/off y tipos de alerta, agente de noticias, trading de noticias FX, revisión IA automática, funding, revalidación automática, alertas de desconexión, chat). Nunca credenciales ni modo REAL. Requiere confirmación del usuario.",
       {"bot": BOT_PARAM, "changes": {"type": "object"}, "reason": {"type": "string"}}, ["bot", "changes", "reason"]),
    _t("propose_action", "Propone una acción operativa que el usuario debe confirmar: start_bot, stop_bot, scan, close_position (args.position_id), close_all_positions, refresh_news, set_profile (args.level 0-4).",
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
        return {"settings_keys": ranges, "symbols": FX_AVAILABLE, "timeframes": FX_TIMEFRAMES, "strategies": FX_STRATEGY_KEYS, "signal_modes": SIGNAL_MODES,
                "sessions": [s[0] for s in SESSIONS], "usd_regime_sma": REGIME_SMAS, "ai_news_weight": ["normal", "bajo"],
                "profiles": [{"level": p["level"], "name": p["name"], "settings": p["settings"]} for p in FX_PROFILES],
                "config_keys": sorted(proposals.FX_CONFIG_KEYS | proposals.GLOBAL_CONFIG_KEYS), "actions": proposals.ACTIONS}
    return {"settings_keys": ranges, "symbols": AVAILABLE_SYMBOLS, "timeframes": TIMEFRAMES, "strategies": CRYPTO_STRATEGY_KEYS, "signal_modes": SIGNAL_MODES,
            "entry_order_type": ENTRY_ORDER_TYPES, "btc_regime_sma": REGIME_SMAS,
            "profiles": [{"level": p["level"], "name": p["name"], "settings": p["settings"]} for p in PROFILES],
            "config_keys": sorted(proposals.GLOBAL_CONFIG_KEYS), "actions": proposals.ACTIONS}


async def _backtest(bot: str, symbol: str, timeframe: str, strategy: str) -> dict:
    eng = _eng(bot)
    s = await eng.get_settings()
    if bot in FXB:
        from fx.routes_analysis import backtest
        req = FxBacktestRequest(symbol=symbol, timeframe=timeframe or s.timeframe, strategy=strategy or s.strategy, risk_per_trade=s.risk_per_trade, leverage=s.leverage)
    else:
        from routers.analysis_routes import backtest
        req = BacktestRequest(symbol=symbol, timeframe=timeframe or s.timeframe, strategy=strategy or s.strategy, risk_per_trade=s.risk_per_trade, leverage=s.leverage)
    return await backtest(req)


async def _optimize(bot: str, symbol: str) -> dict:
    if bot in FXB:
        from fx.routes_analysis import optimize_symbol
    else:
        from routers.analysis_routes import optimize_symbol
    return await optimize_symbol(OptimizeRequest(symbol=symbol))


async def _walk_forward(bot: str, symbol: str) -> dict:
    if bot in FXB:
        from fx.routes_analysis import optimize_walk_forward
    else:
        from routers.walk_forward_routes import optimize_walk_forward
    res = await optimize_walk_forward(OptimizeRequest(symbol=symbol))
    res["folds"] = len(res.get("folds") or [])
    return res


async def _market_context(bot: str) -> dict:
    if bot in FXB:
        from fx.routes_analysis import market_context
    else:
        from routers.market_routes import market_context
    return await market_context()


async def run_tool(name: str, args: dict, session_id: str) -> dict:
    args = args or {}
    try:
        bot = args.get("bot")
        if name == "get_performance":
            eng = _eng(bot)
            return _compact(await (performance(eng.db, fx_shadow_stats) if bot in FXB else performance(eng.db)))
        if name == "get_learned_rules":
            eng = {x.bot_key: x for x in LABS}.get(bot) or _eng(bot)
            rules = await ai_rules.list_rules(eng.db)
            needle = str(args.get("search") or "").strip().lower()
            view = [r for r in rules if (r["enabled"] or not args.get("only_active")) and (not needle or needle in r["text"].lower() or needle in (r.get("symbol") or "").lower())]
            return {"bot": bot, "total": len(rules), "activas": sum(r["enabled"] for r in rules), "automaticas": sum(r["auto"] for r in rules), "devueltas": len(view),
                    "reglas": view, "actividad_reciente": await ai_rules.events(eng.db)}
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
        if name == "get_market_context":
            return _compact(await _market_context(bot))
        if name == "get_news":
            agent = FXB[bot][2] if bot in FXB else news
            latest = agent.latest or {}
            return _compact({"summary": agent.summary(), "market": latest.get("market"), "pairs": latest.get("pairs"), "alerts": (latest.get("alerts") or [])[:8],
                             "events": [e for e in (latest.get("events") or []) if e.get("impact") == "ALTO"][:12], "headlines": [h.get("title") for h in (latest.get("headlines") or [])[:10]]})
        if name == "get_evolution":
            eng = _eng(bot)
            tl = await evolution.timeline(eng, max(1, min(int(args.get("limit") or 15), 40)))
            return _compact(tl)
        if name == "get_plan_checkin":
            return await _eng(bot).checkin()
        if name == "get_walk_forward_status":
            return _compact(await wfcheck.latest(_eng(bot).db) or {"info": "sin revalidaciones todavía"})
        if name == "run_backtest":
            return _compact(await _backtest(bot, str(args.get("symbol", "")).upper(), args.get("timeframe"), args.get("strategy")))
        if name == "run_optimizer":
            return _compact(await _optimize(bot, str(args.get("symbol", "")).upper()))
        if name == "run_walk_forward":
            return _compact(await _walk_forward(bot, str(args.get("symbol", "")).upper()))
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
