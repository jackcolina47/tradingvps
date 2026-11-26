import { useEffect, useRef, useState } from "react";
import { Check, GripHorizontal, Loader2, X } from "lucide-react";
import { fmtPrice } from "@/lib/api";

const STYLE = { sl: ["SL", "#EF4444", "bg-rose-500"], tp: ["TP", "#10B981", "bg-emerald-500"] };

/** Draggable SL / TP lines over the chart of an open position. Nothing is sent until the user confirms. */
export default function LevelHandles({ api, wrap, digits, levels }) {
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [, tick] = useState(0);
  const drag = useRef(null);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 120);   // follow zoom / scroll of the price scale
    return () => clearInterval(id);
  }, []);
  useEffect(() => setDraft(null), [levels.sl, levels.tp]);
  const a = api.current;
  if (!a?.main) return null;
  const cur = { sl: draft?.sl ?? levels.sl, tp: draft?.tp ?? levels.tp };
  const changed = draft && (draft.sl !== levels.sl || draft.tp !== levels.tp);
  const move = (e) => {
    if (!drag.current || !wrap.current) return;
    const price = a.main.coordinateToPrice(e.clientY - wrap.current.getBoundingClientRect().top);
    if (price > 0) setDraft({ ...cur, [drag.current]: Number(price.toFixed(digits ?? 5)) });
  };
  const apply = async () => {
    setBusy(true);
    const ok = await levels.onSave({ sl: cur.sl, tp: cur.tp });
    setBusy(false);
    if (!ok) setDraft(null);
  };
  return (
    <>
      {Object.entries(STYLE).map(([k, [name, color, bg]]) => {
        const y = a.main.priceToCoordinate(cur[k]);
        if (y == null || y < 0) return null;
        return (
          <div key={k} data-testid={`level-handle-${k}`} title={`Arrastra para mover el ${name}`} style={{ top: y, touchAction: "none" }}
            onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = k; }} onPointerMove={move} onPointerUp={() => { drag.current = null; }}
            className="absolute left-0 right-[64px] z-20 flex h-5 -translate-y-1/2 cursor-ns-resize items-center">
            <span className="h-0 flex-1 border-t-2 border-dashed" style={{ borderColor: color }} />
            <span className={`absolute left-12 flex items-center gap-1 rounded-sm px-1.5 py-px font-mono text-[10px] font-bold text-[#07090E] ${bg}`}>
              <GripHorizontal className="h-3 w-3" /> {name} <span data-testid={`level-handle-${k}-price`}>{fmtPrice(cur[k], digits)}</span>
            </span>
          </div>
        );
      })}
      {changed && (
        <div data-testid="level-confirm-bar" className="absolute left-1/2 top-2 z-30 flex -translate-x-1/2 flex-wrap items-center justify-center gap-2 rounded-sm border border-amber-500/50 bg-[#0D111A]/95 px-3 py-1.5 font-mono text-[11px] text-slate-200 shadow-lg">
          <span>SL <span className="text-rose-400">{fmtPrice(cur.sl, digits)}</span> · TP <span className="text-emerald-400">{fmtPrice(cur.tp, digits)}</span>{levels.live ? " · REAL" : ""}</span>
          <button data-testid="level-confirm-apply" disabled={busy} onClick={apply} className="inline-flex items-center gap-1 rounded-sm bg-amber-500 px-2 py-1 font-bold uppercase text-[#07090E] hover:bg-amber-400 disabled:opacity-50">
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />} Aplicar
          </button>
          <button data-testid="level-confirm-cancel" disabled={busy} onClick={() => setDraft(null)} className="inline-flex items-center gap-1 rounded-sm border border-[#1E293B] px-2 py-1 uppercase text-slate-300 hover:text-slate-100">
            <X className="h-3 w-3" /> Cancelar
          </button>
        </div>
      )}
    </>
  );
}
