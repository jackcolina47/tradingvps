import { Bar, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtSignedUsd, pnlClass } from "@/lib/api";

const fmtDay = (d) => `${d.slice(8, 10)}/${d.slice(5, 7)}`;

function DayTip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div data-testid="daily-pnl-tooltip" className="rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2 font-mono text-[11px] text-slate-300">
      <p className="text-slate-400">{d.day}</p>
      <p className={pnlClass(d.pnl)}>PnL del día: {fmtSignedUsd(d.pnl)}</p>
      <p>Operaciones: {d.trades} · Ganadoras: {d.wins}</p>
      <p className={pnlClass(d.cum)}>Acumulado: {fmtSignedUsd(d.cum)}</p>
    </div>
  );
}

export default function DailyPnl({ data }) {
  if (!data?.length) return null;
  const total = data[data.length - 1].cum;
  const best = data.reduce((a, b) => (b.pnl > a.pnl ? b : a));
  const worst = data.reduce((a, b) => (b.pnl < a.pnl ? b : a));
  const green = data.filter((d) => d.pnl > 0).length;
  return (
    <div data-testid="daily-pnl" className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="font-display text-sm font-semibold text-slate-100">Historial de PnL diario</h3>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">Últimos {data.length} días con operaciones · barras = PnL del día · línea = acumulado</p>
        </div>
        <div className="flex flex-wrap gap-4 font-mono text-[11px]">
          <span data-testid="daily-pnl-total" className={pnlClass(total)}>Total {fmtSignedUsd(total)}</span>
          <span className="text-slate-400">Días en verde <span className="text-emerald-300">{green}</span> / {data.length}</span>
          <span className="text-slate-400">Mejor <span className="text-emerald-300">{fmtSignedUsd(best.pnl)}</span> ({fmtDay(best.day)})</span>
          <span className="text-slate-400">Peor <span className="text-rose-300">{fmtSignedUsd(worst.pnl)}</span> ({fmtDay(worst.day)})</span>
        </div>
      </div>
      <div className="mt-3 h-52 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <XAxis dataKey="day" tickFormatter={fmtDay} stroke="#334155" tick={{ fill: "#64748B", fontSize: 10, fontFamily: "IBM Plex Mono" }} tickMargin={6} minTickGap={18} />
            <YAxis yAxisId="pnl" stroke="#334155" tick={{ fill: "#64748B", fontSize: 10, fontFamily: "IBM Plex Mono" }} width={48} tickFormatter={(v) => v.toFixed(0)} />
            <YAxis yAxisId="cum" orientation="right" stroke="#334155" tick={{ fill: "#F7A600", fontSize: 10, fontFamily: "IBM Plex Mono" }} width={48} tickFormatter={(v) => v.toFixed(0)} />
            <ReferenceLine yAxisId="pnl" y={0} stroke="#1E293B" />
            <Tooltip content={<DayTip />} cursor={{ fill: "rgba(148,163,184,0.06)" }} />
            <Bar yAxisId="pnl" dataKey="pnl" radius={[2, 2, 0, 0]} isAnimationActive={false} maxBarSize={28}>
              {data.map((d) => <Cell key={d.day} fill={d.pnl >= 0 ? "#10B981" : "#F43F5E"} />)}
            </Bar>
            <Line yAxisId="cum" type="monotone" dataKey="cum" stroke="#F7A600" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
