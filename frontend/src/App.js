import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";
import "@/App.css";
import { Toaster, toast } from "sonner";
import { Layers, Loader2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { http, errMsg, getToken, setToken, setApiBase, fmtTime } from "@/lib/api";
import BotSelector from "@/pages/BotSelector";
import FxDashboard from "@/fx/FxDashboard";
import { usePolling } from "@/hooks/usePolling";
import Header from "@/components/bot/Header";
import Login from "@/components/bot/Login";
import AdminSettings from "@/components/bot/AdminSettings";
import Documentation from "@/components/docs/Documentation";
import VpsInstall from "@/components/deploy/VpsInstall";
import KpiStrip from "@/components/bot/KpiStrip";
import Watchlist from "@/components/bot/Watchlist";
import PriceChart from "@/components/bot/PriceChart";
import BotPanel from "@/components/bot/BotPanel";
import PositionsTable from "@/components/bot/PositionsTable";
import TradeHistory from "@/components/bot/TradeHistory";
import Backtest from "@/components/bot/Backtest";
import Performance from "@/components/bot/Performance";
import PairRanking from "@/components/bot/performance/PairRanking";
import GrowthPlan from "@/components/bot/GrowthPlan";
import Optimizer from "@/components/bot/Optimizer";
import NewsPanel from "@/components/bot/NewsPanel";
import AiSignals from "@/components/bot/AiSignals";
import EvolutionPanel from "@/components/bot/EvolutionPanel";
import { AiHealthBanner } from "@/components/bot/AiHealthBanner";
import SettingsPanel from "@/components/bot/SettingsPanel";
import RegimePanel from "@/components/bot/RegimePanel";
import Terminal from "@/components/bot/Terminal";
import { Panel } from "@/components/bot/Panel";

const TAB_CLS = "rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider data-[state=active]:bg-amber-500 data-[state=active]:text-[#07090E]";

function Dashboard({ onLogout }) {
  setApiBase("");
  const nav = useNavigate();
  const [meta, setMeta] = useState(null);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("panel");
  const { data, refresh } = usePolling(() => http.get("/dashboard").then((r) => r.data), 3000);

  useEffect(() => {
    document.title = "Bybit AI Trader · Panel de control";
    http.get("/meta").then((r) => setMeta(r.data)).catch(() => toast.error("No se pudo cargar la configuración"));
  }, []);

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
      <div data-testid="dashboard-loading" className="flex min-h-screen items-center justify-center gap-3 font-mono text-sm text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin text-amber-400" /> Conectando con el motor de trading...
      </div>
    );
  }

  const toggleBot = async () => {
    setBusy(true);
    const running = data.status.running;
    await act(() => http.post(running ? "/bot/stop" : "/bot/start"), running ? "Bot detenido" : "Bot iniciado · operando 24/7");
    setBusy(false);
  };

  return (
    <div data-testid="dashboard" className="app-bg grain min-h-screen">
      <Header status={data.status} busy={busy} onToggle={toggleBot} view={view} onLogout={onLogout} onSwitchBot={() => nav("/")} bot="crypto" onRefresh={refresh}
        onSettings={() => setView((v) => (v === "settings" ? "panel" : "settings"))}
        onDocs={() => setView((v) => (v === "docs" ? "panel" : "docs"))}
        onDeploy={() => setView((v) => (v === "deploy" ? "panel" : "deploy"))}
        onScan={() => act(() => http.post("/bot/scan"), "Escaneo solicitado")}
        onReset={() => act(() => http.post("/account/reset", {}), "Cuenta paper reiniciada")} />
      {view === "deploy" ? (
        <main className="mx-auto max-w-[1920px] p-4 md:p-6"><VpsInstall onBack={() => setView("panel")} /></main>
      ) : view === "docs" ? (
        <main className="mx-auto max-w-[1920px] p-4 md:p-6"><Documentation bot="crypto" onBack={() => setView("panel")} /></main>
      ) : view === "settings" ? (
        <main className="mx-auto max-w-[1920px] p-4 md:p-6"><AdminSettings account={data.account} refresh={refresh} /></main>
      ) : (
      <main className="mx-auto max-w-[1920px] space-y-4 p-4 md:p-6">
        <AiHealthBanner ai={data.status.ai} />
        {(data.status.exchange_error || data.status.broker_health?.ok === false) && (
          <div data-testid="exchange-error-banner" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-2 font-mono text-[11px] text-rose-200">
            {data.status.broker_health?.down_since
              ? <>Bybit sin respuesta desde las {fmtTime(data.status.broker_health.down_since)} ({data.status.broker_health.down_sec < 60 ? `${data.status.broker_health.down_sec} s` : `${Math.round(data.status.broker_health.down_sec / 60)} min`}) · {data.status.broker_health.error} · el bot no abrirá nuevas operaciones hasta reconectar{data.status.broker_health.alerted ? " · aviso enviado por Telegram" : ""}</>
              : <>Bybit sin respuesta: {data.status.exchange_error} · mostrando los últimos datos locales</>}
          </div>
        )}
        <KpiStrip account={data.account} />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <Watchlist items={data.watchlist} selected={symbol} onSelect={setSymbol} />
          <PriceChart symbol={symbol} defaultTf={data.settings.timeframe} />
          <BotPanel settings={data.settings} meta={meta} account={data.account} equity={data.equity} profileActive={data.profile_active} />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 lg:items-start">
          <Panel testId="operations-panel" className="lg:col-span-8" delay={220}>
            <Tabs defaultValue="positions" className="flex h-full flex-col">
              <div className="flex items-center gap-3 border-b border-[#1E293B] px-4 py-2.5">
                <Layers className="h-4 w-4 text-amber-400" />
                <TabsList className="h-auto gap-1 bg-transparent p-0">
                  <TabsTrigger data-testid="tab-positions" value="positions" className={TAB_CLS}>Posiciones ({data.positions.length})</TabsTrigger>
                  <TabsTrigger data-testid="tab-history" value="history" className={TAB_CLS}>Historial</TabsTrigger>
                  <TabsTrigger data-testid="tab-backtest" value="backtest" className={TAB_CLS}>Backtest</TabsTrigger>
                  <TabsTrigger data-testid="tab-performance" value="performance" className={TAB_CLS}>Rendimiento</TabsTrigger>
                  <TabsTrigger data-testid="tab-pairs" value="pairs" className={TAB_CLS}>Pares 30d</TabsTrigger>
                  <TabsTrigger data-testid="tab-optimizer" value="optimizer" className={TAB_CLS}>Optimizador</TabsTrigger>
                  <TabsTrigger data-testid="tab-news" value="news" className={`${TAB_CLS} relative`}>
                    Noticias
                    {(data.news?.risk === "ALTO" || data.news?.blocked || data.news?.critical?.length > 0) && (
                      <span data-testid="tab-news-alert" className="pulse-dot absolute -right-1 -top-1 bg-rose-400" />
                    )}
                  </TabsTrigger>
                  <TabsTrigger data-testid="tab-plan" value="plan" className={`${TAB_CLS} relative`}>
                    Plan de crecimiento
                    {(data.checkin?.ready_to_advance || data.checkin?.warnings?.length > 0) && (
                      <span data-testid="tab-plan-alert" className={`pulse-dot absolute -right-1 -top-1 ${data.checkin.ready_to_advance ? "bg-emerald-400" : "bg-rose-400"}`} />
                    )}
                  </TabsTrigger>
                  <TabsTrigger data-testid="tab-evolution" value="evolution" className={TAB_CLS}>Evolución</TabsTrigger>
                  <TabsTrigger data-testid="tab-regime" value="regime" className={TAB_CLS}>Régimen</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="evolution" className="mt-0"><EvolutionPanel /></TabsContent>
              <TabsContent value="regime" className="mt-0">
                <RegimePanel settings={data.settings} onSave={(body) => act(() => http.put("/settings", body), "Configuración actualizada")} />
              </TabsContent>
              <TabsContent value="positions" className="mt-0">
                <PositionsTable positions={data.positions} onClose={(id) => act(() => http.post(`/positions/${id}/close`), "Posición cerrada")}
                  onCloseAll={() => act(() => http.post("/positions/close-all"), "Cierre de emergencia ejecutado")} />
              </TabsContent>
              <TabsContent value="history" className="mt-0"><TradeHistory trades={data.trades} /></TabsContent>
              <TabsContent value="backtest" className="mt-0"><Backtest meta={meta} settings={data.settings} /></TabsContent>
              <TabsContent value="performance" className="mt-0">
                <Performance tradeCount={data.account.total_trades} settings={data.settings} strategyKeys={meta?.strategy_keys} onSave={(body) => act(() => http.put("/settings", body), "Configuración actualizada")} />
              </TabsContent>
              <TabsContent value="pairs" className="mt-0">
                <PairRanking tradeCount={data.account.total_trades} settings={data.settings} onSave={(body) => act(() => http.put("/settings", body), "Pares actualizados")} />
              </TabsContent>
              <TabsContent value="optimizer" className="mt-0">
                <Optimizer settings={data.settings} refresh={refresh} onSave={(body) => act(() => http.put("/settings", body), "Parámetros del bot actualizados")} />
              </TabsContent>
              <TabsContent value="news" className="mt-0"><NewsPanel summary={data.news} symbols={data.settings.symbols} /></TabsContent>
              <TabsContent value="plan" className="mt-0">
                <GrowthPlan account={data.account} settings={data.settings} checkin={data.checkin} refresh={refresh}
                  onSave={(body) => act(() => http.put("/settings", body), "Plan actualizado")} />
              </TabsContent>
            </Tabs>
          </Panel>
          <AiSignals signals={data.signals} ai={data.status.ai} usage={data.status.ai_usage} minConfidence={data.settings.ai_min_confidence} />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <SettingsPanel settings={data.settings} meta={meta} profileActive={data.profile_active} strategyKeys={meta?.strategy_keys} longKeys={meta?.long_horizon_keys}
            extraNums={[{ k: "max_long_positions", label: "Máx. posiciones largo plazo", min: 1, max: 6, step: 1 }, { k: "maker_wait_bars", label: "Espera orden maker (velas)", min: 1, max: 3, step: 1 },
              { k: "be_start_r", label: "Breakeven a (R)", min: 0.5, max: 3, step: 0.25 }, { k: "trail_start_r", label: "Trailing desde (R)", min: 0.5, max: 3, step: 0.25 },
              { k: "trail_atr_mult", label: "Distancia trailing (ATR)", min: 0.5, max: 3, step: 0.25 }, { k: "partial_tp_r", label: "Cierre parcial a (R)", min: 0.5, max: 3, step: 0.25 },
              { k: "partial_tp_pct", label: "Cierre parcial (%)", min: 25, max: 75, step: 5 }]}
            extraToggles={[{ k: "btc_regime_filter", label: "Filtro régimen BTC", hint: "Cortos plazos solo a favor de BTC vs SMA200 diaria" }, { k: "rotation_enabled", label: "Rotación semanal", hint: "Momentum 7 d ponderado por volumen: sesgo y tamaño por par" },
              { k: "partial_tp_enabled", label: "Cierre parcial de beneficios", hint: "Cierra una parte al alcanzar el nivel en R y deja correr el resto con trailing" }]}
            onSave={(body) => act(() => http.put("/settings", body), "Configuración guardada")}
            onProfile={(level) => act(() => http.post("/profile", { level }), `Perfil de riesgo aplicado: ${meta?.profiles?.[level]?.name || level}`)} />
          <Terminal logs={data.logs} onClear={() => act(() => http.delete("/logs"))} />
        </div>
        <p className="pb-4 pt-2 font-mono text-[10px] uppercase tracking-wider text-slate-600">
          Simulación con precios reales · No es asesoría financiera · El trading con apalancamiento implica alto riesgo
        </p>
      </main>
      )}
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(() => !!getToken());
  useEffect(() => {
    const onLogout = () => { setAuthed(false); toast.error("Sesión cerrada · vuelve a introducir la clave"); };
    window.addEventListener("auth:logout", onLogout);
    return () => window.removeEventListener("auth:logout", onLogout);
  }, []);
  const logout = () => { setToken(null); setAuthed(false); };
  return (
    <BrowserRouter>
      {authed ? (
        <Routes>
          <Route path="/" element={<BotSelector onLogout={logout} />} />
          <Route path="/bybit" element={<Dashboard onLogout={logout} />} />
          <Route path="/hotforex" element={<FxDashboard onLogout={logout} bot="fx" />} />
          <Route path="/pepperstone" element={<FxDashboard onLogout={logout} bot="pepper" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      ) : <Login onLogin={() => setAuthed(true)} />}
      <Toaster theme="dark" position="bottom-right" toastOptions={{ style: { background: "#0D111A", border: "1px solid #1E293B", fontFamily: "Outfit" } }} />
    </BrowserRouter>
  );
}
