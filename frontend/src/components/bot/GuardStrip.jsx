import { useEffect, useState } from "react";
import { Clock, ShieldAlert, CalendarClock } from "lucide-react";
import { http } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";

const fmtLeft = (sec) => {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m}:${String(s).padStart(2, "0")}`;
};

export const useGuard = () => usePolling(() => http.get("/guard").then((r) => r.data), 30000);

export default function GuardStrip({ guard }) {
  const hours = guard?.hours;
  const [left, setLeft] = useState(null);
  useEffect(() => {
    if (!hours?.next_change) { setLeft(null); return undefined; }
    const tick = () => setLeft(Math.max(0, Math.round((new Date(hours.next_change) - Date.now()) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [hours?.next_change]);
  if (!hours) return null;
  const blocked = Object.entries(guard.blocked || {});
  const next = (guard.events || []).find((e) => e.state === "PROXIMO" && e.affected.length > 0);
  return (
    <div data-testid="guard-strip" className="panel rise flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5" style={{ animationDelay: "20ms" }}>
      <span data-testid="guard-hours-badge" className={`inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${
        hours.in_hours ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" : "border-slate-600 bg-slate-500/10 text-slate-300"}`}>
        <span className={`h-1.5 w-1.5 rounded-full ${hours.in_hours ? "pulse-dot bg-emerald-400" : "bg-slate-500"}`} />
        {hours.in_hours ? `En horario · ${hours.window}` : "Fuera de horario"}
      </span>
      <span data-testid="guard-countdown" className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">
        <Clock className="h-3 w-3" />
        {hours.in_hours && !hours.next_change ? "Opera siempre" : <>{hours.in_hours ? "Cierra en" : `${hours.next_window || "Próxima ventana"} en`} <span className="num text-slate-100">{fmtLeft(left)}</span></>}
        <span className="text-slate-600">· NY {hours.ny_time}</span>
      </span>
      {blocked.length > 0 ? (
        <span data-testid="guard-news-block" title={blocked.map(([s, e]) => `${s}: ${e}`).join("\n")}
          className="inline-flex items-center gap-1.5 rounded-sm border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-rose-300">
          <ShieldAlert className="h-3 w-3" /> Evento rojo · {blocked.length} par(es) bloqueado(s) ±{guard.before_min} min
        </span>
      ) : next && (
        <span data-testid="guard-next-event" className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">
          <CalendarClock className="h-3 w-3 text-rose-400" /> Próximo rojo: {next.country} {next.title} · {next.ny_time} NY
        </span>
      )}
      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        {hours.windows.map((w) => (
          <span key={w.name} data-testid={`guard-window-${w.start.replace(":", "")}`} title={`${w.name} · hora de Nueva York · todos los días`}
            className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] ${hours.window === w.name ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" : "border-[#1E293B] text-slate-500"}`}>
            {w.start !== w.end && <span className="num">{w.start}–{w.end} </span>}<span className="opacity-70">{w.name}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
