import { useEffect, useState } from "react";
import { Loader2, Sparkles, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { http, errMsg } from "@/lib/api";

const VERDICT = {
  BUENA: "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
  ACEPTABLE: "border-sky-500/50 bg-sky-500/10 text-sky-300",
  EVITABLE: "border-amber-500/50 bg-amber-500/10 text-amber-300",
  MALA: "border-rose-500/50 bg-rose-500/10 text-rose-300",
};
const BLOCKS = [
  ["why_opened", "Por qué se abrió"], ["chart_context", "Cómo estaba el gráfico"], ["what_happened", "Qué pasó después"], ["better_decision", "¿Había una decisión mejor?"],
];

export default function TradeAiReview({ trade, autoRun = false, onReviewed }) {
  const [review, setReview] = useState(trade.ai_review || null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = async (force = false) => {
    setBusy(true); setErr("");
    try {
      const { data } = await http.post(`/trades/${trade.id}/analyze${force ? "?force=true" : ""}`);
      setReview(data.review);
      onReviewed?.(data.review);
      if (!data.cached) toast.success("Análisis de la IA listo");
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { setReview(trade.ai_review || null); }, [trade.id, trade.ai_review]);
  useEffect(() => { if (autoRun && !trade.ai_review) run(); }, [trade.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div data-testid="trade-ai-review" className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"><Sparkles className="h-3.5 w-3.5 text-amber-300" /> Análisis IA de la operación</p>
        <div className="flex items-center gap-2">
          {review && <span data-testid="trade-ai-verdict" className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${VERDICT[review.verdict] || VERDICT.ACEPTABLE}`}>{review.verdict} · {review.confidence} %</span>}
          <button data-testid="trade-ai-analyze-button" onClick={() => run(!!review)} disabled={busy}
            className="inline-flex items-center gap-1 rounded-sm border border-amber-500/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-300 transition-colors hover:bg-amber-500/10 disabled:opacity-50">
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : review ? <RefreshCw className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
            {busy ? "Analizando…" : review ? "Volver a analizar" : "Analizar IA"}
          </button>
        </div>
      </div>
      {err && <p data-testid="trade-ai-error" className="text-xs text-rose-400">{err}</p>}
      {busy && !review && <p className="text-xs text-slate-500">La IA está revisando la señal original, las velas previas y el recorrido de la operación (10-40 s)…</p>}
      {!busy && !review && !err && <p className="text-xs text-slate-500">Pide a la IA un análisis detallado: por qué se abrió, cómo estaba el gráfico en ese momento y si había una decisión mejor.</p>}
      {review && (
        <div className="space-y-2.5">
          {review.verdict_reason && <p className="text-xs text-slate-300">{review.verdict_reason}</p>}
          {BLOCKS.map(([k, title]) => review[k] && (
            <div key={k} data-testid={`trade-ai-${k.replace(/_/g, "-")}`}>
              <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{title}</p>
              <p className="text-[13px] leading-relaxed text-slate-200">{review[k]}</p>
            </div>
          ))}
          {review.price_action?.length > 0 && (
            <div>
              <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Hechos del gráfico (calculados)</p>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-slate-400">{review.price_action.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </div>
          )}
          {review.lesson && (
            <p data-testid="trade-ai-lesson" className="rounded-sm border border-amber-500/30 bg-amber-500/5 px-2.5 py-1.5 text-xs text-amber-200"><span className="font-mono text-[10px] uppercase tracking-wider text-amber-400">Regla sugerida · </span>{review.lesson}</p>
          )}
          <p className="text-[10px] text-slate-600">{review.model} · {new Date(review.at).toLocaleString("es-ES")}</p>
        </div>
      )}
    </div>
  );
}
