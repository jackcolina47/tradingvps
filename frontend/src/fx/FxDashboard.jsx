import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Layers, Loader2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { http, errMsg, setApiBase, SESSION_NAMES, fmtTime } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import Header from "@/components/bot/Header";
import KpiStrip from "@/components/bot/KpiStrip";
import Watchlist from "@/components/bot/Watchlist";
import PriceChart from "@/components/bot/PriceChart";
import BotPanel from "@/components/bot/BotPanel";
import PositionsTable from "@/components/bot/PositionsTable";
import TradeHistory from "@/components/bot/TradeHistory";
import Performance from "@/components/bot/Performance";
import Backtest from "@/components/bot/Backtest";
import Optimizer from "@/components/bot/Optimizer";
import AiSignals from "@/components/bot/AiSignals";
import EvolutionPanel from "@/components/bot/EvolutionPanel";
import { AiHealthBanner } from "@/components/bot/AiHealthBanner";
import SettingsPanel from "@/components/bot/SettingsPanel";
import Terminal from "@/components/bot/Terminal";
import GrowthPlan from "@/components/bot/GrowthPlan";
import { Panel } from "@/components/bot/Panel";
import SessionsStrip from "./SessionsStrip";
import FxNewsPanel from "./FxNewsPanel";
import FxAdminSettings from "./FxAdminSettings";
import FxFiltersPanel from "./FxFiltersPanel";
import Documentation from "@/components/docs/Documentation";
import VpsInstall from "@/components/deploy/VpsInstall";
import { BRANDS, BrandContext } from "./brand";
import LabModeCard from "./LabModeCard";

const TAB_CLS = "rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider data-[state=active]:bg-amber-500 data-[state=active]:text-[#07090E]";
const FX_TFS = ["5m", "15m", "1h", "4h", "1d"];
export const FX_BRAND = BRANDS.fx.header;
export const NEWS_WEIGHT_LABELS = { normal: "Normal · las alertas generales pueden rechazar", bajo: "Bajo · solo restan confianza" };
const EXTRA_SELECTS = [{ k: "ai_news_weight", label: "Peso de las noticias en la IA", options: ["normal", "bajo"], render: (o) => NEWS_WEIGHT_LABELS[o] || o,
  hint: "Cuánto pesan las alertas generales de mercado (no específicas del par) en el veredicto de la IA por señal. La optimización con IA lo calibra sola con las operaciones sombra «Rechazos de la IA por riesgo de noticias»." }];
