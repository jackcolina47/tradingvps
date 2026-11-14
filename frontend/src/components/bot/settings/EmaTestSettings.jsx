import { AlertTriangle, FlaskConical } from "lucide-react";
import { Switch } from "@/components/ui/switch";

const CHIP = "relative rounded-sm border px-2 py-1 font-mono text-[11px] transition-colors";

export default function EmaTestSettings({ form, set, meta }) {
  if (!("ema_test_enabled" in form)) return null;
  const on = !!form.ema_test_enabled;
  const syms = form.ema_test_symbols || [];
  const toggleSym = (s) => set("ema_test_symbols", syms.includes(s) ? syms.filter((x) => x !== s) : [...syms, s]);
  return (
    <div data-testid="ema-test-card" className={`rounded-sm border-2 p-3 transition-colors ${on ? "border-fuchsia-500/70 bg-fuchsia-500/[0.07] shadow-[0_0_24px_-8px_rgba(217,70,239,0.6)]" : "border-fuchsia-500/30 bg-fuchsia-500/[0.03]"}`}>
      <label className="flex cursor-pointer items-center justify-between gap-3">
        <span className="flex items-start gap-2">
          <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-fuchsia-300" />
          <span>
            <span className="block font-display text-[13px] font-semibold uppercase tracking-wide text-fuchsia-200">Test Cruce EMA 10/20 <span className="ml-1 rounded-sm bg-fuchsia-500/30 px-1 text-[9px] text-fuchsia-100">EXPERIMENTAL</span></span>
            <span className="block text-[11px] text-slate-400">EMA10 cruza EMA20 al cierre de la vela ({form.timeframe}) → largo al alza, corto a la baja. Sin SL ni TP: cierra y se invierte en el siguiente cruce contrario.</span>
          </span>
        </span>
        <Switch data-testid="settings-ema-test-enabled-switch" checked={on} onCheckedChange={(v) => set("ema_test_enabled", v)} className="data-[state=checked]:bg-fuchsia-500" />
      </label>
      <p data-testid="ema-test-warning" className="mt-2 flex items-start gap-1.5 rounded-sm border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Riesgo alto: estas operaciones no tienen stop loss (ni paper ni REAL), ignoran la IA de aprobación, el embudo y los filtros de mercado, y no cuentan en «Máx. posiciones». El tamaño se calcula con un stop virtual de 2×ATR y tu riesgo por operación. Rentabilidad no comprobada: úsalo con capital que puedas arriesgar. La revisión IA de posiciones abiertas sí las vigila (puede cerrarlas). Al apagar el test, las posiciones abiertas siguen hasta su cruce de cierre.</span>
      </p>
      <div className="mt-2">
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">Pares del test ({syms.length})</p>
        <div className="flex flex-wrap gap-1.5">
          {(meta?.symbols || []).map((s) => (
            <button key={s} data-testid={`settings-ema-test-symbol-${s}`} onClick={() => toggleSym(s)}
              className={`${CHIP} ${syms.includes(s) ? "border-fuchsia-400/70 bg-fuchsia-500/20 text-fuchsia-100" : "border-[#1E293B] text-slate-500 hover:border-[#334155]"}`}>
              {s.replace("USDT", "")}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[11px] text-slate-500">Operan aunque no estén en «Pares a operar». Las operaciones del test llevan la etiqueta «Cruce EMAs Especial» en posiciones e historial.</p>
      </div>
    </div>
  );
}
