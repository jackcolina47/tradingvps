import { useState } from "react";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { http, errMsg, fmtDateTime } from "@/lib/api";

const STATE = {
  BLOQUEANDO: { label: "Bloqueando", cls: "border-rose-500/40 bg-rose-500/10 text-rose-300" },
  PROXIMO: { label: "Próximo", cls: "border-amber-500/40 bg-amber-500/10 text-amber-300" },
  PASADO: { label: "Pasado", cls: "border-[#1E293B] text-slate-500" },
};

export default function RedCalendar({ guard, onRefresh }) {
  const [busy, setBusy] = useState(false);
  if (!guard) return null;
  const refresh = async () => {
    setBusy(true);
    try {
      await http.post("/guard/refresh");
      toast.success("Calendario actualizado");
      await onRefresh?.();
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  const cal = guard.calendar;
  return (
    <section data-testid="red-calendar" className="border-b border-[#1E293B] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 className="flex items-center gap-2 font-display text-[13px] font-semibold uppercase tracking-[0.14em] text-slate-200">
            <ShieldAlert className="h-4 w-4 text-rose-400" /> Eventos rojos de la semana
          </h4>
          <p className="mt-1 max-w-3xl text-[12px] leading-relaxed text-slate-400">
            Sin entradas nuevas en los pares afectados desde {guard.before_min} min antes hasta {guard.after_min} min después de cada evento.
            {" "}{guard.before_min} min antes, las posiciones afectadas en ganancia pasan a breakeven y las que están en pérdida se cierran.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span data-testid="red-calendar-updated" className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
            {cal.source} · {cal.fetched_at ? `actualizado ${fmtDateTime(cal.fetched_at)}` : "sin descargar"}
          </span>
          <Button data-testid="red-calendar-refresh" size="sm" variant="outline" onClick={refresh} disabled={busy}
            className="h-7 border-[#1E293B] bg-transparent font-mono text-[10px] uppercase tracking-wider text-slate-300 hover:bg-[#111827]">
            <RefreshCw className={`mr-1 h-3 w-3 ${busy ? "animate-spin" : ""}`} /> Actualizar
          </Button>
        </div>
      </div>
      {cal.error && <p data-testid="red-calendar-error" className="mt-2 rounded-sm border border-orange-500/40 bg-orange-500/10 px-3 py-1.5 text-[12px] text-orange-200">{cal.error} · se usa la última copia guardada</p>}
      {guard.events.length === 0 ? (
        <p data-testid="red-calendar-empty" className="mt-4 text-sm text-slate-500">No hay eventos de alto impacto en el calendario de esta semana.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-[12px]">
            <thead className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              <tr><th className="py-1.5 pr-3">Hora (NY)</th><th className="pr-3">Divisa</th><th className="pr-3">Evento</th><th className="pr-3">Pares afectados</th><th className="text-right">Estado</th></tr>
            </thead>
            <tbody>
              {guard.events.map((e) => (
                <tr key={e.key} data-testid={`red-event-${e.key}`} className={`border-t border-[#1E293B]/70 ${e.state === "PASADO" ? "opacity-50" : ""}`}>
                  <td className="num py-2 pr-3 text-slate-300">{e.ny_time}</td>
                  <td className="pr-3 font-mono text-slate-200">{e.country}</td>
                  <td className="pr-3 text-slate-200">{e.title}{e.forecast && <span className="ml-2 font-mono text-[10px] text-slate-500">prev. {e.forecast}</span>}</td>
                  <td className="pr-3 font-mono text-[10px] text-slate-400">{e.affected.length ? e.affected.map((s) => s.replace("USDT", "")).join(" · ") : "ninguno de tus pares"}</td>
                  <td className="text-right"><span className={`rounded-sm border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${STATE[e.state].cls}`}>{STATE[e.state].label}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
