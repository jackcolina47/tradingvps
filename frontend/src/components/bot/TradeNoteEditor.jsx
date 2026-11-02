import { useEffect, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { http, errMsg } from "@/lib/api";

export const SUGGESTED_TAGS = ["Buena entrada", "Entrada tardía", "Salida prematura", "SL muy ajustado", "Contra tendencia", "Noticias", "Revisar"];

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export default function TradeNoteEditor({ trade, onSaved }) {
  const [note, setNote] = useState(trade.note || "");
  const [tags, setTags] = useState(trade.tags || []);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setNote(trade.note || ""); setTags(trade.tags || []); }, [trade.id, trade.note, trade.tags]);
  const dirty = note !== (trade.note || "") || !same(tags, trade.tags || []);
  const toggle = (t) => setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t].slice(0, 10)));
  const addCustom = () => { const v = custom.trim().slice(0, 30); if (v && !tags.includes(v)) setTags((c) => [...c, v].slice(0, 10)); setCustom(""); };
  const save = async () => {
    setBusy(true);
    try {
      const r = await http.put(`/trades/${trade.id}/note`, { note, tags });
      onSaved?.(r.data);
      toast.success("Nota guardada");
    } catch (e) { toast.error(errMsg(e)); }
    setBusy(false);
  };
  return (
    <div data-testid="trade-note-editor" className="rounded-sm border border-[#1E293B] bg-[#0B0F17]">
      <p className="border-b border-[#1E293B] px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-amber-400/90">Mis notas y etiquetas</p>
      <div className="space-y-2 px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {[...SUGGESTED_TAGS, ...tags.filter((t) => !SUGGESTED_TAGS.includes(t))].map((t) => {
            const on = tags.includes(t);
            return (
              <button key={t} type="button" data-testid={`trade-tag-${t.toLowerCase().replace(/\s+/g, "-")}`} onClick={() => toggle(t)}
                className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors ${on ? "border-amber-500/50 bg-amber-500/15 text-amber-200" : "border-slate-600/60 text-slate-400 hover:bg-[#111827]"}`}>
                {t}{on && <X className="h-2.5 w-2.5" />}
              </button>
            );
          })}
          <span className="inline-flex items-center gap-1">
            <input data-testid="trade-tag-custom-input" value={custom} onChange={(e) => setCustom(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addCustom())}
              placeholder="Otra etiqueta…" maxLength={30} className="h-6 w-28 rounded-sm border border-[#1E293B] bg-[#07090E] px-1.5 font-mono text-[10px] text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none" />
            <button type="button" data-testid="trade-tag-custom-add" onClick={addCustom} className="rounded-sm border border-[#1E293B] p-1 text-slate-400 hover:bg-[#111827]"><Plus className="h-3 w-3" /></button>
          </span>
        </div>
        <textarea data-testid="trade-note-input" value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={2000}
          placeholder="Conclusiones del análisis: qué salió bien, qué cambiarías, contexto del mercado…"
          className="w-full resize-y rounded-sm border border-[#1E293B] bg-[#07090E] px-2 py-1.5 text-[12px] leading-relaxed text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none" />
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] text-slate-600">{note.length}/2000</span>
          <button type="button" data-testid="trade-note-save" onClick={save} disabled={!dirty || busy}
            className="inline-flex items-center gap-1.5 rounded-sm bg-amber-500 px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-black transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-40">
            {busy && <Loader2 className="h-3 w-3 animate-spin" />}{dirty || !(trade.note || trade.tags?.length) ? "Guardar nota" : "Guardada"}
          </button>
        </div>
      </div>
    </div>
  );
}
