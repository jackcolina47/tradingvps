import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Layers, Loader2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { http, errMsg, setApiBase, fmtTime } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import Header from "@/components/bot/Header";
import KpiStrip from "@/components/bot/KpiStrip";
import Watchlist from "@/components/bot/Watchlist";
import PriceChart from "@/components/bot/PriceChart";
import BotPanel from "@/components/bot/BotPanel";
import PositionsTable from "@/components/bot/PositionsTable";
import TradeHistory from "@/components/bot/TradeHistory";
import Performance from "@/components/bot/Performance";
import AiSignals from "@/components/bot/AiSignals";
import EvolutionPanel from "@/components/bot/EvolutionPanel";
import { AiHealthBanner } from "@/components/bot/AiHealthBanner";
import SettingsPanel from "@/components/bot/SettingsPanel";
import Terminal from "@/components/bot/Terminal";
import GrowthPlan from "@/components/bot/GrowthPlan";
import { Panel } from "@/components/bot/Panel";
import GuardStrip, { useGuard } from "@/components/bot/GuardStrip";
import RedCalendar from "@/components/bot/RedCalendar";
import SessionsStrip from "./SessionsStrip";
import FxAdminSettings from "./FxAdminSettings";
import Documentation from "@/components/docs/Documentation";
import VpsInstall from "@/components/deploy/VpsInstall";
import { BRANDS, BrandContext } from "./brand";

