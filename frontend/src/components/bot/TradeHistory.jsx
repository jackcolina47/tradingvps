import { useState } from "react";
import { Search } from "lucide-react";
import { SideBadge, Empty } from "./Panel";
import { TH, TD } from "./PositionsTable";
import TradeDetailDialog from "./TradeDetailDialog";
import { fmtPrice, fmtSignedUsd, fmtDateTime, pnlClass, REASON_LABELS, STRATEGY_LABELS } from "@/lib/api";

const REASON_CLS = {
  TP: "text-emerald-400 border-emerald-500/30",
  SL: "text-rose-400 border-rose-500/30",
  TRAILING: "text-amber-400 border-amber-500/30",
  MANUAL: "text-slate-300 border-slate-500/30",
};

export default function TradeHistory({ trades }) {
  const [selected, setSelected] = useState(null);
  if (!trades.length) return <Empty testId="trades-empty" text="Aún no hay operaciones cerradas." />;
  return (
    <div className="max-h-[420px] overflow-auto thin-scroll">
      <table data-testid="trades-table" className="w-full text-sm">
        <thead className="sticky top-0 border-b border-[#1E293B] bg-[#0D111A]">
          <tr><TH>Cierre</TH><TH>Símbolo</TH><TH>Lado</TH><TH right>Entrada</TH><TH right>Salida</TH><TH>Motivo</TH><TH right>Comisiones</TH><TH right>PnL</TH><TH right>Detalle</TH></tr>
        </thead>
        <tbody className="num text-[12px]">
          {trades.map((t) => (
            <tr key={t.id} data-testid={`trade-row-${t.id}`} className="border-b border-[#1E293B]/60 transition-colors hover:bg-[#111827]">
              <TD className="text-slate-400">
                {fmtDateTime(t.closed_at)}
                {t.period && <span data-testid={`trade-period-${t.id}`} title={t.period} className="mt-0.5 block w-fit rounded-[2px] border border-slate-600/60 px-1 font-mono text-[9px] uppercase tracking-wider text-slate-500">periodo anterior</span>}
              </TD>
              <TD>
                <p className="font-display text-[13px] font-semibold text-slate-100">{t.symbol}</p>
                <p className="text-[10px] text-slate-500">{STRATEGY_LABELS[t.strategy]}</p>
              </TD>
              <TD><SideBadge side={t.side} /></TD>
              <TD right>{fmtPrice(t.entry_price)}</TD>
              <TD right>{fmtPrice(t.exit_price)}</TD>
              <TD><span className={`rounded-sm border px-1.5 py-0.5 text-[10px] uppercase ${REASON_CLS[t.reason]}`}>{REASON_LABELS[t.reason]}</span>
                {t.mfe_r != null && t.mfe_tp_pct != null && (
                  <span data-testid={`trade-mfe-${t.id}`} className={`mt-0.5 block text-[10px] ${t.reason !== "TP" && t.mfe_tp_pct >= 70 ? "text-amber-300" : "text-slate-500"}`}
                    title={`Máximo a favor +${t.mfe_r} R (${t.mfe_tp_pct} % del camino al TP) · máximo en contra ${t.mae_r} R${t.partial_tp ? " · con cierre parcial" : ""}`}>
                    máx +{t.mfe_r}R · {Math.round(t.mfe_tp_pct)}% TP{t.partial_tp && <span className="ml-1 rounded-sm bg-emerald-500/20 px-1 text-[9px] text-emerald-300">½</span>}
                  </span>
                )}
              </TD>
              <TD right className="text-slate-500">{t.fees.toFixed(2)}</TD>
              <TD right className={pnlClass(t.pnl)}>
                {fmtSignedUsd(t.pnl)}
                <span className="block text-[10px]">{t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%</span>
              </TD>
              <TD right>
                <button data-testid={`trade-detail-btn-${t.id}`} onClick={() => setSelected(t)} title="Ver todos los detalles"
                  className="inline-flex items-center gap-1 rounded-sm border border-amber-500/30 px-1.5 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-300 transition-colors hover:bg-amber-500/10">
                  <Search className="h-3 w-3" strokeWidth={2.2} /> Ver
                </button>
              </TD>
            </tr>
          ))}
        </tbody>
      </table>
      <TradeDetailDialog trade={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
