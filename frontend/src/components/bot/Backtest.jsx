import { useState } from "react";
import { FlaskConical, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { SideBadge } from "./Panel";
import { http, errMsg, fmtUsd, fmtSignedUsd, fmtPrice, fmtDateTime, pnlClass, STRATEGY_LABELS, REASON_LABELS, SESSION_NAMES, tzOpts } from "@/lib/api";

const Field = ({ label, value, onChange, options, testId, render = (o) => o }) => (
  <div className="min-w-[140px] flex-1">
    <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger data-testid={testId} className="h-9 border-[#1E293B] bg-[#07090E] font-mono text-xs"><SelectValue /></SelectTrigger>
      <SelectContent className="border-[#1E293B] bg-[#0D111A]">
        {options.map((o) => <SelectItem key={o} value={o} className="font-mono text-xs">{render(o)}</SelectItem>)}
      </SelectContent>
    </Select>
  </div>
);

const Metric = ({ label, value, tone, testId }) => (
  <div className="bg-[#0D111A] px-3 py-2.5">
    <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
    <p data-testid={testId} className={`num mt-1 text-base font-semibold ${tone != null ? pnlClass(tone) : "text-slate-100"}`}>{value}</p>
  </div>
);

const DEFAULT_INTRO = (settings) => `Evalúa la estrategia sobre las últimas 1.000 velas reales (sin filtro IA ni TF superior) con el capital inicial de tu cuenta, lotes mínimos de Bybit y el mismo plan de riesgo: ${settings.risk_per_trade}% por operación, ${settings.leverage}x, comisiones 0.055% y trailing stop.`;
const LP_INTRO = (settings, tf, fx) => `Estrategia de largo plazo: se evalúa en su propia temporalidad (${tf}) con todo el histórico disponible${fx ? " (≈1 año de velas horarias de Yahoo: unos 12 fines de mes)" : tf === "1d" ? " (hasta 1.500 velas diarias, ≈4 años)" : " (1.000 velas de 4h, ≈6 meses)"}, sin filtro IA, con el capital inicial de tu cuenta, ${fx ? "lotes de 0.01 y el spread (y comisión) de tu bróker" : "lotes mínimos de Bybit y comisiones 0.055%"} y tu plan de riesgo: ${settings.risk_per_trade}% por operación, ${fx ? `1:${settings.leverage}` : `${settings.leverage}x`}, trailing stop${fx ? " y cierre por tiempo al mediodía siguiente de Londres" : ""}.`;
const fmtDateYear = (iso) => (iso ? new Date(iso).toLocaleString("es-ES", { ...tzOpts(), day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "—");

const SessionStats = ({ stats }) => (
  <div data-testid="backtest-sessions" className="rounded-sm border border-[#1E293B]">
    <p className="border-b border-[#1E293B] px-3 py-2 font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">Por sesión de apertura</p>
    {Object.entries(stats).map(([name, s]) => (
      <div key={name} data-testid={`backtest-session-${name}`} className="flex items-center justify-between gap-3 border-b border-[#1E293B]/60 px-3 py-1.5 font-mono text-[11px] last:border-0">
        <span className="text-slate-200">{name}</span>
        <span className="text-slate-500">{s.trades} ops · {s.trades ? Math.round((s.wins / s.trades) * 100) : 0}%</span>
        <span className={pnlClass(s.pips)}>{s.pips > 0 ? "+" : ""}{s.pips} pips</span>
        <span className={pnlClass(s.pnl)}>{fmtSignedUsd(s.pnl)}</span>
      </div>
    ))}
  </div>
);

export default function Backtest({ meta, settings, intro = DEFAULT_INTRO }) {
  const [form, setForm] = useState({ symbol: settings.symbols[0], strategy: settings.strategy, timeframe: settings.timeframe });
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const lpInfo = (meta?.long_horizon_keys || []).includes(form.strategy) ? meta?.strategy_info?.[form.strategy] : null;
  const lpTf = lpInfo ? lpInfo.bt_tf || lpInfo.tf : null;
  const isFx = Boolean(meta?.currencies);
  const fmtD = lpTf || res?.timeframe === "1d" || res?.timeframe === "4h" ? fmtDateYear : fmtDateTime;

  const run = async () => {
    setLoading(true);
    try {
      const { data } = await http.post("/backtest", { ...form, risk_per_trade: settings.risk_per_trade, leverage: settings.leverage });
      setRes(data);
    } catch (e) {
      toast.error(`Backtest falló: ${errMsg(e)}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid="backtest-panel" className="space-y-4 p-4">
      <p data-testid="backtest-intro" className="text-[12px] leading-relaxed text-slate-400">{lpTf ? LP_INTRO(settings, lpTf, isFx) : intro(settings)}</p>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Par" value={form.symbol} onChange={set("symbol")} options={meta?.symbols || []} testId="backtest-symbol-select" />
        <Field label="Estrategia" value={form.strategy} onChange={set("strategy")} options={meta?.strategies || []} testId="backtest-strategy-select" render={(o) => STRATEGY_LABELS[o]} />
        {lpTf ? (
          <div className="min-w-[140px] flex-1">
            <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Temporalidad</p>
            <div data-testid="backtest-timeframe-fixed" className="flex h-9 items-center rounded-md border border-[#1E293B] bg-[#07090E] px-3 font-mono text-xs text-slate-300" title="Las estrategias de largo plazo usan su propia temporalidad">{lpTf} · propia de la estrategia</div>
          </div>
        ) : (
          <Field label="Temporalidad" value={form.timeframe} onChange={set("timeframe")} options={meta?.timeframes || []} testId="backtest-timeframe-select" />
        )}
        <Button data-testid="backtest-run-button" onClick={run} disabled={loading} className="h-9 bg-amber-500 font-mono text-[11px] font-bold uppercase tracking-wider text-[#07090E] hover:bg-amber-400">
          {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="mr-1.5 h-3.5 w-3.5" />} Ejecutar
        </Button>
      </div>

      {res && (
        <div data-testid="backtest-results" className="space-y-4">
          <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
            {res.symbol} · {STRATEGY_LABELS[res.strategy]} · {res.timeframe} · {fmtD(res.period_start)} → {fmtD(res.period_end)}
          </p>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-[#1E293B] bg-[#1E293B] sm:grid-cols-4">
            <Metric label="Retorno" value={`${res.return_pct > 0 ? "+" : ""}${res.return_pct}%`} tone={res.return_pct} testId="backtest-return" />
            <Metric label="Operaciones" value={res.total_trades} testId="backtest-trades" />
            <Metric label="Tasa de acierto" value={`${res.win_rate}%`} testId="backtest-winrate" />
            <Metric label="Profit factor" value={res.profit_factor} testId="backtest-pf" />
            <Metric label="Máx. drawdown" value={`${res.max_drawdown}%`} tone={res.max_drawdown ? -1 : 0} testId="backtest-dd" />
            <Metric label="Equidad final" value={fmtUsd(res.final_equity)} testId="backtest-final" />
            <Metric label="Ganancia media" value={fmtSignedUsd(res.avg_win)} tone={res.avg_win} testId="backtest-avg-win" />
            <Metric label="Pérdida media" value={fmtSignedUsd(res.avg_loss)} tone={res.avg_loss} testId="backtest-avg-loss" />
            {res.total_pips != null && <Metric label="Pips netos" value={`${res.total_pips > 0 ? "+" : ""}${res.total_pips}`} tone={res.total_pips} testId="backtest-pips" />}
            {res.excursion && <Metric label="Rozaron el TP sin tocarlo" value={`${res.excursion.near_miss} (${res.excursion.near_miss_pct}%)`} tone={res.excursion.near_miss ? -1 : 0} testId="backtest-near-miss" />}
            {res.excursion && <Metric label="Recorrido medio a favor" value={`+${res.excursion.avg_mfe_r} R`} tone={1} testId="backtest-mfe" />}
            {res.excursion && <Metric label="Recorrido medio en contra" value={`${res.excursion.avg_mae_r} R`} tone={-1} testId="backtest-mae" />}
            {res.excursion && <Metric label="Cierres parciales" value={res.excursion.partial_closes} testId="backtest-partials" />}
            {res.total_pips != null && <Metric label="Pips / operación" value={`${res.avg_pips > 0 ? "+" : ""}${res.avg_pips}`} tone={res.avg_pips} testId="backtest-avg-pips" />}
            {res.weekend_closed != null && <Metric label="Cierres fin de semana" value={res.weekend_closed} testId="backtest-weekend" />}
            {res.spread_pips != null && <Metric label="Spread aplicado" value={`${res.spread_pips} pips`} testId="backtest-spread" />}
          </div>
          {res.rejected_lot > 0 && (
            <p data-testid="backtest-rejected" className="font-mono text-[11px] text-amber-300">
              {res.rejected_lot} señal{res.rejected_lot !== 1 ? "es" : ""} sin operar por tamaño menor al lote mínimo · sube el capital, el tope de riesgo con lote mínimo o usa una temporalidad menor
            </p>
          )}
          {res.skipped_session > 0 && (
            <p data-testid="backtest-skipped-session" className="font-mono text-[11px] text-sky-300">
              {res.skipped_session} señal{res.skipped_session !== 1 ? "es" : ""} descartada{res.skipped_session !== 1 ? "s" : ""} fuera de las sesiones permitidas ({(res.sessions_allowed || []).map((s) => SESSION_NAMES[s] || s).join(", ")})
            </p>
          )}
          {res.session_stats && Object.keys(res.session_stats).length > 0 && <SessionStats stats={res.session_stats} />}
          <div className="h-40" data-testid="backtest-equity-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={res.equity_curve} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="btFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={res.return_pct >= 0 ? "#10B981" : "#EF4444"} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={res.return_pct >= 0 ? "#10B981" : "#EF4444"} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" hide />
                <YAxis domain={["dataMin - 20", "dataMax + 20"]} tickFormatter={(v) => fmtUsd(v, 0)} tick={{ fill: "#64748B", fontSize: 10, fontFamily: "IBM Plex Mono" }} width={60} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "#0D111A", border: "1px solid #1E293B", fontFamily: "IBM Plex Mono", fontSize: 11 }}
                  labelFormatter={(l) => fmtD(l)} formatter={(v) => [fmtUsd(v), "Equidad"]} />
                <Area type="monotone" dataKey="equity" stroke={res.return_pct >= 0 ? "#10B981" : "#EF4444"} strokeWidth={1.5} fill="url(#btFill)" isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="max-h-56 overflow-y-auto thin-scroll rounded-sm border border-[#1E293B]">
            {res.trades.slice(0, 20).map((t, i) => (
              <div key={i} className="flex items-center justify-between gap-3 border-b border-[#1E293B]/60 px-3 py-2 font-mono text-[11px]">
                <span className="text-slate-500">{fmtD(t.opened_at)}</span>
                <SideBadge side={t.side} />
                <span className="hidden text-slate-400 sm:inline">{STRATEGY_LABELS[t.strategy]}</span>
                <span className="text-slate-300">{fmtPrice(t.entry)} → {fmtPrice(t.exit)}</span>
                {t.pips != null && <span className={`hidden sm:inline ${pnlClass(t.pips)}`}>{t.pips > 0 ? "+" : ""}{t.pips} pips</span>}
                <span className="text-slate-500">{REASON_LABELS[t.reason]}</span>
                <span className={pnlClass(t.pnl)}>{fmtSignedUsd(t.pnl)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
