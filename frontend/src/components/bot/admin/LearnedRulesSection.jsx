import { useEffect, useState } from "react";
import { BookOpen, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Panel } from "../Panel";
import { http, errMsg } from "@/lib/api";
import { INPUT, BTN } from "./SecuritySection";

function RulesSummary({ rules, max }) {
  const today = new Date().toDateString();
  const since = (r) => (r.created_at ? (Date.now() - new Date(r.created_at).getTime()) / 86400000 : Infinity);
  const sum = (k) => rules.reduce((a, r) => a + (r[k] || 0), 0);
  const active = rules.filter((r) => r.enabled).length;
  const auto = rules.filter((r) => r.auto).length;
  const closed = sum("blocked_closed");
  const pnl = Math.round(sum("blocked_pnl") * 100) / 100;
  const cards = [
    ["total", "Reglas en total", `${rules.length}/${max}`, `${active} activas · ${rules.length - active} apagadas`],
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

export default function LearnedRulesSection() {
  const [rules, setRules] = useState(null);
  const [max, setMax] = useState(30);
  const [text, setText] = useState("");
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState("");
  const load = () => http.get("/ai-rules").then((r) => { setRules(r.data.rules); setMax(r.data.max); }).catch(() => setRules([]));
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (text.trim().length < 5) return toast.error("Escribe una regla de al menos 5 caracteres");
    setBusy("add");
    try {
      await http.post("/ai-rules", { text: text.trim(), symbol: symbol.trim() || null });
      setText(""); setSymbol(""); toast.success("Regla añadida"); await load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(""); }
  };
  const toggle = async (r) => {
    try { await http.patch(`/ai-rules/${r.id}`, { enabled: !r.enabled }); await load(); } catch (e) { toast.error(errMsg(e)); }
  };
  const remove = async (r) => {
    setBusy(r.id);
    try { await http.delete(`/ai-rules/${r.id}`); toast.success("Regla eliminada"); await load(); } catch (e) { toast.error(errMsg(e)); } finally { setBusy(""); }
  };
  const active = (rules || []).filter((r) => r.enabled).length;

  return (
    <Panel title="Reglas aprendidas" icon={BookOpen} testId="learned-rules-section" bodyClass="space-y-3 p-4"
      actions={<span data-testid="learned-rules-count" className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{active} activas · {(rules || []).length}/{max}</span>}>
      <p className="text-[12px] text-slate-400">
        Lecciones de los análisis de operaciones cerradas o reglas propias. La IA las comprueba en cada señal de este bot y cita la regla cuando una señal la incumple.
        En Bybit y HotForex son el <span className="text-amber-300">único filtro de entrada</span> y se crean solas tras cada pérdida evitable; en Pepperstone acompañan a la evaluación IA. Debajo de cada regla ves cuántas señales bloqueó y qué habría pasado sin ella.
      </p>
      {rules && rules.length > 0 && <RulesSummary rules={rules} max={max} />}
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
      {rules && rules.length === 0 && <p data-testid="learned-rules-empty" className="text-xs text-slate-500">Todavía no hay reglas. Analiza una operación del historial y pulsa «Guardar como regla», o escribe una arriba.</p>}
      {rules && rules.length > 0 && (
        <ul data-testid="learned-rules-list" className="divide-y divide-[#1E293B] rounded-sm border border-[#1E293B]">
          {rules.map((r) => (
            <li key={r.id} data-testid={`learned-rule-${r.id}`} className={`flex items-start gap-3 px-3 py-2 ${r.enabled ? "" : "opacity-50"}`}>
              <Switch data-testid={`learned-rule-toggle-${r.id}`} checked={r.enabled} onCheckedChange={() => toggle(r)} className="mt-0.5" />
              <div className="min-w-0 flex-1">
                <p className="text-xs text-slate-200">{r.text}</p>
                <p className="mt-0.5 font-mono text-[10px] text-slate-500">
                  {r.symbol ? `Solo ${r.symbol}` : "Todos los símbolos"}{r.verdict ? ` · de un análisis ${r.verdict}` : ""}{r.created_at ? ` · ${new Date(r.created_at).toLocaleDateString("es-ES")}` : ""}{r.auto ? " · automática" : ""}
                </p>
                <p data-testid={`learned-rule-stats-${r.id}`} className="mt-0.5 font-mono text-[10px] text-slate-500">
                  {r.hits || 0} señales bloqueadas{r.blocked_closed ? <> · de {r.blocked_closed} ya resueltas, {r.blocked_wins} habrían ganado (<span className={r.blocked_pnl > 0 ? "text-rose-300" : "text-emerald-300"}>{r.blocked_pnl > 0 ? "+" : ""}{r.blocked_pnl} sin la regla</span>)</> : ""}
                  {r.misses ? <span className="text-amber-300"> · {r.misses} ganadoras que habría bloqueado</span> : ""}
                </p>
              </div>
              <button data-testid={`learned-rule-delete-${r.id}`} onClick={() => remove(r)} disabled={busy === r.id} title="Eliminar regla" className="rounded-sm p-1 text-slate-500 transition-colors hover:bg-rose-500/10 hover:text-rose-300">
                {busy === r.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
