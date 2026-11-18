import { ShieldCheck, Sparkles } from "lucide-react";
import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { Panel } from "./Panel";
import { fmtUsd } from "@/lib/api";

const Stat = ({ label, value, testId }) => (
  <div className="rounded-sm border border-[#1E293B] bg-[#07090E]/60 px-3 py-2">
    <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
    <p data-testid={testId} className="num mt-0.5 text-sm font-semibold text-slate-100">{value}</p>
  </div>
);

const STAGES = [
  ["Etapa 0 · Filtro técnico (sin IA)", "EMAs 20/50/200, RSI, MACD, ADX, ATR, volumen y zonas de oferta/demanda de cada par. Descarta mercados planos, pares con posición abierta y pares bloqueados por evento rojo."],
  ["Etapa 1 · Modelo rápido", "Una sola llamada con la tabla de todos los pares: elige como máximo 3 candidatos."],
  ["Etapa 2 · Modelo potente", "Analiza cada candidato como un trader profesional, aplica las reglas aprendidas y responde BUY / SELL / PASS con un % de confianza. Vuelve a analizar cada candidato cada 5 minutos hasta abrir operación o llenar las posiciones máximas."],
];

export default function BotPanel({ settings: s, account, equity, fmtLeverage = (v) => `${v}x` }) {
  const used = Math.min(100, Math.max(0, (-account.daily_pnl_pct / s.daily_loss_limit) * 100));
  const nd0 = equity.length ? equity[0].net_deposits || 0 : 0;
  const hasFlows = equity.some((e) => (e.net_deposits || 0) !== nd0);
  const curve = equity.map((e) => ({ t: e.ts, v: e.equity, a: Math.round((e.equity - ((e.net_deposits || 0) - nd0)) * 100) / 100 }));
  const SERIES = { v: "Equidad", a: "Sin aportes" };

  return (
    <Panel testId="bot-plan-panel" title="Plan de trading" icon={ShieldCheck} className="lg:col-span-3" bodyClass="p-4 space-y-4 overflow-y-auto thin-scroll max-h-[540px]" delay={180}>
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Temporalidad {s.timeframe} · ciclo cada 60 s</p>
        <p data-testid="active-strategy-name" className="font-display mt-1 text-lg font-bold text-amber-400">Embudo IA</p>
        <ul className="mt-2 space-y-2">
          {STAGES.map(([name, text]) => (
            <li key={name} className="border-l-2 border-amber-500/40 pl-2.5">
              <p className="text-[13px] font-semibold text-slate-200">{name}</p>
              <p className="text-[12px] leading-relaxed text-slate-400">{text}</p>
            </li>
          ))}
        </ul>
        <p data-testid="plan-levels" className="mt-2 text-[12px] text-slate-400">
          SL/TP: {s.atr_levels_enabled ? `fijos · SL ${s.atr_sl_mult}×ATR · TP ${s.atr_tp_mult}×ATR` : `los decide la IA (si no son válidos: ${s.atr_sl_mult}×ATR / ${s.atr_tp_mult}×ATR)`}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Stat label="Riesgo / op." value={`${s.risk_per_trade}%`} testId="plan-risk" />
        <Stat label="Máx. posiciones" value={s.max_positions} testId="plan-max-positions" />
        <Stat label="Apalancamiento" value={fmtLeverage(s.leverage)} testId="plan-leverage" />
        <Stat label="Límite diario" value={`-${s.daily_loss_limit}%`} testId="plan-daily-limit" />
      </div>

      <div data-testid="daily-loss-meter">
        <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-wider">
          <span className="text-slate-500">Consumo límite diario</span>
          <span className={account.daily_paused ? "text-rose-400" : "text-slate-400"}>{account.daily_paused ? "Pausado" : `${used.toFixed(0)}%`}</span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[#1E293B]">
          <div className={`h-full transition-[width] duration-500 ${used > 70 ? "bg-rose-500" : used > 35 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${used}%` }} />
        </div>
      </div>

      <div data-testid="ai-config-card" className="rounded-sm border border-purple-500/30 bg-purple-500/[0.08] p-3">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-[13px] font-semibold text-purple-300">
            <Sparkles className="h-3.5 w-3.5" /> Embudo IA
          </span>
          <span data-testid="ai-status-label" className={`font-mono text-[10px] font-bold uppercase ${"text-purple-300"}`}>
            {`Umbral ${s.ai_min_confidence}%`}
          </span>
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
          Cada 60 s, dentro del horario operativo y con huecos libres: filtro técnico sin IA → el modelo rápido elige hasta 3 candidatos → el modelo potente decide BUY / SELL / PASS.
          Solo se abre si su confianza supera el {s.ai_min_confidence}%; el resto se sigue como sombra. Tamaño siempre por riesgo fijo ({s.risk_per_trade}% por operación).
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-wider text-slate-500">
          <span>Curva de equidad{hasFlows && <span data-testid="equity-legend" className="ml-2 normal-case tracking-normal"><span className="text-amber-400">— equidad</span> <span className="ml-1.5 text-sky-400">╌ sin aportes</span></span>}</span>
          <span className="num text-slate-300">{fmtUsd(account.equity)}</span>
        </div>
        <div data-testid="equity-mini-chart" className="mt-2 h-24">
          {curve.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={curve} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#F7A600" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#F7A600" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <YAxis hide domain={["dataMin - 5", "dataMax + 5"]} />
                <Tooltip contentStyle={{ background: "#0D111A", border: "1px solid #1E293B", fontFamily: "IBM Plex Mono", fontSize: 11 }}
                  labelFormatter={(l) => new Date(l).toLocaleString("es-ES")} formatter={(v, name) => [fmtUsd(v), SERIES[name] || name]} />
                <Area type="monotone" dataKey="v" stroke="#F7A600" strokeWidth={1.5} fill="url(#eqFill)" isAnimationActive={false} />
                {hasFlows && <Line data-testid="equity-adjusted-line" type="monotone" dataKey="a" stroke="#38BDF8" strokeWidth={1.2} strokeDasharray="4 3" dot={false} isAnimationActive={false} />}
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <p className="pt-6 text-center text-[12px] text-slate-500">Se dibuja tras unos minutos de actividad</p>
          )}
        </div>
      </div>
    </Panel>
  );
}
