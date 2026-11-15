// Drawing tools over the price pane: pure geometry, painting, hit-testing and storage. Points are stored as {t: candle time (unix s, UTC), p: price}.
export const POINTS = { hline: 1, vline: 1, text: 1, channel: 3 };
export const nPoints = (type) => POINTS[type] || 2;
export const COLORS = ["#F7A600", "#38BDF8", "#10B981", "#EF4444", "#F472B6", "#A78BFA", "#F8FAFC", "#94A3B8"];
const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
const FONT = "11px IBM Plex Mono, monospace";

export const storageKey = (symbol) => `chart.drawings.${window.location.pathname.split("/")[1] || "bot"}.${symbol}`;
export function load(symbol) {
  try { return JSON.parse(localStorage.getItem(storageKey(symbol)) || "[]"); } catch (e) { return []; }
}
export const save = (symbol, list) => localStorage.setItem(storageKey(symbol), JSON.stringify(list));

/** time ↔ x through the candle index, so weekend gaps and candles beyond the last one (future) both work. */
export function scale(a, candles) {
  const n = candles.length;
  const step = n > 1 ? candles[n - 1].time - candles[n - 2].time : 60;
  const ts = a.chart.timeScale();
  const index = (t) => {
    if (t >= candles[n - 1].time) return n - 1 + (t - candles[n - 1].time) / step;
    if (t <= candles[0].time) return (t - candles[0].time) / step;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (candles[m].time <= t) lo = m; else hi = m; }
    return lo + (t - candles[lo].time) / (candles[hi].time - candles[lo].time);
  };
  const time = (i) => (i >= n ? candles[n - 1].time + (i - n + 1) * step : i < 0 ? candles[0].time + i * step : candles[i].time);
  return {
    n, candles,
    x: (t) => ts.logicalToCoordinate(index(t)),
    y: (p) => a.main.priceToCoordinate(p),
    idx: (x) => Math.round(ts.coordinateToLogical(x)),
    t: (x) => time(Math.round(ts.coordinateToLogical(x))),
    p: (y) => a.main.coordinateToPrice(y),
    index,
  };
}

export const toScreen = (d, s) => d.pts.map((q) => ({ x: s.x(q.t), y: s.y(q.p) }));

function far(p1, p2) {  // p2 pushed along p1→p2 until well outside the canvas
  const dx = p2.x - p1.x, dy = p2.y - p1.y;
  const k = 6000 / (Math.hypot(dx, dy) || 1);
  return { x: p1.x + dx * k, y: p1.y + dy * k };
}

/** Screen geometry of a drawing: line segments (hit-test + stroke) and optional filled boxes. */
export function geom(d, pts, W, H) {
  const [a, b, c] = pts;
  const seg = (p, q) => [p.x, p.y, q.x, q.y];
  if (!a || a.x == null || a.y == null) return { segs: [], boxes: [] };
  if (d.type === "hline") return { segs: [[0, a.y, W, a.y]], boxes: [] };
  if (d.type === "vline") return { segs: [[a.x, 0, a.x, H]], boxes: [] };
  if (d.type === "text") return { segs: [], boxes: [[a.x, a.y - 14, Math.max(30, (d.text || "").length * 7), 18]] };
  if (!b || b.x == null || b.y == null) return { segs: [], boxes: [] };
  if (d.type === "ray") return { segs: [seg(a, far(a, b))], boxes: [] };
  if (d.type === "rect" || d.type === "measure") {
    const box = [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y)];
    return { segs: d.type === "measure" ? [seg(a, b)] : [[box[0], box[1], box[0] + box[2], box[1]], [box[0], box[1] + box[3], box[0] + box[2], box[1] + box[3]], [box[0], box[1], box[0], box[1] + box[3]], [box[0] + box[2], box[1], box[0] + box[2], box[1] + box[3]]], boxes: [box] };
  }
  if (d.type === "fib") {
    const x0 = Math.min(a.x, b.x);
    return { segs: FIB.map((l) => { const y = b.y + (a.y - b.y) * l; return [x0, y, W, y]; }).concat([seg(a, b)]), boxes: [] };
  }
  if (d.type === "channel") {
    if (!c || c.y == null) return { segs: [seg(a, b)], boxes: [] };
    const dy = c.y - (a.y + (b.y - a.y) * ((c.x - a.x) / ((b.x - a.x) || 1)));
    return { segs: [seg(a, b), [a.x, a.y + dy, b.x, b.y + dy]], boxes: [], poly: [[a.x, a.y], [b.x, b.y], [b.x, b.y + dy], [a.x, a.y + dy]] };
  }
  if (d.type === "long" || d.type === "short") {
    const x0 = Math.min(a.x, b.x), w = Math.max(Math.abs(b.x - a.x), 40);
    const ty = c && c.y != null ? c.y : a.y - (b.y - a.y) * 2;
    return { segs: [[x0, a.y, x0 + w, a.y]], boxes: [[x0, Math.min(a.y, b.y), w, Math.abs(b.y - a.y), "rgba(239,68,68,0.18)"], [x0, Math.min(a.y, ty), w, Math.abs(ty - a.y), "rgba(16,185,129,0.18)"]] };
  }
  return { segs: [seg(a, b)], boxes: [] };  // trend, arrow
}

const distSeg = (x, y, [x1, y1, x2, y2]) => {
  const dx = x2 - x1, dy = y2 - y1;
  const k = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / ((dx * dx + dy * dy) || 1)));
  return Math.hypot(x - x1 - k * dx, y - y1 - k * dy);
};

