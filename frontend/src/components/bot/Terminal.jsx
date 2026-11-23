import { useEffect, useRef, useState } from "react";
import { TerminalSquare, Trash2, Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Panel } from "./Panel";
import { fmtTime, http, errMsg } from "@/lib/api";

const LEVEL_CLS = {
  INFO: "text-slate-400",
  SCAN: "text-sky-400",
  FILTER: "text-yellow-300",
  SIGNAL: "text-amber-400",
  AI_EVAL: "text-purple-400",
  ORDER: "text-emerald-400",
  RISK: "text-orange-400",
  PLAN: "text-teal-300",
  NEWS: "text-fuchsia-300",
  ERROR: "text-rose-400",
};

const STAGE = { 0: "Fase 0 (sin IA)", 1: "Fase 1 (filtro rápido IA)", 2: "Fase 2 (evaluación final IA)" };
const stage = (s) => STAGE[s.ai_stage] || "Sin fase";
const utc = (iso) => (iso ? `${iso.slice(0, 19).replace("T", " ")} UTC` : "");
const csvCell = (v) => `"${String(v ?? "").replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;

function buildExport(d, format) {
  const rows = [
    ...d.logs.map((l) => ({ ts: l.ts, tipo: "LOG", nivel: l.level, msg: l.message })),
    ...d.signals.map((s) => ({ ts: s.created_at, tipo: "SEÑAL", nivel: stage(s), s,
      msg: `${s.symbol} ${String(s.side).toUpperCase()} · ${s.strategy} · ${s.timeframe} · precio ${s.price} · SL ${s.sl} · TP ${s.tp} · puntuación ${s.score} · decisión ${s.ai_decision}`
        + `${s.ai_confidence ? ` (${s.ai_confidence} %)` : ""}${s.executed ? " · EJECUTADA" : ""}${s.ai_model ? ` · modelo ${s.ai_model}` : ""}`
        + `${s.note ? ` · ${s.note}` : ""}${s.ai_reasoning ? ` · IA: ${s.ai_reasoning}` : ""}${s.reasons?.length ? ` · Motivos: ${s.reasons.join(" | ")}` : ""}` })),
  ].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  if (format === "csv") {
    const head = ["fecha_utc", "tipo", "nivel_o_fase", "simbolo", "lado", "estrategia", "temporalidad", "decision", "confianza", "ejecutada", "mensaje"];
    return "\ufeff" + [head.join(","), ...rows.map((r) => [utc(r.ts), r.tipo, r.nivel, r.s?.symbol, r.s?.side, r.s?.strategy, r.s?.timeframe, r.s?.ai_decision, r.s?.ai_confidence,
      r.s ? (r.s.executed ? "sí" : "no") : "", r.msg].map(csvCell).join(","))].join("\n");
  }
  const count = (n) => d.signals.filter((s) => s.ai_stage === n).length;
  return [`REGISTRO DEL BOT · ${d.bot}`, `Periodo: últimas ${d.hours} horas (desde ${utc(d.since)})`,
    `Líneas de registro: ${d.logs.length} · señales detectadas: ${d.signals.length} (fase 0: ${count(0)} · fase 1: ${count(1)} · fase 2: ${count(2)} · sin fase: ${d.signals.filter((s) => s.ai_stage == null).length})`,
    "", ...rows.map((r) => `${utc(r.ts)} [${r.tipo === "LOG" ? r.nivel : `SEÑAL · ${r.nivel}`}] ${r.msg}`)].join("\n");
}

function ExportControls() {
  const [hours, setHours] = useState("24");
  const [format, setFormat] = useState("txt");
  const [busy, setBusy] = useState(false);
  const SEL = "h-6 rounded-sm border border-[#1E293B] bg-[#07090E] px-1 font-mono text-[10px] uppercase text-slate-300 focus:outline-none";
  const download = async () => {
    setBusy(true);
    try {
      const { data } = await http.get(`/logs/export?hours=${hours}`, { timeout: 60000 });
      if (!data.logs.length && !data.signals.length) { toast.info(`No hay registros en las últimas ${hours} horas`); return; }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([buildExport(data, format)], { type: `${format === "csv" ? "text/csv" : "text/plain"};charset=utf-8;` }));
      a.download = `registro_${data.bot.replace(/\s+/g, "_").toLowerCase()}_${hours}h_${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <>
      <select data-testid="logs-export-hours-select" value={hours} onChange={(e) => setHours(e.target.value)} className={SEL} title="Periodo a descargar">
        <option value="24">24 h</option><option value="48">48 h</option><option value="72">72 h</option>
      </select>
      <select data-testid="logs-export-format-select" value={format} onChange={(e) => setFormat(e.target.value)} className={SEL} title="Formato del archivo">
        <option value="txt">TXT</option><option value="csv">CSV</option>
      </select>
      <button data-testid="logs-export-button" onClick={download} disabled={busy} title="Descarga todo el registro y las señales detectadas (fase 0 sin IA, fase 1 y fase 2) del periodo elegido"
        className="inline-flex items-center gap-1 font-mono text-[10px] uppercase text-slate-400 transition-colors hover:text-amber-300 disabled:opacity-50">
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} Descargar
      </button>
    </>
  );
}

export default function Terminal({ logs, onClear, exportable = false }) {
  const ref = useRef(null);
  const lines = [...logs].reverse();
  const newest = logs[0]?.id;
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [newest]);

  return (
    <Panel testId="terminal-panel" title="Registro del bot" icon={TerminalSquare} className="lg:col-span-7" delay={380}
      actions={
        <span className="flex flex-wrap items-center justify-end gap-2">
          <button data-testid="clear-logs-button" title={exportable ? "Borra todos los registros guardados de este bot (no afecta a señales, posiciones ni historial)" : undefined}
            onClick={() => (!exportable || window.confirm("¿Borrar TODOS los registros de este bot? No se puede deshacer. Descárgalos antes si los necesitas.")) && onClear()}
            className="inline-flex items-center gap-1 font-mono text-[10px] uppercase text-slate-500 transition-colors hover:text-rose-300">
            <Trash2 className="h-3 w-3" /> {exportable ? "Borrar todo" : "Limpiar"}
          </button>
        </span>
      }>
      {exportable && (
        <div data-testid="logs-export-bar" className="flex flex-wrap items-center gap-2 border-b border-[#1E293B] px-4 py-2">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Descargar registro y señales (fases 0, 1 y 2)</span>
          <ExportControls />
        </div>
      )}
      <div ref={ref} data-testid="terminal-log" className="terminal h-[440px] overflow-y-auto thin-scroll px-4 py-3 font-mono text-[11.5px] leading-6">
        {lines.length ? lines.map((l) => (
          <div key={l.id} className="flex gap-3 whitespace-pre-wrap">
            <span className="shrink-0 text-slate-600">{fmtTime(l.ts)}</span>
            <span className={`w-[68px] shrink-0 ${LEVEL_CLS[l.level] || "text-slate-400"}`}>[{l.level}]</span>
            <span className="text-slate-300">{l.message}</span>
          </div>
        )) : <span className="text-slate-600">$ esperando eventos...</span>}
        <span className="inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse bg-amber-400/80" />
      </div>
    </Panel>
  );
}
