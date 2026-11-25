import { fmtSignedUsd, pnlClass, REASON_LABELS, noTp } from "@/lib/api";
import { TH, TD } from "../PositionsTable";

const Card = ({ label, value, hint, cls = "text-slate-100", testId }) => (
  <div data-testid={testId} className="min-w-[140px] flex-1 rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2.5" title={hint}>
    <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
    <p className={`num mt-1 text-base font-semibold ${cls}`}>{value}</p>
    {hint && <p className="mt-0.5 text-[10px] leading-snug text-slate-500">{hint}</p>}
  </div>
);

export default function ExcursionStats({ data }) {
  if (!data) return null;
  const tp = !noTp();
  return (
    <div data-testid="excursion-stats" className="space-y-3">
      <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Recorrido de las operaciones · lo que hizo el precio antes de cerrar</p>
      {!data.tracked ? (
        <p data-testid="excursion-empty" className="text-[12px] text-slate-500">
          Aún no hay operaciones cerradas con recorrido registrado. Desde ahora cada operación guarda hasta dónde llegó a favor (MFE) y en contra (MAE); la IA lo usará para ajustar breakeven, trailing{tp ? ", cierre parcial y TP" : " y cierre parcial"}.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <Card label="Con recorrido" value={data.tracked} testId="excursion-tracked" />
            {tp && <Card label="Rozaron el TP sin tocarlo" value={`${data.near_miss} (${data.near_miss_pct}%)`} hint="Cerradas por trailing/SL tras llegar a ≥70 % del camino al TP" cls={data.near_miss_pct >= 30 ? "text-amber-300" : "text-slate-100"} testId="excursion-near-miss" />}
            <Card label="Máx. a favor medio" value={`+${data.avg_mfe_r} R`} hint="1 R = distancia al stop inicial" cls="text-emerald-400" testId="excursion-mfe" />
            <Card label="Máx. en contra medio" value={`${data.avg_mae_r} R`} cls="text-rose-400" testId="excursion-mae" />
            <Card label="Con cierre parcial" value={data.partial.trades} hint={data.partial.trades ? `${fmtSignedUsd(data.partial.pnl)} · acierto ${data.partial.win_rate}%` : "Ninguna todavía"} testId="excursion-partial" />
          </div>
          <div className="overflow-hidden rounded-sm border border-[#1E293B]">
            <table className="w-full text-sm">
              <thead><tr><TH>Salida</TH><TH right>Ops</TH><TH right>Máx. a favor</TH>{tp && <TH right>% del TP</TH>}<TH right>Máx. en contra</TH>{tp && <TH right>Rozaron TP</TH>}<TH right>Duración</TH><TH right>PnL</TH></tr></thead>
              <tbody className="num text-[12px]">
                {data.by_reason.map((r) => (
                  <tr key={r.key} data-testid={`excursion-row-${r.key}`} className="border-t border-[#1E293B]/60 transition-colors hover:bg-[#111827]">
                    <TD className="font-display text-[13px] font-semibold text-slate-100">{REASON_LABELS[r.key] || r.key}</TD>
                    <TD right>{r.trades}</TD>
                    <TD right className="text-emerald-400">+{r.avg_mfe_r} R</TD>
                    {tp && <TD right>{r.avg_mfe_tp_pct}%</TD>}
                    <TD right className="text-rose-400">{r.avg_mae_r} R</TD>
                    {tp && <TD right className={r.near_miss ? "text-amber-300" : ""}>{r.near_miss}</TD>}
                    <TD right className="text-slate-400">{r.avg_hold_min >= 120 ? `${(r.avg_hold_min / 60).toFixed(1)} h` : `${r.avg_hold_min} min`}</TD>
                    <TD right className={`font-semibold ${pnlClass(r.pnl)}`}>{fmtSignedUsd(r.pnl)}</TD>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
