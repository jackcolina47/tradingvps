import { useEffect, useState } from "react";
import { Loader2, Power, Trophy } from "lucide-react";
import { toast } from "sonner";
import { http, fmtSignedUsd, pnlClass } from "@/lib/api";
import MaintenanceStatus from "./MaintenanceStatus";

const RANGES = [7, 30, 90];
const short = (s) => (s.endsWith("USDT") ? s.slice(0, -4) : s);

function Bar({ pnl, max }) {
  const w = max ? Math.min(100, Math.abs(pnl) / max * 100) : 0;
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-[#111827]">
      <div className={`absolute inset-y-0 ${pnl >= 0 ? "left-1/2 bg-emerald-500/70" : "right-1/2 bg-rose-500/70"}`} style={{ width: `${w / 2}%` }} />
      <div className="absolute inset-y-0 left-1/2 w-px bg-slate-600" />
    </div>
  );
}

export default function PairRanking({ settings, onSave, tradeCount, refresh }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  useEffect(() => {
    http.get(`/performance/pairs?days=${days}`).then((r) => setData(r.data)).catch(() => toast.error("No se pudo cargar el ranking de pares"));
  }, [days, tradeCount, settings.symbols]);
  const toggle = (sym, on) => {
    const next = on ? [...settings.symbols, sym] : settings.symbols.filter((s) => s !== sym);
    if (!next.length) return toast.error("Debe quedar al menos un par activo");
    onSave({ symbols: next });
  };
  if (!data) return <div className="flex min-h-[120px] items-center justify-center text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /></div>;
  const max = Math.max(...data.rows.map((r) => Math.abs(r.pnl)), 0);
  const traded = data.rows.filter((r) => r.trades);
  const best = traded[0], worst = traded[traded.length - 1];

  return (
    <div data-testid="pair-ranking" className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Ranking de pares · últimos {data.days} días · {data.trades} operaciones cerradas</p>
          <p className="mt-1 text-[12px] text-slate-400">
            {traded.length
              ? <>Total <span data-testid="pair-ranking-total" className={`num font-semibold ${pnlClass(data.total_pnl)}`}>{fmtSignedUsd(data.total_pnl)}</span>
                  {best && best.pnl > 0 && <> · mejor <span className="text-emerald-300">{short(best.key)}</span> {fmtSignedUsd(best.pnl)}</>}
                  {worst && worst.pnl < 0 && <> · peor <span className="text-rose-300">{short(worst.key)}</span> {fmtSignedUsd(worst.pnl)}</>}
                  . Apaga desde aquí los pares que restan: el bot deja de escanearlos sin tocar las posiciones abiertas.</>
              : "Sin operaciones cerradas en este rango. Los pares activos aparecen igualmente para que puedas podar la lista."}
          </p>
        </div>
        <div className="flex gap-1">
          {RANGES.map((d) => (
            <button key={d} data-testid={`pair-ranking-range-${d}`} onClick={() => setDays(d)}
              className={`rounded-sm border px-2 py-1 font-mono text-[10px] transition-colors ${days === d ? "border-amber-500/60 bg-amber-500/15 text-amber-300" : "border-[#1E293B] text-slate-500 hover:text-slate-200"}`}>
              {d} d
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-hidden rounded-sm border border-[#1E293B]">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-[#0D111A] font-mono text-[10px] uppercase tracking-wider text-slate-500">
              <th className="px-3 py-2 text-left">#</th><th className="px-3 py-2 text-left">Par</th><th className="px-3 py-2 text-right">Ops</th>
              <th className="px-3 py-2 text-right">Acierto</th><th className="px-3 py-2 text-right">PF</th><th className="hidden px-3 py-2 sm:table-cell">PnL</th>
              <th className="px-3 py-2 text-right">PnL</th><th className="px-3 py-2 text-right">Estado</th>
            </tr>
          </thead>
          <tbody className="num text-[12px]">
            {data.rows.map((r, i) => {
              const none = !r.trades;
              return (
                <tr key={r.key} data-testid={`pair-rank-row-${r.key}`} className={`border-t border-[#1E293B]/60 transition-colors hover:bg-[#111827] ${r.active ? "" : "opacity-50"}`}>
                  <td className="px-3 py-2 text-slate-500">{i === 0 && !none && r.pnl > 0 ? <Trophy className="h-3.5 w-3.5 text-amber-400" /> : i + 1}</td>
                  <td className="px-3 py-2 font-display text-[13px] font-semibold text-slate-100">
                    {short(r.key)}<span className="text-slate-500">/USDT</span>
                    {r.open_positions > 0 && <span className="ml-1.5 rounded-sm bg-sky-500/15 px-1 font-mono text-[9px] text-sky-300" title="Posiciones abiertas ahora">{r.open_positions} abierta{r.open_positions > 1 ? "s" : ""}</span>}
                  </td>
                  <td className={`px-3 py-2 text-right ${none ? "text-slate-500" : ""}`}>{r.trades}</td>
                  <td className="px-3 py-2 text-right text-slate-300">{none ? "—" : `${r.win_rate}%`}</td>
                  <td className={`px-3 py-2 text-right ${none ? "text-slate-600" : r.profit_factor == null ? "text-slate-400" : r.profit_factor >= 1 ? "text-emerald-400" : "text-rose-400"}`}>{none ? "—" : r.profit_factor ?? "∞"}</td>
                  <td className="hidden px-3 py-2 sm:table-cell"><Bar pnl={r.pnl} max={max} /></td>
                  <td className={`px-3 py-2 text-right font-semibold ${none ? "text-slate-600" : pnlClass(r.pnl)}`}>{none ? "—" : fmtSignedUsd(r.pnl)}</td>
                  <td className="px-3 py-2 text-right">
                    <button data-testid={`pair-rank-toggle-${r.key}`} onClick={() => toggle(r.key, !r.active)} title={r.active ? "Apagar: el bot dejará de operarlo" : "Volver a activar"}
                      className={`inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors ${r.active ? "border-emerald-500/40 text-emerald-300 hover:border-rose-500/50 hover:text-rose-300" : "border-slate-600 text-slate-400 hover:text-emerald-300"}`}>
                      <Power className="h-3 w-3" /> {r.active ? "Activo" : "Apagado"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {"reopt_enabled" in settings && <MaintenanceStatus settings={settings} refresh={refresh} />}
    </div>
  );
}
