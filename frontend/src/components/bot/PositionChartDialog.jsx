import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { createChart, CandlestickSeries, LineSeries, createSeriesMarkers, ColorType, CrosshairMode, LineStyle } from "lightweight-charts";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { http, errMsg, fmtPrice, fmtDateTime } from "@/lib/api";
import { shift } from "./chart/useChart";

const EMAS = [["ema9", "EMA 9", "#F7A600"], ["ema21", "EMA 21", "#38BDF8"], ["ema27", "EMA 27", "#F472B6"]];

function draw(el, d) {
  const chart = createChart(el, {
    autoSize: true,
    localization: { locale: "es-ES" },
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#94A3B8", fontFamily: "IBM Plex Mono, monospace", fontSize: 10 },
    grid: { vertLines: { color: "rgba(30,41,59,0.45)" }, horzLines: { color: "rgba(30,41,59,0.45)" } },
    rightPriceScale: { borderColor: "#1E293B" },
    timeScale: { borderColor: "#1E293B", timeVisible: true, secondsVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
  });
  const priceFormat = { type: "price", precision: d.digits, minMove: Number((10 ** -d.digits).toFixed(d.digits)) };
  const candles = chart.addSeries(CandlestickSeries, { upColor: "#10B981", downColor: "#EF4444", borderVisible: false, wickUpColor: "#10B981", wickDownColor: "#EF4444", priceFormat });
  candles.setData(shift(d.candles, false));
  EMAS.forEach(([k, , color]) => chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, priceFormat }).setData(shift(d[k] || [], false)));
  candles.createPriceLine({ price: d.entry, color: "#F8FAFC", title: "ENTRADA", lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true });
  if (d.sl) candles.createPriceLine({ price: d.sl, color: "#EF4444", title: "SL", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true });
  const long = d.side === "long";
  if (d.candles.some((c) => c.time === d.opened)) {
    createSeriesMarkers(candles, shift([{ time: d.opened, position: long ? "belowBar" : "aboveBar", color: long ? "#10B981" : "#EF4444", shape: long ? "arrowUp" : "arrowDown", text: "Entrada" }], false));
  }
  chart.timeScale().fitContent();
  return chart;
}

export default function PositionChartDialog({ position, onClose }) {
  const ref = useRef(null);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const id = position?.id;
  useEffect(() => {
    if (!id) return;
    setData(null); setErr("");
    http.get(`/positions/${id}/candles`, { timeout: 30000 }).then((r) => setData(r.data)).catch((e) => setErr(errMsg(e)));
  }, [id]);
  useEffect(() => {
    if (!data?.candles?.length || !ref.current) return;
    const chart = draw(ref.current, data);
    return () => chart.remove();
  }, [data]);
  return (
    <Dialog open={!!position} onOpenChange={(o) => !o && onClose()}>
      <DialogContent data-testid="position-chart-dialog" className="max-h-[92vh] w-[96vw] max-w-5xl overflow-y-auto border-[#1E293B] bg-[#0D111A] p-4 sm:p-6">
        <DialogHeader>
          <DialogTitle className="font-display text-slate-100">{position?.symbol} · {position?.side === "long" ? "LONG" : "SHORT"}{data ? ` · velas ${data.tf}` : ""}</DialogTitle>
          <DialogDescription className="font-mono text-[11px] text-slate-400">
            Abierta {position ? fmtDateTime(position.opened_at) : ""} · entrada {fmtPrice(position?.entry_price)} · SL {fmtPrice(position?.sl)}
          </DialogDescription>
        </DialogHeader>
        <div className="relative h-[52vh] min-h-[280px] rounded-sm border border-[#1E293B] bg-[#0B0F17] p-1">
          {!data && !err && <div className="flex h-full items-center justify-center gap-2 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Cargando velas…</div>}
          {err && <p data-testid="position-chart-error" className="flex h-full items-center justify-center px-4 text-center text-xs text-rose-400">{err}</p>}
          {data?.candles?.length > 0 && <div ref={ref} data-testid="position-chart-canvas" className="h-full w-full" />}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-wider text-slate-400">
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-[2px] w-4 bg-slate-50" />Entrada</span>
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-[2px] w-4 bg-rose-500" />SL</span>
          {EMAS.map(([k, label, color]) => <span key={k} className="inline-flex items-center gap-1.5"><span className="inline-block h-[2px] w-4" style={{ background: color }} />{label}</span>)}
        </div>
      </DialogContent>
    </Dialog>
  );
}
