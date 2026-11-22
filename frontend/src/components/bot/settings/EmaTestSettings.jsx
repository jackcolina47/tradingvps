import { AlertTriangle, FlaskConical } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";

const CHIP = "relative rounded-sm border px-2 py-1 font-mono text-[11px] transition-colors";

const CONFIRM = [
  ["ema_test_confirm_adx", "ADX mayor que", 1, 5, 60, 24],
  ["ema_test_confirm_dist_atr", "Distancia máx. EMAs (×ATR)", 0.1, 0.1, 3, 0.5],
  ["ema_test_confirm_bars", "Plazo tras el cruce (velas)", 1, 1, 100, 20],
];

const EXITS = [
  ["partial", "Cierre parcial: N % a N × ATR", "Cuando la operación va a favor el múltiplo de ATR indicado, cierra ese porcentaje de la posición con beneficios. El resto sigue abierto. Si el lote mínimo no permite dividir la posición, el parcial se omite. No mueve el stop: para eso usa Breakeven o Trailing.",
    [["ema_partial_pct", "Porcentaje a cerrar (%)", 5, 50], ["ema_partial_at_atr", "Activar a + (×ATR)", 0.1, 1.5]]],
  ["trail", "Trailing: N × ATR", "El stop sigue al precio a la distancia indicada (en ATR de la vela de entrada) y solo se mueve a favor, nunca hacia atrás. Actúa desde la entrada: si la distancia es menor que la del SL, el stop se acerca desde el primer momento.",
    [["ema_trail_atr", "Distancia del stop (×ATR)", 0.1, 2]]],
  ["be", "Breakeven: N × ATR", "Cuando la operación va a favor el múltiplo de ATR indicado, mueve el stop a la entrada más el spread, para que no pueda acabar en pérdida.",
    [["ema_be_atr", "Activar a + (×ATR)", 0.1, 1]]],
];

