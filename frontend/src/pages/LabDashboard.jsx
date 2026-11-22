import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { FlaskConical, Layers, Loader2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { http, errMsg, setApiBase } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import Header from "@/components/bot/Header";
import KpiStrip from "@/components/bot/KpiStrip";
import Watchlist from "@/components/bot/Watchlist";
import PriceChart from "@/components/bot/PriceChart";
import PositionsTable from "@/components/bot/PositionsTable";
import TradeHistory from "@/components/bot/TradeHistory";
import Backtest from "@/components/bot/Backtest";
import Performance from "@/components/bot/Performance";
import AiSignals from "@/components/bot/AiSignals";
import SettingsPanel from "@/components/bot/SettingsPanel";
import Terminal from "@/components/bot/Terminal";
import { Panel } from "@/components/bot/Panel";

const TAB_CLS = "rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider data-[state=active]:bg-fuchsia-500 data-[state=active]:text-[#07090E]";
const brandOf = (suffix) => ({
  title: <>Bybit <span className="text-fuchsia-400">Lab{suffix}</span></>,
  subtitle: () => "Investigación de estrategias · solo paper · test cruce EMA 9/21",
  icon: FlaskConical,
  liveLabel: () => "Paper",
  dataLabel: (s) => (s.provider === "bitget" ? "Datos: Bitget (espejo)" : "Datos: Bybit"),
  dataTooltip: () => "Comparte los precios y velas del bot de Bybit. Este bot nunca envía órdenes reales.",
  resetHint: "Se borrarán posiciones, historial, cruces detectados y curva de equidad del laboratorio. Elige abajo el capital ficticio con el que empieza la cuenta.",
});
const INTRO = (s) => `Test cruce EMA 9/21 sobre las últimas 1.000 velas reales con los ajustes guardados del laboratorio (entrada confirmada, filtro ADX, parcial + breakeven): ${s.risk_per_trade}% por operación, ${s.leverage}x y comisiones 0.055%.`;

export default function LabDashboard({ onLogout, bot = "lab", suffix = "" }) {
  setApiBase(bot);
  const nav = useNavigate();
  const [meta, setMeta] = useState(null);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [busy, setBusy] = useState(false);
  const { data, refresh } = usePolling(() => http.get("/dashboard").then((r) => r.data), 3000);

  useEffect(() => {
    document.title = `Bybit Lab${suffix} · Investigación`;
    http.get("/meta").then((r) => setMeta(r.data)).catch(() => toast.error("No se pudo cargar la configuración"));
  }, [suffix]);

  const pairs = data?.settings.ema_test_symbols;
  useEffect(() => {
    if (pairs?.length && !pairs.includes(symbol)) setSymbol(pairs[0]);
  }, [pairs, symbol]);

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
        <Loader2 className="h-4 w-4 animate-spin text-fuchsia-400" /> Conectando con el laboratorio...
      </div>
    );
  }

  const toggleBot = async () => {
    setBusy(true);
    const running = data.status.running;
    await act(() => http.post(running ? "/bot/stop" : "/bot/start"), running ? "Laboratorio detenido" : "Laboratorio iniciado · paper 24/7");
    setBusy(false);
  };
  const btSettings = { ...data.settings, symbols: pairs?.length ? pairs : ["BTCUSDT"], strategy: "ema_test" };

  return (
    <div data-testid="lab-dashboard" className="app-bg grain min-h-screen">
      <Header status={data.status} busy={busy} onToggle={toggleBot} view="panel" onLogout={onLogout} onSwitchBot={() => nav("/")} bot={bot} brand={brandOf(suffix)} hideChat
        onScan={() => act(() => http.post("/bot/scan"), "Escaneo solicitado")}
        resetCapital={data.account.initial_balance}
        onReset={(capital) => act(() => http.post("/account/reset", { initial_balance: capital }), `Cuenta del laboratorio reiniciada con $${capital} USDT`)} />
      <main className="mx-auto max-w-[1920px] space-y-4 p-4 md:p-6">
        <div data-testid="lab-banner" className="rounded-md border border-fuchsia-500/30 bg-fuchsia-500/[0.07] px-4 py-2 text-[12px] text-fuchsia-100">
          Clon de Bybit solo para investigar: opera únicamente el <b>Test cruce EMA 9/21</b> en paper, con su propia cuenta, historial y ajustes. No usa IA, noticias ni reglas aprendidas y no puede pasar a modo REAL.
        </div>
        <KpiStrip account={data.account} />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <Watchlist items={data.watchlist} selected={symbol} onSelect={setSymbol} />
          <PriceChart symbol={symbol} defaultTf={data.settings.timeframe} />
          <AiSignals rulesOnly signals={data.signals} title="Cruces detectados" emptyText="Cuando el laboratorio detecte un cruce de EMAs válido aparecerá aquí." className="lg:col-span-3" />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 lg:items-start">
          <Panel testId="operations-panel" className="lg:col-span-7" delay={220}>
            <Tabs defaultValue="positions" className="flex h-full flex-col">
              <div className="flex items-center gap-3 border-b border-[#1E293B] px-4 py-2.5">
                <Layers className="h-4 w-4 shrink-0 text-fuchsia-400" />
                <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto bg-transparent p-0 [scrollbar-width:none]">
                  <TabsTrigger data-testid="tab-positions" value="positions" className={TAB_CLS}>Posiciones ({data.positions.length})</TabsTrigger>
                  <TabsTrigger data-testid="tab-history" value="history" className={TAB_CLS}>Historial</TabsTrigger>
                  <TabsTrigger data-testid="tab-backtest" value="backtest" className={TAB_CLS}>Backtest</TabsTrigger>
                  <TabsTrigger data-testid="tab-performance" value="performance" className={TAB_CLS}>Rendimiento</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="positions" className="mt-0">
                <PositionsTable aiReview={false} positions={data.positions} onClose={(id) => act(() => http.post(`/positions/${id}/close`), "Posición cerrada")}
                  onCloseAll={() => act(() => http.post("/positions/close-all"), "Todas las posiciones cerradas")} />
              </TabsContent>
              <TabsContent value="history" className="mt-0"><TradeHistory trades={data.trades} /></TabsContent>
              <TabsContent value="backtest" className="mt-0"><Backtest meta={meta} settings={btSettings} intro={INTRO} /></TabsContent>
              <TabsContent value="performance" className="mt-0">
                <Performance tradeCount={data.account.total_trades} settings={data.settings} strategyKeys={[]} onSave={() => {}} />
              </TabsContent>
            </Tabs>
          </Panel>
          <SettingsPanel testOnly settings={data.settings} meta={meta} hide={["max_positions", "ai_min_confidence", "min_rr", "ai_enabled", "ai_funnel_enabled", "breakeven_enabled", "trailing_enabled", "htf_filter"]}
            onSave={(body) => act(() => http.put("/settings", body), "Ajustes del laboratorio guardados")} />
        </div>
        <Terminal logs={data.logs} onClear={() => act(() => http.delete("/logs"))} />
        <p className="pb-4 pt-2 font-mono text-[10px] uppercase tracking-wider text-slate-600">Simulación con precios reales · No es asesoría financiera</p>
      </main>
    </div>
  );
}
