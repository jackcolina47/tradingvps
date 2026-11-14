import { useState } from "react";
import { Sparkles, Loader2, Undo2, CheckCircle2, Info } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { http, errMsg, STRATEGY_LABELS, SESSION_NAMES } from "@/lib/api";

export const LABELS = {
  symbols: "Pares", strategy: "Estrategia", disabled_strategies: "Estrategias apagadas", risk_per_trade: "Riesgo/op (%)", max_positions: "Máx. posiciones",
  leverage: "Apalancamiento", ai_min_confidence: "Confianza mínima IA", trailing_enabled: "Trailing", breakeven_enabled: "Stop a breakeven", htf_filter: "Filtro TF superior", symbol_params: "Parámetros SL/TP",
  sessions_allowed: "Sesiones permitidas", timeframe: "Temporalidad", signal_mode: "Criterios de entrada", daily_loss_limit: "Límite de pérdida diaria (%)",
  ai_enabled: "Filtro IA", scan_interval_sec: "Intervalo de escaneo (s)", close_before_weekend: "Cierre antes del fin de semana", min_lot_max_risk: "Tope riesgo lote mínimo (%)",
  risk_profile: "Perfil de riesgo", auto_review_enabled: "Revisión IA automática", auto_review_apply: "Aplicar cambios de la revisión",
  ai_news_weight: "Peso de las noticias en la IA", partial_tp_enabled: "Cierre parcial", vol_filter_enabled: "Filtro de volatilidad", seasonality_bias: "Sesgo estacional",
  usd_regime_filter: "Filtro régimen USD", fomc_rule: "Regla FOMC", price_action_block: "Bloqueo por precio extendido", rollover_guard: "Guarda de rollover", currency_exposure_limit: "Exposición por divisa", max_long_positions: "Máx. posiciones largo plazo",
};
export const fmtVal = (k, v) => {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "Sí" : "No";
  if (k === "strategy") return STRATEGY_LABELS[v] || v;
  if (k === "disabled_strategies") return v.map((s) => STRATEGY_LABELS[s] || s).join(", ") || "ninguna";
  if (k === "sessions_allowed") return v.map((s) => SESSION_NAMES[s] || s).join(", ") || "todas";
  if (k === "signal_mode") return { strict: "Estrictos", relaxed: "Relajados", aggressive: "Agresivos" }[v] || v;
  if (k === "risk_profile") return ["Conservador", "Moderado", "Activo", "Agresivo", "Máximo"][v] ?? v;
  if (k === "ai_news_weight") return { normal: "Normal", bajo: "Bajo" }[v] || v;
  if (Array.isArray(v)) return v.map((s) => s.replace("USDT", "")).join(", ") || "ninguno";
  if (typeof v === "object") return Object.entries(v).map(([s, p]) => `${s.replace("USDT", "")}: SL ${p.sl_atr}× TP ${p.tp_atr}× ATR ${p.atr_period}`).join(" · ") || "ninguno";
  return String(v);
};

export function AiResult({ res, testId, onDone }) {
  const [undone, setUndone] = useState(false);
  const undo = async () => {
    try {
      await http.put("/settings", res.previous);
      setUndone(true);
      toast.success("Cambios de la IA revertidos");
      onDone?.();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const applied = Object.entries(res.applied || {});
  return (
    <div data-testid={`${testId}-result`} className="rounded-md border border-purple-500/30 bg-purple-500/5 p-4">
      <div className="flex items-start gap-2">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-purple-300" />
        <p data-testid={`${testId}-summary`} className="text-[12.5px] leading-relaxed text-slate-200">{res.summary}</p>
      </div>
      <div className="mt-3 space-y-1.5">
        <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{applied.length ? `Cambios aplicados (${applied.length})` : "Sin cambios de configuración"}</p>
        {applied.map(([k, v]) => (
          <div key={k} data-testid={`${testId}-change-${k}`} className="flex flex-wrap items-baseline gap-x-2 font-mono text-[11px]">
            <CheckCircle2 className="h-3 w-3 text-emerald-400" />
            <span className="text-slate-300">{LABELS[k] || k}:</span>
            <span className="text-slate-500 line-through">{fmtVal(k, v.antes)}</span>
            <span className="text-emerald-300">→ {fmtVal(k, v.despues)}</span>
          </div>
        ))}
      </div>
      {res.notes?.length > 0 && (
        <ul className="mt-3 space-y-1">
          {res.notes.map((n, i) => (
            <li key={i} className="flex gap-2 text-[11.5px] leading-snug text-slate-400"><Info className="mt-0.5 h-3 w-3 shrink-0 text-slate-500" />{n}</li>
          ))}
        </ul>
      )}
      {applied.length > 0 && !undone && (
        <Button data-testid={`${testId}-undo-button`} onClick={undo} variant="outline" size="sm" className="mt-3 h-7 border-[#1E293B] bg-transparent font-mono text-[10px] uppercase tracking-wider text-slate-300 hover:bg-[#111827]">
          <Undo2 className="mr-1 h-3 w-3" /> Deshacer cambios
        </Button>
      )}
      {undone && <p className="mt-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Cambios revertidos</p>}
    </div>
  );
}

export default function AiAdvice({ scope, payload = {}, onDone, label = "Analizar y ajustar con IA", testId = "ai-advice" }) {
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const { data } = await http.post("/ai/advise", { scope, ...payload });
      setRes(data);
      const n = Object.keys(data.applied).length;
      toast.success(n ? `La IA aplicó ${n} cambio${n > 1 ? "s" : ""} a la configuración` : "La IA revisó el análisis · sin cambios necesarios");
      onDone?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid={testId} className="space-y-3">
      <Button data-testid={`${testId}-button`} onClick={run} disabled={loading}
        className="h-9 border border-purple-500/40 bg-purple-500/10 font-mono text-[11px] font-bold uppercase tracking-wider text-purple-200 hover:bg-purple-500/20">
        {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
        {loading ? "IA analizando..." : label}
      </Button>
      {res && <AiResult key={res.ran_at || res.summary} res={res} testId={testId} onDone={onDone} />}
    </div>
  );
}
