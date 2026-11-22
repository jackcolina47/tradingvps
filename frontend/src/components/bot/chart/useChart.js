import { useEffect, useRef } from "react";
import {
  createChart, CandlestickSeries, BarSeries, LineSeries, HistogramSeries, createSeriesMarkers, ColorType, CrosshairMode, LineStyle,
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

// lines drawn over the price; `keys` = payload series sharing one toggle/style
export const OVERLAYS = {
  ema9: { label: "EMA 9", color: "#F7A600", width: 1, keys: ["ema9"] },
  ema10: { label: "EMA 10", color: "#FB923C", width: 1, keys: ["ema10"] },
  ema20: { label: "EMA 20", color: "#22D3EE", width: 1, keys: ["ema20"] },
  ema21: { label: "EMA 21", color: "#38BDF8", width: 1, keys: ["ema21"] },
  ema50: { label: "EMA 50", color: "#C084FC", width: 1, keys: ["ema50"] },
  ema200: { label: "EMA 200", color: "#CBD5E1", width: 2, keys: ["ema200"] },
  bb: { label: "Bollinger 20,2", color: "#94A3B8", width: 1, keys: ["bb_up", "bb_mid", "bb_low"] },
  vwap: { label: "VWAP", color: "#FACC15", width: 1, keys: ["vwap"] },
};
// indicators in their own pane below the price, in this order; `levels` = dashed reference lines; the first key takes the user's style
export const SUBS = {
  rsi: { label: "RSI 14", color: "#F472B6", width: 1, keys: ["rsi"], levels: [[70, "rgba(239,68,68,0.45)"], [30, "rgba(16,185,129,0.45)"]] },
  macd: { label: "MACD 12,26,9", color: "#60A5FA", width: 1, keys: ["macd", "macd_signal"], hist: "macd_hist", levels: [] },
  adx: { label: "ADX 14", color: "#A78BFA", width: 1, keys: ["adx"], levels: [[24, "rgba(247,166,0,0.5)"]] },
  atr: { label: "ATR 14", color: "#FB7185", width: 1, keys: ["atr"], fine: true, levels: [] },
  gap: { label: "Dist. EMA 9/21 (×ATR)", color: "#2DD4BF", width: 1, keys: ["gap"], levels: [[0.5, "rgba(148,163,184,0.45)"], [0, "rgba(148,163,184,0.25)"], [-0.5, "rgba(148,163,184,0.45)"]] },
};
export const DEFAULT_STYLE = {
  type: "candles", up: "#10B981", down: "#EF4444",
  lines: Object.fromEntries(Object.entries({ ...OVERLAYS, ...SUBS }).map(([k, v]) => [k, { color: v.color, width: v.width }])),
};
export const CHART_TYPES = [["candles", "Velas"], ["heikin", "Heikin Ashi"], ["bars", "Barras"], ["line", "Línea"]];
const SIGNAL_COLOR = "#F97316";

function heikin(c) {
  let po = 0, pc = 0;
  return c.map((k, i) => {
    const close = (k.open + k.high + k.low + k.close) / 4;
    const open = i ? (po + pc) / 2 : (k.open + k.close) / 2;
    po = open; pc = close;
    return { time: k.time, open, high: Math.max(k.high, open, close), low: Math.min(k.low, open, close), close };
  });
}

function mainData(candles, type) {
  if (type === "heikin") return heikin(candles);
  if (type === "line") return candles.map((k) => ({ time: k.time, value: k.close }));
  return candles;
}

function addMain(a, style) {
  const { up, down } = style;
  if (a.main) a.chart.removeSeries(a.main);
  a.main = style.type === "line" ? a.chart.addSeries(LineSeries, { color: up, lineWidth: 2, priceLineVisible: true })
    : style.type === "bars" ? a.chart.addSeries(BarSeries, { upColor: up, downColor: down })
      : a.chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
  a.mainType = style.type;
  a.markers = createSeriesMarkers(a.main, []);
  a.lines = [];
  a.digits = null;
}

const fineFormat = (digits) => { const p = Math.max(2, digits ?? 2); return { type: "price", precision: p, minMove: Number((10 ** -p).toFixed(p)) }; };

function syncPanes(a, ind, d, style) {
  const want = Object.keys(SUBS).filter((k) => ind[k]);
  if (want.join() === a.subKey) return;
  Object.values(a.sub).flat().forEach((s) => a.chart.removeSeries(s.series));
  try { while (a.chart.panes().length > 1) a.chart.removePane(a.chart.panes().length - 1); } catch (e) { /* already gone */ }
  a.sub = {};
  const fmt = { type: "price", precision: 2, minMove: 0.01 };
  const fine = fineFormat(a.digits);
  want.forEach((k, i) => {
    const def = SUBS[k];
    const st = style.lines[k] || def;
    const list = [];
    if (def.hist) list.push({ key: def.hist, series: a.chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, priceFormat: fine }, i + 1), fine: true });
    def.keys.forEach((key, j) => {
      const series = a.chart.addSeries(LineSeries, { ...lineOpts(j ? SIGNAL_COLOR : st.color, j ? 1 : st.width), lastValueVisible: !j, priceFormat: def.hist || def.fine ? fine : fmt }, i + 1);
      if (!j) def.levels.forEach(([price, color]) => series.createPriceLine({ price, color, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false }));
      list.push({ key, series, styled: !j, fine: !!(def.hist || def.fine) });
    });
    if (d) list.forEach((s) => s.series.setData(shift(d[s.key] || [], a.daily)));
    a.sub[k] = list;
  });
  try { a.chart.panes().forEach((p, i) => p.setStretchFactor(i ? 1 : Math.max(3, 8 - want.length))); } catch (e) { /* pane api optional */ }
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
      timeScale: { borderColor: "#1E293B", timeVisible: true, secondsVisible: false, rightOffset: 6 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const a = { chart, vol, over: {}, sub: {}, subKey: null, main: null, lines: [] };
    Object.entries(OVERLAYS).forEach(([k, def]) => {
      a.over[k] = def.keys.map((key) => ({ key, series: chart.addSeries(LineSeries, { ...lineOpts(def.color, def.width), lineStyle: key === "bb_mid" ? LineStyle.Dotted : LineStyle.Solid }) }));
    });
    addMain(a, DEFAULT_STYLE);
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
    [a.main, ...Object.values(a.over).flat().map((s) => s.series)].forEach((s) => s.applyOptions({ priceFormat }));
    Object.values(a.sub).flat().filter((s) => s.fine).forEach((s) => s.series.applyOptions({ priceFormat: fineFormat(digits) }));
    a.lines = [];
  }
  a.main.setData(shift(mainData(d.candles, a.mainType), daily));
  a.vol.setData(shift(d.volume, daily));
  [...Object.values(a.over).flat(), ...Object.values(a.sub).flat()].forEach((s) => s.series.setData(shift(d[s.key] || [], daily)));
  a.markers.setMarkers(shift(d.markers, daily));
  if (lines) {
    a.lines.forEach((l) => { try { a.main.removePriceLine(l); } catch (e) { /* series was replaced */ } });
    a.lines = lines.map((l) => a.main.createPriceLine({ price: l.price, color: l.color, title: l.title, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }));
  }
}

