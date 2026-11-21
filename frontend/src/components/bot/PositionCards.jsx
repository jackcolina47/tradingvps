import { useState } from "react";
import { X, LineChart } from "lucide-react";
import { SideBadge, Empty } from "./Panel";
import PositionChartDialog from "./PositionChartDialog";
import { fmtPrice, fmtSignedUsd, fmtUsd, fmtQty, pnlClass, STRATEGY_LABELS, fmtPips, fmtDateTime } from "@/lib/api";

const BTN = "inline-flex items-center gap-1 rounded-sm border px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider transition-colors";

const Field = ({ label, children, testId, className = "text-slate-200" }) => (
  <div className="min-w-0">
    <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-slate-500">{label}</p>
    <p data-testid={testId} className={`num break-words text-[12px] ${className}`}>{children}</p>
  </div>
);

function PositionCard({ p, onClose, onChart }) {
  const pct = p.margin ? (p.unrealized_pnl / p.margin) * 100 : 0;
  const cross = p.exit_mode === "cross";
  return (
    <article data-testid={`position-card-${p.id}`} className={`rounded-md border border-[#1E293B] bg-[#0B0F17] p-3 transition-colors hover:border-[#334155] ${p.live ? "border-rose-500/40" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-display text-[15px] font-semibold text-slate-100">
            {p.symbol} <SideBadge side={p.side} />
            {p.live && <span className="rounded-sm border border-rose-500/50 px-1 font-mono text-[9px] uppercase text-rose-300">Real</span>}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-500">{STRATEGY_LABELS[p.strategy] || p.strategy} · 1:{p.leverage}</p>
        </div>
        <div className={`text-right ${pnlClass(p.unrealized_pnl)}`}>
          <p data-testid={`position-pnl-${p.id}`} className="num text-[16px] font-semibold leading-tight">{fmtSignedUsd(p.unrealized_pnl)}</p>
          <p className="num text-[11px]">{pct.toFixed(2)}%{p.pips != null && ` · ${fmtPips(p.pips)}`}</p>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 min-[420px]:grid-cols-3">
        <Field label="Apertura" testId={`position-opened-${p.id}`}>{fmtDateTime(p.opened_at)}</Field>
        <Field label="Entrada">{fmtPrice(p.entry_price)}</Field>
        <Field label="Precio actual" className="text-slate-50">{fmtPrice(p.mark_price)}</Field>
        <Field label={cross ? "SL de seguridad" : "SL"} className="text-rose-400">{fmtPrice(p.sl)}{p.breakeven && <span className="ml-1 text-[9px] text-amber-400">BE</span>}</Field>
        <Field label="Salida" className="text-emerald-400">{cross ? "Cruce contrario" : fmtPrice(p.tp)}</Field>
        <Field label="Tamaño (lotes)">{fmtQty(p.qty)}{p.partial_done && p.partial_gross ? <span className="ml-1 rounded-sm bg-emerald-500/20 px-1 text-[9px] text-emerald-300" title={`Cierre parcial ejecutado · ${fmtSignedUsd(p.partial_gross - (p.partial_fees || 0))} realizados`}>½</span> : null}</Field>
        <Field label="Margen">{fmtUsd(p.margin)}</Field>
        <Field label="Máx. a favor">{p.mfe_r > 0 ? `+${p.mfe_r.toFixed(1)}R` : "—"}</Field>
        <Field label="Máx. en contra">{p.mae_r > 0 ? `-${p.mae_r.toFixed(1)}R` : "—"}</Field>
      </div>
      <div className="mt-3 flex flex-wrap justify-end gap-2 border-t border-[#1E293B] pt-2.5">
        <button data-testid={`position-chart-btn-${p.id}`} onClick={() => onChart(p)} className={`${BTN} border-sky-500/40 text-sky-300 hover:bg-sky-500/10`}>
          <LineChart className="h-3 w-3" /> Gráfico
        </button>
        <button data-testid={`close-position-${p.id}`} onClick={() => onClose(p.id)} className={`${BTN} border-rose-500/40 text-rose-300 hover:bg-rose-500/15`}>
          <X className="h-3 w-3" /> Cerrar
        </button>
      </div>
    </article>
  );
}

export default function PositionCards({ positions, onClose, onCloseAll }) {
  const [chart, setChart] = useState(null);
  if (!positions.length) return <Empty testId="positions-empty" text="Sin posiciones abiertas. El bot abrirá una operación en cuanto detecte un cruce de EMAs válido." />;
  return (
    <div data-testid="positions-cards" className="p-3">
      {onCloseAll && positions.length > 1 && (
        <div className="mb-3 flex justify-end">
          <button data-testid="close-all-positions-button" onClick={() => window.confirm(`¿Cerrar las ${positions.length} posiciones a mercado?`) && onCloseAll()} className={`${BTN} border-rose-500/40 text-rose-300 hover:bg-rose-500/10`}>
            Cerrar todas ({positions.length})
          </button>
        </div>
      )}
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr))]">
        {positions.map((p) => <PositionCard key={p.id} p={p} onClose={onClose} onChart={setChart} />)}
      </div>
      <PositionChartDialog position={chart} onClose={() => setChart(null)} />
    </div>
  );
}
