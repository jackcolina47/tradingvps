// Supply / demand zones: a small base (1-3 narrow candles) followed by a strong impulse candle (body ≥ 1.5 × ATR).
// Demand = base before a rally, supply = base before a drop. A zone dies when a candle closes through it.
const IMPULSE_ATR = 1.5, BASE_ATR = 0.6, MAX_HEIGHT_ATR = 2, MAX_PER_SIDE = 4;

export function computeZones(candles, atr) {
  const n = candles.length, out = [];
  const body = (k) => Math.abs(k.close - k.open);
  for (let i = 2; i < n - 1; i++) {
    const a = atr[i]?.value, k = candles[i];
    if (!(a > 0) || body(k) < IMPULSE_ATR * a || body(k) < 0.6 * (k.high - k.low)) continue;
    const up = k.close > k.open;
    let j = i - 1;
    if (body(candles[j]) > BASE_ATR * a) continue;
    while (j > i - 3 && j > 0 && body(candles[j - 1]) <= BASE_ATR * a) j--;
    const base = candles.slice(j, i);
    const top = up ? Math.max(...base.map((b) => Math.max(b.open, b.close))) : Math.max(...base.map((b) => b.high));
    const bottom = up ? Math.min(...base.map((b) => b.low)) : Math.min(...base.map((b) => Math.min(b.open, b.close)));
    if (!(top > bottom) || top - bottom > MAX_HEIGHT_ATR * a) continue;
    let broken = false, touches = 0, away = false;
    for (let m = i + 1; m < n; m++) {
      const c = candles[m];
      if (up ? c.close < bottom : c.close > top) { broken = true; break; }
      const inside = up ? c.low <= top : c.high >= bottom;
      if (inside && away) { touches++; away = false; } else if (!inside) away = true;
    }
    if (!broken) out.push({ type: up ? "demand" : "supply", t: candles[j].time, top, bottom, touches });
  }
  const pick = (type) => out.filter((z) => z.type === type).reverse()
    .filter((z, i, list) => !list.slice(0, i).some((o) => o.bottom <= z.top && z.bottom <= o.top)).slice(0, MAX_PER_SIDE);
  return [...pick("demand"), ...pick("supply")];
}

const COLOR = { demand: "16,185,129", supply: "239,68,68" };

export function paintZones(g, zones, s, W, digits) {
  zones.forEach((z) => {
    const x = Math.max(0, s.x(z.t) ?? 0), y1 = s.y(z.top), y2 = s.y(z.bottom);
    if (y1 == null || y2 == null || x >= W) return;
    const rgb = COLOR[z.type], fresh = z.touches === 0;
    g.fillStyle = `rgba(${rgb},${fresh ? 0.16 : 0.08})`;
    g.fillRect(x, y1, W - x, Math.max(1, y2 - y1));
    g.strokeStyle = `rgba(${rgb},0.55)`;
    g.lineWidth = 1;
    g.setLineDash(fresh ? [] : [4, 3]);
    g.strokeRect(x + 0.5, y1 + 0.5, W - x, Math.max(1, y2 - y1));
    g.setLineDash([]);
    g.font = "10px ui-monospace, monospace";
    g.textAlign = "right";
    g.textBaseline = "middle";
    g.fillStyle = `rgba(${rgb},0.95)`;
    g.fillText(`${z.type === "demand" ? "DEMANDA" : "OFERTA"} ${z.bottom.toFixed(digits)}–${z.top.toFixed(digits)}${fresh ? "" : ` · ${z.touches} toque${z.touches > 1 ? "s" : ""}`}`, W - 6, (y1 + y2) / 2);
  });
}
