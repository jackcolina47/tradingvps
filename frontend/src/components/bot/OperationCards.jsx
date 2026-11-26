import { X, LineChart, Search, Sparkles, SlidersHorizontal } from "lucide-react";
import { SideBadge } from "./Panel";
import { fmtPrice, fmtSignedUsd, fmtUsd, fmtQty, fmtDateTime, pnlClass, STRATEGY_LABELS, REASON_LABELS, fmtPips } from "@/lib/api";

const Cell = ({ label, children, cls = "text-slate-200" }) => (
  <div className="min-w-0">
    <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-slate-500">{label}</p>
    <p className={`num truncate text-[12px] ${cls}`}>{children}</p>
  </div>
);
const BTN = "inline-flex flex-1 items-center justify-center gap-1 rounded-sm border px-2 py-2 font-mono text-[10px] uppercase tracking-wider transition-colors";

export function PositionCard({ p, onClose, onChart, onRules }) {
  const pct = p.margin ? (p.unrealized_pnl / p.margin) * 100 : 0;
  return (
    <article data-testid={`position-row-${p.id}`} className={`border-b border-[#1E293B]/60 p-3 ${p.live ? "bg-rose-500/[0.03]" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-display text-sm font-semibold text-slate-100">{p.symbol} <SideBadge side={p.side} /></p>
          <p className="truncate text-[10px] text-slate-500">{STRATEGY_LABELS[p.strategy]} · {p.leverage}x{p.breakeven ? " · BE" : ""}{p.partial_done ? " · parcial ½" : ""}{p.ai_review ? ` · IA ${p.ai_review.action}` : ""}</p>
        </div>
        <div className={`text-right ${pnlClass(p.unrealized_pnl)}`}>
          <p data-testid={`position-pnl-${p.id}`} className="num text-sm font-semibold">{fmtSignedUsd(p.unrealized_pnl)}</p>
          <p className="num text-[10px]">{pct.toFixed(2)}%{p.pips != null ? ` · ${fmtPips(p.pips)}` : ""}</p>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-x-3 gap-y-2">
        <Cell label="Entrada">{fmtPrice(p.entry_price)}</Cell>
        <Cell label="Marca" cls="text-slate-100">{fmtPrice(p.mark_price)}</Cell>
        <Cell label="Tamaño">{fmtQty(p.qty)}</Cell>
        <Cell label="SL" cls="text-rose-400">{fmtPrice(p.sl)}</Cell>
        <Cell label="TP" cls="text-emerald-400">{p.exit_mode === "cross" ? "— cruce" : fmtPrice(p.tp)}</Cell>
        <Cell label="Margen">{fmtUsd(p.margin)}</Cell>
      </div>
      <div className="mt-3 flex gap-2">
        <button data-testid={`position-chart-${p.id}`} onClick={() => onChart(p)} className={`${BTN} border-amber-500/40 text-amber-300 hover:bg-amber-500/10`}><LineChart className="h-3.5 w-3.5" /> Gráfico</button>
        <button data-testid={`position-rules-${p.id}`} onClick={() => onRules(p)} className={`${BTN} ${p.rules || p.ai_review_minutes ? "border-sky-400/60 text-sky-300" : "border-[#1E293B] text-slate-300"}`}><SlidersHorizontal className="h-3.5 w-3.5" /> Gestión</button>
        <button data-testid={`close-position-${p.id}`} onClick={() => onClose(p.id)} className={`${BTN} border-rose-500/40 text-rose-300 hover:bg-rose-500/15`}><X className="h-3.5 w-3.5" /> Cerrar</button>
      </div>
    </article>
  );
}

export function TradeCard({ t, onOpen }) {
  return (
    <article data-testid={`trade-row-${t.id}`} className="border-b border-[#1E293B]/60 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-display text-sm font-semibold text-slate-100">{t.symbol} <SideBadge side={t.side} /></p>
          <p className="truncate text-[10px] text-slate-500">{fmtDateTime(t.closed_at)} · {STRATEGY_LABELS[t.strategy]}</p>
        </div>
        <div className={`text-right ${pnlClass(t.pnl)}`}>
          <p className="num text-sm font-semibold">{fmtSignedUsd(t.pnl)}</p>
          <p className="num text-[10px]">{t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%{t.pips != null ? ` · ${fmtPips(t.pips)}` : ""}</p>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-x-3 gap-y-2">
        <Cell label="Entrada">{fmtPrice(t.entry_price)}</Cell>
        <Cell label="Salida">{fmtPrice(t.exit_price)}</Cell>
        <Cell label="Motivo">{REASON_LABELS[t.reason]}</Cell>
        <Cell label="Comisiones" cls="text-slate-400">{t.fees.toFixed(2)}</Cell>
        {t.mfe_r != null && <Cell label="Máx. a favor" cls="text-slate-400">+{t.mfe_r}R</Cell>}
      </div>
      <div className="mt-3 flex gap-2">
        <button data-testid={`trade-analyze-btn-${t.id}`} onClick={() => onOpen(t, true)} className={`${BTN} ${t.ai_review ? "border-emerald-500/40 text-emerald-300" : "border-violet-400/40 text-violet-300"}`}><Sparkles className="h-3.5 w-3.5" /> {t.ai_review ? "IA ✓" : "Analizar IA"}</button>
        <button data-testid={`trade-detail-btn-${t.id}`} onClick={() => onOpen(t, false)} className={`${BTN} border-amber-500/30 text-amber-300 hover:bg-amber-500/10`}><Search className="h-3.5 w-3.5" /> Ver</button>
      </div>
    </article>
  );
}
