import { useEffect, useRef, useState } from "react";
import { Camera, CandlestickChart, Loader2, Maximize2, Minimize2, PencilRuler, TrendingUp, TrendingDown } from "lucide-react";
import { Panel } from "./Panel";
import { fmtPrice } from "@/lib/api";
import { useChart, render, applyIndicators, applyStyle, resetScales, OVERLAYS, SUBS, DEFAULT_STYLE } from "./chart/useChart";
import { useDrawings } from "./chart/useDrawings";
import { DrawToolbar, DrawingBar } from "./chart/DrawToolbar";
import ChartLegend from "./chart/ChartLegend";
import ChartStyle from "./chart/ChartStyle";
import ManualOrderDialog from "./chart/ManualOrderDialog";
import { useHistory } from "./chart/useHistory";
import LevelHandles from "./chart/LevelHandles";

const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const SOURCE = { bybit: "Velas Bybit", bitget: "Velas Bitget", hfm: "Velas del bróker · MetaTrader", yahoo: "Velas Yahoo Finance" };
const IND = [...Object.entries({ ...OVERLAYS, ...SUBS }).map(([k, v]) => [k, v.label]), ["vol", "Volumen"], ["zones", "Zonas oferta/demanda"]];
const DEFAULT_IND = { ema9: true, ema21: true, ema200: true, rsi: true, adx: true, gap: true, vol: true, zones: true };
const loadStyle = () => {
  try {
    const s = JSON.parse(localStorage.getItem("chart.style") || "{}");
    return { ...DEFAULT_STYLE, ...s, lines: { ...DEFAULT_STYLE.lines, ...(s.lines || {}) } };
  } catch (e) { return DEFAULT_STYLE; }
};
const loadInd = () => {
  try { return { ...DEFAULT_IND, ...JSON.parse(localStorage.getItem("chart.indicators") || "{}") }; } catch (e) { return DEFAULT_IND; }
};

