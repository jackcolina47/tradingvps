import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { createChart, CandlestickSeries, createSeriesMarkers, ColorType, CrosshairMode, LineStyle } from "lightweight-charts";
import { api, errMsg } from "@/lib/api";
import { shift } from "./chart/useChart";

const LEVELS = [
  ["entry", "ENTRADA", "#F8FAFC", LineStyle.Solid],
  ["exit", "SALIDA", "#F7A600", LineStyle.Solid],
  ["tp", "TP", "#10B981", LineStyle.Dashed],
  ["initial_sl", "SL inicial", "#EF4444", LineStyle.Dashed],
  ["sl", "SL final", "#FB7185", LineStyle.Dotted],
];

function draw(el, d) {
  const daily = d.tf === "1d";
  const chart = createChart(el, {
    autoSize: true,
    localization: { locale: "es-ES" },
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#94A3B8", fontFamily: "IBM Plex Mono, monospace", fontSize: 10 },
    grid: { vertLines: { color: "rgba(30,41,59,0.45)" }, horzLines: { color: "rgba(30,41,59,0.45)" } },
    rightPriceScale: { borderColor: "#1E293B" },
    timeScale: { borderColor: "#1E293B", timeVisible: !daily, secondsVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
    handleScroll: false, handleScale: false,
  });
  const digits = Number.isInteger(d.digits) ? d.digits : 2;
  const candles = chart.addSeries(CandlestickSeries, {
    upColor: "#10B981", downColor: "#EF4444", borderVisible: false, wickUpColor: "#10B981", wickDownColor: "#EF4444",
    priceFormat: { type: "price", precision: digits, minMove: Number((10 ** -digits).toFixed(digits)) },
  });
  candles.setData(shift(d.candles, daily));
  const seen = new Set();
  LEVELS.forEach(([k, title, color, lineStyle]) => {
    const price = d[k];
    if (price == null || price === 0 || seen.has(price)) return;
    seen.add(price);
    candles.createPriceLine({ price, color, title, lineWidth: 1, lineStyle, axisLabelVisible: true });
  });
  const long = d.side === "long";
  const markers = [
    { time: d.opened, position: long ? "belowBar" : "aboveBar", color: long ? "#10B981" : "#EF4444", shape: long ? "arrowUp" : "arrowDown", text: "Apertura" },
    { time: d.closed, position: long ? "aboveBar" : "belowBar", color: "#F7A600", shape: "circle", text: "Cierre" },
  ].filter((m) => d.candles.some((c) => c.time === m.time));
  createSeriesMarkers(candles, shift(markers, daily));
  chart.timeScale().fitContent();
  return chart;
}

export default function TradeMiniChart({ tradeId }) {
  const ref = useRef(null);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setData(null); setErr("");
    api.get(`/trades/${tradeId}/candles`, { timeout: 30000 }).then((r) => setData(r.data)).catch((e) => setErr(errMsg(e)));
  }, [tradeId]);
  useEffect(() => {
    if (!data?.candles?.length || !ref.current) return;
    const chart = draw(ref.current, data);
    return () => chart.remove();
  }, [data]);
  return (
    <div data-testid="trade-mini-chart" className="rounded-sm border border-[#1E293B] bg-[#0B0F17]">
      <div className="flex items-center justify-between border-b border-[#1E293B] px-3 py-1.5">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-amber-400/90">Gráfico de la operación</p>
        {data && <span data-testid="trade-mini-chart-tf" className="font-mono text-[10px] text-slate-500">velas {data.tf} · {data.candles.length}</span>}
      </div>
      <div className="relative h-56 px-1 py-1">
        {!data && !err && <div className="flex h-full items-center justify-center gap-2 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Cargando velas…</div>}
        {err && <p data-testid="trade-mini-chart-error" className="flex h-full items-center justify-center px-4 text-center text-xs text-rose-400">{err}</p>}
        {data && !data.candles.length && <p data-testid="trade-mini-chart-empty" className="flex h-full items-center justify-center text-xs text-slate-500">Sin velas disponibles para este periodo.</p>}
        {data?.candles?.length > 0 && <div ref={ref} className="h-full w-full" />}
      </div>
      {data?.candles?.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-[#1E293B] px-3 py-1.5 font-mono text-[9px] uppercase tracking-wider text-slate-500">
          {LEVELS.filter(([k]) => data[k]).map(([k, title, color]) => (
            <span key={k} className="inline-flex items-center gap-1"><span className="inline-block h-[2px] w-3" style={{ background: color }} />{title}</span>
          ))}
        </div>
      )}
    </div>
  );
}
