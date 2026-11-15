import { Settings2, RotateCcw } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CHART_TYPES, DEFAULT_STYLE, OVERLAYS, SUBS } from "./useChart";

const SWATCH = "h-5 w-7 cursor-pointer rounded-sm border border-[#1E293B] bg-transparent p-0";

/** Chart type, candle colours, and colour + width of every indicator line. */
export default function ChartStyle({ style, setStyle }) {
  const line = (k, patch) => setStyle((s) => ({ ...s, lines: { ...s.lines, [k]: { ...s.lines[k], ...patch } } }));
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button data-testid="chart-style-btn" title="Tipo de gráfico, colores y grosores" className="rounded-sm border border-[#1E293B] p-1 text-slate-400 transition-colors hover:text-slate-100"><Settings2 className="h-3.5 w-3.5" /></button>
      </PopoverTrigger>
      <PopoverContent align="end" data-testid="chart-style-popover" className="z-[90] max-h-[70vh] w-72 overflow-y-auto border-[#1E293B] bg-[#0B0F17] p-3 text-slate-200">
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">Tipo de gráfico</p>
        <div className="grid grid-cols-2 gap-1">
          {CHART_TYPES.map(([k, label]) => (
            <button key={k} data-testid={`chart-type-${k}`} onClick={() => setStyle((s) => ({ ...s, type: k }))}
              className={`rounded-sm border px-2 py-1 text-[11px] transition-colors ${style.type === k ? "border-amber-500 bg-amber-500/15 text-amber-300" : "border-[#1E293B] text-slate-400 hover:text-slate-100"}`}>{label}</button>
          ))}
        </div>
        <p className="mb-1.5 mt-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Velas</p>
        <div className="flex items-center gap-3 text-[11px]">
          <label className="flex items-center gap-1.5">Alcista <input data-testid="chart-color-up" type="color" value={style.up} onChange={(e) => setStyle((s) => ({ ...s, up: e.target.value }))} className={SWATCH} /></label>
          <label className="flex items-center gap-1.5">Bajista <input data-testid="chart-color-down" type="color" value={style.down} onChange={(e) => setStyle((s) => ({ ...s, down: e.target.value }))} className={SWATCH} /></label>
        </div>
        <p className="mb-1.5 mt-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Indicadores · color y grosor</p>
        <div className="space-y-1">
          {Object.entries({ ...OVERLAYS, ...SUBS }).map(([k, def]) => (
            <div key={k} className="flex items-center justify-between gap-2 text-[11px]">
              <span className="truncate">{def.label}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                <input data-testid={`chart-line-color-${k}`} type="color" value={style.lines[k].color} onChange={(e) => line(k, { color: e.target.value })} className={SWATCH} />
                <select data-testid={`chart-line-width-${k}`} value={style.lines[k].width} onChange={(e) => line(k, { width: Number(e.target.value) })} className="h-5 rounded-sm border border-[#1E293B] bg-[#07090E] px-1 font-mono text-[10px]">
                  {[1, 2, 3, 4].map((w) => <option key={w} value={w}>{w}px</option>)}
                </select>
              </span>
            </div>
          ))}
        </div>
        <button data-testid="chart-style-reset" onClick={() => setStyle(DEFAULT_STYLE)} className="mt-3 flex items-center gap-1 text-[11px] text-slate-400 transition-colors hover:text-slate-100"><RotateCcw className="h-3 w-3" /> Restaurar colores por defecto</button>
      </PopoverContent>
    </Popover>
  );
}
