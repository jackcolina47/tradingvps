import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

const FIELDS = [["atr_sl_mult", "Multiplicador SL (× ATR)", 0.5, 10, 2], ["atr_tp_mult", "Multiplicador TP (× ATR)", 0.5, 20, 4]];

export default function AtrLevelsSettings({ form, set }) {
  const on = !!form.atr_levels_enabled;
  return (
    <div data-testid="settings-atr-levels" className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-3">
      <label className="flex cursor-pointer items-center justify-between gap-3">
        <span>
          <span className="block text-[13px] text-slate-200">SL/TP fijo por ATR</span>
          <span className="block text-[11px] text-slate-500">Encendido: todas las operaciones usan SL = precio ∓ multiplicador × ATR(14) y TP = precio ± multiplicador × ATR(14), así la pérdida es siempre 1R y la ganancia siempre el mismo múltiplo. Apagado: el modelo potente propone el SL y el TP de cada operación. En ambos casos el tamaño se calcula con la distancia al SL y tu «Riesgo por operación».</span>
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
          <p className="text-[11px] text-slate-500 sm:col-span-2">R:R resultante {(Number(form.atr_tp_mult ?? 4) / (Number(form.atr_sl_mult ?? 2) || 1)).toFixed(2)}.</p>
        </div>
      )}
    </div>
  );
}