export default function PriceChart({ symbol, defaultTf, kind = "Perpetuo", tfs = TFS, trade, tid = "", levels, className = "lg:col-span-6", heightClass = "h-[420px] sm:h-[560px]" }) {
  const [order, setOrder] = useState(null);
  const [tf, setTf] = useState(defaultTf);
  const [fs, setFs] = useState(false);
  const [ind, setInd] = useState(loadInd);
  const [style, setStyle] = useState(loadStyle);
  const [tools, setTools] = useState(() => (localStorage.getItem("chart.tools") ?? (window.innerWidth >= 640 ? "1" : "0")) === "1");
  const ref = useRef(null);
  const wrap = useRef(null);
  const canvas = useRef(null);
  const fitted = useRef("");
  const { data, meta, loadOlder } = useHistory(symbol, tf);
  const api = useChart(ref, () => { if (fitted.current === `${symbol}-${tf}`) loadOlder(); });

  const dr = useDrawings(api, data, symbol, wrap, canvas, !!ind.zones);
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);

  useEffect(() => setTf(defaultTf), [defaultTf]);
  useEffect(() => { localStorage.setItem("chart.tools", tools ? "1" : "0"); }, [tools]);

  useEffect(() => {
    localStorage.setItem("chart.style", JSON.stringify(style));
    const a = api.current;
    if (a && applyStyle(a, style) && data) render(a, data, meta.lines, tf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [style, api]);

  const screenshot = () => {
    const a = api.current;
    if (!a) return;
    const shot = a.chart.takeScreenshot();
    const out = document.createElement("canvas");
    out.width = shot.width; out.height = shot.height;
    const g = out.getContext("2d");
    g.fillStyle = "#07090E";
    g.fillRect(0, 0, out.width, out.height);
    g.drawImage(shot, 0, 0);
    if (canvas.current) g.drawImage(canvas.current, 0, 0, out.width, out.height);
    const link = document.createElement("a");
    link.download = `${symbol}-${tf}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "")}.png`;
    link.href = out.toDataURL("image/png");
    link.click();
  };

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
    if (api.current) applyIndicators(api.current, ind, data, style);
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
    <Panel testId={`${tid}price-chart-panel`} title={`${symbol} · ${kind}`} icon={CandlestickChart} bodyClass="flex flex-col" delay={120}
      className={fs ? "fixed inset-0 z-[70] rounded-none !bg-[#07090E] !backdrop-blur-0" : className}
      actions={
        <div className="flex flex-wrap items-center justify-end gap-1.5 sm:gap-2">
          <div className="flex items-center gap-0.5 rounded-sm border border-[#1E293B] p-0.5 sm:gap-1">
            {tfs.map((t) => (
              <button key={t} data-testid={`${tid}chart-tf-${t}`} onClick={() => setTf(t)}
                className={`rounded-sm px-1.5 py-0.5 font-mono text-[11px] transition-colors sm:px-2 ${t === tf ? "bg-amber-500 text-[#07090E]" : "text-slate-400 hover:text-slate-100"}`}>
                {t}
              </button>
            ))}
          </div>
          <button data-testid={`${tid}chart-tools-btn`} onClick={() => setTools((v) => !v)} title={tools ? "Ocultar herramientas de dibujo" : "Herramientas de dibujo"} aria-pressed={tools}
            className={`rounded-sm border p-1 transition-colors ${tools ? "border-amber-500/60 text-amber-400" : "border-[#1E293B] text-slate-400 hover:text-slate-100"}`}><PencilRuler className="h-3.5 w-3.5" /></button>
          <ChartStyle style={style} setStyle={setStyle} />
          <button data-testid={`${tid}chart-screenshot-btn`} onClick={screenshot} title="Descargar captura del gráfico (PNG)"
            className="rounded-sm border border-[#1E293B] p-1 text-slate-400 transition-colors hover:text-slate-100"><Camera className="h-3.5 w-3.5" /></button>
          <button data-testid={`${tid}chart-fullscreen-btn`} onClick={() => setFs((v) => !v)} title={fs ? "Salir de pantalla completa (Esc)" : "Pantalla completa"}
            className="rounded-sm border border-[#1E293B] p-1 text-slate-400 transition-colors hover:text-slate-100">
            {fs ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        </div>
      }>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 font-mono text-[11px]">
        {trade && (
          <span className="flex items-center gap-1.5">
            <button data-testid={`${tid}chart-buy-btn`} onClick={() => setOrder("long")} disabled={!last} title="Abrir una compra manual con tu propio SL, TP, riesgo y gestión" className="inline-flex items-center gap-1.5 rounded-sm bg-emerald-500 px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider text-[#07090E] shadow-[0_0_0_1px_rgba(16,185,129,0.35)] transition-[background-color,transform] hover:bg-emerald-400 active:scale-95 disabled:opacity-50"><TrendingUp className="h-3.5 w-3.5" strokeWidth={2.6} /> Comprar</button>
            <button data-testid={`${tid}chart-sell-btn`} onClick={() => setOrder("short")} disabled={!last} title="Abrir una venta manual con tu propio SL, TP, riesgo y gestión" className="inline-flex items-center gap-1.5 rounded-sm bg-rose-500 px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider text-[#07090E] shadow-[0_0_0_1px_rgba(244,63,94,0.35)] transition-[background-color,transform] hover:bg-rose-400 active:scale-95 disabled:opacity-50"><TrendingDown className="h-3.5 w-3.5" strokeWidth={2.6} /> Vender</button>
          </span>
        )}
        <span data-testid={`${tid}chart-last-price`} className="text-base font-semibold text-slate-50">{fmtPrice(last?.close, data?.digits)}</span>
        {IND.map(([k, label]) => (
          <button key={k} data-testid={`${tid}chart-ind-${k}`} onClick={() => setInd((v) => ({ ...v, [k]: !v[k] }))} aria-pressed={!!ind[k]} style={ind[k] ? { color: style.lines[k]?.color || "#94A3B8" } : undefined}
            className={`rounded-sm border px-1.5 py-px transition-colors ${ind[k] ? "border-[#1E293B]" : "border-transparent text-slate-600 line-through hover:text-slate-400"}`}>
            {label}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          {meta.loading && <span data-testid={`${tid}chart-loading-older`} className="flex items-center gap-1 text-amber-400"><Loader2 className="h-3 w-3 animate-spin" /> cargando historial…</span>}
          {!meta.loading && !meta.hasMore && data && <span data-testid={`${tid}chart-history-end`}>inicio del historial disponible</span>}
          {meta.source && <span data-testid={`${tid}chart-source`}>{SOURCE[meta.source] || meta.source} · {data?.candles?.length || 0} velas · hora local</span>}
        </span>
      </div>
      <div ref={wrap} data-testid={`${tid}price-chart-wrap`} className={`relative mt-1 w-full ${fs ? "h-[calc(100dvh-150px)]" : heightClass}`}>
        <div ref={ref} data-testid="price-chart" className="absolute inset-0" />
        <canvas ref={canvas} data-testid={`${tid}chart-drawings-canvas`} onPointerDown={dr.onDown} onPointerMove={dr.onMove}
          className="absolute inset-0 z-10 h-full w-full" style={{ pointerEvents: dr.tool ? "auto" : "none", cursor: "crosshair", touchAction: "none" }} />
        <ChartLegend api={api} ready={ready} data={data} ind={ind} style={style} offset={tools ? 44 : 8} />
        {levels && ready && data && <LevelHandles api={api} wrap={wrap} digits={data.digits} levels={levels} />}
        {tools && <DrawToolbar dr={dr} />}
        <DrawingBar dr={dr} />
        {trade && <ManualOrderDialog side={order} symbol={symbol} price={last?.close} atr={[...(data?.atr || [])].reverse().find((x) => x.value > 0)?.value} digits={data?.digits} tf={tf} trade={trade} onClose={() => setOrder(null)} />}
      </div>
    </Panel>
  );
}
