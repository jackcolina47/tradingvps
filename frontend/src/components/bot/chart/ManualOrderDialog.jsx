import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { http, errMsg, fmtPrice } from "@/lib/api";

const MULTS = [2, 4, 5, 6, 8];
const INPUT = "num h-9 w-full rounded-sm border border-[#1E293B] bg-[#07090E] px-2 text-sm text-slate-100 outline-none focus:border-amber-500/60";
const LABEL = "mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500";

export default function ManualOrderDialog({ side, symbol, price, atr, digits, tf, trade, onClose }) {
  const d = side === "long" ? 1 : -1;
  const level = (k, dir) => (price && atr ? Number((price + dir * k * atr).toFixed(digits ?? 5)) : "");
  const [slK, setSlK] = useState(2);
  const [tpK, setTpK] = useState(4);
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [risk, setRisk] = useState(trade.riskPct);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!side) return;
    setSlK(2); setTpK(4); setRisk(trade.riskPct);
    setSl(level(2, -d)); setTp(level(4, d));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [side, symbol]);

  if (!side) return null;
  const long = side === "long";
  const dist = Math.abs(price - Number(sl));
  const rr = dist > 0 ? Math.abs(Number(tp) - price) / dist : 0;
  const riskUsd = (trade.equity || 0) * (Number(risk) || 0) / 100;
  const noTp = !!trade.noTp;  // EMA-only bot: no take profit, the trade leaves on the opposite EMA cross
  const valid = atr > 0 && Number(sl) > 0 && (price - Number(sl)) * d > 0 && (noTp || (Number(tp) > 0 && (Number(tp) - price) * d > 0)) && Number(risk) >= 0.1 && Number(risk) <= 5;
  const submit = async () => {
    setBusy(true);
    try {
      await http.post("/manual-order", { symbol, side, sl: Number(sl), ...(noTp ? {} : { tp: Number(tp) }), atr, risk_pct: Number(risk) });
      toast.success(`${long ? "Compra" : "Venta"} manual de ${symbol} abierta`);
      trade.onDone?.();
      onClose();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  const mult = (id, value, onChange) => (
    <select data-testid={id} value={value} onChange={(e) => onChange(Number(e.target.value))} className={`${INPUT} font-mono text-xs`}>
      {MULTS.map((m) => <option key={m} value={m}>{m} × ATR</option>)}
    </select>
  );

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent data-testid="manual-order-dialog" className="z-[80] max-w-md border-[#1E293B] bg-[#0D111A] text-slate-200">
        <DialogHeader>
          <DialogTitle className={long ? "text-emerald-400" : "text-rose-400"}>{long ? "Comprar" : "Vender"} {symbol} · manual</DialogTitle>
          <DialogDescription className="text-slate-400">
            Orden a mercado a ~{fmtPrice(price, digits)}. ATR(14) en {tf}: {fmtPrice(atr, digits)}. Sin IA, reglas ni filtros; se aplican los límites de riesgo del bot.
          </DialogDescription>
        </DialogHeader>
        {trade.live && <p data-testid="manual-order-live-warning" className="rounded-sm border border-rose-500/50 bg-rose-500/10 px-3 py-2 text-[12px] font-semibold text-rose-200">Modo REAL: esta orden se envía al bróker con dinero real.</p>}
        <div className="grid grid-cols-2 gap-3">
          <label><span className={LABEL}>Stop loss · distancia</span>{mult("manual-order-sl-mult", slK, (k) => { setSlK(k); setSl(level(k, -d)); })}</label>
          <label><span className={LABEL}>Precio SL</span><input data-testid="manual-order-sl-input" type="number" step="any" value={sl} onChange={(e) => setSl(e.target.value)} className={INPUT} /></label>
          {!noTp && <><label><span className={LABEL}>Take profit · distancia</span>{mult("manual-order-tp-mult", tpK, (k) => { setTpK(k); setTp(level(k, d)); })}</label>
          <label><span className={LABEL}>Precio TP</span><input data-testid="manual-order-tp-input" type="number" step="any" value={tp} onChange={(e) => setTp(e.target.value)} className={INPUT} /></label></>}
          <label className="col-span-2"><span className={LABEL}>Riesgo de la operación (% de la equidad)</span>
            <input data-testid="manual-order-risk-input" type="number" step={0.1} min={0.1} max={5} value={risk} onChange={(e) => setRisk(e.target.value)} className={INPUT} /></label>
        </div>
        <p data-testid="manual-order-summary" className="font-mono text-[11px] text-slate-400">
          Riesgo ≈ {riskUsd.toFixed(2)} {trade.currency} si salta el SL · {noTp ? "sin TP: se cierra en el cruce contrario de las EMAs" : `R:R ${rr.toFixed(2)}`} · tamaño = riesgo ÷ distancia al SL, ajustado al lote mínimo del bróker al enviar.
        </p>
        {!valid && <p className="text-[11px] text-rose-300">{atr > 0 ? `Revisa los niveles: el SL debe quedar ${long ? "por debajo" : "por encima"} del precio${noTp ? "" : " y el TP al otro lado"}; riesgo entre 0,1 y 5%.` : "Sin ATR disponible para este par todavía."}</p>}
        <div className="flex justify-end gap-2">
          <button data-testid="manual-order-cancel" onClick={onClose} className="rounded-sm border border-[#1E293B] px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-slate-100">Cancelar</button>
          <button data-testid="manual-order-confirm" disabled={!valid || busy} onClick={submit}
            className={`flex items-center gap-1.5 rounded-sm px-3 py-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-[#07090E] transition-colors disabled:opacity-50 ${long ? "bg-emerald-500 hover:bg-emerald-400" : "bg-rose-500 hover:bg-rose-400"}`}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {long ? "Comprar" : "Vender"}{trade.live ? " (REAL)" : ""}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
