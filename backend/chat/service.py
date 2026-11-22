"""AI expert chat: one shared conversation (both bots), native tool calling, proposals with confirmation, rolling summary + durable memory."""
import os
import uuid
import asyncio
import logging
from bson import ObjectId
from models import now_iso
import ai_advisor
from deps import db, store
from chat import context
from chat.tools import TOOLS, run_tool, dumps
from chat import proposals

logger = logging.getLogger("chat")
KNOWLEDGE = open(os.path.join(os.path.dirname(__file__), "knowledge.md"), encoding="utf-8").read()
MAX_HOPS = 8
MAX_TOOL_CHARS = 14_000
RULES_TOOL_CHARS = 150_000   # learned rules are never trimmed in practice
MAX_MEMORY_NOTES = 30
JOB_TTL_SEC = 600
_summarizing: set = set()
_lock = asyncio.Lock()
_jobs: dict = {}
TOOL_STEPS = {"get_performance": "Consultando el rendimiento real", "get_trades": "Leyendo el historial de operaciones", "get_signals": "Revisando las señales recientes",
              "get_logs": "Leyendo el registro del bot", "get_options": "Comprobando rangos y opciones", "get_guard": "Consultando horario y eventos rojos",
              "get_evolution": "Revisando la evolución de ajustes", "get_plan_checkin": "Chequeando el plan de crecimiento",
              "list_code": "Explorando el código", "read_code": "Leyendo el código", "search_code": "Buscando en el código",
              "propose_settings_change": "Preparando una propuesta de ajustes", "propose_config_change": "Preparando una propuesta de configuración",
              "propose_action": "Preparando una propuesta de acción", "remember": "Guardando en la memoria"}

PERSONA = """Eres «IA expert», el asistente experto de esta plataforma de trading automático. Perfil: trader y gestor de riesgo cuantitativo senior (futuros cripto y forex/metales), \
pragmático, directo y honesto; priorizas preservar capital y decidir con evidencia (operaciones reales y operaciones sombra) antes que con intuición. Hablas siempre en español, \
con frases claras y sin jerga innecesaria; usas Markdown ligero (negritas, listas cortas, encabezados breves) y nunca tablas enormes.

Conoces la aplicación al completo (guía más abajo) y recibes en cada mensaje un snapshot en vivo de los tres bots. Reglas:
1. Responde con los datos del snapshot cuando basten; si necesitas más detalle (rendimiento completo, señales, logs, código…) usa las herramientas antes de responder en vez de suponer. Cita cifras concretas. El snapshot en vivo prevalece siempre sobre lo que recuerdes de la conversación: los ajustes pueden haber cambiado a mano o por otros procesos desde entonces.
2. Cambios y acciones SIEMPRE pasan por una propuesta (propose_settings_change / propose_config_change / propose_action). Nunca digas que algo quedó aplicado hasta que llegue un evento del sistema confirmándolo. Antes de proponer, consulta get_options si dudas de claves o rangos. Explica el porqué y los riesgos; si el usuario pide algo imprudente, adviértelo pero respeta su decisión (no hay topes, salvo los rangos de cada campo). Si el usuario te pide «aplica» algo que ya propusiste, recuérdale que debe pulsar Aplicar en la tarjeta.
3. Prohibido: tocar credenciales, API keys, contraseñas, cambiar a modo REAL, reiniciar cuentas o modificar código. Si piden algo así, explica cómo hacerlo a mano en Ajustes o, para código, redacta un bloque «Sugerencia de cambio de código» (archivo, líneas, qué cambiar y por qué) listo para que el usuario lo pegue al asistente de desarrollo de la plataforma.
4. Puedes leer el código fuente (list_code / read_code / search_code) para explicar con exactitud cómo funciona algo o diagnosticar un comportamiento.
5. Usa remember cuando el usuario exprese preferencias estables, objetivos o pida que recuerdes algo. Lo recordado aparece en «Memoria duradera».
6. Sé breve en lo simple y completo en lo importante; estructura las revisiones como: diagnóstico → qué cambiar y por qué → riesgo.
7. Si el usuario escribe desde un panel concreto, prioriza ese bot salvo que pregunte por el otro o por ambos."""

