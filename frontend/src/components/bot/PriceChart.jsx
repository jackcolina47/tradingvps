import { useEffect, useRef, useState } from "react";
import { CandlestickChart, Loader2, Maximize2, Minimize2 } from "lucide-react";
import { Panel } from "./Panel";
import { fmtPrice } from "@/lib/api";
import { useChart, render, applyIndicators, resetScales } from "./chart/useChart";
import { useHistory } from "./chart/useHistory";

const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const SOURCE = { bybit: "Velas Bybit", bitget: "Velas Bitget", hfm: "Velas del bróker · MetaTrader", yahoo: "Velas Yahoo Finance" };
const IND = [["ema9", "EMA 9", "text-amber-400"], ["ema21", "EMA 21", "text-sky-400"], ["ema200", "EMA 200", "text-slate-300"], ["rsi", "RSI 14", "text-pink-400"], ["adx", "ADX 14", "text-violet-400"], ["gap", "Dist. EMA 10/20 (×ATR)", "text-teal-400"], ["vol", "Volumen", "text-slate-400"]];
const DEFAULT_IND = { ema9: true, ema21: true, ema200: true, rsi: true, adx: true, gap: true, vol: true };
const loadInd = () => {
  try { return { ...DEFAULT_IND, ...JSON.parse(localStorage.getItem("chart.indicators") || "{}") }; } catch (e) { return DEFAULT_IND; }
};

export default function PriceChart({ symbol, defaultTf, kind = "Perpetuo", tfs = TFS }) {
  const [tf, setTf] = useState(defaultTf);
  const [fs, setFs] = useState(false);
  const [ind, setInd] = useState(loadInd);
  const ref = useRef(null);
  const fitted = useRef("");
  const { data, meta, loadOlder } = useHistory(symbol, tf);
  const api = useChart(ref, () => { if (fitted.current === `${symbol}-${tf}`) loadOlder(); });

  useEffect(() => setTf(defaultTf), [defaultTf]);

  useEffect(() => {
    const a = api.current;
    if (!a || !data) return;
    render(a, data, meta.lines, tf);
    const key = `${symbol}-${tf}`;
    if (fitted.current !== key) {
      const n = data.candles.length;
      resetScales(a);
      // the chart applies new data on its next frame; fit after that so the default range does not override us
      requestAnimationFrame(() => {
        if (!api.current) return;
        api.current.chart.timeScale().setVisibleLogicalRange({ from: n - 120, to: n + 4 });
        fitted.current = key;
      });
    }
  }, [data, meta.lines, symbol, tf, api]);

  useEffect(() => {
    localStorage.setItem("chart.indicators", JSON.stringify(ind));
    if (api.current) applyIndicators(api.current, ind, data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ind, api]);

  useEffect(() => {
    if (!fs) return;
    const onKey = (e) => e.key === "Escape" && setFs(false);
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [fs]);

  const last = data?.candles?.[data.candles.length - 1];
  return (
    <Panel testId="price-chart-panel" title={`${symbol} · ${kind}`} icon={CandlestickChart} bodyClass="flex flex-col" delay={120}
      className={fs ? "fixed inset-0 z-[70] rounded-none !bg-[#07090E] !backdrop-blur-0" : "lg:col-span-6"}
      actions={
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 rounded-sm border border-[#1E293B] p-0.5">
            {tfs.map((t) => (
              <button key={t} data-testid={`chart-tf-${t}`} onClick={() => setTf(t)}
                className={`rounded-sm px-2 py-0.5 font-mono text-[11px] transition-colors ${t === tf ? "bg-amber-500 text-[#07090E]" : "text-slate-400 hover:text-slate-100"}`}>
                {t}
              </button>
            ))}
          </div>
          <button data-testid="chart-fullscreen-btn" onClick={() => setFs((v) => !v)} title={fs ? "Salir de pantalla completa (Esc)" : "Pantalla completa"}
            className="rounded-sm border border-[#1E293B] p-1 text-slate-400 transition-colors hover:text-slate-100">
            {fs ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        </div>
      }>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 font-mono text-[11px]">
        <span data-testid="chart-last-price" className="text-base font-semibold text-slate-50">{fmtPrice(last?.close, data?.digits)}</span>
        {IND.map(([k, label, color]) => (
          <button key={k} data-testid={`chart-ind-${k}`} onClick={() => setInd((v) => ({ ...v, [k]: !v[k] }))} aria-pressed={!!ind[k]}
            className={`rounded-sm border px-1.5 py-px transition-colors ${ind[k] ? `border-[#1E293B] ${color}` : "border-transparent text-slate-600 line-through hover:text-slate-400"}`}>
            {label}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          {meta.loading && <span data-testid="chart-loading-older" className="flex items-center gap-1 text-amber-400"><Loader2 className="h-3 w-3 animate-spin" /> cargando historial…</span>}
          {!meta.loading && !meta.hasMore && data && <span data-testid="chart-history-end">inicio del historial disponible</span>}
          {meta.source && <span data-testid="chart-source">{SOURCE[meta.source] || meta.source} · {data?.candles?.length || 0} velas · hora local</span>}
        </span>
      </div>
      <div ref={ref} data-testid="price-chart" className={`w-full ${fs ? "h-[calc(100vh-96px)]" : "h-[560px]"}`} />
    </Panel>
  );
}
