import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { http, errMsg } from "@/lib/api";
import TradeRulesFields, { rulesState, rulesValid, rulesBody } from "./chart/TradeRulesFields";

/** Edits breakeven / trailing / partial close / own AI review of ONE open position; the bot settings are not touched. */
export default function PositionRulesDialog({ position: p, settings, onClose, onSaved }) {
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const id = p?.id;
  useEffect(() => {
    if (p) setR(rulesState(settings, p.rules, p.ai_review_minutes));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  if (!p || !r) return null;
  const valid = rulesValid(r);
  const save = async () => {
    setBusy(true);
    try {
      await http.put(`/positions/${p.id}/rules`, rulesBody(r));
      toast.success(`Gestión de ${p.symbol} actualizada`);
      onSaved?.();
      onClose();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent data-testid="position-rules-dialog" className="z-[80] max-h-[92dvh] w-[calc(100vw-1rem)] max-w-xl overflow-y-auto border-[#1E293B] bg-[#0D111A] text-slate-200 thin-scroll">
        <DialogHeader>
          <DialogTitle className="text-amber-400">Gestión de {p.symbol} · {p.side === "long" ? "Long" : "Short"}</DialogTitle>
          <DialogDescription className="text-slate-400">
            {p.rules ? "Esta posición ya tiene su propia gestión." : "Esta posición sigue los ajustes del bot."} Lo que guardes aquí vale solo para ella{p.live ? " (posición REAL)" : ""}; los ajustes del bot no cambian.
          </DialogDescription>
        </DialogHeader>
        <TradeRulesFields value={r} onChange={setR} prefix="position-rules" partialDone={p.partial_done} />
        {!valid && <p data-testid="position-rules-error" className="text-[11px] text-rose-300">Niveles en R y distancia en ATR entre 0,5 y 3; cierre parcial entre 25 y 75 %.</p>}
        <div className="flex justify-end gap-2">
          <button data-testid="position-rules-cancel" onClick={onClose} className="rounded-sm border border-[#1E293B] px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-slate-100">Cancelar</button>
          <button data-testid="position-rules-save" disabled={!valid || busy} onClick={save}
            className="flex items-center gap-1.5 rounded-sm bg-amber-500 px-4 py-2 font-mono text-[11px] font-bold uppercase tracking-wider text-[#07090E] transition-colors hover:bg-amber-400 disabled:opacity-50">
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Guardar gestión
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