SUMMARY_SYSTEM = ("Eres el módulo de memoria de un asistente de trading. Resumes conversaciones en español conservando TODO lo importante: decisiones tomadas, cambios propuestos y si se aplicaron o no, "
                  "cifras clave, preferencias y objetivos del usuario, tareas pendientes y advertencias. Devuelves exclusivamente JSON válido.")


class Busy(Exception):
    pass


def _mem_text(mem: dict) -> str:
    notes = (mem or {}).get("notes") or []
    return "\n".join(f"- {n['text']}" for n in notes) if notes else "(vacía)"


def _system(mem: dict, session: dict, snap: dict) -> str:
    return (f"{PERSONA}\n\n# Guía de la aplicación\n{KNOWLEDGE}\n\n# Memoria duradera (hechos que no deben olvidarse)\n{_mem_text(mem)}\n\n"
            f"# Resumen de la conversación anterior\n{session.get('summary') or '(la conversación acaba de empezar)'}\n\n"
            f"# Snapshot en vivo (JSON)\n{dumps(snap)}")


async def _memory() -> dict:
    return await db.chat_memory.find_one({"_id": "main"}) or {"_id": "main", "notes": []}


async def get_session() -> dict:
    s = await db.chat_sessions.find_one({"status": "active"}, sort=[("_id", -1)])
    if not s:
        s = {"status": "active", "created_at": now_iso(), "updated_at": now_iso(), "summary": "", "summary_upto": 0, "n_messages": 0, "summaries": 0}
        s["_id"] = (await db.chat_sessions.insert_one(s)).inserted_id
    return s


def _msg_view(m: dict, props: dict) -> dict:
    return {"id": str(m["_id"]), "idx": m["idx"], "role": m["role"], "content": m.get("content", ""), "ts": m.get("ts"), "tools": m.get("tools") or [],
            "error": bool(m.get("error")), "proposals": [props[p] for p in (m.get("proposals") or []) if p in props]}


async def _proposal_map(ids: list) -> dict:
    oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
    return {str(d["_id"]): proposals.view(d) async for d in db.chat_proposals.find({"_id": {"$in": oids}})} if oids else {}


def _job_view(j: dict) -> dict:
    return {k: j.get(k) for k in ("id", "status", "steps", "started_at", "finished_at", "error")}


def current_job() -> dict | None:
    for j in _jobs.values():
        if j["status"] == "running":
            return _job_view(j)
    return None


async def state(limit: int = 80) -> dict:
    s = await get_session()
    sid = str(s["_id"])
    msgs = await db.chat_messages.find({"session_id": sid}).sort("idx", -1).limit(limit).to_list(limit)
    msgs.reverse()
    props = await _proposal_map([p for m in msgs for p in (m.get("proposals") or [])])
    mem = await _memory()
    return {"session": {"id": sid, "created_at": s["created_at"], "updated_at": s["updated_at"], "n_messages": s["n_messages"], "summary": s.get("summary") or "",
                        "summary_upto": s.get("summary_upto", 0), "summaries": s.get("summaries", 0)},
            "messages": [_msg_view(m, props) for m in msgs], "memory": mem.get("notes") or [], "pending": await proposals.pending(sid), "job": current_job(),
            "config": {"summary_every": store.cfg.chat_summary_every, "keep_last": store.cfg.chat_keep_last}, "ai": {k: v for k, v in ai_advisor.current().items() if k != "health"},
            "ai_health": ai_advisor.health()}


def _to_llm(m: dict) -> dict:
    if m["role"] == "assistant":
        tools = m.get("tools") or []
        return {"role": "assistant", "content": (m.get("content") or "") + (f"\n\n[Herramientas consultadas en esta respuesta: {', '.join(tools)}]" if tools else "")}
    if m["role"] == "event":
        return {"role": "user", "content": f"[Evento del sistema] {m.get('content', '')}"}
    return {"role": "user", "content": m.get("content") or ""}


