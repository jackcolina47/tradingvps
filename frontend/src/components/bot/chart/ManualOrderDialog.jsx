import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { http, errMsg, fmtPrice } from "@/lib/api";

const MULTS = [1, 1.5, 2, 3, 4, 5, 6, 8];
const REVIEW_MIN = [5, 10, 15, 20, 25, 30];
const INPUT = "num h-9 w-full rounded-sm border border-[#1E293B] bg-[#07090E] px-2 text-sm text-slate-100 outline-none focus:border-amber-500/60 disabled:opacity-40";
const LABEL = "mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500";
const inR = (v) => Number(v) >= 0.5 && Number(v) <= 3;

/** Exit rules of the bot settings: the starting values of the order, editable for this trade only. */
const fromSettings = (s = {}) => ({
  breakeven_enabled: s.breakeven_enabled ?? true, be_start_r: s.be_start_r ?? 1,
  trailing_enabled: s.trailing_enabled ?? true, trail_start_r: s.trail_start_r ?? 1.5, trail_atr_mult: s.trail_atr_mult ?? 1,
  partial_tp_enabled: s.partial_tp_enabled ?? false, partial_tp_r: s.partial_tp_r ?? 1.5, partial_tp_pct: s.partial_tp_pct ?? 50,
  ai_review: false, ai_review_minutes: 15,
});

function Rule({ id, label, hint, on, onToggle, children }) {
  return (
    <div data-testid={`manual-order-${id}`} className={`rounded-sm border p-2.5 transition-colors ${on ? "border-amber-500/40 bg-amber-500/[0.04]" : "border-[#1E293B]"}`}>
      <label className="flex cursor-pointer items-start gap-2">
        <input data-testid={`manual-order-${id}-toggle`} type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} className="mt-0.5 h-4 w-4 accent-amber-500" />
        <span className="min-w-0">
          <span className="block text-[12px] font-semibold text-slate-100">{label}</span>
          <span className="block text-[10px] leading-snug text-slate-500">{hint}</span>
        </span>
      </label>
      {on && <div className="mt-2 grid grid-cols-2 gap-2">{children}</div>}
    </div>
  );
}

