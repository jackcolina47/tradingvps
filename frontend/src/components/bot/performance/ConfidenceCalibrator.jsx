import { Gauge } from "lucide-react";
import { TH, TD } from "../PositionsTable";
import { fmtSignedUsd, pnlClass } from "@/lib/api";

function verdict(data, current) {
  if (data.status === "insufficient")
    return { value: "Faltan datos", cls: "text-slate-300", text: `Hay ${data.sample} de ${data.min_sample} operaciones cerradas con confianza IA. Deja que se acumulen más antes de mover el umbral.` };
  if (data.status === "no_edge")
    return { value: "Sin recomendación", cls: "text-rose-300", text: "Ningún umbral habría sido rentable con los datos actuales. Mantén el riesgo bajo y espera más operaciones." };
  if (data.recommended === current)
    return { value: `${current} %`, cls: "text-emerald-400", text: "Tu umbral actual ya es el que mejor resultado habría dado. No hace falta cambiarlo." };
  return { value: `${data.recommended} %`, cls: "text-amber-300",
    text: `${data.recommended > current ? "Subir" : "Bajar"} el umbral de ${current} % a ${data.recommended} % habría dado el mejor resultado. Cámbialo en Configuración → «Confianza IA para abrir operación».` };
}

export default function ConfidenceCalibrator({ data, current }) {
  if (!data) return null;
  const v = verdict(data, current);
  return (
    <div data-testid="confidence-calibrator" className="space-y-3 rounded-md border border-amber-500/25 bg-amber-500/[0.04] p-3.5">
      <div>
        <p className="flex items-center gap-1.5 font-display text-[14px] font-bold text-amber-200"><Gauge className="h-4 w-4" /> Calibrador de confianza IA</p>
        <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
          Qué habría pasado con cada umbral: junta las operaciones ejecutadas por el embudo y las sombras de baja confianza, y suma solo las que superan cada valor.
          Es una estimación con datos pasados, no una garantía.
        </p>
      </div>
      <div className="flex flex-wrap items-stretch gap-2">
        <div className="min-w-[150px] rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2.5">
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">Umbral recomendado</p>
          <p data-testid="calibrator-recommended" className={`num mt-1 text-xl font-bold ${v.cls}`}>{v.value}</p>
          <p data-testid="calibrator-current" className="text-[10px] text-slate-500">Actual: {current} %</p>
        </div>
        <div className="min-w-[220px] flex-1 rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2.5">
          <p data-testid="calibrator-verdict" className="text-[12px] leading-relaxed text-slate-300">{v.text}</p>
          <p data-testid="calibrator-sample" className="mt-1 text-[10px] text-slate-500">Muestra: {data.sample} operaciones ({data.real} reales · {data.shadow} sombra). Un umbral necesita al menos {data.min_trades} operaciones para ser recomendado.</p>
        </div>
      </div>
      {data.sample > 0 && (
        <div className="thin-scroll overflow-x-auto rounded-sm border border-[#1E293B]">
          <table data-testid="calibrator-table" className="w-full text-sm">
            <thead><tr><TH>Umbral</TH><TH right>Ops que entrarían</TH><TH right>Acierto</TH><TH right>PF</TH><TH right>PnL</TH></tr></thead>
            <tbody className="num text-[12px]">
              {data.levels.map((l) => (
                <tr key={l.threshold} data-testid={`calibrator-row-${l.threshold}`} className={`border-t border-[#1E293B]/60 ${l.threshold === data.recommended ? "bg-amber-500/[0.08]" : ""}`}>
                  <TD className="font-display text-[13px] font-semibold text-slate-100">
                    &gt; {l.threshold} %
                    {l.threshold === current && <span className="ml-2 rounded-sm border border-slate-600 px-1 py-px font-mono text-[9px] uppercase text-slate-400">actual</span>}
                    {l.threshold === data.recommended && <span className="ml-2 rounded-sm border border-amber-500/50 px-1 py-px font-mono text-[9px] uppercase text-amber-300">recomendado</span>}
                  </TD>
                  <TD right>{l.trades}</TD>
                  <TD right>{l.trades ? `${l.win_rate}%` : "—"}</TD>
                  <TD right>{l.profit_factor ?? "—"}</TD>
                  <TD right className={`font-semibold ${pnlClass(l.pnl)}`}>{l.trades ? fmtSignedUsd(l.pnl) : "—"}</TD>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
