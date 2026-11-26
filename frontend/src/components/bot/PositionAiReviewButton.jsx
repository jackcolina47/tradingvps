import { useState } from "react";
import { toast } from "sonner";
import { Sparkles, Loader2 } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { http, errMsg } from "@/lib/api";

/** AI analysis of ONE open position, behind a confirmation so no AI credit is spent by accident. */
export default function PositionAiReviewButton({ p, onDone, className = "", label = "" }) {
  const [busy, setBusy] = useState(false);
  const [opinion, setOpinion] = useState(true);
  const run = async () => {
    setBusy(true);
    try {
      const { data } = await http.post(`/positions/${p.id}/ai-review`, null, { timeout: 120000, params: { opinion_only: opinion } });
      const r = data.review;
      toast.success(`${p.symbol}: IA ${r.action} (${r.confidence} %)${r.acted ? ` · ${r.acted}` : opinion ? " · solo opinión, sin cambios" : ""}`, { description: r.reasoning, duration: 12000 });
      onDone?.();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button data-testid={`position-ai-review-btn-${p.id}`} disabled={busy} title={`Pedir a la IA que analice solo ${p.symbol}`}
          className={`inline-flex items-center justify-center gap-1 rounded-sm border border-violet-400/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-violet-300 transition-colors hover:bg-violet-500/10 disabled:opacity-50 ${className}`}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}{label && <span>{busy ? "Analizando…" : label}</span>}
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent data-testid="position-ai-review-confirm" className="border-[#1E293B] bg-[#0D111A]">
        <AlertDialogHeader>
          <AlertDialogTitle className="font-display">¿Analizar {p.symbol} {p.side === "long" ? "LONG" : "SHORT"} con IA?</AlertDialogTitle>
          <AlertDialogDescription>
            Se hará una llamada al modelo de IA solo para esta operación, y consume saldo de IA. La respuesta será MANTENER, BREAKEVEN o CERRAR.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <label className={`flex cursor-pointer items-start gap-2 rounded-sm border p-2.5 transition-colors ${opinion ? "border-emerald-500/40 bg-emerald-500/[0.05]" : "border-rose-500/40 bg-rose-500/[0.05]"}`}>
          <input data-testid="position-ai-review-opinion-only" type="checkbox" checked={opinion} onChange={(e) => setOpinion(e.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-500" />
          <span className="min-w-0">
            <span className="block text-[12px] font-semibold text-slate-100">Solo opinión</span>
            <span data-testid="position-ai-review-opinion-hint" className="block text-[11px] leading-snug text-slate-400">
              {opinion ? "La IA solo responde: no cierra la posición ni mueve el stop."
                : `Se aplica el modo de revisión de Ajustes: si es automático y la confianza supera el mínimo, la IA puede mover el stop a breakeven o cerrar la posición${p.live ? " REAL" : ""}.`}
            </span>
          </span>
        </label>
        <AlertDialogFooter>
          <AlertDialogCancel data-testid="position-ai-review-cancel">Cancelar</AlertDialogCancel>
          <AlertDialogAction data-testid="position-ai-review-accept" onClick={run} className="bg-violet-500 text-white hover:bg-violet-600">Analizar con IA</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
