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

const NUMS = [
  { k: "risk_per_trade", label: "Riesgo por operación (%)", step: 0.1, min: 0.1, max: 5 },
  { k: "max_positions", label: "Máx. posiciones", step: 1, min: 1, max: 10 },
  { k: "leverage", label: "Apalancamiento (x)", step: 1, min: 1, max: 20 },
  { k: "daily_loss_limit", label: "Límite pérdida diaria (%)", step: 0.5, min: 1, max: 20 },
  { k: "ai_min_confidence", label: "Confianza mínima IA (%)", step: 5, min: 0, max: 100 },
  { k: "scan_interval_sec", label: "Intervalo escaneo (seg)", step: 10, min: 30, max: 900 },
];
const TOGGLES = [
  { k: "ai_enabled", label: "Confirmación con IA", hint: "La IA aprueba o rechaza cada señal" },
  { k: "trailing_enabled", label: "Breakeven + trailing stop", hint: "BE a +1R, trailing ATR desde +1.5R" },
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

export default function SettingsPanel({ settings, meta, profileActive, onSave, onProfile, leverageMax = 20, maxPositions = 10, extraToggles = [], extraNums = [], extraChips = [], extraSelects = [], fmtLeverage, strategyKeys = STRATEGY_KEYS, longKeys = [] }) {
  const [form, setForm] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!dirty) setForm(settings); }, [settings, dirty]);
  const set = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setDirty(true); };
  const toggleSym = (s) => set("symbols", form.symbols.includes(s) ? form.symbols.filter((x) => x !== s) : [...form.symbols, s]);
  const toggleIn = (k, id) => { const cur = form[k] || []; set(k, cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]); };
  const active = activeStrategies(form, strategyKeys);
  const nums = [...NUMS.map((n) => (n.k === "leverage" ? { ...n, max: leverageMax } : n.k === "max_positions" ? { ...n, max: maxPositions } : n)), ...extraNums];
  const toggles = [...TOGGLES.map((t) => (t.k === "trailing_enabled" && extraNums.some((n) => n.k === "be_start_r")
    ? { ...t, hint: "Stop a breakeven y trailing por ATR según los niveles en R de los campos superiores" } : t)), ...extraToggles];
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
    <Panel testId="settings-panel" title="Estrategia y riesgo" icon={SlidersHorizontal} className="lg:col-span-5" bodyClass="p-4 space-y-4" delay={320}
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
        <Sel label="Criterios de entrada" value={form.signal_mode} onChange={(v) => set("signal_mode", v)} options={meta?.signal_modes || []}
          render={(o) => MODE_LABELS[o]} testId="settings-signal-mode-select" />
      </div>
      {meta?.mode_info?.[form.signal_mode] && <p data-testid="settings-signal-mode-hint" className="-mt-2 text-[11px] text-slate-500">{meta.mode_info[form.signal_mode].desc}</p>}
      <div>
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
        <AiPositionReviewSettings form={form} set={set} />
      </div>
    </Panel>
  );
}
