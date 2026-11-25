import { useCallback, useEffect, useRef, useState } from "react";
import { http } from "@/lib/api";

const SERIES = ["candles", "volume", "ema9", "ema10", "ema20", "ema21", "ema27", "ema50", "ema200", "bb_up", "bb_mid", "bb_low", "vwap", "rsi", "macd", "macd_signal", "macd_hist", "adx", "gap", "atr"];
const PAGE = 500;
export const TF_STEP = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };

function fresh() {
  const m = { markers: new Map() };
  SERIES.forEach((k) => (m[k] = new Map()));
  return m;
}

function absorb(store, d) {
  if (Number.isInteger(d.digits)) store.digits = d.digits;
  SERIES.forEach((k) => (d[k] || []).forEach((p) => store[k].set(p.time, p)));
  (d.markers || []).forEach((m) => store.markers.set(`${m.time}|${m.position}|${m.text}`, m));
}

function materialize(store) {
  const out = { digits: store.digits };
  [...SERIES, "markers"].forEach((k) => (out[k] = [...store[k].values()].sort((x, y) => x.time - y.time)));
  return out;
}

function oldest(store) {
  let t = Infinity;
  for (const k of store.candles.keys()) if (k < t) t = k;
  return t === Infinity ? null : t;
}

function newest(store) {
  let t = -Infinity;
  for (const k of store.candles.keys()) if (k > t) t = k;
  return t === -Infinity ? null : t;
}

/** Latest candles polled every `interval` ms + older pages (`before`) merged by timestamp, so the series never has gaps.
 * `anchor` (unix s): a past window ending there instead of the live one (closed trade); newer pages load on demand until the present. */
export function useHistory(symbol, tf, interval = 10000, anchor = null) {
  const store = useRef(fresh());
  const busy = useRef(false);
  const more = useRef(true);
  const ahead = useRef(false);
  const cool = useRef(0);
  const [data, setData] = useState(null);
  const [meta, setMeta] = useState({ lines: [], source: "", hasMore: true, loading: false, error: null });

  useEffect(() => {
    store.current = fresh();
    more.current = true;
    ahead.current = !!anchor;
    setData(null);
    setMeta((m) => ({ ...m, hasMore: true, lines: [], loading: false }));
  }, [symbol, tf, anchor]);

  useEffect(() => {
    let alive = true;
    const st = store.current;
    const tick = async () => {
      try {
        if (anchor && st.candles.size) return;  // past window (closed trade): loaded once, newer pages come from loadNewer
        const { data: d } = await http.get("/klines", { params: anchor ? { symbol, tf, before: anchor } : { symbol, tf } });
        if (!alive || store.current !== st || d.symbol !== symbol || d.tf !== tf) return;
        absorb(st, d);
        setData(materialize(st));
        setMeta((m) => ({ ...m, lines: d.lines, source: d.source, error: null }));
      } catch (e) {
        if (alive) setMeta((m) => ({ ...m, error: e }));
      }
    };
    tick();
    const id = setInterval(tick, interval);
    return () => { alive = false; clearInterval(id); };
  }, [symbol, tf, interval, anchor]);

  const loadOlder = useCallback(async () => {
    const st = store.current;
    const before = oldest(st);
    if (!before || busy.current || !more.current || Date.now() < cool.current) return;
    busy.current = true;
    setMeta((m) => ({ ...m, loading: true }));
    try {
      const { data: d } = await http.get("/klines", { params: { symbol, tf, before, limit: PAGE } });
      if (store.current !== st) return;
      absorb(st, d);
      more.current = !!d.has_more && d.candles.length > 0;
      setData(materialize(st));
      if (d.source) setMeta((m) => ({ ...m, source: d.source }));
    } catch (e) {
      cool.current = Date.now() + 15000;
    } finally {
      busy.current = false;
      if (store.current === st) setMeta((m) => ({ ...m, loading: false, hasMore: more.current }));
    }
  }, [symbol, tf]);

  const loadNewer = useCallback(async () => {
    const st = store.current;
    const last = newest(st);
    if (!ahead.current || !last || busy.current || Date.now() < cool.current) return;
    busy.current = true;
    const step = TF_STEP[tf];
    const before = last + (PAGE + 1) * step;
    const live = before >= Date.now() / 1000;  // the next page reaches the present: take the live window instead
    try {
      const { data: d } = await http.get("/klines", { params: live ? { symbol, tf, limit: 700 } : { symbol, tf, before, limit: PAGE } });
      if (store.current !== st) return;
      absorb(st, d);
      if (live || !d.candles.length) ahead.current = false;
      setData(materialize(st));
    } catch (e) {
      cool.current = Date.now() + 15000;
    } finally {
      busy.current = false;
    }
  }, [symbol, tf]);

  return { data, meta, loadOlder, loadNewer };
}
