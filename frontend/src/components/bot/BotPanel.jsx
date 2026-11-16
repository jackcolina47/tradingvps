import { ShieldCheck, Sparkles } from "lucide-react";
import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { Panel } from "./Panel";
import { fmtUsd, STRATEGY_LABELS, MODE_LABELS, activeStrategies } from "@/lib/api";

const Stat = ({ label, value, testId }) => (
  <div className="rounded-sm border border-[#1E293B] bg-[#07090E]/60 px-3 py-2">
    <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
    <p data-testid={testId} className="num mt-0.5 text-sm font-semibold text-slate-100">{value}</p>
  </div>
);

const PROFILE_TONE = ["text-emerald-300", "text-lime-300", "text-amber-300", "text-orange-300", "text-rose-300"];

export default function BotPanel({ settings: s, meta, account, equity, profileActive, fmtLeverage = (v) => `${v}x`, rulesOnly = false }) {
  const info = meta?.strategy_info || {};
  const active = activeStrategies(s, meta?.strategy_keys || undefined);
  const profile = profileActive != null ? meta?.profiles?.[profileActive] : null;
  const used = Math.min(100, Math.max(0, (-account.daily_pnl_pct / s.daily_loss_limit) * 100));
  const nd0 = equity.length ? equity[0].net_deposits || 0 : 0;
  const hasFlows = equity.some((e) => (e.net_deposits || 0) !== nd0);
  const curve = equity.map((e) => ({ t: e.ts, v: e.equity, a: Math.round((e.equity - ((e.net_deposits || 0) - nd0)) * 100) / 100 }));
  const SERIES = { v: "Equidad", a: "Sin aportes" };

  return (
    <Panel testId="bot-plan-panel" title="Plan de trading" icon={ShieldCheck} className="lg:col-span-3" bodyClass="p-4 space-y-4 overflow-y-auto thin-scroll max-h-[540px]" delay={180}>
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">
          Perfil · <span data-testid="plan-profile-name" className={profile ? PROFILE_TONE[profileActive] : "text-slate-300"}>{profile ? profile.name : "Personalizado"}</span> · {s.timeframe} · criterios {MODE_LABELS[s.signal_mode]?.toLowerCase()}
        </p>
        <p data-testid="active-strategy-name" className="font-display mt-1 text-lg font-bold text-amber-400">
          {active.length > 1 ? `Multi-estrategia (${active.length})` : STRATEGY_LABELS[active[0]]}
        </p>
        <ul className="mt-2 space-y-2">
          {active.map((k) => info[k] && (
            <li key={k} className="border-l-2 border-amber-500/40 pl-2.5">
              <p className="text-[13px] font-semibold text-slate-200">{info[k].name}</p>
              <p className="text-[12px] leading-relaxed text-slate-400">{info[k].rules}</p>
            </li>
          ))}
        </ul>
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
            <Sparkles className="h-3.5 w-3.5" /> {rulesOnly ? "Reglas aprendidas" : "Confirmación IA"}
          </span>
          <span data-testid="ai-status-label" className={`font-mono text-[10px] font-bold uppercase ${(rulesOnly ? s.rules_auto_learn : s.ai_enabled) ? "text-purple-300" : "text-slate-500"}`}>
            {rulesOnly ? (s.rules_auto_learn ? "Aprendiendo" : "Aprendizaje apagado") : s.ai_enabled ? "Activa" : "Inactiva"}
          </span>
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
          {rulesOnly ? "Cada señal detectada se abre directamente. El único filtro son las reglas aprendidas de las operaciones cerradas (Ajustes → Reglas aprendidas)."
            : `La IA revisa cada señal y solo se ejecuta si la aprueba con confianza ≥ ${s.ai_min_confidence}%.`}
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
