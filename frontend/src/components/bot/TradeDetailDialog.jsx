import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { SideBadge } from "./Panel";
import PriceChart from "./PriceChart";
import TradeNoteEditor from "./TradeNoteEditor";
import TradeAiReview from "./TradeAiReview";
import {
  http, errMsg, fmtPrice, fmtQty, fmtSignedUsd, fmtPct, pnlClass,
  REASON_LABELS, STRATEGY_LABELS, HORIZON_LABELS, ENTRY_LABELS, fmtPips, PIPS_HINT,
  tzOpts, noTp,
} from "@/lib/api";

const fmtFull = (iso) =>
  iso ? new Date(iso).toLocaleString("es-ES", { ...tzOpts(), day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "—";
const fmtDuration = (min) => {
  if (min == null) return "—";
  const m = Math.round(min);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
};
const distPct = (from, to) => (from && to ? `${(Math.abs(to - from) / from * 100).toFixed(2)} %` : "—");
const yesNo = (v) => (v ? "Sí" : "No");
const AI_LABELS = { APROBADA: "Aprobada", RECHAZADA: "Rechazada", SIN_IA: "Sin IA", BLOQUEADA: "Bloqueada" };

const FX_TFS = ["5m", "15m", "1h", "4h", "1d"];
const LEVELS = [["entry_price", "ENTRADA", "#F8FAFC"], ["exit_price", "SALIDA", "#F7A600"], ["tp", "TP", "#10B981"], ["initial_sl", "SL inicial", "#EF4444"], ["sl", "SL final", "#FB7185"]];

/** The main chart (indicators, drawings, timeframes, infinite history) opened on the candles of this closed trade. */
function TradeChart({ trade: t, tf, fx, hideTp, onFs }) {
  const focus = useMemo(() => {
    const seen = new Set();
    const lines = LEVELS.filter(([k]) => !(hideTp && k === "tp") && t[k] && !seen.has(t[k]) && seen.add(t[k])).map(([k, title, color]) => ({ price: t[k], title, color }));
    return { opened: Math.floor(new Date(t.opened_at).getTime() / 1000), closed: Math.floor(new Date(t.closed_at).getTime() / 1000), lines };
  }, [t.id, hideTp]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div data-testid="trade-chart"><PriceChart symbol={t.symbol} defaultTf={tf} kind="Gráfico de la operación" tfs={fx ? FX_TFS : undefined} focus={focus} onFs={onFs} /></div>;
}

const Section = ({ title, children, testId }) => (
  <div data-testid={testId} className="rounded-sm border border-[#1E293B] bg-[#0B0F17]">
    <p className="border-b border-[#1E293B] px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-amber-400/90">{title}</p>
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 px-3 py-2 sm:grid-cols-3">{children}</dl>
  </div>
);
const Row = ({ label, value, className = "", testId }) => (
  <div className="min-w-0">
    <dt className="text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
    <dd data-testid={testId} className={`num truncate text-[12px] text-slate-200 ${className}`} title={typeof value === "string" ? value : undefined}>{value ?? "—"}</dd>
  </div>
);

function TradeDetailBody({ trade: t, signal: s, currency, onSaved, autoAnalyze, tf, fx, onFs }) {
  const hideTp = noTp() || t.exit_mode === "cross";
  const price = (v) => (v == null || v === 0 ? "—" : fmtPrice(v));
  const rr = t.initial_sl && t.tp ? (Math.abs(t.tp - t.entry_price) / Math.abs(t.entry_price - t.initial_sl)).toFixed(2) : null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-sm border border-[#1E293B] bg-[#0B0F17] px-3 py-2">
        <SideBadge side={t.side} />
        <span className="font-display text-sm font-semibold text-slate-100">{t.symbol}</span>
        <span className="text-xs text-slate-400">{STRATEGY_LABELS[t.strategy] || t.strategy}</span>
        <span className="rounded-sm border border-slate-600/60 px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-300">{REASON_LABELS[t.reason] || t.reason}</span>
        <span className="rounded-sm border border-slate-600/60 px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-300">{t.live ? "Real" : "Simulado"}</span>
        <span data-testid="trade-detail-pnl" className={`ml-auto font-display text-base font-semibold ${pnlClass(t.pnl)}`}>
          {fmtSignedUsd(t.pnl)} <span className="text-xs">({fmtPct(t.pnl_pct)})</span>
        </span>
      </div>

      {tf && <TradeChart trade={t} tf={tf} fx={fx} hideTp={hideTp} onFs={onFs} />}
      <TradeAiReview trade={t} autoRun={autoAnalyze} onReviewed={(review) => onSaved?.({ id: t.id, ai_review: review })} />
      <TradeNoteEditor trade={t} onSaved={onSaved} />

      <Section title="Tiempos" testId="trade-detail-times">
        <Row label="Apertura" value={fmtFull(t.opened_at)} testId="trade-detail-opened" />
        <Row label="Cierre" value={fmtFull(t.closed_at)} testId="trade-detail-closed" />
        <Row label="Duración" value={fmtDuration(t.hold_min)} />
        {t.period && <Row label="Periodo" value={t.period} />}
      </Section>

      <Section title="Precios y niveles" testId="trade-detail-prices">
        <Row label="Entrada" value={price(t.entry_price)} />
        <Row label="Salida" value={price(t.exit_price)} />
        {!hideTp && <Row label="Take Profit" value={price(t.tp)} className="text-emerald-300" testId="trade-detail-tp" />}
        <Row label="Stop Loss inicial" value={price(t.initial_sl)} className="text-rose-300" testId="trade-detail-sl" />
        <Row label="Stop Loss final" value={price(t.sl)} className="text-rose-300" />
        <Row label="ATR" value={price(t.atr)} />
        <Row label="Distancia al SL" value={distPct(t.entry_price, t.initial_sl)} />
        {!hideTp && <Row label="Distancia al TP" value={distPct(t.entry_price, t.tp)} />}
        {!hideTp && <Row label="R:R planificado" value={rr ? `1 : ${rr}` : "—"} />}
      </Section>

      <Section title="Tamaño y resultado" testId="trade-detail-size">
        <Row label="Cantidad" value={fmtQty(t.qty)} />
        <Row label="Apalancamiento" value={`${t.leverage}x`} />
        <Row label="Margen" value={`${fmtPrice(t.margin, 2)} ${currency}`} />
        <Row label="Comisiones" value={`${Number(t.fees).toFixed(4)} ${currency}`} />
        <Row label="PnL neto" value={`${fmtSignedUsd(t.pnl)} ${currency}`} className={pnlClass(t.pnl)} />
        <Row label="PnL sobre margen" value={fmtPct(t.pnl_pct)} className={pnlClass(t.pnl)} />
        <Row label="Recorrido en pips" value={<span data-testid="trade-detail-pips" title={PIPS_HINT}>{fmtPips(t.pips)}</span>} className={pnlClass(t.pips)} />
      </Section>

      <Section title="Ejecución y recorrido" testId="trade-detail-exec">
        <Row label="Tipo de entrada" value={ENTRY_LABELS[t.entry_type] || t.entry_type} />
        <Row label="Horizonte" value={HORIZON_LABELS[t.horizon] || t.horizon} />
        <Row label="Breakeven activado" value={yesNo(t.breakeven)} />
        <Row label="Cierre parcial" value={yesNo(t.partial_tp)} />
        <Row label="Máx. a favor (MFE)" value={t.mfe_r != null ? `+${t.mfe_r} R` : "—"} />
        <Row label="Máx. en contra (MAE)" value={t.mae_r != null ? `${t.mae_r} R` : "—"} />
        {!hideTp && <Row label="Camino al TP" value={t.mfe_tp_pct != null ? `${Math.round(t.mfe_tp_pct)} %` : "—"} />}
        {t.order_id && <Row label="ID orden" value={t.order_id} />}
        <Row label="ID operación" value={t.id} className="text-slate-500" />
      </Section>

      {s ? (
        <Section title="Señal de origen" testId="trade-detail-signal">
          <Row label="Fecha señal" value={fmtFull(s.created_at)} />
          <Row label="Timeframe" value={s.timeframe} />
          <Row label="Puntuación" value={`${s.score} / 100`} />
          <Row label="Precio señal" value={price(s.price)} />
          <Row label={hideTp ? "SL señal" : "SL / TP señal"} value={hideTp ? price(s.sl) : `${price(s.sl)} / ${price(s.tp)}`} />
          <Row label="Decisión IA" value={AI_LABELS[s.ai_decision] || s.ai_decision} />
          <Row label="Confianza IA" value={s.ai_confidence ? `${s.ai_confidence} %${s.ai_min_confidence != null ? ` (mín. ${s.ai_min_confidence} %)` : ""}` : "—"} />
          <Row label="Riesgo IA" value={s.ai_risk} />
          {s.reasons?.length > 0 && (
            <div className="col-span-full">
              <dt className="text-[10px] uppercase tracking-wider text-slate-500">Motivos técnicos</dt>
              <dd className="mt-0.5 flex flex-wrap gap-1">
                {s.reasons.map((r, i) => <span key={i} className="rounded-sm border border-slate-600/60 px-1.5 py-0.5 text-[11px] text-slate-300">{r}</span>)}
              </dd>
            </div>
          )}
          {s.ai_reasoning && (
            <div className="col-span-full">
              <dt className="text-[10px] uppercase tracking-wider text-slate-500">Razonamiento IA</dt>
              <dd className="mt-0.5 whitespace-pre-wrap text-[12px] leading-relaxed text-slate-300">{s.ai_reasoning}</dd>
            </div>
          )}
          {s.indicators && Object.keys(s.indicators).length > 0 && (
            <div className="col-span-full">
              <dt className="text-[10px] uppercase tracking-wider text-slate-500">Indicadores en la señal</dt>
              <dd className="mt-0.5 grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
                {Object.entries(s.indicators).map(([k, v]) => (
                  <span key={k} className="num truncate text-[11px] text-slate-300" title={`${k}: ${v}`}><span className="text-slate-500">{k}:</span> {typeof v === "number" ? fmtPrice(v) : String(v)}</span>
                ))}
              </dd>
            </div>
          )}
        </Section>
      ) : (
        <p data-testid="trade-detail-no-signal" className="text-[11px] text-slate-500">Sin señal de origen vinculada (operación manual o anterior a esta versión).</p>
      )}
    </div>
  );
}

export default function TradeDetailDialog({ trade, onClose, onSaved, autoAnalyze = false }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [fs, setFs] = useState(false);  // chart in full screen: the dialog itself takes the whole viewport
  const box = useRef(null);
  const onFs = (v) => { if (v && box.current) box.current.scrollTop = 0; setFs(v); };
  const tradeId = trade?.id;
  useEffect(() => {
    if (!tradeId) return;
    setData(null); setErr(""); setFs(false);
    http.get(`/trades/${tradeId}/detail`).then((r) => setData(r.data)).catch((e) => setErr(errMsg(e)));
  }, [tradeId]);
  const t = trade && data?.trade ? { ...data.trade, note: trade.note ?? data.trade.note, tags: trade.tags ?? data.trade.tags } : trade;
  return (
    <Dialog open={!!trade} onOpenChange={(o) => !o && onClose()}>
      <DialogContent ref={box} data-testid="trade-detail-dialog" onEscapeKeyDown={(e) => fs && e.preventDefault()}
        className={`thin-scroll border-[#1E293B] bg-[#0D111A] text-slate-200 ${fs ? "h-screen max-h-screen w-screen max-w-none overflow-hidden sm:rounded-none" : "max-h-[94vh] w-[97vw] max-w-6xl overflow-y-auto"}`}>
        <DialogHeader>
          <DialogTitle className="font-display text-sm uppercase tracking-[0.14em]">Detalle de la operación</DialogTitle>
          <DialogDescription className="text-xs text-slate-500">Todos los datos registrados para análisis manual.</DialogDescription>
        </DialogHeader>
        {err && <p data-testid="trade-detail-error" className="text-xs text-rose-400">{err}</p>}
        {!data && !err && t && <div className="flex items-center gap-2 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Cargando detalle…</div>}
        {t && (data || err) && <TradeDetailBody trade={t} signal={data?.signal} currency={data?.currency || "USD"} onSaved={onSaved} autoAnalyze={autoAnalyze} tf={data?.tf} fx={data?.fx} onFs={onFs} />}
      </DialogContent>
    </Dialog>
  );
}
