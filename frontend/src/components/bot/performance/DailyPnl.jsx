import { useMemo, useState } from "react";
import { Bar, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtSignedUsd, pnlClass } from "@/lib/api";
import { RANGES, GROUPS, filterRange, groupBy, fmtBucket, bucketTitle, unitLabel } from "./dailyPnlUtils";

function Tip({ active, payload, mode }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div data-testid="daily-pnl-tooltip" className="rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2 font-mono text-[11px] text-slate-300">
      <p className="text-slate-400">{bucketTitle(d.day, mode)}</p>
      <p className={pnlClass(d.pnl)}>PnL: {fmtSignedUsd(d.pnl)}</p>
      <p>Operaciones: {d.trades} · Ganadoras: {d.wins}{mode !== "day" ? ` · Días operados: ${d.days}` : ""}</p>
      <p className={pnlClass(d.cum)}>Acumulado: {fmtSignedUsd(d.cum)}</p>
    </div>
  );
}

function Seg({ items, value, onChange, testId }) {
  return (
    <div data-testid={testId} className="flex overflow-hidden rounded-sm border border-[#1E293B]">
      {items.map((it) => (
        <button key={it.key} type="button" data-testid={`${testId}-${it.key}`} onClick={() => onChange(it.key)} aria-pressed={value === it.key}
          className={`px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${value === it.key ? "bg-[#F7A600] text-black" : "bg-[#0D111A] text-slate-400 hover:text-slate-100"}`}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export default function DailyPnl({ data }) {
  const [range, setRange] = useState(30);
  const [mode, setMode] = useState("day");
  const rows = useMemo(() => groupBy(filterRange(data || [], range), mode), [data, range, mode]);
  if (!data?.length) return null;
  const total = rows.length ? rows[rows.length - 1].cum : 0;
  const best = rows.length ? rows.reduce((a, b) => (b.pnl > a.pnl ? b : a)) : null;
  const worst = rows.length ? rows.reduce((a, b) => (b.pnl < a.pnl ? b : a)) : null;
  const green = rows.filter((d) => d.pnl > 0).length;
  const unit = unitLabel(mode, rows.length);
  return (
    <div data-testid="daily-pnl" className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-sm font-semibold text-slate-100">Historial de PnL {mode === "day" ? "diario" : mode === "week" ? "semanal" : "mensual"}</h3>
          <p data-testid="daily-pnl-subtitle" className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
            {rows.length} {unit} con operaciones{range ? ` en los últimos ${range} días` : " · todo el historial"} · barras = PnL · línea = acumulado
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Seg items={RANGES} value={range} onChange={setRange} testId="daily-pnl-range" />
          <Seg items={GROUPS} value={mode} onChange={setMode} testId="daily-pnl-group" />
        </div>
      </div>
      {rows.length === 0 ? (
        <p data-testid="daily-pnl-empty" className="mt-4 text-sm text-slate-500">Sin operaciones cerradas en los últimos {range} días. Prueba con un rango mayor o «Todo».</p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap gap-4 font-mono text-[11px]">
            <span data-testid="daily-pnl-total" className={pnlClass(total)}>Total {fmtSignedUsd(total)}</span>
            <span className="text-slate-400">En verde <span className="text-emerald-300">{green}</span> / {rows.length}</span>
            <span className="text-slate-400">Mejor <span className="text-emerald-300">{fmtSignedUsd(best.pnl)}</span> ({fmtBucket(best.day, mode)})</span>
            <span className="text-slate-400">Peor <span className="text-rose-300">{fmtSignedUsd(worst.pnl)}</span> ({fmtBucket(worst.day, mode)})</span>
          </div>
          <div className="mt-3 h-52 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="day" tickFormatter={(d) => fmtBucket(d, mode)} stroke="#334155" tick={{ fill: "#64748B", fontSize: 10, fontFamily: "IBM Plex Mono" }} tickMargin={6} minTickGap={18} />
                <YAxis yAxisId="pnl" stroke="#334155" tick={{ fill: "#64748B", fontSize: 10, fontFamily: "IBM Plex Mono" }} width={48} tickFormatter={(v) => v.toFixed(0)} />
                <YAxis yAxisId="cum" orientation="right" stroke="#334155" tick={{ fill: "#F7A600", fontSize: 10, fontFamily: "IBM Plex Mono" }} width={48} tickFormatter={(v) => v.toFixed(0)} />
                <ReferenceLine yAxisId="pnl" y={0} stroke="#1E293B" />
                <Tooltip content={<Tip mode={mode} />} cursor={{ fill: "rgba(148,163,184,0.06)" }} />
                <Bar yAxisId="pnl" dataKey="pnl" radius={[2, 2, 0, 0]} isAnimationActive={false} maxBarSize={28}>
                  {rows.map((d) => <Cell key={d.day} fill={d.pnl >= 0 ? "#10B981" : "#F43F5E"} />)}
                </Bar>
                <Line yAxisId="cum" type="monotone" dataKey="cum" stroke="#F7A600" strokeWidth={1.5} dot={rows.length < 3} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}
