import { useEffect, useState } from "react";
import { fmtPrice } from "@/lib/api";
import { OVERLAYS, SUBS } from "./useChart";

const num = (v, digits) => (v == null ? "—" : Number(v).toFixed(digits));

/** OHLC + the value of every visible indicator on the candle under the crosshair (last candle when the pointer is outside). */
export default function ChartLegend({ api, ready, data, ind, style, offset }) {
  const [idx, setIdx] = useState(null);
  useEffect(() => {
    const a = api.current;
    if (!a) return undefined;
    const fn = (p) => setIdx(p.time != null && Number.isFinite(p.logical) ? Math.round(p.logical) : null);
    a.chart.subscribeCrosshairMove(fn);
    return () => { try { a.chart.unsubscribeCrosshairMove(fn); } catch (e) { /* chart already removed */ } };
  }, [api, ready]);
  const n = data?.candles?.length || 0;
  if (!n) return null;
  const i = idx != null && idx >= 0 && idx < n ? idx : n - 1;
  const k = data.candles[i];
  const prev = data.candles[i - 1]?.close ?? k.open;
  const chg = prev ? ((k.close - prev) / prev) * 100 : 0;
  const up = k.close >= k.open;
  const col = up ? style.up : style.down;
  const val = (key) => data[key]?.[i]?.value;
  return (
    <div data-testid="chart-legend" className="pointer-events-none absolute top-1.5 z-10 flex max-w-[calc(100%-120px)] flex-wrap gap-x-2.5 gap-y-0.5 font-mono text-[10px] leading-tight" style={{ left: offset }}>
      <span data-testid="chart-legend-ohlc" className="text-slate-500">
        O <b style={{ color: col }}>{fmtPrice(k.open, data.digits)}</b> H <b style={{ color: col }}>{fmtPrice(k.high, data.digits)}</b> L <b style={{ color: col }}>{fmtPrice(k.low, data.digits)}</b> C <b style={{ color: col }}>{fmtPrice(k.close, data.digits)}</b>{" "}
        <b style={{ color: chg >= 0 ? style.up : style.down }}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</b>
      </span>
      {Object.entries(OVERLAYS).filter(([key]) => ind[key]).map(([key, def]) => (
        <span key={key} data-testid={`chart-legend-${key}`} style={{ color: style.lines[key].color }}>{def.label} {def.keys.map((s) => fmtPrice(val(s), data.digits)).join(" / ")}</span>
      ))}
      {Object.entries(SUBS).filter(([key]) => ind[key]).map(([key, def]) => (
        <span key={key} data-testid={`chart-legend-${key}`} style={{ color: style.lines[key].color }}>{def.label.split(" (")[0]} {def.keys.map((s) => num(val(s), def.hist ? Math.max(2, data.digits) : 2)).join(" / ")}</span>
      ))}
    </div>
  );
}
