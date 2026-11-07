import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { http, fmtDateTime, SESSION_NAMES } from "@/lib/api";
import { TH, TD } from "@/components/bot/PositionsTable";

const BIAS = { long: ["Sesgo alcista", "text-emerald-300 border-emerald-500/40"], short: ["Sesgo bajista", "text-rose-300 border-rose-500/40"], both: ["Neutral", "text-slate-400 border-slate-600"] };
const VOL = { MUERTO: "text-slate-400", NORMAL: "text-emerald-300", EXTREMO: "text-amber-300", "N/D": "text-slate-500" };

const Stat = ({ label, value, testId, cls = "text-slate-100", hint }) => (
  <div data-testid={testId} className="min-w-[150px] flex-1 rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2.5" title={hint}>
    <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
    <p className={`num mt-1 text-base font-semibold ${cls}`}>{value}</p>
    {hint && <p className="mt-0.5 text-[10px] leading-snug text-slate-500">{hint}</p>}
  </div>
);

const Toggle = ({ on, label, onClick, testId }) => (
  <button data-testid={testId} onClick={onClick}
    className={`rounded-sm border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${on ? "border-emerald-500/40 text-emerald-300" : "border-slate-600 text-slate-400"}`}>
    {label}: {on ? "Activo" : "Apagado"}
  </button>
);

