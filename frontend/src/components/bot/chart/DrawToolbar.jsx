import {
  MousePointer2, Slash, MoveUpRight, Minus, SeparatorVertical, Rows3, Square, Equal, Ruler, TrendingUp, TrendingDown, Type, ArrowUpRight,
  Magnet, Eye, EyeOff, Trash2, Pencil,
} from "lucide-react";
import { COLORS } from "./drawings";

const TOOLS = [
  [null, "Cursor (Esc)", MousePointer2],
  ["trend", "Línea de tendencia", Slash],
  ["ray", "Rayo", MoveUpRight],
  ["hline", "Línea horizontal", Minus],
  ["vline", "Línea vertical", SeparatorVertical],
  ["channel", "Canal paralelo (3 clics)", Equal],
  ["rect", "Rectángulo de zona", Square],
  ["fib", "Retroceso de Fibonacci", Rows3],
  ["measure", "Regla: precio, % y velas", Ruler],
  ["long", "Posición larga (entrada → stop)", TrendingUp],
  ["short", "Posición corta (entrada → stop)", TrendingDown],
  ["arrow", "Flecha", ArrowUpRight],
  ["text", "Texto", Type],
];
const BTN = "flex h-7 w-7 items-center justify-center rounded-sm transition-colors";
const cls = (on) => `${BTN} ${on ? "bg-amber-500 text-[#07090E]" : "text-slate-400 hover:bg-[#1E293B] hover:text-slate-100"}`;

export function DrawToolbar({ dr }) {
  return (
    <div data-chart-ui data-testid="chart-draw-toolbar" className="absolute left-1.5 top-1.5 z-20 flex max-h-[calc(100%-36px)] flex-col gap-0.5 overflow-y-auto rounded-sm border border-[#1E293B] bg-[#0B0F17]/95 p-0.5 shadow-lg">
      {TOOLS.map(([k, label, Icon]) => (
        <button key={k || "cursor"} data-testid={`chart-tool-${k || "cursor"}`} title={label} aria-pressed={dr.tool === k} onClick={() => dr.setTool(k)} className={cls(dr.tool === k)}>
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
      <span className="my-0.5 h-px bg-[#1E293B]" />
      <button data-testid="chart-tool-magnet" title="Imán a máximo / mínimo / apertura / cierre de la vela" aria-pressed={dr.snap} onClick={() => dr.setSnap((v) => !v)} className={cls(dr.snap)}><Magnet className="h-3.5 w-3.5" /></button>
      <button data-testid="chart-tool-hide" title={dr.hidden ? "Mostrar dibujos" : "Ocultar dibujos"} onClick={() => dr.setHidden((v) => !v)} className={cls(dr.hidden)}>{dr.hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}</button>
      <button data-testid="chart-tool-clear" title={`Borrar todos los dibujos de este par (${dr.count})`} disabled={!dr.count}
        onClick={() => window.confirm(`¿Borrar los ${dr.count} dibujos de este par?`) && dr.clear()} className={`${BTN} text-slate-400 hover:bg-rose-500/20 hover:text-rose-300 disabled:opacity-30`}><Trash2 className="h-3.5 w-3.5" /></button>
    </div>
  );
}

export function DrawingBar({ dr }) {
  const d = dr.current;
  if (!d) return null;
  return (
    <div data-chart-ui data-testid="chart-drawing-bar" className="absolute left-1/2 top-9 z-20 flex max-w-[calc(100%-90px)] -translate-x-1/2 flex-wrap items-center gap-1 rounded-sm border border-[#1E293B] bg-[#0B0F17]/95 px-1.5 py-1 shadow-lg">
      {COLORS.map((c) => (
        <button key={c} data-testid={`chart-drawing-color-${c.slice(1)}`} title="Color" onClick={() => dr.update({ color: c })}
          className={`h-4 w-4 rounded-full border-2 transition-transform hover:scale-110 ${d.color === c ? "border-white" : "border-transparent"}`} style={{ background: c }} />
      ))}
      <span className="mx-0.5 h-4 w-px bg-[#1E293B]" />
      {[1, 2, 3, 4].map((w) => (
        <button key={w} data-testid={`chart-drawing-width-${w}`} title={`Grosor ${w}`} onClick={() => dr.update({ width: w })}
          className={`flex h-5 w-6 items-center justify-center rounded-sm ${d.width === w ? "bg-[#1E293B]" : "hover:bg-[#1E293B]/60"}`}>
          <span className="block w-4 rounded-full bg-slate-200" style={{ height: w }} />
        </button>
      ))}
      {d.type === "text" && (
        <button data-testid="chart-drawing-edit-text" title="Editar texto" onClick={() => { const t = window.prompt("Texto de la nota", d.text); if (t && t.trim()) dr.update({ text: t.trim() }); }}
          className={`${BTN} h-5 w-6 text-slate-300 hover:bg-[#1E293B]`}><Pencil className="h-3 w-3" /></button>
      )}
      <span className="mx-0.5 h-4 w-px bg-[#1E293B]" />
      <button data-testid="chart-drawing-delete" title="Eliminar (Supr)" onClick={dr.remove} className={`${BTN} h-5 w-6 text-rose-300 hover:bg-rose-500/20`}><Trash2 className="h-3 w-3" /></button>
    </div>
  );
}
