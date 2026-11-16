import { useEffect, useMemo, useState } from "react";
import { BookOpen, Check, ChevronLeft, ChevronRight, History, Loader2, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Panel } from "../Panel";
import { http, errMsg } from "@/lib/api";
import { INPUT, BTN } from "./SecuritySection";

const PAGE = 10;
const FILTERS = [["all", "Todas"], ["on", "Activas"], ["off", "Apagadas"], ["auto", "Automáticas"], ["manual", "Manuales"], ["ai", "Tocadas por la IA"]];
const SORTS = [["new", "Más recientes"], ["hits", "Más bloqueos"], ["cost", "Peor efecto"], ["good", "Mejor efecto"], ["misses", "Más ganadoras en riesgo"]];
const MATCH = { all: () => true, on: (r) => r.enabled, off: (r) => !r.enabled, auto: (r) => r.auto, manual: (r) => !r.auto, ai: (r) => !!r.ai_edited_at };
const ORDER = { new: () => 0, hits: (a, b) => (b.hits || 0) - (a.hits || 0), cost: (a, b) => b.blocked_pnl - a.blocked_pnl, good: (a, b) => a.blocked_pnl - b.blocked_pnl, misses: (a, b) => (b.misses || 0) - (a.misses || 0) };
const EVENT = { NUEVA: ["Nueva", "text-emerald-300"], MODIFICAR: ["Reescrita", "text-sky-300"], DESACTIVAR: ["Apagada", "text-amber-300"], ELIMINAR: ["Eliminada", "text-rose-300"] };
const SEL = "h-8 rounded-sm border border-[#1E293B] bg-[#07090E] px-2 font-mono text-[11px] text-slate-300 outline-none";
const ICON_BTN = "rounded-sm p-1 text-slate-500 transition-colors hover:bg-[#111827] hover:text-slate-200 disabled:opacity-40";

function RulesSummary({ rules }) {
  const today = new Date().toDateString();
  const since = (r) => (r.created_at ? (Date.now() - new Date(r.created_at).getTime()) / 86400000 : Infinity);
  const sum = (k) => rules.reduce((a, r) => a + (r[k] || 0), 0);
  const active = rules.filter((r) => r.enabled).length;
  const auto = rules.filter((r) => r.auto).length;
  const closed = sum("blocked_closed");
  const pnl = Math.round(sum("blocked_pnl") * 100) / 100;
  const cards = [
    ["total", "Reglas en total", rules.length, `${active} activas · ${rules.length - active} apagadas`],
    ["today", "Añadidas hoy", rules.filter((r) => r.created_at && new Date(r.created_at).toDateString() === today).length, `${rules.filter((r) => since(r) <= 7).length} en los últimos 7 días`],
    ["origin", "Automáticas", auto, `${rules.length - auto} escritas o guardadas a mano`],
    ["hits", "Señales bloqueadas", sum("hits"), closed ? `${closed} ya resueltas · ${sum("blocked_wins")} habrían ganado` : "ninguna resuelta todavía"],
    ["pnl", "Efecto de las reglas", closed ? `${pnl > 0 ? "−" : "+"}${Math.abs(pnl)}` : "—",
      closed ? (pnl > 0 ? "bloquearon más ganancias que pérdidas" : "pérdidas netas evitadas") : "se calcula al resolverse las bloqueadas", closed ? (pnl > 0 ? "text-rose-300" : "text-emerald-300") : ""],
    ["misses", "Ganadoras en riesgo", sum("misses"), "operaciones ganadoras que una regla activa habría bloqueado", sum("misses") ? "text-amber-300" : ""],
  ];
  return (
    <div data-testid="learned-rules-summary" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {cards.map(([k, label, value, hint, cls]) => (
        <div key={k} data-testid={`learned-rules-stat-${k}`} className="rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2">
          <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-slate-500">{label}</p>
          <p className={`num mt-0.5 text-base font-semibold ${cls || "text-slate-100"}`}>{value}</p>
          <p className="mt-0.5 text-[10px] leading-snug text-slate-500">{hint}</p>
        </div>
      ))}
    </div>
  );
}

