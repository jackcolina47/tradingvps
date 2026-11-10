import { X, Sparkles, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { SideBadge, Empty } from "./Panel";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { http, errMsg, fmtPrice, fmtSignedUsd, fmtUsd, fmtQty, pnlClass, STRATEGY_LABELS, ENTRY_LABELS } from "@/lib/api";

const AI_CLS = { MANTENER: "border-emerald-500/40 text-emerald-300", BREAKEVEN: "border-amber-500/40 text-amber-300", CERRAR: "border-rose-500/40 text-rose-300" };

function AiReviewNowButton() {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const { data } = await http.post("/positions/ai-review");
      const acted = data.reviews.filter((r) => r.acted).length;
      toast.success(`Revisión IA completada: ${data.count} posición(es) · ${data.reviews.map((r) => `${r.symbol} ${r.action}`).join(", ") || "sin posiciones"}${acted ? ` · ${acted} acción(es)` : ""}`);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <button data-testid="positions-ai-review-button" onClick={run} disabled={busy} title="Pedir a la IA que revise ahora todas las posiciones abiertas (según el modo configurado en Ajustes)"
      className="inline-flex items-center gap-1 rounded-sm border border-violet-400/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-violet-300 transition-colors hover:bg-violet-500/10 disabled:opacity-50">
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />} {busy ? "Revisando…" : "Revisar con IA"}
    </button>
  );
}

const TH = ({ children, right }) => (
  <th className={`whitespace-nowrap px-3 py-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-slate-500 ${right ? "text-right" : "text-left"}`}>{children}</th>
);
const TD = ({ children, right, className = "" }) => (
  <td className={`whitespace-nowrap px-3 py-2.5 ${right ? "text-right" : ""} ${className}`}>{children}</td>
);