export default function EmaTestSettings({ form, set, meta, main = false }) {
  if (!("ema_test_enabled" in form)) return null;
  const on = main || !!form.ema_test_enabled;
  const fx = !!meta?.sessions;
  const syms = form.ema_test_symbols || [];
  const toggleSym = (s) => set("ema_test_symbols", syms.includes(s) ? syms.filter((x) => x !== s) : [...syms, s]);
  return (
    <div data-testid="ema-test-card" className={`rounded-sm border-2 p-3 transition-colors ${on ? "border-fuchsia-500/70 bg-fuchsia-500/[0.07] shadow-[0_0_24px_-8px_rgba(217,70,239,0.6)]" : "border-fuchsia-500/30 bg-fuchsia-500/[0.03]"}`}>
      {main ? (
        <div data-testid="ema-main-header" className="flex items-start gap-2">
          <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-fuchsia-300" />
          <span>
            <span className="block font-display text-[13px] font-semibold uppercase tracking-wide text-fuchsia-200">Cruce EMA 9 y 21 Personalizado <span className="ml-1 rounded-sm bg-fuchsia-500/30 px-1 text-[9px] text-fuchsia-100">ESTRATEGIA ÚNICA</span></span>
            <span className="block text-[11px] text-slate-400">EMA9 cruza EMA21 al cierre de la vela ({form.timeframe}) → largo al alza, corto a la baja, en todos los «Pares a operar». Sin TP: la operación se cierra en el siguiente cruce contrario (o antes por el SL de seguridad). Sin IA en entradas ni en la gestión.</span>
          </span>
        </div>
      ) : <>
      <label className="flex cursor-pointer items-center justify-between gap-3">
        <span className="flex items-start gap-2">
          <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-fuchsia-300" />
          <span>
            <span className="block font-display text-[13px] font-semibold uppercase tracking-wide text-fuchsia-200">Test Cruce EMA 9/21 <span className="ml-1 rounded-sm bg-fuchsia-500/30 px-1 text-[9px] text-fuchsia-100">EXPERIMENTAL</span></span>
            <span className="block text-[11px] text-slate-400">EMA9 cruza EMA21 al cierre de la vela ({form.timeframe}) → largo al alza, corto a la baja. Sin TP ni stop normal (solo SL de seguridad 3×ATR): cierra y se invierte en el siguiente cruce contrario.</span>
          </span>
        </span>
        <Switch data-testid="settings-ema-test-enabled-switch" checked={on} onCheckedChange={(v) => set("ema_test_enabled", v)} className="data-[state=checked]:bg-fuchsia-500" />
      </label>
      <p data-testid="ema-test-warning" className="mt-2 flex items-start gap-1.5 rounded-sm border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Riesgo alto: estas operaciones no tienen take profit ni stop normal, solo un SL de seguridad a 3×ATR (ni paper ni REAL), ignoran la IA de aprobación, el embudo{fx ? ", las sesiones permitidas" : ""} y los filtros de mercado, y no cuentan en «Máx. posiciones». El tamaño se calcula con ese SL de 3×ATR y tu riesgo por operación. Rentabilidad no comprobada: úsalo con capital que puedas arriesgar. Son inmunes también a la revisión IA de posiciones abiertas. Al apagar el test, las posiciones abiertas siguen hasta su cruce de cierre.{fx ? " Solo el límite de pérdida diaria y el cierre de fin de semana (si está activo) las cierran antes." : ""}</span>
      </p>
      <div className="mt-2">
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">Pares del test ({syms.length})</p>
        <div className="flex flex-wrap gap-1.5">
          {(meta?.symbols || []).map((s) => (
            <button key={s} data-testid={`settings-ema-test-symbol-${s}`} onClick={() => toggleSym(s)} title={meta?.symbol_info?.[s]?.name}
              className={`${CHIP} ${syms.includes(s) ? "border-fuchsia-400/70 bg-fuchsia-500/20 text-fuchsia-100" : "border-[#1E293B] text-slate-500 hover:border-[#334155]"}`}>
              {s.replace("USDT", "")}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-slate-500">Pulsa un par para encenderlo o apagarlo en el test. Operan aunque no estén en «Pares a operar». Las operaciones del test llevan la etiqueta «Cruce EMAs Especial» en posiciones e historial.</p>
      </div>
      </>}
      {main && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-sm border border-[#1E293B] bg-[#07090E]/60 p-2.5">
          <div className="min-w-[180px] flex-1">
            <p className="text-sm text-slate-200">Stop loss (N × ATR)</p>
            <p className="text-[11px] text-slate-500">Distancia del SL desde la entrada en múltiplos del ATR de la vela de entrada. Con él se calcula el tamaño de la posición según tu riesgo por operación. Rango 0.5–10.</p>
          </div>
          <Input data-testid="settings-ema-sl-atr-input" type="number" step={0.25} min={0.5} max={10} value={form.ema_sl_atr ?? 3}
            onChange={(e) => set("ema_sl_atr", e.target.value)} className="num h-8 w-24 border-[#1E293B] bg-[#07090E] text-xs" />
        </div>
      )}
      {main && (
        <div className="mt-3 rounded-sm border border-[#1E293B] bg-[#07090E]/60 p-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-[180px] flex-1">
              <p className="text-sm text-slate-200">Cierre por toque de la EMA 21</p>
              <p className="text-[11px] text-slate-500">Con la operación abierta, si una vela cierra habiendo tocado la EMA 21 (la EMA queda entre el mínimo y el máximo de la vela), la posición se cierra sin esperar al cruce contrario. Solo cuentan las velas que cierran después de la entrada.</p>
            </div>
            <Switch data-testid="settings-ema-touch-exit-switch" checked={!!form.ema_touch_exit_enabled} onCheckedChange={(v) => set("ema_touch_exit_enabled", v)} className="data-[state=checked]:bg-fuchsia-500" />
          </div>
        </div>
      )}
      {!main && <div className="mt-3 rounded-sm border border-[#1E293B] bg-[#07090E]/60 p-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm text-slate-200">Filtro de lateral (ADX)</p>
            <p className="text-[11px] text-slate-500">Ignora los cruces de entrada cuando el ADX de la vela está por debajo del umbral (mercado sin tendencia = serrucho). La salida en el cruce contrario no se filtra: una posición abierta se cierra igual y, si el ADX es bajo, no se invierte hasta el siguiente cruce con tendencia.</p>
          </div>
          <Switch data-testid="settings-ema-test-adx-filter-switch" checked={!!form.ema_test_adx_filter} onCheckedChange={(v) => set("ema_test_adx_filter", v)} className="data-[state=checked]:bg-fuchsia-500" />
        </div>
        {form.ema_test_adx_filter && (
          <div className="mt-2 flex items-center gap-3">
            <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">ADX mínimo</p>
            <Input data-testid="settings-ema-test-adx-min-input" type="number" step={1} min={5} max={60} value={form.ema_test_adx_min ?? 20}
              onChange={(e) => set("ema_test_adx_min", e.target.value)} className="num h-8 w-24 border-[#1E293B] bg-[#07090E] text-xs" />
            <p className="text-[11px] text-slate-500">Típico 20–25 (ADX 14). Compruébalo en Backtest con «Test cruce EMA 9/21» antes y después.</p>
          </div>
        )}
      </div>}
      <div className="mt-3 rounded-sm border border-[#1E293B] bg-[#07090E]/60 p-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm text-slate-200">Entrada confirmada (ADX + EMAs cercanas)</p>
            <p className="text-[11px] text-slate-500">Cambia el criterio de entrada: tras un cruce EMA9/21 espera a la primera vela con ADX por encima del umbral y entra en el sentido de la EMA10 solo si en ese momento las EMAs están a menos de la distancia indicada. Si están más lejos, o pasa el plazo de velas sin ADX suficiente, el cruce se descarta. La salida no cambia.{main ? "" : " Con esto encendido el «Filtro de lateral (ADX)» de arriba no se usa."}</p>
          </div>
          <Switch data-testid="settings-ema-test-confirm-switch" checked={!!form.ema_test_confirm_enabled} onCheckedChange={(v) => set("ema_test_confirm_enabled", v)} className="data-[state=checked]:bg-fuchsia-500" />
        </div>
        {form.ema_test_confirm_enabled && (
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {CONFIRM.map(([k, label, step, min, max, def]) => (
              <label key={k} className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
                <Input data-testid={`settings-${k.replaceAll("_", "-")}-input`} type="number" step={step} min={min} max={max} value={form[k] ?? def}
                  onChange={(e) => set(k, e.target.value)} className="num h-8 w-full border-[#1E293B] bg-[#07090E] text-xs" />
              </label>
            ))}
            <p className="text-[11px] text-slate-500 sm:col-span-3">Por defecto ADX 24 · 0.5×ATR · 20 velas. Ajusta el plazo de velas a tu temporalidad y compáralo en Backtest.</p>
          </div>
        )}
      </div>
      {main && EXITS.map(([key, title, hint, fields]) => (
        <div key={key} data-testid={`ema-exit-${key}`} className="mt-3 rounded-sm border border-[#1E293B] bg-[#07090E]/60 p-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-[180px] flex-1">
              <p className="text-sm text-slate-200">{title}</p>
              <p className="text-[11px] text-slate-500">{hint}</p>
            </div>
            <Switch data-testid={`settings-ema-${key}-switch`} checked={!!form[`ema_${key}_on`]} onCheckedChange={(v) => set(`ema_${key}_on`, v)} className="data-[state=checked]:bg-fuchsia-500" />
          </div>
          {form[`ema_${key}_on`] && (
            <div className="mt-2 flex flex-wrap items-end gap-3">
              {fields.map(([k, label, step, def]) => (
                <label key={k} className="block">
                  <span className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
                  <Input data-testid={`settings-${k.replaceAll("_", "-")}-input`} type="number" step={step} min={0} value={form[k] ?? def}
                    onChange={(e) => set(k, e.target.value)} className="num h-8 w-28 border-[#1E293B] bg-[#07090E] text-xs" />
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
      {!main && <div className="mt-3 rounded-sm border border-[#1E293B] bg-[#07090E]/60 p-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm text-slate-200">Cierre parcial 50% + breakeven</p>
            <p className="text-[11px] text-slate-500">Cuando la operación va a favor el múltiplo de ATR indicado, cierra el 50% con beneficios y mueve el SL a breakeven (entrada + comisiones). El resto sigue abierto y se cierra solo: por breakeven o en el siguiente cruce contrario. Si el lote mínimo no permite dividir la posición, solo se mueve el SL a breakeven.</p>
          </div>
          <Switch data-testid="settings-ema-test-partial-switch" checked={!!form.ema_test_partial_enabled} onCheckedChange={(v) => set("ema_test_partial_enabled", v)} className="data-[state=checked]:bg-fuchsia-500" />
        </div>
        {form.ema_test_partial_enabled && (
          <div className="mt-2 flex items-center gap-3">
            <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Activar a +×ATR</p>
            <Input data-testid="settings-ema-test-partial-atr-input" type="number" step={0.1} min={0.5} max={3} value={form.ema_test_partial_atr ?? 1.5}
              onChange={(e) => set("ema_test_partial_atr", e.target.value)} className="num h-8 w-24 border-[#1E293B] bg-[#07090E] text-xs" />
            <p className="text-[11px] text-slate-500">Recomendado: la mitad del múltiplo del SL. Rango 0.5–3. Compáralo en Backtest con el interruptor encendido y apagado.</p>
          </div>
        )}
      </div>}
    </div>
  );
}
