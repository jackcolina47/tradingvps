import { useEffect, useState } from "react";
import { SlidersHorizontal, Save } from "lucide-react";
import { toast } from "sonner";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Panel } from "./Panel";

import AiPositionReviewSettings from "./settings/AiPositionReviewSettings";
import AtrLevelsSettings from "./settings/AtrLevelsSettings";

const NUMS = [
  { k: "risk_per_trade", label: "Riesgo por operación (%)", step: 0.1, min: 0.1, max: 5 },
  { k: "max_positions", label: "Máx. posiciones", step: 1, min: 1, max: 10 },
  { k: "leverage", label: "Apalancamiento (x)", step: 1, min: 1, max: 20 },
  { k: "daily_loss_limit", label: "Límite pérdida diaria (%)", step: 0.5, min: 1, max: 20 },
];
const TOGGLES = [
  { k: "breakeven_enabled", label: "Stop a breakeven", hint: "Mueve el stop a la entrada al alcanzar el nivel en R. Funciona por sí solo, con o sin «Cierre parcial de beneficios»" },
  { k: "trailing_enabled", label: "Trailing stop", hint: "Trailing por ATR desde el nivel en R configurado; independiente del breakeven" },
];
const CONFIDENCE_LEVELS = ["10", "20", "30", "40", "50", "60", "70", "80", "90"];
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

export default function SettingsPanel({ settings, meta, profileActive, onSave, onProfile, leverageMax = 20, maxPositions = 10, extraToggles = [], extraNums = [], extraChips = [], extraSelects = [], hide = [] }) {
  const [form, setForm] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!dirty) setForm(settings); }, [settings, dirty]);
  const set = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setDirty(true); };
  const toggleSym = (s) => set("symbols", form.symbols.includes(s) ? form.symbols.filter((x) => x !== s) : [...form.symbols, s]);
  const toggleIn = (k, id) => { const cur = form[k] || []; set(k, cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]); };
  const nums = [...NUMS.filter((n) => !hide.includes(n.k)).map((n) => (n.k === "leverage" ? { ...n, max: leverageMax } : n.k === "max_positions" ? { ...n, max: maxPositions } : n)), ...extraNums];
  const toggles = [...TOGGLES.filter((t) => !hide.includes(t.k)), ...extraToggles];
  const save = async () => {
    setSaving(true);
    const body = { ...form };
    const clamped = [];
    nums.forEach(({ k, min, max, label }) => {
      const v = Number(body[k]);
      body[k] = Math.min(max, Math.max(min, v || min));
      if (body[k] !== v) clamped.push(`${label.replace(/ \(.*\)$/, "")} → ${body[k]}`);
    });
    [["atr_sl_mult", 0.5, 10, 2, "Multiplicador SL (×ATR)"], ["atr_tp_mult", 0.5, 20, 4, "Multiplicador TP (×ATR)"]].forEach(([k, min, max, def, label]) => {
      if (!(k in body)) return;
      const v = Number(body[k]);
      body[k] = Math.min(max, Math.max(min, v || def));
      if (body[k] !== v) clamped.push(`${label} → ${body[k]}`);
    });
    delete body.bot_running;
    delete body.symbol_params;
    body.ai_min_confidence = Number(form.ai_min_confidence);
    const ok = await onSave(body);
    setSaving(false);
    if (ok) {
      setDirty(false);
      setForm((f) => ({ ...f, ...body }));
      if (clamped.length) toast.info(`Valores ajustados al rango permitido: ${clamped.join(" · ")}`);
    }
  };

  return (
    <Panel testId="settings-panel" title="Embudo IA y riesgo" icon={SlidersHorizontal} className="lg:col-span-5" bodyClass="p-4 space-y-4" delay={320}
      actions={
        <Button data-testid="settings-save-button" size="sm" onClick={save} disabled={!dirty || saving || !form.symbols.length}
          className="h-7 bg-amber-500 font-mono text-[10px] font-bold uppercase tracking-wider text-[#07090E] hover:bg-amber-400">
          <Save className="mr-1 h-3 w-3" /> {saving ? "Guardando" : "Guardar"}
        </Button>
      }>
      <div className="grid grid-cols-2 gap-3">
        <Sel label="Temporalidad" value={form.timeframe} onChange={(v) => set("timeframe", v)} options={meta?.timeframes || []} testId="settings-timeframe-select" />
        <Sel label="Confianza IA para abrir operación" value={String(form.ai_min_confidence)} onChange={(v) => set("ai_min_confidence", Number(v))}
          options={CONFIDENCE_LEVELS} render={(o) => `${o} %`} testId="settings-ai-confidence-select" />
      </div>
      <p data-testid="settings-ai-confidence-hint" className="-mt-2 text-[11px] text-slate-500">
        El modelo potente termina su análisis con un % de confianza de éxito. Si supera el {form.ai_min_confidence} % la operación se ejecuta; si no, se rechaza y se sigue como operación sombra para saber si el umbral es demasiado estricto.
        Un par ya analizado no se vuelve a enviar al modelo potente hasta que cierra la vela de {form.timeframe}.
      </p>
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
        <AtrLevelsSettings form={form} set={set} />
        <AiPositionReviewSettings form={form} set={set} />
      </div>
    </Panel>
  );
}
