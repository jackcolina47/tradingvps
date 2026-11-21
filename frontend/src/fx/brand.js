import { createContext, useContext } from "react";
import { Landmark, Flame } from "lucide-react";

// Branding of the two MetaApi/MetaTrader bots that share the FX dashboard. `bot` = "fx" (HotForex) | "pepper" (Pepperstone).
const liveLabel = (bk) => (status) => `Modo REAL · ${bk}${/demo/i.test(status.env || "") ? "" : " Live"}`;
const dataLabel = (bk) => (status) => (status.mode === "REAL" ? `Señales: Yahoo · Ejecución: ${bk}` : "Datos: Yahoo Finance");
const dataTooltip = (bk) => (status) => (status.mode === "REAL"
  ? `Las señales se calculan con velas de Yahoo Finance; las órdenes, el PnL y la equidad vienen de tu cuenta ${bk} vía MetaApi.`
  : `Precios y velas de Yahoo Finance (sin API key): pares spot y futuros de oro (GC=F) y plata (SI=F) que replican el spot. En modo REAL las órdenes y el PnL vienen de tu cuenta ${bk} vía MetaApi.`);
const resetHint = (bk) => `Se borrarán posiciones, historial, señales, operaciones de noticias y curva de equidad. En paper el balance vuelve al capital inicial configurado (por defecto $500); en REAL las estadísticas parten de la equidad actual de ${bk}.`;

export const BRANDS = {
  fx: {
    bot: "fx", path: "/hotforex", api: "/api/fx", name: "HotForex AI Trading", short: "HotForex", broker: "HFM", brokerLong: "HotForex", accent: "sky",
    accentText: "text-sky-400", chip: "border-sky-500/40 bg-sky-500/10 text-sky-300", spinner: "text-sky-400",
    costsNote: "spread HFM como único coste", serverExample: "HFMarketsGlobal-Live2", icon: Landmark,
    header: { title: <>HotForex <span className="text-sky-400">AI</span> Trading</>, subtitle: (s) => `Forex · Metales · ${s.ai?.label || "IA"}`, icon: Landmark,
      liveLabel: liveLabel("HFM"), dataLabel: dataLabel("HFM"), dataTooltip: dataTooltip("HFM"), resetHint: resetHint("HFM") },
  },
  pepper: {
    bot: "pepper", path: "/pepperstone", api: "/api/pepper", name: "Pepperstone AI trading", short: "Pepperstone", broker: "Pepperstone", brokerLong: "Pepperstone", accent: "teal", ctrader: true, emaOnly: true,
    accentText: "text-teal-300", chip: "border-teal-500/40 bg-teal-500/10 text-teal-300", spinner: "text-teal-300",
    costsNote: "spread y comisión según tu tipo de cuenta Pepperstone (Razor o Standard)", serverExample: "Pepperstone-MT5-Live01", icon: Flame,
    header: { title: <>Pepperstone <span className="text-teal-300">AI</span> trading</>, subtitle: (s) => `Forex · Metales · Cripto · ${s.costs?.label || "Razor"} · Cruce EMA 10 y 20`, icon: Flame,
      liveLabel: liveLabel("Pepperstone"), dataLabel: dataLabel("Pepperstone"), dataTooltip: dataTooltip("Pepperstone"), resetHint: resetHint("Pepperstone") },
  },
};

export const BrandContext = createContext(BRANDS.fx);
export const useBrand = () => useContext(BrandContext);
// Human names for every bot key used by shared components (chat, backups, docs).
export const BOT_NAMES = { crypto: "Bybit", fx: "HotForex", pepper: "Pepperstone" };
export const BROKER_NAMES = { crypto: "Bybit", fx: "HFM", pepper: "Pepperstone" };