export function applyIndicators(a, ind, d, style) {
  Object.entries(a.over).forEach(([k, list]) => list.forEach((s) => s.series.applyOptions({ visible: !!ind[k] })));
  a.vol.applyOptions({ visible: !!ind.vol });
  syncPanes(a, ind, d, style);
}

/** Chart type, candle colours and each indicator's colour / width. Returns true when the main series was rebuilt (caller must re-render the data). */
export function applyStyle(a, style) {
  const rebuilt = a.mainType !== style.type;
  if (rebuilt) addMain(a, style);
  else if (style.type === "line") a.main.applyOptions({ color: style.up });
  else if (style.type === "bars") a.main.applyOptions({ upColor: style.up, downColor: style.down });
  else a.main.applyOptions({ upColor: style.up, downColor: style.down, wickUpColor: style.up, wickDownColor: style.down });
  Object.entries(a.over).forEach(([k, list]) => list.forEach((s) => s.series.applyOptions({ color: style.lines[k].color, lineWidth: style.lines[k].width })));
  Object.entries(a.sub).forEach(([k, list]) => list.filter((s) => s.styled).forEach((s) => s.series.applyOptions({ color: style.lines[k].color, lineWidth: style.lines[k].width })));
  return rebuilt;
}

/** New pair / timeframe: a price axis the user dragged keeps the old instrument's range (empty chart) → back to auto-scale. */
export function resetScales(a) {
  a.chart.panes().forEach((p) => { try { p.priceScale("right").applyOptions({ autoScale: true }); } catch (e) { /* pane without right scale */ } });
}