const EXTRA_TOGGLES = [
  { k: "close_before_weekend", label: "Cerrar antes del fin de semana", hint: "Cierra todo 30 min antes del cierre del viernes (evita gaps del domingo)" },
  { k: "rollover_guard", label: "Guardia de rollover (17:00 NY)", hint: "Sin entradas 16:50–17:15 Nueva York; en paper los stops no se evalúan durante el pico de spread" },
  { k: "vol_filter_enabled", label: "Filtro de volatilidad (ATR)", hint: "Sin entradas con ATR bajo el percentil mínimo; riesgo a la mitad por encima del percentil alto" },
  { k: "seasonality_bias", label: "Estacionalidad intradía", hint: "Una divisa tiende a depreciarse en su horario local: señales contra el sesgo bloqueadas en los pares marcados y reducidas en el resto" },
  { k: "usd_regime_filter", label: "Filtro de régimen USD", hint: "Índice USD sintético vs SMA diaria: pares con USD solo a favor del régimen (cruces y metales no)" },
  { k: "fomc_rule", label: "Regla FOMC sin continuación", hint: "24 h tras la decisión de la Fed no se opera la continuación del movimiento en pares con USD" },
  { k: "price_action_block", label: "Bloqueo por precio extendido", hint: "Regla dura: sin entradas tras N velas grandes seguidas en la dirección de la señal ni pegadas a un swing contrario (resistencia en LONG / soporte en SHORT). La señal queda en sombra PRECIO_EXTENDIDO" },
  { k: "partial_tp_enabled", label: "Cierre parcial de beneficios", hint: "Cierra una parte al alcanzar el nivel en R y deja correr el resto con trailing" },
];
const EXTRA_NUMS = [
  { k: "min_lot_max_risk", label: "Riesgo máx. lote mínimo (%)", step: 0.5, min: 0, max: 10,
    hint: "Si el tamaño ideal queda por debajo de 0.01 lotes (típico en oro/plata con poco capital), se abre el lote mínimo siempre que su riesgo no supere este % de la equidad. 0 = nunca." },
  { k: "currency_exposure_limit", label: "Tope exposición por divisa", step: 1, min: 1, max: 6, hint: "Máximo de posiciones en la misma dirección sobre una misma divisa (long EURUSD + long GBPUSD = 2 cortos de USD)" },
  { k: "max_long_positions", label: "Máx. posiciones largo plazo", step: 1, min: 1, max: 6, hint: "Tope propio de la reversión de fin de mes; no consume «Máx. posiciones»" },
  { k: "vol_min_pct", label: "Volatilidad: percentil mínimo", step: 5, min: 0, max: 50 },
  { k: "vol_high_pct", label: "Volatilidad: percentil alto", step: 5, min: 60, max: 100 },
  { k: "seasonality_size_factor", label: "Tamaño contra sesgo estacional", step: 0.05, min: 0.25, max: 1 },
  { k: "price_action_max_candles", label: "Precio extendido: velas seguidas", step: 1, min: 2, max: 5, hint: "Nº de velas grandes consecutivas (≥ 2 ATR en total) a partir del cual la entrada se considera «perseguir el precio»" },
  { k: "price_action_swing_atr", label: "Precio extendido: swing a (ATR)", step: 0.25, min: 0.25, max: 3, hint: "Si el swing contrario más cercano está a menos de esta distancia en ATR, la entrada se bloquea" },
  { k: "be_start_r", label: "Breakeven a (R)", min: 0.5, max: 3, step: 0.25 }, { k: "trail_start_r", label: "Trailing desde (R)", min: 0.5, max: 3, step: 0.25 },
  { k: "trail_atr_mult", label: "Distancia trailing (ATR)", min: 0.5, max: 3, step: 0.25 }, { k: "partial_tp_r", label: "Cierre parcial a (R)", min: 0.5, max: 3, step: 0.25 },
  { k: "partial_tp_pct", label: "Cierre parcial (%)", min: 25, max: 75, step: 5 },
];
const SEASON_CHIPS = (meta) => [{ k: "seasonality_block_pairs", label: "Pares con bloqueo estacional", emptyLabel: "ninguno · solo reduce tamaño",
  options: (meta?.symbols || []).map((s) => ({ id: s, name: s })),
  hint: "En estos pares la señal contra el sesgo horario se bloquea (evidencia neta solo en EURUSD); en el resto se opera con el tamaño reducido configurado." }];
const BT_INTRO = (brand) => (s) => `Evalúa la estrategia sobre las últimas 1.000 velas de Yahoo Finance (sin filtro IA ni TF superior) con el capital inicial de tu cuenta, lotes de 0.01 y el mismo plan de riesgo: ${s.risk_per_trade}% por operación, 1:${s.leverage}, ${brand.costsNote}, trailing stop${s.close_before_weekend ? " y cierre antes del fin de semana" : ""}. Cada operación se atribuye a la sesión (Sídney, Tokio, Londres, Nueva York) en la que se abrió${s.sessions_allowed?.length ? `; solo se abren entradas en ${s.sessions_allowed.map((x) => SESSION_NAMES[x]).join(" y ")}, como en el bot` : ""}.`;
const OPT_INTRO = "Optimización por par con lotes, spread y cierre de fin de semana como en el bot: ";
const AI_ALL_INTRO = (n) => `Corre el optimizador y el walk-forward en los ${n} pares activos y la IA cruza esos resultados con las operaciones reales del bot, las estadísticas por sesión del backtest, las noticias y la revalidación semanal. Aplica los SL/TP que se sostienen fuera de muestra, restringe las sesiones si hay evidencia y poda lo que pierde, siempre dentro de los topes de tu perfil de riesgo forex. Los cambios se aplican directamente y te avisa por Telegram.`;
const SESSION_CHIPS = (meta) => [{ k: "sessions_allowed", label: "Sesiones para abrir posiciones", emptyLabel: "todas", options: meta?.sessions || [],
  hint: "El bot solo abre nuevas entradas cuando alguna de las sesiones marcadas está abierta (Londres y Nueva York concentran la liquidez). Sin selección opera en todo el horario forex. Las posiciones abiertas se gestionan igual en cualquier sesión. El backtest aplica el mismo filtro." }];
