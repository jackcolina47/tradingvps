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

function addRsi(chart) {
  const rsi = chart.addSeries(LineSeries, { ...lineOpts("#F472B6"), lastValueVisible: true }, 1);
  rsi.createPriceLine({ price: 70, color: "rgba(239,68,68,0.45)", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false });
  rsi.createPriceLine({ price: 30, color: "rgba(16,185,129,0.45)", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false });
  try { chart.panes()[1].setHeight(90); } catch (e) { /* pane api optional */ }
  return rsi;
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
      e200: chart.addSeries(LineSeries, lineOpts("#CBD5E1", 2)), rsi: null, markers: createSeriesMarkers(candles, []), lines: [] };
    a.rsi = addRsi(chart);
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
  if (a.rsi) a.rsi.setData(shift(d.rsi, daily));
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
  if (ind.rsi && !a.rsi) {
    a.rsi = addRsi(a.chart);
    if (d) a.rsi.setData(shift(d.rsi, a.daily));
  } else if (!ind.rsi && a.rsi) {
    a.chart.removeSeries(a.rsi);
    a.rsi = null;
    try { if (a.chart.panes().length > 1) a.chart.removePane(1); } catch (e) { /* already gone */ }
  }
}
