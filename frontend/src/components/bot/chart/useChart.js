import { useEffect, useRef } from "react";
import {
  createChart, CandlestickSeries, LineSeries, HistogramSeries, createSeriesMarkers, ColorType, CrosshairMode, LineStyle,
} from "lightweight-charts";

const lineOpts = (color, width = 1) => ({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
// lightweight-charts draws UTC: shift every timestamp by the browser offset so axis + crosshair read in local time (DST-aware per candle)
export const toLocal = (t) => t - new Date(t * 1000).getTimezoneOffset() * 60;

export function shift(points, daily) {
  if (daily) return points; // daily candles are dates (00:00 UTC): keep them as calendar days
  let prev = -Infinity;
  return points.map((p) => {
    let t = toLocal(p.time);
    if (t <= prev) t = prev + 1;
    prev = t;
    return { ...p, time: t };
  });
}

// indicators drawn in their own pane below the price, in this order; `levels` = dashed reference lines
const SUB = {
  rsi: { color: "#F472B6", levels: [[70, "rgba(239,68,68,0.45)"], [30, "rgba(16,185,129,0.45)"]] },
  adx: { color: "#A78BFA", levels: [[24, "rgba(247,166,0,0.5)"]] },
  gap: { color: "#2DD4BF", levels: [[0.5, "rgba(148,163,184,0.45)"], [0, "rgba(148,163,184,0.25)"], [-0.5, "rgba(148,163,184,0.45)"]] },
};

function syncPanes(a, ind, d) {
  const want = Object.keys(SUB).filter((k) => ind[k]);
  if (want.join() === a.subKey) return;
  Object.values(a.sub).forEach((s) => a.chart.removeSeries(s));
  try { while (a.chart.panes().length > 1) a.chart.removePane(a.chart.panes().length - 1); } catch (e) { /* already gone */ }
  a.sub = {};
  want.forEach((k, i) => {
    const s = a.chart.addSeries(LineSeries, { ...lineOpts(SUB[k].color), lastValueVisible: true, priceFormat: { type: "price", precision: 2, minMove: 0.01 } }, i + 1);
    SUB[k].levels.forEach(([price, color]) => s.createPriceLine({ price, color, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false }));
    if (d) s.setData(shift(d[k] || [], a.daily));
    a.sub[k] = s;
  });
  try { a.chart.panes().forEach((p, i) => p.setStretchFactor(i ? 1 : 8 - want.length)); } catch (e) { /* pane api optional */ }
  a.subKey = want.join();
}

export function useChart(ref, onNeedOlder) {
  const api = useRef(null);
  const need = useRef(onNeedOlder);
  need.current = onNeedOlder;
  useEffect(() => {
    const chart = createChart(ref.current, {
      autoSize: true,
      localization: { locale: "es-ES" },
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#94A3B8", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, panes: { separatorColor: "#1E293B" } },
      grid: { vertLines: { color: "rgba(30,41,59,0.45)" }, horzLines: { color: "rgba(30,41,59,0.45)" } },
      rightPriceScale: { borderColor: "#1E293B" },
      timeScale: { borderColor: "#1E293B", timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: "#10B981", downColor: "#EF4444", borderVisible: false, wickUpColor: "#10B981", wickDownColor: "#EF4444" });
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const a = { chart, candles, vol, e9: chart.addSeries(LineSeries, lineOpts("#F7A600")), e21: chart.addSeries(LineSeries, lineOpts("#38BDF8")),
      e200: chart.addSeries(LineSeries, lineOpts("#CBD5E1", 2)), sub: {}, subKey: null, markers: createSeriesMarkers(candles, []), lines: [] };
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r && r.from < 80) need.current?.(); });
    api.current = a;
    return () => { chart.remove(); api.current = null; };
  }, [ref]);
  return api;
}

export function render(a, d, lines, tf) {
  const daily = tf === "1d";
  a.daily = daily;
  a.chart.timeScale().applyOptions({ timeVisible: !daily });
  const digits = Number.isInteger(d.digits) ? d.digits : 2;
  if (a.digits !== digits) {  // axis + crosshair labels with the instrument's real precision (EURUSD 0.70xxx, USDJPY 157.9xx, XAUUSD 2 dec.)
    a.digits = digits;
    const priceFormat = { type: "price", precision: digits, minMove: Number((10 ** -digits).toFixed(digits)) };
    [a.candles, a.e9, a.e21, a.e200].forEach((s) => s.applyOptions({ priceFormat }));
  }
  a.candles.setData(shift(d.candles, daily));
  a.vol.setData(shift(d.volume, daily));
  a.e9.setData(shift(d.ema9, daily));
  a.e21.setData(shift(d.ema21, daily));
  a.e200.setData(shift(d.ema200, daily));
  Object.entries(a.sub).forEach(([k, s]) => s.setData(shift(d[k] || [], daily)));
  a.markers.setMarkers(shift(d.markers, daily));
  if (lines) {
    a.lines.forEach((l) => a.candles.removePriceLine(l));
    a.lines = lines.map((l) => a.candles.createPriceLine({ price: l.price, color: l.color, title: l.title, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }));
  }
}

export function applyIndicators(a, ind, d) {
  a.e9.applyOptions({ visible: !!ind.ema9 });
  a.e21.applyOptions({ visible: !!ind.ema21 });
  a.e200.applyOptions({ visible: !!ind.ema200 });
  a.vol.applyOptions({ visible: !!ind.vol });
  syncPanes(a, ind, d);
}

/** New pair / timeframe: a price axis the user dragged keeps the old instrument's range (empty chart) → back to auto-scale. */
export function resetScales(a) {
  a.chart.panes().forEach((p) => { try { p.priceScale("right").applyOptions({ autoScale: true }); } catch (e) { /* pane without right scale */ } });
}