async def _insert(sid: str, s: dict, role: str, content: str, **extra) -> dict:
    doc = {"session_id": sid, "idx": s["n_messages"], "role": role, "content": content, "ts": now_iso(), **extra}
    doc["_id"] = (await db.chat_messages.insert_one(doc)).inserted_id
    s["n_messages"] += 1
    await db.chat_sessions.update_one({"_id": s["_id"]}, {"$set": {"n_messages": s["n_messages"], "updated_at": doc["ts"]}})
    return doc


async def _run_turn(chat, text: str, sid: str, job: dict) -> tuple:
    tools_used, prop_ids = [], []
    resp = await ai_advisor.ask_tools(chat, text)
    hops = 0
    while resp.tool_calls and hops <= MAX_HOPS:
        hops += 1
        for tc in resp.tool_calls:
            job["steps"].append(TOOL_STEPS.get(tc.name, tc.name))
            if hops > MAX_HOPS:
                result = {"error": "Límite de herramientas por mensaje alcanzado: responde ahora con la información que ya tienes."}
            else:
                result = await run_tool(tc.name, tc.arguments, sid)
            if tc.name.startswith("propose_") and result.get("proposal_id"):
                prop_ids.append(result["proposal_id"])
            tools_used.append(tc.name)
            payload = dumps(result)
            cap = RULES_TOOL_CHARS if tc.name == "get_rules" else MAX_TOOL_CHARS
            if len(payload) > cap:
                payload = payload[:cap] + '… [resultado recortado: pide un subconjunto más pequeño]"}'
            chat.add_tool_result(tc.id, payload)
        job["steps"].append("Redactando la respuesta")
        resp = await ai_advisor.ask_tools(chat, None)
    reply = (resp.content or "").strip()
    if not reply:
        reply = ("Te dejo la propuesta en la tarjeta de abajo para que la revises." if prop_ids
                 else "He recopilado la información, pero no pude redactar la respuesta final. Vuelve a preguntar o concreta un poco más.")
    return reply, sorted(set(tools_used)), prop_ids


async def _process(job: dict, text: str, panel: str):
    async with _lock:
        s = await get_session()
        sid = str(s["_id"])
        user_doc = await _insert(sid, s, "user", text, panel=panel)
        tools_used, prop_ids, error = [], [], False
        try:
            job["steps"].append("Leyendo el estado en vivo de los bots")
            mem = await _memory()
            snap = await context.snapshot(panel)
            history = [_to_llm(m) async for m in db.chat_messages.find({"session_id": sid, "idx": {"$gte": s.get("summary_upto", 0), "$lt": user_doc["idx"]}}).sort("idx", 1)]
            chat = ai_advisor.tool_chat(_system(mem, s, snap), "chat", history, TOOLS)
            job["steps"].append("Pensando")
            reply, tools_used, prop_ids = await _run_turn(chat, text, sid, job)
        except Exception as e:
            logger.warning(f"chat: {type(e).__name__}: {str(e)[:300]}")
            reply, error = f"No pude responder: {ai_advisor.friendly_error(e)}", True
        bot_doc = await _insert(sid, s, "assistant", reply, tools=tools_used, proposals=prop_ids, error=error)
        props = await _proposal_map(prop_ids)
        job.update(status="done", finished_at=now_iso(), result={"user": _msg_view(user_doc, {}), "assistant": _msg_view(bot_doc, props), "n_messages": s["n_messages"]})
        if not error:
            asyncio.create_task(maybe_summarize(sid))


def _prune_jobs():
    import time
    now = time.time()
    for k in [k for k, j in _jobs.items() if j["status"] != "running" and now - j["_t"] > JOB_TTL_SEC]:
        _jobs.pop(k, None)


def start(text: str, panel: str) -> dict:
    """Queue one user message; the answer is fetched by polling job(id) (long tool chains exceed proxy timeouts)."""
    import time
    _prune_jobs()
    if current_job():
        raise Busy()
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "steps": [], "started_at": now_iso(), "finished_at": None, "error": None, "result": None, "_t": time.time()}
    _jobs[job["id"]] = job
    job["_task"] = asyncio.create_task(_process(job, text, panel))
    return _job_view(job)


def job(job_id: str) -> dict | None:
    j = _jobs.get(job_id)
    if not j:
        return None
    return {**_job_view(j), "result": j.get("result")}


