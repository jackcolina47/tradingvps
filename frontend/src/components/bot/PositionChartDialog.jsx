import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import PriceChart from "./PriceChart";
import { http, errMsg, fmtPrice, fmtDateTime } from "@/lib/api";

const FX_TFS = ["5m", "15m", "1h", "4h", "1d"];
const fxBot = () => /\/api\/(fx|pepper)$/.test(http.defaults.baseURL || "");

/** Open position on the main chart (live candles, indicators, drawings, timeframes) with its entry / SL lines. */
export default function PositionChartDialog({ position, onClose }) {
  const [tf, setTf] = useState(null);
  const [err, setErr] = useState("");
  const [fs, setFs] = useState(false);  // chart in full screen: the dialog itself takes the whole viewport
  const box = useRef(null);
  const id = position?.id;
  useEffect(() => {
    if (!id) return;
    setTf(null); setErr(""); setFs(false);
    http.get("/settings").then((r) => setTf(r.data.timeframe)).catch((e) => setErr(errMsg(e)));
  }, [id]);
  const focus = useMemo(() => (position ? {
    opened: Math.floor(new Date(position.opened_at).getTime() / 1000), closed: Math.floor(Date.now() / 1000), hideTp: position.exit_mode === "cross",
  } : null), [id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={!!position} onOpenChange={(o) => !o && onClose()}>
      <DialogContent ref={box} data-testid="position-chart-dialog" onEscapeKeyDown={(e) => fs && e.preventDefault()}
        className={`thin-scroll border-[#1E293B] bg-[#0D111A] p-4 sm:p-6 ${fs ? "h-screen max-h-screen w-screen max-w-none overflow-hidden sm:rounded-none" : "max-h-[94vh] w-[97vw] max-w-6xl overflow-y-auto"}`}>
        <DialogHeader>
          <DialogTitle className="font-display text-slate-100">{position?.symbol} · {position?.side === "long" ? "LONG" : "SHORT"}</DialogTitle>
          <DialogDescription className="font-mono text-[11px] text-slate-400">
            Abierta {position ? fmtDateTime(position.opened_at) : ""} · entrada {fmtPrice(position?.entry_price)} · SL {fmtPrice(position?.sl)}
          </DialogDescription>
        </DialogHeader>
        {!tf && !err && <div className="flex h-40 items-center justify-center gap-2 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Cargando gráfico…</div>}
        {err && <p data-testid="position-chart-error" className="px-4 py-10 text-center text-xs text-rose-400">{err}</p>}
        {tf && position && (
          <div data-testid="position-chart-canvas">
            <PriceChart symbol={position.symbol} defaultTf={tf} kind="Posición abierta" tfs={fxBot() ? FX_TFS : undefined} focus={focus}
              onFs={(v) => { if (v && box.current) box.current.scrollTop = 0; setFs(v); }} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
