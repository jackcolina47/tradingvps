import { X } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { REASON_LABELS, STRATEGY_LABELS } from "@/lib/api";

export const EMPTY_FILTERS = { symbol: "all", strategy: "all", reason: "all", from: "", to: "", q: "" };
const dayKey = (iso) => {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export function applyFilters(trades, f) {
  const q = f.q.trim().toLowerCase();
  return trades.filter((t) => {
    if (f.symbol !== "all" && t.symbol !== f.symbol) return false;
    if (f.strategy !== "all" && t.strategy !== f.strategy) return false;
    if (f.reason !== "all" && t.reason !== f.reason) return false;
    const day = dayKey(t.closed_at);
    if (f.from && day < f.from) return false;
    if (f.to && day > f.to) return false;
    if (q && !`${t.note || ""} ${(t.tags || []).join(" ")}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

const Pick = ({ testId, value, onChange, label, options }) => (
  <Select value={value} onValueChange={onChange}>
    <SelectTrigger data-testid={testId} className="h-7 w-auto min-w-[120px] gap-1 border-[#1E293B] bg-[#07090E] px-2 font-mono text-[10px] uppercase tracking-wider text-slate-300">
      <SelectValue />
    </SelectTrigger>
    <SelectContent className="border-[#1E293B] bg-[#0D111A]">
      <SelectItem value="all" className="font-mono text-[11px]">{label}: todos</SelectItem>
      {options.map(([v, l]) => <SelectItem key={v} value={v} className="font-mono text-[11px]">{l}</SelectItem>)}
    </SelectContent>
  </Select>
);

const DATE_CLS = "h-7 rounded-sm border border-[#1E293B] bg-[#07090E] px-1.5 font-mono text-[10px] text-slate-300 [color-scheme:dark] focus:border-amber-500/50 focus:outline-none";

export default function TradeFilters({ trades, filters, onChange, shown }) {
  const uniq = (k) => [...new Set(trades.map((t) => t[k]))].sort();
  const set = (k) => (v) => onChange({ ...filters, [k]: v });
  const active = Object.keys(EMPTY_FILTERS).some((k) => filters[k] !== EMPTY_FILTERS[k]);
  return (
    <div data-testid="trade-filters" className="flex flex-wrap items-center gap-1.5 border-b border-[#1E293B] px-3 py-2">
      <Pick testId="trade-filter-symbol" value={filters.symbol} onChange={set("symbol")} label="Símbolo" options={uniq("symbol").map((s) => [s, s])} />
      <Pick testId="trade-filter-strategy" value={filters.strategy} onChange={set("strategy")} label="Estrategia" options={uniq("strategy").map((s) => [s, STRATEGY_LABELS[s] || s])} />
      <Pick testId="trade-filter-reason" value={filters.reason} onChange={set("reason")} label="Motivo" options={uniq("reason").map((r) => [r, REASON_LABELS[r] || r])} />
      <label className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
        Desde <input data-testid="trade-filter-from" type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set("from")(e.target.value)} className={DATE_CLS} />
      </label>
      <label className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
        Hasta <input data-testid="trade-filter-to" type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set("to")(e.target.value)} className={DATE_CLS} />
      </label>
      <input data-testid="trade-filter-q" value={filters.q} onChange={(e) => set("q")(e.target.value)} placeholder="Buscar en notas…"
        className="h-7 w-32 rounded-sm border border-[#1E293B] bg-[#07090E] px-2 font-mono text-[10px] text-slate-300 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none" />
      <span data-testid="trade-filter-count" className="ml-auto font-mono text-[10px] uppercase tracking-wider text-slate-500">{shown} de {trades.length}</span>
      {active && (
        <button type="button" data-testid="trade-filter-clear" onClick={() => onChange(EMPTY_FILTERS)}
          className="inline-flex items-center gap-1 rounded-sm border border-slate-600/60 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-300 hover:bg-[#111827]">
          <X className="h-3 w-3" /> Limpiar
        </button>
      )}
    </div>
  );
}
