import { useEffect, useRef, useState } from "react";
import { Activity } from "lucide-react";
import { Panel } from "./Panel";
import { fmtPrice, fmtPct, pnlClass } from "@/lib/api";

const TREND_CLS = {
  ALCISTA: "text-emerald-400 bg-emerald-500/10 border-emerald-500/25",
  BAJISTA: "text-rose-400 bg-rose-500/10 border-rose-500/25",
  NEUTRAL: "text-slate-400 bg-slate-500/10 border-slate-500/25",
};

const FUNNEL_CLS = { EJECUTADA: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", SOMBRA: "border-purple-500/40 bg-purple-500/10 text-purple-300",
  NOTICIAS: "border-rose-500/40 bg-rose-500/10 text-rose-300", NO_EJECUTADA: "border-orange-500/40 bg-orange-500/10 text-orange-300" };
const FUNNEL_LABEL = { DESCARTADO: "descartado", PASS: "pass", SOMBRA: "sombra", EJECUTADA: "abierta", NOTICIAS: "noticia", NO_EJECUTADA: "no ejecutada" };
const FUNDING_CLS = { EXTREMO: "bg-rose-500/15 text-rose-300", ALTO: "bg-amber-500/15 text-amber-300" };
const pct = (v) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(3)}%`;

function FundingChip({ w }) {
  const h = w.funding_interval_h || 8;
  const tiny = Math.abs(w.funding_rate) < 0.000005;
  const pays = tiny ? "neutro" : w.funding_rate > 0 ? "largos pagan" : "cortos pagan";
  const title = `Funding ${w.funding_level === "NORMAL" ? "normal" : w.funding_level} · cada ${h} h · ${pays}`
    + (h !== 8 && !tiny ? ` · ≈ ${pct((w.funding_rate * 8) / h)} por 8 h` : "");
  return (
    <span data-testid={`watchlist-funding-${w.symbol}`} title={title}
      className={`num rounded-sm px-1 text-[10px] ${FUNDING_CLS[w.funding_level] || "text-slate-500"}`}>
      {tiny ? "F ≈0%" : `F ${pct(w.funding_rate)}`}
      {h !== 8 && <span data-testid={`watchlist-funding-interval-${w.symbol}`} className="ml-px opacity-70">/{h}h</span>}
    </span>
  );
}

const splitSymbol = (s) => (s.endsWith("USDT") ? [s.slice(0, -4), "USDT"] : [s.slice(0, 3), s.slice(3)]);

function Row({ w, active, onSelect }) {
  const [base, quote] = splitSymbol(w.symbol);
  const prev = useRef(w.price);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    if (prev.current != null && w.price != null && w.price !== prev.current) {
      setFlash(w.price > prev.current ? "flash-up" : "flash-down");
      const t = setTimeout(() => setFlash(""), 700);
      prev.current = w.price;
      return () => clearTimeout(t);
    }
    prev.current = w.price;
  }, [w.price]);

  return (
    <button
      data-testid={`watchlist-row-${w.symbol}`}
      onClick={() => onSelect(w.symbol)}
      className={`group w-full border-b border-[#1E293B]/70 px-4 py-3 text-left transition-colors duration-150 ${flash} ${
        active ? "bg-amber-500/[0.06] shadow-[inset_2px_0_0_#F7A600]" : "hover:bg-[#111827]"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-display text-sm font-semibold text-slate-100">
          {base}<span className="text-slate-500">/{quote}</span>
        </span>
        <span data-testid={`watchlist-price-${w.symbol}`} className="num text-sm text-slate-100">{fmtPrice(w.price)}</span>
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          {w.trend && (
            <span className={`rounded-sm border px-1.5 py-px font-mono text-[9px] font-semibold uppercase tracking-wider ${TREND_CLS[w.trend]}`}>
              {w.trend}
            </span>
          )}
          {w.rsi != null && <span className="num text-[10px] text-slate-500">RSI {w.rsi}</span>}
          {w.funding_rate != null && <FundingChip w={w} />}
          {w.funnel && (
            <span data-testid={`watchlist-funnel-${w.symbol}`} title={`Embudo IA · etapa ${w.funnel.stage} · ${w.funnel.text}`}
              className={`rounded-sm border px-1.5 py-px font-mono text-[9px] font-semibold uppercase ${FUNNEL_CLS[w.funnel.state] || "border-[#1E293B] text-slate-500"}`}>
              E{w.funnel.stage} {FUNNEL_LABEL[w.funnel.state] || w.funnel.state}{w.funnel.confidence != null ? ` ${w.funnel.confidence}%` : ""}
            </span>
          )}
        </div>
        <span className={`num text-[11px] ${pnlClass(w.change_pct)}`}>{fmtPct(w.change_pct)}</span>
      </div>
    </button>
  );
}

export default function Watchlist({ items, selected, onSelect }) {
  return (
    <Panel testId="watchlist-panel" title="Mercado en vivo" icon={Activity} className="lg:col-span-3" bodyClass="overflow-y-auto thin-scroll max-h-[540px]" delay={60}
      actions={<span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{items.length} pares</span>}>
      {items.map((w) => <Row key={w.symbol} w={w} active={w.symbol === selected} onSelect={onSelect} />)}
    </Panel>
  );
}
