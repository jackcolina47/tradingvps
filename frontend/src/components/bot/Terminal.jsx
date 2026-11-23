import { useEffect, useRef, useState } from "react";
import { Download, Loader2, TerminalSquare, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Panel } from "./Panel";
import { http, fmtTime } from "@/lib/api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";

const LEVEL_CLS = {
  INFO: "text-slate-400",
  SCAN: "text-sky-400",
  FILTER: "text-slate-500",
  SIGNAL: "text-amber-400",
  AI_EVAL: "text-purple-400",
  ORDER: "text-emerald-400",
  RISK: "text-orange-400",
  PLAN: "text-teal-300",
  NEWS: "text-fuchsia-300",
  ERROR: "text-rose-400",
};
const TRIGGER_CLS = "h-7 w-auto gap-1 border-[#1E293B] bg-[#07090E] px-2 font-mono text-[10px] uppercase text-slate-300";
const BTN_CLS = "inline-flex h-7 items-center gap-1 rounded-sm border border-[#1E293B] px-2 font-mono text-[10px] uppercase transition-colors disabled:opacity-50";

function Pick({ value, onChange, options, testId }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger data-testid={testId} className={TRIGGER_CLS}><SelectValue /></SelectTrigger>
      <SelectContent>
        {options.map(([v, label]) => <SelectItem key={v} value={v} data-testid={`${testId}-${v}`} className="font-mono text-xs">{label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function LogExport() {
  const [hours, setHours] = useState("24");
  const [fmt, setFmt] = useState("txt");
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const res = await http.get("/logs/export", { params: { hours, fmt }, responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `registro-${window.location.pathname.split("/")[1] || "bot"}-${hours}h-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Registro descargado · ${res.headers["x-log-lines"] ?? "0"} líneas de las últimas ${hours} h`);
    } catch {
      toast.error("No se pudo descargar el registro");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Pick value={hours} onChange={setHours} testId="logs-export-hours" options={[["24", "24 h"], ["48", "48 h"], ["72", "72 h"]]} />
      <Pick value={fmt} onChange={setFmt} testId="logs-export-format" options={[["txt", "TXT"], ["csv", "CSV"]]} />
      <button data-testid="logs-download-button" onClick={download} disabled={busy} className={`${BTN_CLS} text-amber-300 hover:border-amber-500/40 hover:bg-amber-500/10`}>
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} Descargar
      </button>
    </>
  );
}

function ClearLogs({ onClear }) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button data-testid="clear-logs-button" className={`${BTN_CLS} text-slate-400 hover:border-rose-500/40 hover:text-rose-300`}>
          <Trash2 className="h-3 w-3" /> Borrar todo
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent data-testid="clear-logs-dialog" className="border-[#1E293B] bg-[#0B0F17]">
        <AlertDialogHeader>
          <AlertDialogTitle>¿Borrar todo el registro?</AlertDialogTitle>
          <AlertDialogDescription>
            Se eliminan todas las líneas guardadas de este bot y ya no se podrán descargar. Las operaciones, las decisiones del embudo y las reglas no se tocan.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel data-testid="clear-logs-cancel">Cancelar</AlertDialogCancel>
          <AlertDialogAction data-testid="clear-logs-confirm" onClick={onClear} className="bg-rose-600 text-white hover:bg-rose-500">Borrar todo</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export default function Terminal({ logs, onClear }) {
  const ref = useRef(null);
  const lines = [...logs].reverse();
  const newest = logs[0]?.id;
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [newest]);

  return (
    <Panel testId="terminal-panel" title="Registro del bot" icon={TerminalSquare} className="lg:col-span-7" delay={380}>
      <div data-testid="logs-toolbar" className="flex flex-wrap items-center gap-2 border-b border-[#1E293B] px-4 py-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">Descargar registro completo</span>
        <LogExport />
        <span className="ml-auto"><ClearLogs onClear={onClear} /></span>
      </div>
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
