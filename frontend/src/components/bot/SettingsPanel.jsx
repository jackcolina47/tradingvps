import { useEffect, useState } from "react";
import { SlidersHorizontal, Save } from "lucide-react";
import { toast } from "sonner";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Panel } from "./Panel";
import RiskProfileBar from "./RiskProfileBar";
import { STRATEGY_LABELS, STRATEGY_KEYS, MODE_LABELS, activeStrategies } from "@/lib/api";
import AiPositionReviewSettings from "./settings/AiPositionReviewSettings";
import EmaTestSettings from "./settings/EmaTestSettings";
import AtrLevelsSettings from "./settings/AtrLevelsSettings";

const NUMS = [
  { k: "risk_per_trade", label: "Riesgo por operación (%)", step: 0.1, min: 0.1, max: 5 },
  { k: "max_positions", label: "Máx. posiciones", step: 1, min: 1, max: 10 },
  { k: "leverage", label: "Apalancamiento (x)", step: 1, min: 1, max: 20 },
  { k: "daily_loss_limit", label: "Límite pérdida diaria (%)", step: 0.5, min: 1, max: 20 },
  { k: "ai_min_confidence", label: "Confianza mínima IA (%)", step: 5, min: 0, max: 100 },
  { k: "min_rr", label: "R:R mínimo (prefiltro)", step: 0.1, min: 0, max: 5, hint: "Etapa 0 gratis: señales de corto plazo con riesgo/beneficio por debajo se descartan antes de llamar a la IA (0 = apagado)" },
  { k: "scan_interval_sec", label: "Intervalo escaneo (seg)", step: 10, min: 30, max: 900 },
];
const TOGGLES = [
  { k: "ai_enabled", label: "Confirmación con IA", hint: "La IA aprueba o rechaza cada señal" },
  { k: "ai_funnel_enabled", label: "Embudo IA en 2 etapas", hint: "Primero un modelo rápido y barato descarta el ruido técnico (etapa 1); solo lo que pasa llega al modelo principal (etapa 2). Si el filtro rápido falla, la señal se rechaza" },
  { k: "breakeven_enabled", label: "Stop a breakeven", hint: "Mueve el stop a la entrada al alcanzar el nivel en R. Funciona por sí solo, con o sin «Cierre parcial de beneficios»" },
  { k: "trailing_enabled", label: "Trailing stop", hint: "Trailing por ATR desde el nivel en R configurado; independiente del breakeven" },
  { k: "htf_filter", label: "Filtro de temporalidad superior", hint: "No operar contra la tendencia mayor" },
];
const CHIP = "relative rounded-sm border px-2 py-1 font-mono text-[10px] transition-colors";
const ON = "border-amber-500/60 bg-amber-500/15 text-amber-300";
const OFF = "border-[#1E293B] text-slate-500 hover:text-slate-200";

const Sel = ({ label, value, onChange, options, render, testId }) => (
  <div>
    <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger data-testid={testId} className="h-9 border-[#1E293B] bg-[#07090E] font-mono text-xs"><SelectValue /></SelectTrigger>
      <SelectContent className="border-[#1E293B] bg-[#0D111A]">
        {options.map((o) => <SelectItem key={o} value={o} className="font-mono text-xs">{render ? render(o) : o}</SelectItem>)}
      </SelectContent>
    </Select>
  </div>
);