export default function ManualOrderDialog({ side, symbol, price, atr, digits, tf, trade, onClose }) {
  const d = side === "long" ? 1 : -1;
  const level = (k, dir) => (price && atr ? Number((price + dir * k * atr).toFixed(digits ?? 5)) : "");
  const [slK, setSlK] = useState(2);
  const [tpK, setTpK] = useState(4);
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [risk, setRisk] = useState(trade.riskPct);
  const [r, setR] = useState(() => fromSettings(trade.settings));
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setR((x) => ({ ...x, [k]: v }));
  const num = (k, id, label, props) => (
    <label><span className={LABEL}>{label}</span>
      <input data-testid={`manual-order-${id}`} type="number" value={r[k]} onChange={(e) => set(k)(e.target.value)} className={INPUT} {...props} /></label>
  );

  useEffect(() => {
    if (!side) return;
    setSlK(2); setTpK(4); setRisk(trade.riskPct); setR(fromSettings(trade.settings));
    setSl(level(2, -d)); setTp(level(4, d));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [side, symbol]);

  if (!side) return null;
  const long = side === "long";
  const dist = Math.abs(price - Number(sl));
  const rr = dist > 0 ? Math.abs(Number(tp) - price) / dist : 0;
  const riskUsd = (trade.equity || 0) * (Number(risk) || 0) / 100;
  const levelsOk = atr > 0 && Number(sl) > 0 && Number(tp) > 0 && (price - Number(sl)) * d > 0 && (Number(tp) - price) * d > 0 && Number(risk) >= 0.1 && Number(risk) <= 5;
  const rulesOk = (!r.breakeven_enabled || inR(r.be_start_r)) && (!r.trailing_enabled || (inR(r.trail_start_r) && inR(r.trail_atr_mult)))
    && (!r.partial_tp_enabled || (inR(r.partial_tp_r) && Number(r.partial_tp_pct) >= 25 && Number(r.partial_tp_pct) <= 75));
  const valid = levelsOk && rulesOk;
  const submit = async () => {
    setBusy(true);
    const body = { symbol, side, sl: Number(sl), tp: Number(tp), atr, risk_pct: Number(risk),
      breakeven_enabled: r.breakeven_enabled, trailing_enabled: r.trailing_enabled, partial_tp_enabled: r.partial_tp_enabled,
      ...(r.breakeven_enabled ? { be_start_r: Number(r.be_start_r) } : {}),
      ...(r.trailing_enabled ? { trail_start_r: Number(r.trail_start_r), trail_atr_mult: Number(r.trail_atr_mult) } : {}),
      ...(r.partial_tp_enabled ? { partial_tp_r: Number(r.partial_tp_r), partial_tp_pct: Math.round(Number(r.partial_tp_pct)) } : {}),
      ...(r.ai_review ? { ai_review_minutes: Number(r.ai_review_minutes) } : {}) };
    try {
      await http.post("/manual-order", body);
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
  const R = { step: 0.25, min: 0.5, max: 3 };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent data-testid="manual-order-dialog" className="z-[80] max-h-[92dvh] w-[calc(100vw-1rem)] max-w-xl overflow-y-auto border-[#1E293B] bg-[#0D111A] text-slate-200 thin-scroll">
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
          <label><span className={LABEL}>Take profit · distancia</span>{mult("manual-order-tp-mult", tpK, (k) => { setTpK(k); setTp(level(k, d)); })}</label>
          <label><span className={LABEL}>Precio TP</span><input data-testid="manual-order-tp-input" type="number" step="any" value={tp} onChange={(e) => setTp(e.target.value)} className={INPUT} /></label>
          <label className="col-span-2"><span className={LABEL}>Riesgo de la operación (% de la equidad)</span>
            <input data-testid="manual-order-risk-input" type="number" step={0.1} min={0.1} max={5} value={risk} onChange={(e) => setRisk(e.target.value)} className={INPUT} /></label>
        </div>
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-amber-400/90">Gestión de esta operación · parte de los ajustes actuales del bot</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Rule id="breakeven" label="Breakeven" hint="Mueve el stop a la entrada al alcanzar el nivel." on={r.breakeven_enabled} onToggle={set("breakeven_enabled")}>
            <div className="col-span-2">{num("be_start_r", "be-r", "Activar a (R)", R)}</div>
          </Rule>
          <Rule id="trailing" label="Trailing stop" hint="El stop sigue al precio a una distancia fija en ATR." on={r.trailing_enabled} onToggle={set("trailing_enabled")}>
            {num("trail_start_r", "trail-r", "Desde (R)", R)}
            {num("trail_atr_mult", "trail-atr", "Distancia (ATR)", R)}
          </Rule>
          <Rule id="partial" label="Cierre parcial" hint="Cierra una parte al alcanzar el nivel y deja correr el resto." on={r.partial_tp_enabled} onToggle={set("partial_tp_enabled")}>
            {num("partial_tp_r", "partial-r", "Cerrar a (R)", R)}
            {num("partial_tp_pct", "partial-pct", "Parte (%)", { step: 5, min: 25, max: 75 })}
          </Rule>
          <Rule id="ai-review" label="Revisión automática de IA" hint="La IA revisa solo esta operación (mantener, breakeven o cerrar) con el modo y la confianza mínima de Ajustes." on={r.ai_review} onToggle={set("ai_review")}>
            <label className="col-span-2"><span className={LABEL}>Revisar cada</span>
              <select data-testid="manual-order-ai-review-minutes" value={r.ai_review_minutes} onChange={(e) => set("ai_review_minutes")(Number(e.target.value))} className={`${INPUT} font-mono text-xs`}>
                {REVIEW_MIN.map((m) => <option key={m} value={m}>{m} minutos</option>)}
              </select></label>
          </Rule>
        </div>
        <p data-testid="manual-order-summary" className="font-mono text-[11px] text-slate-400">
          Riesgo ≈ {riskUsd.toFixed(2)} {trade.currency} si salta el SL · R:R {rr.toFixed(2)} · tamaño = riesgo ÷ distancia al SL, ajustado al lote mínimo del bróker al enviar.
        </p>
        {!levelsOk && <p className="text-[11px] text-rose-300">{atr > 0 ? `Revisa los niveles: el SL debe quedar ${long ? "por debajo" : "por encima"} del precio y el TP al otro lado; riesgo entre 0,1 y 5%.` : "Sin ATR disponible para este par todavía."}</p>}
        {levelsOk && !rulesOk && <p data-testid="manual-order-rules-error" className="text-[11px] text-rose-300">Revisa la gestión: niveles en R y distancia en ATR entre 0,5 y 3; cierre parcial entre 25 y 75 %.</p>}
        <div className="flex justify-end gap-2">
          <button data-testid="manual-order-cancel" onClick={onClose} className="rounded-sm border border-[#1E293B] px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-slate-100">Cancelar</button>
          <button data-testid="manual-order-confirm" disabled={!valid || busy} onClick={submit}
            className={`flex items-center gap-1.5 rounded-sm px-4 py-2 font-mono text-[11px] font-bold uppercase tracking-wider text-[#07090E] transition-colors disabled:opacity-50 ${long ? "bg-emerald-500 hover:bg-emerald-400" : "bg-rose-500 hover:bg-rose-400"}`}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {long ? "Comprar" : "Vender"}{trade.live ? " (REAL)" : ""}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
