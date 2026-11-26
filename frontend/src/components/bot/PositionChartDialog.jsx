import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { SideBadge } from "./Panel";
import PriceChart from "./PriceChart";
import { toast } from "sonner";
import { http, errMsg, fmtPrice, fmtSignedUsd, pnlClass } from "@/lib/api";

const Level = ({ label, value, cls = "text-slate-100" }) => (
  <span className="whitespace-nowrap">{label} <span className={`num ${cls}`}>{value}</span></span>
);

/** Full chart of one open position (same tools as the main chart); entry, SL and TP lines come with the candles. */
export default function PositionChartDialog({ position: p, chart, onClose, onChanged }) {
  if (!p) return null;
  const saveLevels = async (body) => {
    try {
      await http.put(`/positions/${p.id}/levels`, body);
      toast.success(`SL y TP de ${p.symbol} actualizados`);
      await onChanged?.();
      return true;
    } catch (e) { toast.error(errMsg(e)); return false; }
  };
  const levels = p.exit_mode === "cross" ? undefined : { sl: p.sl, tp: p.tp, live: p.live, onSave: saveLevels };
  return createPortal(
    <div data-testid="position-chart-dialog" className="fixed inset-0 z-[60] flex flex-col bg-[#07090E]/95 p-2 backdrop-blur-sm sm:p-4" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-[#1E293B] bg-[#0D111A] px-3 py-2 font-mono text-[11px] text-slate-400">
        <span className="font-display text-sm font-semibold text-slate-100">{p.symbol}</span>
        <SideBadge side={p.side} />
        <Level label="Entrada" value={fmtPrice(p.entry_price)} />
        <Level label="Marca" value={fmtPrice(p.mark_price)} />
        <Level label="SL" value={fmtPrice(p.sl)} cls="text-rose-400" />
        {p.exit_mode !== "cross" && <Level label="TP" value={fmtPrice(p.tp)} cls="text-emerald-400" />}
        {levels && <span className="hidden text-[10px] text-amber-300/80 sm:inline">Arrastra las líneas SL / TP para moverlas</span>}
        <span data-testid="position-chart-pnl" className={`num ${pnlClass(p.unrealized_pnl)}`}>{fmtSignedUsd(p.unrealized_pnl)}</span>
        <button data-testid="position-chart-close" onClick={onClose} title="Cerrar gráfico"
          className="ml-auto inline-flex items-center gap-1 rounded-sm border border-[#1E293B] px-2 py-1 uppercase tracking-wider text-slate-300 transition-colors hover:border-amber-500/60 hover:text-amber-300">
          <X className="h-3.5 w-3.5" /> Cerrar
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto thin-scroll">
        <PriceChart tid="position-" levels={levels} symbol={p.symbol} defaultTf={chart?.tf || "15m"} tfs={chart?.tfs} kind="posición abierta" className="" heightClass="h-[calc(100dvh-190px)] min-h-[320px]" />
      </div>
    </div>,
    document.body,
  );
}
