import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

const FIELDS = [["atr_sl_mult", "Multiplicador SL (× ATR)", 0.5, 10, 2], ["atr_tp_mult", "Multiplicador TP (× ATR)", 0.5, 20, 4]];

export default function AtrLevelsSettings({ form, set }) {
  const on = !!form.atr_levels_enabled;
  return (
    <div data-testid="settings-atr-levels" className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-3">
      <label className="flex cursor-pointer items-center justify-between gap-3">
        <span>
          <span className="block text-[13px] text-slate-200">Usar SL/TP dinámico por ATR</span>
          <span className="block text-[11px] text-slate-500">Sustituye los niveles propios de cada estrategia por SL = precio ∓ multiplicador × ATR(14) y TP = precio ± multiplicador × ATR(14), en la temporalidad del bot. El tamaño de la posición se calcula después con esa distancia de SL y tu «Riesgo por operación». No afecta al test de cruce de EMAs ni a las estrategias de largo plazo.</span>
        </span>
        <Switch data-testid="settings-atr-levels-enabled-switch" checked={on} onCheckedChange={(v) => set("atr_levels_enabled", v)} />
      </label>
      {on && (
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {FIELDS.map(([k, label, min, max, def]) => (
            <div key={k}>
              <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
              <Input data-testid={`settings-${k.replaceAll("_", "-")}-input`} type="number" step={0.1} min={min} max={max} value={form[k] ?? def}
                onChange={(e) => set(k, e.target.value)} className="num h-9 border-[#1E293B] bg-[#07090E] text-xs" />
            </div>
          ))}
          <p className="text-[11px] text-slate-500 sm:col-span-2">R:R resultante {(Number(form.atr_tp_mult ?? 4) / (Number(form.atr_sl_mult ?? 2) || 1)).toFixed(2)}. Compáralo en Backtest con el interruptor encendido y apagado antes de usarlo.</p>
        </div>
      )}
    </div>
  );
}