const TAB_CLS = "rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider data-[state=active]:bg-amber-500 data-[state=active]:text-[#07090E]";
const FX_TFS = ["5m", "15m", "1h", "4h", "1d"];
export const FX_BRAND = BRANDS.fx.header;
const EXTRA_TOGGLES = [
  { k: "close_before_weekend", label: "Cerrar antes del fin de semana", hint: "Cierra todo 30 min antes del cierre del viernes (evita gaps del domingo)" },
  { k: "rollover_guard", label: "Guardia de rollover (17:00 NY)", hint: "Sin entradas 16:50–17:15 Nueva York; en paper los stops no se evalúan durante el pico de spread" },
  { k: "partial_tp_enabled", label: "Cierre parcial de beneficios", hint: "Cierra una parte al alcanzar el nivel en R y deja correr el resto con trailing" },
];
const LEARN_TOGGLE = { k: "rules_auto_learn", label: "Aprendizaje automático", hint: "La IA analiza cada operación cerrada: de las pérdidas evitables guarda una regla nueva y apaga las reglas que habrían bloqueado 3 operaciones ganadoras. Gasta saldo de IA por operación cerrada" };
const EXTRA_NUMS = [
  { k: "min_lot_max_risk", label: "Riesgo máx. lote mínimo (%)", step: 0.5, min: 0, max: 10,
    hint: "Si el tamaño ideal queda por debajo de 0.01 lotes (típico en oro/plata con poco capital), se abre el lote mínimo siempre que su riesgo no supere este % de la equidad. 0 = nunca." },
  { k: "currency_exposure_limit", label: "Tope exposición por divisa", step: 1, min: 1, max: 6, hint: "Máximo de posiciones en la misma dirección sobre una misma divisa (long EURUSD + long GBPUSD = 2 cortos de USD)" },
  { k: "be_start_r", label: "Breakeven a (R)", min: 0.5, max: 3, step: 0.25 }, { k: "trail_start_r", label: "Trailing desde (R)", min: 0.5, max: 3, step: 0.25 },
  { k: "trail_atr_mult", label: "Distancia trailing (ATR)", min: 0.5, max: 3, step: 0.25 }, { k: "partial_tp_r", label: "Cierre parcial a (R)", min: 0.5, max: 3, step: 0.25 },
  { k: "partial_tp_pct", label: "Cierre parcial (%)", min: 25, max: 75, step: 5 },
];
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
  const { data: guard, refresh: refreshGuard } = useGuard();

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
  const newsAlert = Object.keys(guard?.blocked || {}).length > 0;

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
        <SessionsStrip hours={data.status.market_hours} />
        <GuardStrip guard={guard} />
        <KpiStrip account={data.account} />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <Watchlist items={data.watchlist} selected={symbol} onSelect={setSymbol} />
          <PriceChart symbol={symbol} defaultTf={data.settings.timeframe} kind={meta?.symbol_info?.[symbol]?.name || "Forex"} tfs={FX_TFS}
            trade={{ riskPct: data.settings.risk_per_trade, equity: data.account.equity, live: data.status.mode === "REAL", currency: "USD", onDone: refresh }} />
          <BotPanel mtf settings={data.settings} meta={meta} account={data.account} equity={data.equity} fmtLeverage={fmtLev} />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 lg:items-start">
          <Panel testId="fx-operations-panel" className="lg:col-span-8" delay={220}>
            <Tabs defaultValue="positions" className="flex h-full flex-col">
              <div className="flex items-center gap-3 border-b border-[#1E293B] px-4 py-2.5">
                <Layers className="h-4 w-4 shrink-0 text-amber-400" />
                <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto bg-transparent p-0 [scrollbar-width:none]">
                  <TabsTrigger data-testid="fx-tab-positions" value="positions" className={TAB_CLS}>Posiciones ({data.positions.length})</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-history" value="history" className={TAB_CLS}>Historial</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-performance" value="performance" className={TAB_CLS}>Rendimiento</TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-news" value="news" className={`${TAB_CLS} relative`}>
                    Eventos rojos
                    {newsAlert && <span data-testid="fx-tab-news-alert" className="pulse-dot absolute -right-1 -top-1 bg-rose-400" />}
                  </TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-plan" value="plan" className={`${TAB_CLS} relative`}>
                    Plan de crecimiento
                    {(data.checkin?.ready_to_advance || data.checkin?.warnings?.length > 0) && (
                      <span data-testid="fx-tab-plan-alert" className={`pulse-dot absolute -right-1 -top-1 ${data.checkin.ready_to_advance ? "bg-emerald-400" : "bg-rose-400"}`} />
                    )}
                  </TabsTrigger>
                  <TabsTrigger data-testid="fx-tab-evolution" value="evolution" className={TAB_CLS}>Evolución</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="evolution" className="mt-0"><EvolutionPanel /></TabsContent>
              <TabsContent value="positions" className="mt-0">
                <PositionsTable positions={data.positions} onClose={(id) => act(() => http.post(`/positions/${id}/close`), "Posición cerrada")}
                  onCloseAll={() => act(() => http.post("/positions/close-all"), "Cierre de emergencia ejecutado")} />
              </TabsContent>
              <TabsContent value="history" className="mt-0"><TradeHistory trades={data.trades} /></TabsContent>
              <TabsContent value="performance" className="mt-0">
                <Performance tradeCount={data.account.total_trades} settings={data.settings} onSave={(body) => act(() => http.put("/settings", body), "Configuración actualizada")} />
              </TabsContent>
              <TabsContent value="news" className="mt-0"><RedCalendar guard={guard} onRefresh={refreshGuard} /></TabsContent>
              <TabsContent value="plan" className="mt-0">
                <GrowthPlan account={data.account} settings={data.settings} checkin={data.checkin} refresh={refresh} currency="USD" intro={PLAN_INTRO(brand)}
                  onSave={(body) => act(() => http.put("/settings", body), "Plan actualizado")} />
              </TabsContent>
            </Tabs>
          </Panel>
          <AiSignals signals={data.signals} ai={data.status.ai} usage={data.status.ai_usage} minConfidence={data.settings.ai_min_confidence} title="Decisiones del embudo IA"
            emptyText="Aquí aparecen las decisiones BUY / SELL del modelo potente: las que superan el umbral de confianza se ejecutan y el resto se sigue como sombra." />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <SettingsPanel settings={data.settings} meta={meta} leverageMax={500} maxPositions={12}
            extraToggles={[LEARN_TOGGLE, ...EXTRA_TOGGLES]} extraNums={EXTRA_NUMS}
            onSave={(body) => act(() => http.put("/settings", body), "Configuración guardada")} />
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