const fmtLev = (v) => `1:${v}`;
const PLAN_INTRO = (brand) => `Plan de crecimiento compuesto para la cuenta forex: cuándo reinvertir, cuándo subir el riesgo y cuándo empezar a retirar. Los escenarios Prudente / Realista / Ambicioso son rentabilidades mensuales supuestas para la proyección (no tienen relación con el perfil de riesgo del bot); el escenario «Según backtest» usa Monte Carlo sobre tus propias estrategias en lotes y pips (${brand.costsNote}, lote mínimo, sesiones y cierre de fin de semana como en el bot, sin filtro IA). Tu meta, aporte y escenario quedan guardados en la configuración de este bot.`;

export default function FxDashboard({ onLogout, bot = "fx" }) {
  setApiBase(bot);
  const brand = BRANDS[bot] || BRANDS.fx;
  const nav = useNavigate();
  const [meta, setMeta] = useState(null);
  const [symbol, setSymbol] = useState("XAUUSD");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("panel");
  const { data, refresh } = usePolling(() => http.get("/dashboard").then((r) => r.data), 4000);

  useEffect(() => {
    document.title = `${brand.name} · Panel de control`;
    http.get("/meta").then((r) => setMeta(r.data)).catch(() => toast.error("No se pudo cargar la configuración"));
  }, [brand.name, bot]);

  const symbols = data?.settings.symbols;
  useEffect(() => {
    if (symbols?.length && !symbols.includes(symbol)) setSymbol(symbols[0]);
  }, [symbols, symbol]);

  const act = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) toast.success(okMsg);
      await refresh();
      return true;
    } catch (e) {
      toast.error(errMsg(e));
      return false;
    }
  };

  if (!data) {
    return (
      <div data-testid="fx-dashboard-loading" className="flex min-h-screen items-center justify-center gap-3 font-mono text-sm text-slate-400">
        <Loader2 className={`h-4 w-4 animate-spin ${brand.spinner}`} /> Conectando con el motor {brand.short}...
      </div>
    );
  }

  const toggleBot = async () => {
    setBusy(true);
    const running = data.status.running;
    await act(() => http.post(running ? "/bot/stop" : "/bot/start"), running ? "Bot detenido" : data.status.mode === "REAL" ? `Bot iniciado · MODO REAL en ${brand.broker}` : "Bot iniciado · operando en horario forex");
    setBusy(false);
  };
  const newsAlert = data.news?.risk === "ALTO" || data.news?.blocked || data.news?.critical?.length > 0;

  return (
    <BrandContext.Provider value={brand}>
    <div data-testid="fx-dashboard" data-bot={bot} className="app-bg grain min-h-screen">
      <Header status={data.status} busy={busy} onToggle={toggleBot} view={view} onLogout={onLogout} brand={brand.header} onSwitchBot={() => nav("/")} bot={bot} onRefresh={refresh}
        onSettings={() => setView((v) => (v === "settings" ? "panel" : "settings"))}
        onDocs={() => setView((v) => (v === "docs" ? "panel" : "docs"))}
        onDeploy={() => setView((v) => (v === "deploy" ? "panel" : "deploy"))}
        onScan={() => act(() => http.post("/bot/scan"), "Escaneo solicitado")}
        onReset={() => act(() => http.post("/account/reset", {}), "Cuenta paper reiniciada")} />
      {view === "deploy" ? (
        <main className="mx-auto max-w-[1920px] p-4 md:p-6"><VpsInstall onBack={() => setView("panel")} /></main>
      ) : view === "docs" ? (
        <main className="mx-auto max-w-[1920px] p-4 md:p-6"><Documentation bot={bot} onBack={() => setView("panel")} /></main>
      ) : view === "settings" ? (
        <main className="mx-auto max-w-[1920px] p-4 md:p-6"><FxAdminSettings account={data.account} refresh={refresh} /></main>
      ) : (
      <main className="mx-auto max-w-[1920px] space-y-4 p-4 md:p-6">
        <AiHealthBanner ai={data.status.ai} />
        {data.status.exchange_error && (
          <div data-testid="fx-exchange-error-banner" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-2 font-mono text-[11px] text-rose-200">
            Yahoo Finance sin respuesta: {data.status.exchange_error} · mostrando los últimos datos locales
          </div>
        )}
        {(data.status.broker_error || data.status.broker_health?.ok === false) && (
          <div data-testid="fx-broker-error-banner" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-2 font-mono text-[11px] text-rose-200">
            {data.status.broker_health?.down_since
              ? <>Cuenta {brand.broker} sin conexión con MetaApi desde las {fmtTime(data.status.broker_health.down_since)} ({data.status.broker_health.down_sec < 60 ? `${data.status.broker_health.down_sec} s` : `${Math.round(data.status.broker_health.down_sec / 60)} min`}) · {data.status.broker_health.error} · el bot no abrirá nuevas operaciones hasta reconectar{data.status.broker_health.alerted ? " · aviso enviado por Telegram" : ""}</>
              : <>{brand.broker} / MetaApi sin respuesta: {data.status.broker_error} · equidad y posiciones reales pendientes de sincronizar</>}
          </div>
        )}
        <SessionsStrip hours={data.status.market_hours} allowed={data.settings.sessions_allowed} />
        <KpiStrip account={data.account} />
        {brand.lab && <LabModeCard lab={data.lab} onStart={() => act(() => http.post("/lab/start"), "Modo laboratorio activado · solo Cruce EMA 10/20")} onStop={() => act(() => http.post("/lab/stop"), "Laboratorio desactivado · ajustes restaurados")} />}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <Watchlist items={data.watchlist} selected={symbol} onSelect={setSymbol} />
          <PriceChart symbol={symbol} defaultTf={data.settings.timeframe} kind={meta?.symbol_info?.[symbol]?.name || "Forex"} tfs={FX_TFS} />
          <BotPanel settings={data.settings} meta={meta} account={data.account} equity={data.equity} profileActive={data.profile_active} fmtLeverage={fmtLev} />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 lg:items-start">
          <Panel testId="fx-operations-panel" className="lg:col-span-8" delay={220}>
            <Tabs defaultValue="positions" className="flex h-full flex-col">
              <div className="flex items-center gap-3 border-b border-[#1E293B] px-4 py-2.5">
                <Layers className="h-4 w-4 shrink-0 text-amber-400" />
                <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto bg-transparent p-0 [scrollbar-width:none]">
                  <TabsTrigger data-testid="fx-tab-positions" value="positions" className={TAB_CLS}>Posiciones ({data.positions.length})</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-history" value="history" className={TAB_CLS}>Historial</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-backtest" value="backtest" className={TAB_CLS}>Backtest</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-performance" value="performance" className={TAB_CLS}>Rendimiento</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-optimizer" value="optimizer" className={TAB_CLS}>Optimizador</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-news" value="news" className={`${TAB_CLS} relative`}>
                    Noticias y agenda
                    {newsAlert && <span data-testid="fx-tab-news-alert" className="pulse-dot absolute -right-1 -top-1 bg-rose-400" />}
                  </TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-plan" value="plan" className={`${TAB_CLS} relative`}>
                    Plan de crecimiento
                    {(data.checkin?.ready_to_advance || data.checkin?.warnings?.length > 0) && (
                      <span data-testid="fx-tab-plan-alert" className={`pulse-dot absolute -right-1 -top-1 ${data.checkin.ready_to_advance ? "bg-emerald-400" : "bg-rose-400"}`} />
                    )}
                  </TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-evolution" value="evolution" className={TAB_CLS}>Evolución</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-filters" value="filters" className={TAB_CLS}>Filtros</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="evolution" className="mt-0"><EvolutionPanel /></TabsContent>
              <TabsContent value="filters" className="mt-0">
                <FxFiltersPanel settings={data.settings} symbolNames={meta?.symbol_info} onSave={(body) => act(() => http.put("/settings", body), "Configuración actualizada")} />
              </TabsContent>
              <TabsContent value="positions" className="mt-0">
                <PositionsTable positions={data.positions} onClose={(id) => act(() => http.post(`/positions/${id}/close`), "Posición cerrada")}
                  onCloseAll={() => act(() => http.post("/positions/close-all"), "Cierre de emergencia ejecutado")} />
              </TabsContent>
              <TabsContent value="history" className="mt-0"><TradeHistory trades={data.trades} /></TabsContent>
              <TabsContent value="backtest" className="mt-0"><Backtest meta={meta} settings={data.settings} intro={BT_INTRO(brand)} /></TabsContent>
              <TabsContent value="performance" className="mt-0">
                <Performance tradeCount={data.account.total_trades} settings={data.settings} strategyKeys={meta?.strategy_keys} onSave={(body) => act(() => http.put("/settings", body), "Configuración actualizada")} />
              </TabsContent>
              <TabsContent value="optimizer" className="mt-0">
                <Optimizer settings={data.settings} refresh={refresh} intro={OPT_INTRO} aiAllIntro={AI_ALL_INTRO}
                  onSave={(body) => act(() => http.put("/settings", body), "Parámetros del bot actualizados")} />
              </TabsContent>
              <TabsContent value="news" className="mt-0"><FxNewsPanel summary={data.news} symbols={data.settings.symbols} currencyNames={meta?.currency_names} /></TabsContent>
              <TabsContent value="plan" className="mt-0">
                <GrowthPlan account={data.account} settings={data.settings} checkin={data.checkin} refresh={refresh} currency="USD" intro={PLAN_INTRO(brand)}
                  onSave={(body) => act(() => http.put("/settings", body), "Plan actualizado")} />
              </TabsContent>
            </Tabs>
          </Panel>
          <AiSignals signals={data.signals} ai={data.status.ai} usage={data.status.ai_usage} minConfidence={data.settings.ai_min_confidence} />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <SettingsPanel settings={data.settings} meta={meta} profileActive={data.profile_active} leverageMax={500} maxPositions={12} extraToggles={EXTRA_TOGGLES} extraNums={EXTRA_NUMS}
            extraChips={[...SESSION_CHIPS(meta), ...SEASON_CHIPS(meta)]} extraSelects={EXTRA_SELECTS} fmtLeverage={fmtLev} strategyKeys={meta?.strategy_keys} longKeys={meta?.long_horizon_keys}
            onSave={(body) => act(() => http.put("/settings", body), "Configuración guardada")}
            onProfile={(level) => act(() => http.post("/profile", { level }), `Perfil de riesgo aplicado: ${meta?.profiles?.[level]?.name || level}`)} />
          <Terminal logs={data.logs} onClear={() => act(() => http.delete("/logs"))} />
        </div>
        <p className="pb-4 pt-2 font-mono text-[10px] uppercase tracking-wider text-slate-600">
          Simulación con precios reales de Yahoo Finance · Lotes y pips según especificaciones de {brand.broker}{data.status.costs?.commission_lot_usd ? ` · comisión ${data.status.costs.commission_lot_usd} USD/lote` : ""} · No es asesoría financiera · El apalancamiento implica alto riesgo
        </p>
      </main>
      )}
    </div>
    </BrandContext.Provider>
  );
}