/** → {id, handle} of the top-most drawing under the pointer (handle = point index, or -1 for the body). */
export function hit(list, s, x, y, W, H, selectedId) {
  for (let i = list.length - 1; i >= 0; i--) {
    const d = list[i];
    const pts = toScreen(d, s);
    if (d.id === selectedId) {
      const h = pts.findIndex((q) => q.x != null && q.y != null && Math.hypot(q.x - x, q.y - y) < 9);
      if (h >= 0) return { id: d.id, handle: h };
    }
    const g = geom(d, pts, W, H);
    const inBox = g.boxes.some(([bx, by, bw, bh]) => x >= bx && x <= bx + bw && y >= by && y <= by + bh);
    if (g.segs.some((sg) => distSeg(x, y, sg) < 6) || (inBox && d.type !== "rect")) return { id: d.id, handle: -1 };
  }
  return null;
}

const fmt = (v, digits) => Number(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const alpha = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};

function label(ctx, text, x, y, color, bg = "rgba(7,9,14,0.85)") {
  ctx.font = FONT;
  const w = ctx.measureText(text).width + 8;
  ctx.fillStyle = bg;
  ctx.fillRect(x, y - 13, w, 16);
  ctx.fillStyle = color;
  ctx.fillText(text, x + 4, y - 1);
}

export function paint(ctx, d, s, W, H, digits, selected) {
  const pts = toScreen(d, s);
  const g = geom(d, pts, W, H);
  const [a, b, c] = pts;
  const color = d.color || COLORS[0];
  ctx.save();
  ctx.lineWidth = d.width || 1;
  ctx.strokeStyle = color;
  ctx.fillStyle = alpha(color, 0.12);
  if (g.poly) { ctx.beginPath(); g.poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.fill(); }
  if (d.type !== "text") g.boxes.forEach(([x, y, w, h, fill]) => { ctx.fillStyle = fill || alpha(color, 0.12); ctx.fillRect(x, y, w, h); });
  if (d.type === "measure") ctx.setLineDash([4, 3]);
  g.segs.forEach(([x1, y1, x2, y2], i) => {
    ctx.globalAlpha = d.type === "fib" && i === g.segs.length - 1 ? 0.35 : 1;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  });
  ctx.globalAlpha = 1;
  ctx.setLineDash([]);
  if (d.type === "arrow" && b && b.x != null) {
    const ang = Math.atan2(b.y - a.y, b.x - a.x), r = 9 + (d.width || 1) * 2;
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(b.x, b.y);
    ctx.lineTo(b.x - r * Math.cos(ang - 0.4), b.y - r * Math.sin(ang - 0.4));
    ctx.lineTo(b.x - r * Math.cos(ang + 0.4), b.y - r * Math.sin(ang + 0.4));
    ctx.closePath(); ctx.fill();
  }
  if (d.type === "hline" && a) label(ctx, fmt(d.pts[0].p, digits), W - 90, a.y - 3, color);
  if (d.type === "text" && a && a.x != null) { ctx.font = `${12 + (d.width || 1) * 2}px IBM Plex Sans, sans-serif`; ctx.fillStyle = color; ctx.fillText(d.text || "", a.x, a.y); }
  if (d.type === "fib" && b && b.x != null) {
    const x0 = Math.min(a.x, b.x);
    FIB.forEach((l) => label(ctx, `${l} · ${fmt(d.pts[1].p + (d.pts[0].p - d.pts[1].p) * l, digits)}`, x0 + 4, b.y + (a.y - b.y) * l - 2, color, "rgba(7,9,14,0.6)"));
  }
  if (d.type === "measure" && b && b.x != null) {
    const dp = d.pts[1].p - d.pts[0].p;
    const bars = Math.round(s.index(d.pts[1].t) - s.index(d.pts[0].t));
    label(ctx, `${dp >= 0 ? "+" : ""}${fmt(dp, digits)} (${dp >= 0 ? "+" : ""}${((dp / d.pts[0].p) * 100).toFixed(2)}%) · ${bars} velas`, Math.min(a.x, b.x), Math.min(a.y, b.y) - 4, dp >= 0 ? "#10B981" : "#EF4444");
  }
  if ((d.type === "long" || d.type === "short") && b && b.x != null) {
    const entry = d.pts[0].p, stop = d.pts[1].p, target = d.pts[2] ? d.pts[2].p : entry - (stop - entry) * 2;
    const x0 = Math.min(a.x, b.x);
    const ty = c && c.y != null ? c.y : s.y(target);
    const pct = (v) => `${(Math.abs(v - entry) / entry * 100).toFixed(2)}%`;
    label(ctx, `Objetivo ${fmt(target, digits)} (${pct(target)})`, x0 + 4, Math.min(ty, a.y) + 16, "#10B981");
    label(ctx, `Stop ${fmt(stop, digits)} (${pct(stop)})`, x0 + 4, Math.max(b.y, a.y) - 4, "#EF4444");
    label(ctx, `${d.type === "long" ? "LARGO" : "CORTO"} ${fmt(entry, digits)} · R:R ${(Math.abs(target - entry) / (Math.abs(stop - entry) || 1)).toFixed(2)}`, x0 + 4, a.y - 3, "#F8FAFC");
  }
  if (selected) {
    pts.forEach((q, i) => {
      if (q.x == null || q.y == null) return;
      const hx = (d.type === "long" || d.type === "short") && i === 2 && b ? b.x : q.x;
      ctx.fillStyle = "#07090E"; ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(hx, q.y, 4.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    });
  }
  ctx.restore();
}

/** Snap a price to the nearest open/high/low/close of the candle under the pointer when it is closer than 14 px. */
export function magnet(s, x, price) {
  const k = s.candles[s.idx(x)];
  if (!k) return price;
  const y = s.y(price);
  const best = [k.open, k.high, k.low, k.close].map((v) => [Math.abs(s.y(v) - y), v]).sort((m, n) => m[0] - n[0])[0];
  return best[0] < 14 ? best[1] : price;
}