async def record_event(sid: str, text: str) -> dict:
    s = await db.chat_sessions.find_one({"_id": ObjectId(sid)}) if ObjectId.is_valid(sid) else None
    if not s or s.get("status") != "active":
        s = await get_session()
    doc = await _insert(str(s["_id"]), s, "event", text)
    return _msg_view(doc, {})


async def maybe_summarize(sid: str, force: bool = False) -> bool:
    if sid in _summarizing:
        return False
    s = await db.chat_sessions.find_one({"_id": ObjectId(sid)})
    if not s:
        return False
    every, keep = store.cfg.chat_summary_every, store.cfg.chat_keep_last
    upto_now = s.get("summary_upto", 0)
    pending = s["n_messages"] - upto_now
    if not force and pending < every:
        return False
    new_upto = max(upto_now, s["n_messages"] - keep)
    if new_upto <= upto_now:
        return False
    _summarizing.add(sid)
    try:
        msgs = await db.chat_messages.find({"session_id": sid, "idx": {"$gte": upto_now, "$lt": new_upto}}).sort("idx", 1).to_list(1000)
        mem = await _memory()
        transcript = "\n".join(f"[{m['role']}] {m.get('content', '')[:1500]}" for m in msgs)
        prompt = (f"Resumen previo:\n{s.get('summary') or '(ninguno)'}\n\nMemoria duradera actual:\n{_mem_text(mem)}\n\nMensajes nuevos a integrar:\n{transcript}\n\n"
                  "Devuelve JSON con dos claves: \"summary\" (texto en español, máx. 2500 caracteres, que fusione el resumen previo con los mensajes nuevos sin perder decisiones, cifras, "
                  f"propuestas y su estado, pendientes y preferencias) y \"memory\" (lista de máx. {MAX_MEMORY_NOTES} frases cortas, ≤ 160 caracteres, con los hechos duraderos: "
                  "preferencias, objetivos, decisiones firmes, cosas que el usuario pidió recordar; conserva las notas previas que sigan vigentes y añade las nuevas).")
        chat = ai_advisor.tool_chat(SUMMARY_SYSTEM, "chat-summary", [], None)
        text = await ai_advisor.ask_text(chat, prompt)
        data = ai_advisor.parse_json(text, "resumen del chat")
        summary = str(data.get("summary") or "")[:4000]
        notes_in = [str(x).strip()[:160] for x in (data.get("memory") or []) if str(x).strip()][:MAX_MEMORY_NOTES]
        prev = {n["text"]: n["ts"] for n in (mem.get("notes") or [])}
        notes = [{"text": t, "ts": prev.get(t) or now_iso()} for t in notes_in]
        await db.chat_sessions.update_one({"_id": s["_id"]}, {"$set": {"summary": summary, "summary_upto": new_upto, "summaries": s.get("summaries", 0) + 1, "summarized_at": now_iso()}})
        await db.chat_memory.update_one({"_id": "main"}, {"$set": {"notes": notes, "updated_at": now_iso()}}, upsert=True)
        logger.info(f"chat: conversación resumida hasta el mensaje {new_upto} · {len(notes)} notas de memoria")
        return True
    except Exception as e:
        logger.warning(f"chat summary: {e}")
        return False
    finally:
        _summarizing.discard(sid)


async def new_conversation() -> dict:
    if current_job():
        raise Busy()
    s = await get_session()
    await db.chat_proposals.update_many({"session_id": str(s["_id"]), "status": "pending"}, {"$set": {"status": "discarded", "result": "Conversación reiniciada", "resolved_at": now_iso()}})
    await db.chat_sessions.update_many({"status": "active"}, {"$set": {"status": "archived", "archived_at": now_iso()}})
    return await state()


async def set_memory(notes: list) -> list:
    clean = [{"text": str(n.get("text") if isinstance(n, dict) else n).strip()[:160], "ts": (n.get("ts") if isinstance(n, dict) else None) or now_iso()}
             for n in notes if str(n.get("text") if isinstance(n, dict) else n).strip()][:MAX_MEMORY_NOTES]
    await db.chat_memory.update_one({"_id": "main"}, {"$set": {"notes": clean, "updated_at": now_iso()}}, upsert=True)
    return clean