export default function PositionsTable({ positions, onClose, onCloseAll }) {
  if (!positions.length) return <Empty testId="positions-empty" text="Sin posiciones abiertas. El bot abrirá operaciones cuando una señal pase los filtros de riesgo y la IA." />;
  const anyLive = positions.some((p) => p.live);
  return (
    <div className="overflow-x-auto thin-scroll">
      <div className="flex items-center justify-end gap-2 border-b border-[#1E293B] px-3 py-1.5">
        <AiReviewNowButton />
        {onCloseAll && positions.length > 1 && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <button data-testid="close-all-positions-button" className="rounded-sm border border-rose-500/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-rose-300 transition-colors hover:bg-rose-500/10">
                Cerrar todas ({positions.length}){anyLive ? " · REAL" : ""}
              </button>
            </AlertDialogTrigger>
            <AlertDialogContent data-testid="close-all-dialog" className="border-[#1E293B] bg-[#0D111A]">
              <AlertDialogHeader>
                <AlertDialogTitle className="font-display">¿Cerrar las {positions.length} posiciones a mercado?</AlertDialogTitle>
                <AlertDialogDescription>{anyLive ? "Se enviarán órdenes reduce-only a Bybit con dinero real. " : ""}El PnL se realiza al precio actual.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel data-testid="close-all-cancel">Cancelar</AlertDialogCancel>
                <AlertDialogAction data-testid="close-all-confirm" onClick={onCloseAll} className="bg-rose-500 text-white hover:bg-rose-600">Cerrar todas</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
      <table data-testid="positions-table" className="w-full text-sm">
        <thead className="border-b border-[#1E293B]">
          <tr><TH>Símbolo</TH><TH>Lado</TH><TH right>Entrada</TH><TH right>Marca</TH><TH right>SL</TH><TH right>TP</TH><TH right>Tamaño</TH><TH right>Margen</TH><TH right>PnL</TH><TH right>Acción</TH></tr>
        </thead>
        <tbody className="num text-[12px]">
          {positions.map((p) => {
            const pct = p.margin ? (p.unrealized_pnl / p.margin) * 100 : 0;
            return (
              <tr key={p.id} data-testid={`position-row-${p.id}`} className={`border-b border-[#1E293B]/60 transition-colors hover:bg-[#111827] ${p.live ? "bg-rose-500/[0.03]" : ""}`}>
                <TD>
                  <p className="font-display text-[13px] font-semibold text-slate-100">{p.symbol}</p>
                  <p className="text-[10px] text-slate-500">
                    {STRATEGY_LABELS[p.strategy]} · {p.leverage}x
                    {p.horizon === "long" && <span data-testid={`position-horizon-${p.id}`} className="ml-1 rounded-sm bg-sky-500/20 px-1 text-[9px] text-sky-300" title="Largo plazo: no cuenta en Máx. posiciones">LP</span>}
                    {p.expires_at && <span data-testid={`position-expires-${p.id}`} className="ml-1 text-[9px] text-slate-500" title="Cierre por tiempo programado">⏱ {new Date(p.expires_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}</span>}
                    {p.entry_type && p.entry_type !== "market" && <span data-testid={`position-entry-${p.id}`} className="ml-1 rounded-sm bg-slate-700/60 px-1 text-[9px] text-slate-300" title={ENTRY_LABELS[p.entry_type]}>{p.entry_type === "maker" ? "maker" : "mkt↩"}</span>}
                  </p>
                  {p.ai_review && (
                    <p data-testid={`position-ai-review-${p.id}`} title={`${p.ai_review.reasoning}${p.ai_review.btc_context ? `\n${p.ai_review.btc_context}` : ""}\n${new Date(p.ai_review.at).toLocaleString("es-ES")}`}
                      className={`mt-0.5 inline-flex items-center gap-1 rounded-sm border px-1 text-[9px] font-mono uppercase ${AI_CLS[p.ai_review.action] || AI_CLS.MANTENER}`}>
                      <Sparkles className="h-2.5 w-2.5" /> IA: {p.ai_review.action} {p.ai_review.confidence}%{p.ai_review.acted ? ` · ${p.ai_review.acted}` : ""}
                    </p>
                  )}
                </TD>
                <TD><SideBadge side={p.side} /></TD>
                <TD right>{fmtPrice(p.entry_price)}</TD>
                <TD right className="text-slate-100">{fmtPrice(p.mark_price)}</TD>
                <TD right className="text-rose-400">{fmtPrice(p.sl)}{p.breakeven && <span className="ml-1 text-[9px] text-amber-400">BE</span>}</TD>
                <TD right className="text-emerald-400">{fmtPrice(p.tp)}</TD>
                <TD right>{fmtQty(p.qty)}{p.partial_done && p.partial_gross ? <span data-testid={`position-partial-${p.id}`} className="ml-1 rounded-sm bg-emerald-500/20 px-1 text-[9px] text-emerald-300" title={`Cierre parcial ejecutado · ${fmtSignedUsd(p.partial_gross - (p.partial_fees || 0))} realizados`}>½</span> : null}</TD>
                <TD right>{fmtUsd(p.margin)}</TD>
                <TD right className={pnlClass(p.unrealized_pnl)}>
                  <span data-testid={`position-pnl-${p.id}`}>{fmtSignedUsd(p.unrealized_pnl)}</span>
                  <span className="block text-[10px]">{pct.toFixed(2)}%{p.mfe_r > 0 && <span data-testid={`position-mfe-${p.id}`} className="ml-1 text-slate-500" title="Máximo recorrido a favor (en R)">· máx +{p.mfe_r.toFixed(1)}R</span>}</span>
                </TD>
                <TD right>
                  <button data-testid={`close-position-${p.id}`} onClick={() => onClose(p.id)}
                    className="inline-flex items-center gap-1 rounded-sm border border-rose-500/40 px-2 py-1 font-mono text-[10px] uppercase text-rose-300 transition-colors hover:bg-rose-500/15">
                    <X className="h-3 w-3" /> Cerrar
                  </button>
                </TD>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export { TH, TD };