export default function FxFiltersPanel({ settings, onSave, symbolNames = {} }) {
  const [data, setData] = useState(null);
  const load = () => http.get("/market/context").then((r) => setData(r.data)).catch(() => toast.error("No se pudo cargar el estado de los filtros"));
  useEffect(() => { load(); }, [settings.usd_regime_filter, settings.rollover_guard, settings.vol_filter_enabled, settings.seasonality_bias, settings.fomc_rule, settings.currency_exposure_limit, settings.price_action_block]);
  if (!data) return <div className="flex justify-center p-8"><Loader2 className="h-5 w-5 animate-spin text-slate-500" /></div>;
  const rg = data.regimen_usd, exp = data.exposicion_por_divisa, roll = data.rollover, fix = data.fix_reversion, strong = rg.state === "USD FUERTE";
  const net = Object.entries(exp.neta_actual || {}).filter(([, n]) => n !== 0).sort();

  return (
    <div data-testid="fx-filters-panel" className="space-y-5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Régimen USD · índice sintético (EUR, JPY, GBP, CAD, CHF) vs SMA{rg.sma || settings.usd_regime_sma} diaria</p>
        <Toggle on={settings.usd_regime_filter} label="Filtro régimen USD" testId="fx-usd-regime-toggle" onClick={() => onSave({ usd_regime_filter: !settings.usd_regime_filter })} />
      </div>
      <div className="flex flex-wrap gap-2">
        <Stat label="Estado" value={rg.state || "—"} testId="fx-usd-regime-state" cls={rg.state === "N/D" || rg.state === "apagado" ? "text-slate-400" : strong ? "text-emerald-400" : "text-rose-400"} />
        <Stat label="Distancia a la SMA" value={rg.distance_pct == null ? "—" : `${rg.distance_pct > 0 ? "+" : ""}${rg.distance_pct}%`} testId="fx-usd-regime-distance" />
        <Stat label="Efecto" value={strong ? "xxxUSD solo cortos · USDxxx solo largos" : rg.state === "USD DÉBIL" ? "xxxUSD solo largos · USDxxx solo cortos" : "sin restricción"} testId="fx-usd-regime-effect" cls="text-slate-300 text-sm" hint="Cruces y metales no se ven afectados" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Exposición neta por divisa · tope {exp.tope} posiciones en la misma dirección</p>
      </div>
      <div data-testid="fx-exposure" className="flex flex-wrap gap-2">
        {net.length ? net.map(([c, n]) => (
          <span key={c} data-testid={`fx-exposure-${c}`} className={`rounded-sm border px-2 py-1 font-mono text-[11px] ${Math.abs(n) >= exp.tope ? "border-amber-500/40 text-amber-300" : "border-slate-600 text-slate-300"}`}>
            {c} {n > 0 ? "+" : ""}{n}
          </span>
        )) : <p className="text-[12px] text-slate-500">Sin posiciones abiertas: exposición cero.</p>}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">
          Rollover 17:00 Nueva York · ventana {roll.window_ny} · {roll.active ? "ACTIVA ahora" : `próxima ${fmtDateTime(roll.next_start)}`}{roll.triple_swap ? " · hoy triple swap" : ""}
        </p>
        <div className="flex gap-2">
          <Toggle on={settings.rollover_guard} label="Guardia rollover" testId="fx-rollover-toggle" onClick={() => onSave({ rollover_guard: !settings.rollover_guard })} />
          <Toggle on={settings.fomc_rule} label="Regla FOMC" testId="fx-fomc-toggle" onClick={() => onSave({ fomc_rule: !settings.fomc_rule })} />
        </div>
      </div>
      <p className="text-[11px] text-slate-500">
        Sin entradas nuevas en la ventana; en paper los stops no se evalúan durante el pico de spread. {data.fomc?.evento_activo ? `FOMC activo: ${data.fomc.evento_activo.title} (${fmtDateTime(data.fomc.evento_activo.time)}) → sin operar la continuación en pares con USD durante 24 h.` : "Sin decisión de la Fed en las últimas 24 h."}
      </p>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Volatilidad (percentil ATR 200 velas) y sesgo horario por par</p>
        <div className="flex gap-2">
          <Toggle on={settings.vol_filter_enabled} label="Filtro volatilidad" testId="fx-vol-toggle" onClick={() => onSave({ vol_filter_enabled: !settings.vol_filter_enabled })} />
          <Toggle on={settings.seasonality_bias} label="Estacionalidad" testId="fx-season-toggle" onClick={() => onSave({ seasonality_bias: !settings.seasonality_bias })} />
        </div>
      </div>
      <div className="overflow-hidden rounded-sm border border-[#1E293B]">
        <table className="w-full text-sm">
          <thead><tr><TH>Par</TH><TH right>Percentil ATR</TH><TH>Régimen</TH><TH>Sesgo horario</TH><TH>Contra el sesgo</TH></tr></thead>
          <tbody className="num text-[12px]">
            {Object.entries(data.watch || {}).map(([sym, w]) => (
              <tr key={sym} data-testid={`fx-filter-row-${sym}`} className="border-t border-[#1E293B]/60 transition-colors hover:bg-[#111827]">
                <TD className="font-display text-[13px] font-semibold text-slate-100">{sym}<span className="ml-1 text-[10px] font-normal text-slate-500">{symbolNames[sym]?.name}</span></TD>
                <TD right>{w.vol_pct == null ? "—" : w.vol_pct}</TD>
                <TD className={VOL[w.vol_regime] || "text-slate-400"}>{w.vol_regime || "N/D"}</TD>
                <TD><span className={`rounded-sm border px-1.5 py-0.5 font-mono text-[10px] ${BIAS[w.season_bias || "both"][1]}`}>{BIAS[w.season_bias || "both"][0]}</span></TD>
                <TD className="text-slate-400">{(settings.seasonality_block_pairs || []).includes(sym) ? "bloqueo" : `tamaño ×${settings.seasonality_size_factor}`}</TD>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-500">
        MUERTO (percentil &lt; {settings.vol_min_pct}): sin entradas · EXTREMO (≥ {settings.vol_high_pct}): riesgo a la mitad. Sesgo horario (Breedon &amp; Ranaldo): una divisa tiende a depreciarse en su horario local; las señales contra el sesgo se bloquean en los pares marcados y se reducen en el resto. Solo afecta a estrategias de corto plazo.
      </p>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Precio extendido · regla dura antes de la IA · {settings.price_action_max_candles ?? 3} velas grandes seguidas o swing contrario a &lt; {settings.price_action_swing_atr ?? 1} ATR</p>
        <Toggle on={settings.price_action_block} label="Bloqueo precio extendido" testId="fx-price-action-toggle" onClick={() => onSave({ price_action_block: !settings.price_action_block })} />
      </div>
      <p data-testid="fx-price-action-hint" className="text-[11px] text-slate-500">
        No se abre una señal de corto plazo si llega tras {settings.price_action_max_candles ?? 3} o más velas grandes seguidas a favor (≥ 2 ATR en total: perseguir el precio) o si el swing contrario más cercano (resistencia en LONG, soporte en SHORT) está a menos de {settings.price_action_swing_atr ?? 1} ATR. La señal queda en sombra PRECIO_EXTENDIDO para medir cuánto ahorra. Umbrales en Configuración.
      </p>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Reversión fin de mes (fix de Londres) · largo plazo · máx. {data.largo_plazo?.max_long_positions} posiciones LP</p>
        <span data-testid="fx-fix-status" className={`rounded-sm border px-2 py-1 font-mono text-[10px] uppercase ${fix.enabled ? "border-emerald-500/40 text-emerald-300" : "border-slate-600 text-slate-400"}`}>
          {fix.enabled ? (fix.window_open ? "Ventana abierta ahora" : `Próximo fix: ${fix.next_fix_london} Londres`) : "Apagada (Configuración → estrategias)"}
        </span>
      </div>
      <p className="text-[11px] text-slate-500">
        Último día hábil del mes, 16:00–16:30 Londres (dos velas de 15 m, por si el bot arranca tarde): si el movimiento 15:00→16:00 supera 0,6×ATR(1h) y el precio no ha deshecho aún más de la mitad, se abre en contra; TP en el precio de las 15:00, SL tras el extremo, cierre a las 12:00 del día siguiente. Pares: {fix.pairs?.join(", ")}. Sesiones permitidas para el corto plazo: {(settings.sessions_allowed || []).map((s) => SESSION_NAMES[s]).join(", ") || "todas"} (la estrategia LP ignora ese filtro y no cuenta en «Máx. posiciones»).
      </p>
    </div>
  );
}
