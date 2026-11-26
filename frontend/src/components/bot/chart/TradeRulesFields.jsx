const REVIEW_MIN = [5, 10, 15, 20, 25, 30];
export const INPUT = "num h-9 w-full rounded-sm border border-[#1E293B] bg-[#07090E] px-2 text-sm text-slate-100 outline-none focus:border-amber-500/60 disabled:opacity-40";
export const LABEL = "mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500";
const inR = (v) => Number(v) >= 0.5 && Number(v) <= 3;
const R = { step: 0.25, min: 0.5, max: 3 };

/** Form state of the exit management of one trade: the bot settings, overridden by the trade's own rules when it has them. */
export const rulesState = (settings = {}, own = {}, reviewMinutes = null) => {
  const s = { ...settings, ...(own || {}) };
  return {
    breakeven_enabled: s.breakeven_enabled ?? true, be_start_r: s.be_start_r ?? 1,
    trailing_enabled: s.trailing_enabled ?? true, trail_start_r: s.trail_start_r ?? 1.5, trail_atr_mult: s.trail_atr_mult ?? 1,
    partial_tp_enabled: s.partial_tp_enabled ?? false, partial_tp_r: s.partial_tp_r ?? 1.5, partial_tp_pct: s.partial_tp_pct ?? 50,
    ai_review: !!reviewMinutes, ai_review_minutes: reviewMinutes || 15,
  };
};

export const rulesValid = (r) => (!r.breakeven_enabled || inR(r.be_start_r)) && (!r.trailing_enabled || (inR(r.trail_start_r) && inR(r.trail_atr_mult)))
  && (!r.partial_tp_enabled || (inR(r.partial_tp_r) && Number(r.partial_tp_pct) >= 25 && Number(r.partial_tp_pct) <= 75));

export const rulesBody = (r) => ({
  breakeven_enabled: !!r.breakeven_enabled, trailing_enabled: !!r.trailing_enabled, partial_tp_enabled: !!r.partial_tp_enabled,
  ...(r.breakeven_enabled ? { be_start_r: Number(r.be_start_r) } : {}),
  ...(r.trailing_enabled ? { trail_start_r: Number(r.trail_start_r), trail_atr_mult: Number(r.trail_atr_mult) } : {}),
  ...(r.partial_tp_enabled ? { partial_tp_r: Number(r.partial_tp_r), partial_tp_pct: Math.round(Number(r.partial_tp_pct)) } : {}),
  ...(r.ai_review ? { ai_review_minutes: Number(r.ai_review_minutes) } : {}),
});

function Rule({ id, label, hint, on, onToggle, children }) {
  return (
    <div data-testid={`${id}`} className={`rounded-sm border p-2.5 transition-colors ${on ? "border-amber-500/40 bg-amber-500/[0.04]" : "border-[#1E293B]"}`}>
      <label className="flex cursor-pointer items-start gap-2">
        <input data-testid={`${id}-toggle`} type="checkbox" checked={!!on} onChange={(e) => onToggle(e.target.checked)} className="mt-0.5 h-4 w-4 accent-amber-500" />
        <span className="min-w-0">
          <span className="block text-[12px] font-semibold text-slate-100">{label} <span className={`ml-1 font-mono text-[9px] uppercase ${on ? "text-emerald-400" : "text-slate-500"}`}>{on ? "activado" : "desactivado"}</span></span>
          <span className="block text-[10px] leading-snug text-slate-500">{hint}</span>
        </span>
      </label>
      {on && <div className="mt-2 grid grid-cols-2 gap-2">{children}</div>}
    </div>
  );
}

/** Breakeven · trailing · partial close · own AI review, each with its on/off check. `prefix` namespaces the test ids. */
export default function TradeRulesFields({ value: r, onChange, prefix = "manual-order", partialDone = false }) {
  const set = (k) => (v) => onChange({ ...r, [k]: v });
  const num = (k, id, label, props) => (
    <label><span className={LABEL}>{label}</span>
      <input data-testid={`${prefix}-${id}`} type="number" value={r[k]} onChange={(e) => set(k)(e.target.value)} className={INPUT} {...props} /></label>
  );
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      <Rule id={`${prefix}-breakeven`} label="Breakeven" hint="Mueve el stop a la entrada al alcanzar el nivel." on={r.breakeven_enabled} onToggle={set("breakeven_enabled")}>
        <div className="col-span-2">{num("be_start_r", "be-r", "Activar a (R)", R)}</div>
      </Rule>
      <Rule id={`${prefix}-trailing`} label="Trailing stop" hint="El stop sigue al precio a una distancia fija en ATR." on={r.trailing_enabled} onToggle={set("trailing_enabled")}>
        {num("trail_start_r", "trail-r", "Desde (R)", R)}
        {num("trail_atr_mult", "trail-atr", "Distancia (ATR)", R)}
      </Rule>
      <Rule id={`${prefix}-partial`} label="Cierre parcial" hint={partialDone ? "Ya se ejecutó en esta posición: cambiarlo no tiene efecto." : "Cierra una parte al alcanzar el nivel y deja correr el resto."} on={r.partial_tp_enabled} onToggle={set("partial_tp_enabled")}>
        {num("partial_tp_r", "partial-r", "Cerrar a (R)", R)}
        {num("partial_tp_pct", "partial-pct", "Parte (%)", { step: 5, min: 25, max: 75 })}
      </Rule>
      <Rule id={`${prefix}-ai-review`} label="Revisión automática de IA" hint="La IA revisa solo esta operación (mantener, breakeven o cerrar) con el modo y la confianza mínima de Ajustes." on={r.ai_review} onToggle={set("ai_review")}>
        <label className="col-span-2"><span className={LABEL}>Revisar cada</span>
          <select data-testid={`${prefix}-ai-review-minutes`} value={r.ai_review_minutes} onChange={(e) => set("ai_review_minutes")(Number(e.target.value))} className={`${INPUT} font-mono text-xs`}>
            {REVIEW_MIN.map((m) => <option key={m} value={m}>{m} minutos</option>)}
          </select></label>
      </Rule>
    </div>
  );
}
