import { useCallback, useEffect, useRef, useState } from "react";
import { computeZones, paintZones } from "./zones";
import { COLORS, hit, load, magnet, nPoints, paint, save, scale, toScreen } from "./drawings";

const xy = (e, el) => {
  const r = el.getBoundingClientRect();
  const p = e.touches?.[0] || e.changedTouches?.[0] || e;
  return [p.clientX - r.left, p.clientY - r.top];
};
const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);

/** Overlay canvas with TradingView-style drawings, kept per pair in this browser. `wrap` = element holding the chart + canvas. */
export function useDrawings(api, data, symbol, wrap, canvas, zonesOn = false) {
  const zc = useRef({ key: "", zones: [] });
  const [tool, setTool] = useState(null);
  const [selected, setSelected] = useState(null);
  const [hidden, setHidden] = useState(false);
  const [snap, setSnap] = useState(() => localStorage.getItem("chart.magnet") === "1");
  const [, bump] = useState(0);
  const st = useRef({ list: [], draft: null, cursor: null, drag: null, v: 0 });
  const live = useRef({});
  live.current = { tool, selected, hidden, snap, data, symbol, zonesOn };

  const touch = useCallback((persist = true) => {
    st.current.v++;
    if (persist) save(live.current.symbol, st.current.list);
    bump((n) => n + 1);
  }, []);

  useEffect(() => {
    st.current = { ...st.current, list: load(symbol), draft: null, drag: null };
    setSelected(null);
    setTool(null);
    touch(false);
  }, [symbol, touch]);
  useEffect(() => { localStorage.setItem("chart.magnet", snap ? "1" : "0"); }, [snap]);
  useEffect(() => { st.current.draft = null; st.current.v++; }, [tool]);

  const ctxOf = () => {
    const a = api.current, d = live.current.data;
    if (!a || !d?.candles?.length) return null;
    return { a, s: scale(a, d.candles), W: a.chart.timeScale().width(), H: a.chart.panes()[0].getHeight(), digits: Number.isInteger(d.digits) ? d.digits : 2 };
  };
  const point = (c, x, y) => {
    let p = c.s.p(y);
    if (p == null) return null;
    if (live.current.snap) p = magnet(c.s, x, p);
    return { t: c.s.t(x), p };
  };

  // paint loop: redraws only when the view, the drawings or the pointer changed
  useEffect(() => {
    let raf, sig = "";
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const cv = canvas.current, c = ctxOf();
      if (!cv || !c) return;
      const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
      const ref = c.s.candles[c.s.n - 1];
      const s = st.current;
      const now = [w, h, c.W, c.H, c.s.x(ref.time), c.s.x(c.s.candles[0].time), c.s.y(ref.close), c.s.y(ref.close * 1.01), s.v, s.cursor?.t, s.cursor?.p, live.current.hidden, live.current.selected, c.a.mainType, live.current.zonesOn].join();
      if (now === sig) return;
      sig = now;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
      const g = cv.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      g.save();
      g.beginPath(); g.rect(0, 0, c.W, c.H); g.clip();
      if (live.current.zonesOn) {
        const d = live.current.data, key = `${live.current.symbol}:${c.s.n}:${c.s.candles[0].time}:${ref.time}:${ref.close}`;
        if (zc.current.key !== key) zc.current = { key, zones: computeZones(d.candles, d.atr || []) };
        paintZones(g, zc.current.zones, c.s, c.W, c.digits);
      }
      if (live.current.hidden) { g.restore(); return; }
      s.list.forEach((d) => paint(g, d, c.s, c.W, c.H, c.digits, d.id === live.current.selected));
      if (s.draft && s.cursor) paint(g, { ...s.draft, pts: [...s.draft.pts, s.cursor] }, c.s, c.W, c.H, c.digits, true);
      g.restore();
    };
    loop();
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // placing points with the active tool (click · click, like TradingView)
  const onDown = useCallback((e) => {
    const c = ctxOf(), tl = live.current.tool;
    if (!c || !tl) return;
    const [x, y] = xy(e, canvas.current);
    if (x > c.W || y > c.H) return;
    const q = point(c, x, y);
    if (!q) return;
    const s = st.current;
    const d = s.draft || { id: `${Date.now()}${Math.round(Math.random() * 1e4)}`, type: tl, pts: [], color: tl === "long" ? "#10B981" : tl === "short" ? "#EF4444" : COLORS[0], width: 1 };
    d.pts = [...d.pts, q];
    const need = tl === "long" || tl === "short" ? 2 : nPoints(tl);
    if (d.pts.length < need) { s.draft = d; s.v++; return; }
    if (tl === "long" || tl === "short") d.pts.push({ t: d.pts[1].t, p: d.pts[0].p - (d.pts[1].p - d.pts[0].p) * 2 });
    if (tl === "text") {
      d.text = (window.prompt("Texto de la nota") || "").trim();
      if (!d.text) { s.draft = null; setTool(null); return; }
    }
    s.list = [...s.list, d];
    s.draft = null;
    setTool(null);
    setSelected(d.id);
    touch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [touch]);
  const onMove = useCallback((e) => {
    const c = ctxOf();
    if (!c || !live.current.tool) return;
    const [x, y] = xy(e, canvas.current);
    st.current.cursor = point(c, x, y);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // selecting / dragging existing drawings: intercepted before the chart sees the press, so the chart does not pan
  useEffect(() => {
    const el = wrap.current;
    if (!el) return undefined;
    const down = (e) => {
      const c = ctxOf();
      if (!c || live.current.tool || live.current.hidden || e.target.closest("[data-chart-ui]")) return;
      const [x, y] = xy(e, el);
      const h = x <= c.W && y <= c.H ? hit(st.current.list, c.s, x, y, c.W, c.H, live.current.selected) : null;
      if (!h) { if (live.current.selected) setSelected(null); return; }
      e.stopPropagation();
      if (e.cancelable) e.preventDefault();
      setSelected(h.id);
      const d = st.current.list.find((k) => k.id === h.id);
      st.current.drag = { ...h, x, y, start: toScreen(d, c.s), orig: d.pts };
    };
    const move = (e) => {
      const g = st.current.drag, c = ctxOf();
      if (!g || !c) return;
      if (e.cancelable) e.preventDefault();
      const [x, y] = xy(e, el);
      const pts = g.orig.map((q, i) => {
        if (g.handle >= 0 && g.handle !== i) return q;
        const nx = g.start[i].x + (x - g.x), ny = g.start[i].y + (y - g.y);
        const np = g.handle >= 0 ? point(c, nx, ny) : { t: c.s.t(nx), p: c.s.p(ny) };
        return np && np.p != null ? np : q;
      });
      st.current.list = st.current.list.map((d) => (d.id === g.id ? { ...d, pts } : d));
      st.current.v++;
    };
    const up = () => { if (st.current.drag) { st.current.drag = null; touch(); } };
    const key = (e) => {
      if (typing()) return;
      if (e.key === "Escape") { st.current.draft = null; setTool(null); setSelected(null); }
      if ((e.key === "Delete" || e.key === "Backspace") && live.current.selected) {
        st.current.list = st.current.list.filter((d) => d.id !== live.current.selected);
        setSelected(null);
        touch();
      }
    };
    el.addEventListener("mousedown", down, true);
    el.addEventListener("touchstart", down, { capture: true, passive: false });
    window.addEventListener("mousemove", move);
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("mouseup", up);
    window.addEventListener("touchend", up);
    window.addEventListener("keydown", key);
    return () => {
      el.removeEventListener("mousedown", down, true);
      el.removeEventListener("touchstart", down, true);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("touchmove", move);
      window.removeEventListener("mouseup", up);
      window.removeEventListener("touchend", up);
      window.removeEventListener("keydown", key);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wrap, touch]);

  const current = st.current.list.find((d) => d.id === selected) || null;
  const update = (patch) => { st.current.list = st.current.list.map((d) => (d.id === selected ? { ...d, ...patch } : d)); touch(); };
  const remove = () => { st.current.list = st.current.list.filter((d) => d.id !== selected); setSelected(null); touch(); };
  const clear = () => { st.current.list = []; setSelected(null); touch(); };
  return { tool, setTool, current, update, remove, clear, hidden, setHidden, snap, setSnap, count: st.current.list.length, onDown, onMove };
}