function RuleRow({ r, busy, onToggle, onRemove, onSave }) {
  const [draft, setDraft] = useState(null);
  const save = async () => { if (await onSave(r, draft)) setDraft(null); };
  return (
    <li data-testid={`learned-rule-${r.id}`} className={`flex items-start gap-3 px-3 py-2 ${r.enabled ? "" : "opacity-60"}`}>
      <Switch data-testid={`learned-rule-toggle-${r.id}`} checked={r.enabled} onCheckedChange={() => onToggle(r)} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        {draft === null ? <p className="break-words text-xs text-slate-200">{r.text}</p> : (
          <textarea data-testid={`learned-rule-edit-input-${r.id}`} value={draft} maxLength={300} rows={2} autoFocus onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded-sm border border-amber-500/40 bg-[#07090E] px-2 py-1 text-xs text-slate-100 outline-none" />
        )}
        <p className="mt-0.5 font-mono text-[10px] text-slate-500">
          {r.symbol ? `Solo ${r.symbol}` : "Todos los símbolos"}{r.verdict ? ` · de un análisis ${r.verdict}` : ""}{r.created_at ? ` · ${new Date(r.created_at).toLocaleDateString("es-ES")}` : ""}{r.auto ? " · automática" : ""}
        </p>
        <p data-testid={`learned-rule-stats-${r.id}`} className="mt-0.5 font-mono text-[10px] text-slate-500">
          {r.hits || 0} señales bloqueadas{r.blocked_closed ? <> · de {r.blocked_closed} ya resueltas, {r.blocked_wins} habrían ganado (<span className={r.blocked_pnl > 0 ? "text-rose-300" : "text-emerald-300"}>{r.blocked_pnl > 0 ? "+" : ""}{r.blocked_pnl} sin la regla</span>)</> : ""}
          {r.misses ? <span className="text-amber-300"> · {r.misses} ganadoras que habría bloqueado</span> : ""}
        </p>
        {r.ai_note && <p data-testid={`learned-rule-ai-note-${r.id}`} className="mt-0.5 text-[10px] text-sky-300/80">{r.ai_note} · {new Date(r.ai_edited_at).toLocaleDateString("es-ES")}</p>}
      </div>
      {draft === null ? (
        <>
          <button data-testid={`learned-rule-edit-${r.id}`} onClick={() => setDraft(r.text)} title="Editar el texto" className={ICON_BTN}><Pencil className="h-3.5 w-3.5" /></button>
          <button data-testid={`learned-rule-delete-${r.id}`} onClick={() => onRemove(r)} disabled={busy === r.id} title="Eliminar regla" className={`${ICON_BTN} hover:bg-rose-500/10 hover:text-rose-300`}>
            {busy === r.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          </button>
        </>
      ) : (
        <>
          <button data-testid={`learned-rule-edit-save-${r.id}`} onClick={save} title="Guardar" className={`${ICON_BTN} text-emerald-300`}><Check className="h-3.5 w-3.5" /></button>
          <button data-testid={`learned-rule-edit-cancel-${r.id}`} onClick={() => setDraft(null)} title="Cancelar" className={ICON_BTN}><X className="h-3.5 w-3.5" /></button>
        </>
      )}
    </li>
  );
}

function Activity({ events }) {
  const [open, setOpen] = useState(false);
  if (!events?.length) return null;
  return (
    <div data-testid="learned-rules-activity" className="rounded-sm border border-[#1E293B]">
      <button data-testid="learned-rules-activity-toggle" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-400 hover:text-slate-200">
        <History className="h-3.5 w-3.5" /> Actividad del aprendizaje automático ({events.length}) <span className="ml-auto">{open ? "Ocultar" : "Ver"}</span>
      </button>
      {open && (
        <ul className="thin-scroll max-h-64 divide-y divide-[#1E293B] overflow-y-auto border-t border-[#1E293B]">
          {events.map((e, i) => (
            <li key={i} className="px-3 py-1.5 text-[11px] text-slate-300">
              <span className={`font-mono text-[10px] font-bold uppercase ${EVENT[e.action]?.[1] || "text-slate-400"}`}>{EVENT[e.action]?.[0] || e.action}</span>
              <span className="ml-2 font-mono text-[10px] text-slate-500">{new Date(e.at).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" })}{e.symbol ? ` · tras ${e.symbol} ${e.pnl > 0 ? "+" : ""}${e.pnl}` : ""}</span>
              <p className="mt-0.5 break-words">{e.prev ? <><span className="text-slate-500 line-through">{e.prev}</span> → </> : ""}{e.text}</p>
              {e.reason && <p className="text-[10px] text-slate-500">{e.reason}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function LearnedRulesSection() {
  const [rules, setRules] = useState(null);
  const [events, setEvents] = useState([]);
  const [text, setText] = useState("");
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("new");
  const [page, setPage] = useState(0);
  const load = () => http.get("/ai-rules").then((r) => { setRules(r.data.rules); setEvents(r.data.events || []); }).catch(() => setRules([]));
  useEffect(() => { load(); }, []);
  useEffect(() => setPage(0), [q, filter, sort]);

  const run = async (key, fn, ok) => {
    setBusy(key);
    try { await fn(); if (ok) toast.success(ok); await load(); return true; } catch (e) { toast.error(errMsg(e)); return false; } finally { setBusy(""); }
  };
  const add = () => {
    if (text.trim().length < 5) return toast.error("Escribe una regla de al menos 5 caracteres");
    return run("add", async () => { await http.post("/ai-rules", { text: text.trim(), symbol: symbol.trim() || null }); setText(""); setSymbol(""); }, "Regla añadida");
  };
  const saveText = (r, t) => {
    if (t.trim().length < 5) { toast.error("La regla debe tener al menos 5 caracteres"); return false; }
    return run(r.id, () => http.patch(`/ai-rules/${r.id}`, { text: t.trim() }), "Regla actualizada");
  };

  const view = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rules || []).filter(MATCH[filter]).filter((r) => !needle || r.text.toLowerCase().includes(needle) || (r.symbol || "").toLowerCase().includes(needle)).sort(ORDER[sort]);
  }, [rules, q, filter, sort]);
  const pages = Math.max(1, Math.ceil(view.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const off = (rules || []).filter((r) => !r.enabled);
  const removeOff = () => window.confirm(`¿Eliminar las ${off.length} reglas apagadas? No se puede deshacer.`)
    && run("bulk", () => Promise.all(off.map((r) => http.delete(`/ai-rules/${r.id}`))), "Reglas apagadas eliminadas");

  return (
    <Panel title="Reglas aprendidas" icon={BookOpen} testId="learned-rules-section" bodyClass="space-y-3 p-4"
      actions={<span data-testid="learned-rules-count" className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{(rules || []).length - off.length} activas · {(rules || []).length} en total</span>}>
      <p className="text-[12px] text-slate-400">
        Lecciones de los análisis de operaciones cerradas o reglas propias, sin límite de cantidad. La IA las comprueba en cada señal de este bot y cita la regla cuando una señal la incumple.
        En Bybit y HotForex son el <span className="text-amber-300">único filtro de entrada</span>: tras cada operación cerrada la IA puede añadir una regla nueva y también apagar, reescribir o eliminar las existentes; todo queda en «Actividad». En Pepperstone acompañan a la evaluación IA.
      </p>
      {rules && rules.length > 0 && <RulesSummary rules={rules} />}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[240px] flex-1">
          <Input data-testid="learned-rule-text-input" value={text} maxLength={300} placeholder="Ej.: No comprar tras 3 velas grandes seguidas si hay un swing alto a menos de 1 ATR" onChange={(e) => setText(e.target.value)} className={INPUT} />
        </div>
        <div className="w-[130px]">
          <Input data-testid="learned-rule-symbol-input" value={symbol} maxLength={20} placeholder="Símbolo (opc.)" onChange={(e) => setSymbol(e.target.value.toUpperCase())} className={INPUT} />
        </div>
        <Button data-testid="learned-rule-add-button" onClick={add} disabled={busy === "add"} className={BTN}>
          {busy === "add" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-1.5 h-3.5 w-3.5" />} Añadir
        </Button>
      </div>
      {rules === null && <p className="text-xs text-slate-500">Cargando…</p>}
      {rules && rules.length === 0 && <p data-testid="learned-rules-empty" className="text-xs text-slate-500">Todavía no hay reglas. Se crearán solas al analizar operaciones cerradas, o puedes escribir una arriba.</p>}
      {rules && rules.length > 0 && (
        <>
          <div data-testid="learned-rules-toolbar" className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[160px] flex-1">
              <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-slate-600" />
              <input data-testid="learned-rules-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar en las reglas…" className={`${SEL} w-full pl-8`} />
            </div>
            <select data-testid="learned-rules-filter" value={filter} onChange={(e) => setFilter(e.target.value)} className={SEL}>
              {FILTERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <select data-testid="learned-rules-sort" value={sort} onChange={(e) => setSort(e.target.value)} className={SEL}>
              {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            {off.length > 0 && (
              <button data-testid="learned-rules-delete-off" onClick={removeOff} disabled={busy === "bulk"} className="h-8 rounded-sm border border-rose-500/30 px-2 font-mono text-[10px] uppercase tracking-wider text-rose-300 transition-colors hover:bg-rose-500/10">
                Borrar apagadas ({off.length})
              </button>
            )}
          </div>
          {view.length === 0 ? <p data-testid="learned-rules-no-match" className="text-xs text-slate-500">Ninguna regla coincide con la búsqueda o el filtro.</p> : (
            <ul data-testid="learned-rules-list" className="divide-y divide-[#1E293B] rounded-sm border border-[#1E293B]">
              {view.slice(cur * PAGE, cur * PAGE + PAGE).map((r) => (
                <RuleRow key={r.id} r={r} busy={busy}
                  onToggle={(x) => run("", () => http.patch(`/ai-rules/${x.id}`, { enabled: !x.enabled }))}
                  onRemove={(x) => run(x.id, () => http.delete(`/ai-rules/${x.id}`), "Regla eliminada")} onSave={saveText} />
              ))}
            </ul>
          )}
          {view.length > PAGE && (
            <div data-testid="learned-rules-pagination" className="flex items-center justify-between font-mono text-[10px] uppercase tracking-wider text-slate-500">
              <span>{cur * PAGE + 1}–{Math.min(view.length, cur * PAGE + PAGE)} de {view.length}</span>
              <span className="flex items-center gap-1">
                <button data-testid="learned-rules-prev" onClick={() => setPage(cur - 1)} disabled={cur === 0} className={ICON_BTN}><ChevronLeft className="h-4 w-4" /></button>
                <span data-testid="learned-rules-page">Página {cur + 1} / {pages}</span>
                <button data-testid="learned-rules-next" onClick={() => setPage(cur + 1)} disabled={cur >= pages - 1} className={ICON_BTN}><ChevronRight className="h-4 w-4" /></button>
              </span>
            </div>
          )}
        </>
      )}
      <Activity events={events} />
    </Panel>
  );
}