export default function SettingsPanel({ settings, meta, profileActive, onSave, onProfile, leverageMax = 20, maxPositions = 10, extraToggles = [], extraNums = [], extraChips = [], extraSelects = [], fmtLeverage, strategyKeys = STRATEGY_KEYS, longKeys = [], hide = [], testOnly = false, emaOnly = false, className = "lg:col-span-5" }) {
  const [form, setForm] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!dirty) setForm(settings); }, [settings, dirty]);
  const set = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setDirty(true); };
  const toggleSym = (s) => set("symbols", form.symbols.includes(s) ? form.symbols.filter((x) => x !== s) : [...form.symbols, s]);
  const toggleIn = (k, id) => { const cur = form[k] || []; set(k, cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]); };
  const active = activeStrategies(form, strategyKeys);
  const nums = [...NUMS.filter((n) => !hide.includes(n.k)).map((n) => (n.k === "leverage" ? { ...n, max: leverageMax } : n.k === "max_positions" ? { ...n, max: maxPositions } : n)), ...extraNums];
  const toggles = [...TOGGLES.filter((t) => !hide.includes(t.k)), ...extraToggles];
  const toggleStrat = (k) => {
    const next = active.includes(k) ? active.filter((x) => x !== k) : [...active, k];
    if (!next.length) return;
    setForm((f) => ({ ...f, strategy: "all", disabled_strategies: strategyKeys.filter((x) => !next.includes(x)) }));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    const body = { ...form };
    const clamped = [];
    nums.forEach(({ k, min, max, label }) => {
      const v = Number(body[k]);
      body[k] = Math.min(max, Math.max(min, v || min));
      if (body[k] !== v) clamped.push(`${label.replace(/ \(.*\)$/, "")} → ${body[k]}`);
    });
    if ("ema_test_adx_min" in body) {
      const v = Number(body.ema_test_adx_min);
      body.ema_test_adx_min = Math.min(60, Math.max(5, v || 20));
      if (body.ema_test_adx_min !== v) clamped.push(`ADX mínimo (test EMA) → ${body.ema_test_adx_min}`);
    }
    if ("ema_test_partial_atr" in body) {
      const v = Number(body.ema_test_partial_atr);
      body.ema_test_partial_atr = Math.min(3, Math.max(0.5, v || 1.5));
      if (body.ema_test_partial_atr !== v) clamped.push(`Parcial test EMA (×ATR) → ${body.ema_test_partial_atr}`);
    }
    [["ema_test_confirm_adx", 5, 60, 24, "ADX entrada confirmada"], ["ema_test_confirm_dist_atr", 0.1, 3, 0.5, "Distancia EMAs (×ATR)"], ["ema_test_confirm_bars", 1, 100, 20, "Plazo de velas"]].forEach(([k, min, max, def, label]) => {
      if (!(k in body)) return;
      const v = Number(body[k]);
      body[k] = Math.min(max, Math.max(min, (k.endsWith("bars") ? Math.round(v) : v) || def));
      if (body[k] !== v) clamped.push(`${label} → ${body[k]}`);
    });
    [["ema_sl_atr", 0.5, 10, 3, "Stop loss (×ATR)"], ["atr_sl_mult", 0.5, 10, 2, "Multiplicador SL (×ATR)"], ["atr_tp_mult", 0.5, 20, 4, "Multiplicador TP (×ATR)"]].forEach(([k, min, max, def, label]) => {
      if (!(k in body)) return;
      const v = Number(body[k]);
      body[k] = Math.min(max, Math.max(min, v || def));
      if (body[k] !== v) clamped.push(`${label} → ${body[k]}`);
    });
    for (const [k, def, label] of [["ema_partial_pct", 50, "Parcial (%)"], ["ema_partial_at_atr", 1.5, "Parcial (×ATR)"], ["ema_trail_atr", 2, "Trailing (×ATR)"], ["ema_be_atr", 1, "Breakeven (×ATR)"]]) {
      if (!(k in body)) continue;
      const v = Number(body[k]);  // no range limit: only a positive number (and at most 100 %)
      body[k] = v > 0 ? (k === "ema_partial_pct" ? Math.min(100, v) : v) : def;
      if (body[k] !== v) clamped.push(`${label} → ${body[k]}`);
    }
    delete body.bot_running;
    delete body.symbol_params;
    const ok = await onSave(body);
    setSaving(false);
    if (ok) {
      setDirty(false);
      setForm((f) => ({ ...f, ...body }));
      if (clamped.length) toast.info(`Valores ajustados al rango permitido: ${clamped.join(" · ")}`);
    }
  };

  return (
    <Panel testId="settings-panel" title={testOnly ? "Ajustes del laboratorio" : "Estrategia y riesgo"} icon={SlidersHorizontal} className={className} bodyClass="p-4 space-y-4" delay={320}
      actions={
        <Button data-testid="settings-save-button" size="sm" onClick={save} disabled={!dirty || saving || !form.symbols.length}
          className="h-7 bg-amber-500 font-mono text-[10px] font-bold uppercase tracking-wider text-[#07090E] hover:bg-amber-400">
          <Save className="mr-1 h-3 w-3" /> {saving ? "Guardando" : "Guardar"}
        </Button>
      }>
      {meta?.profiles && (
        <RiskProfileBar profiles={meta.profiles} active={profileActive} selected={settings.risk_profile} fmtLeverage={fmtLeverage} strategyCount={strategyKeys.length}
          onApply={async (level) => { const ok = await onProfile(level); if (ok) setDirty(false); return ok; }} />
      )}
      <div className="grid grid-cols-2 gap-3">
        <Sel label="Temporalidad" value={form.timeframe} onChange={(v) => set("timeframe", v)} options={meta?.timeframes || []} testId="settings-timeframe-select" />
        {!testOnly && !emaOnly && <Sel label="Criterios de entrada" value={form.signal_mode} onChange={(v) => set("signal_mode", v)} options={meta?.signal_modes || []}
          render={(o) => MODE_LABELS[o]} testId="settings-signal-mode-select" />}
        {emaOnly && <Sel label="Zona horaria" value={form.display_tz || "utc"} onChange={(v) => set("display_tz", v)} options={["utc", "bst"]}
          render={(o) => (o === "utc" ? "UTC" : "BST · hora de Londres")} testId="settings-display-tz-select" />}
      </div>
      {emaOnly && <p data-testid="settings-display-tz-hint" className="-mt-2 text-[11px] text-slate-500">Zona horaria de todas las horas del panel y de los mensajes de Telegram de este bot. «BST · hora de Londres» cambia sola entre BST (UTC+1, verano) y GMT (UTC+0, invierno). Las velas se siguen calculando en UTC.</p>}
      {meta?.mode_info?.[form.signal_mode] && <p data-testid="settings-signal-mode-hint" className="-mt-2 text-[11px] text-slate-500">{meta.mode_info[form.signal_mode].desc}</p>}
      {!testOnly && <>
      <div className={emaOnly ? "hidden" : ""}>
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">Estrategias activas ({active.length}/{strategyKeys.length})</p>
        <div className="flex flex-wrap gap-1.5">
          {strategyKeys.map((k) => (
            <button key={k} data-testid={`settings-strategy-${k}`} onClick={() => toggleStrat(k)} title={meta?.strategy_info?.[k]?.rules}
              className={`${CHIP} ${active.includes(k) ? ON : OFF}`}>
              {STRATEGY_LABELS[k]}{longKeys.includes(k) && <span className="ml-1 rounded-sm bg-sky-500/20 px-1 text-[8px] text-sky-300">LP</span>}
            </button>
          ))}
        </div>
        {longKeys.length > 0 && <p className="mt-1 text-[11px] text-slate-500">LP = largo plazo (velas diarias/4h): no cuentan en «Máx. posiciones», usan su propio tope.</p>}
      </div>
      <div>
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">Pares a operar ({form.symbols.length})</p>
        <div className="flex flex-wrap gap-1.5">
          {(meta?.symbols || []).map((s) => {
            const on = form.symbols.includes(s);
            return (
              <button key={s} data-testid={`settings-symbol-${s}`} onClick={() => toggleSym(s)} title={form.symbol_params?.[s] ? `SL ${form.symbol_params[s].sl_atr}× · TP ${form.symbol_params[s].tp_atr}× · ATR ${form.symbol_params[s].atr_period} (optimizado)` : undefined}
                className={`${CHIP} ${on ? ON : OFF}`}>
                {s.replace("USDT", "")}
                {form.symbol_params?.[s] && <span data-testid={`settings-symbol-${s}-optimized`} className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-emerald-400" />}
              </button>
            );
          })}
        </div>
      </div>
      </>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {nums.map((n) => (
          <div key={n.k}>
            <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{n.label}</p>
            <Input data-testid={`settings-${n.k.replace(/_/g, "-")}-input`} type="number" step={n.step} min={n.min} max={n.max} value={form[n.k] ?? ""} title={n.hint}
              onChange={(e) => set(n.k, e.target.value)} className="num h-9 border-[#1E293B] bg-[#07090E] text-xs" />
          </div>
        ))}
      </div>
      {extraSelects.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {extraSelects.map((x) => (
            <div key={x.k} data-testid={`settings-${x.k.replace(/_/g, "-")}`}>
              <Sel label={x.label} value={form[x.k] ?? x.options[0]} onChange={(v) => set(x.k, v)} options={x.options} render={x.render} testId={`settings-${x.k.replace(/_/g, "-")}-select`} />
              {x.hint && <p className="mt-1 text-[11px] text-slate-500">{x.hint}</p>}
            </div>
          ))}
        </div>
      )}
      {extraChips.map((c) => {
        const sel = form[c.k] || [];
        return (
          <div key={c.k} data-testid={`settings-${c.k.replace(/_/g, "-")}`}>
            <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">{c.label} ({sel.length ? sel.length : c.emptyLabel})</p>
            <div className="flex flex-wrap gap-1.5">
              {c.options.map((o) => (
                <button key={o.id} data-testid={`settings-${c.k.replace(/_/g, "-")}-${o.id}`} onClick={() => toggleIn(c.k, o.id)} className={`${CHIP} ${sel.includes(o.id) ? ON : OFF}`}>{o.name}</button>
              ))}
            </div>
            <p className="mt-1 text-[11px] text-slate-500">{c.hint}</p>
          </div>
        );
      })}
      <div className="space-y-2.5">
        {toggles.map((t) => (
          <label key={t.k} className="flex cursor-pointer items-center justify-between gap-3 rounded-sm border border-[#1E293B] px-3 py-2 transition-colors hover:border-[#334155]">
            <span>
              <span className="block text-[13px] text-slate-200">{t.label}</span>
              <span className="block text-[11px] text-slate-500">{t.hint}</span>
            </span>
            <Switch data-testid={`settings-${t.k.replace(/_/g, "-")}-switch`} checked={form[t.k]} onCheckedChange={(v) => set(t.k, v)} />
          </label>
        ))}
        {!testOnly && !emaOnly && <AtrLevelsSettings form={form} set={set} />}
        {!testOnly && !emaOnly && <AiPositionReviewSettings form={form} set={set} />}
        <EmaTestSettings form={form} set={set} meta={meta} main={emaOnly} />
      </div>
    </Panel>
  );
}
