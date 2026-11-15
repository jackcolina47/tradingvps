import { useEffect, useState } from "react";
import { Zap, Play, Square, RefreshCw, RotateCcw, Database, Timer, Settings2, LogOut, LayoutGrid, BookOpen, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatLauncher } from "@/components/chat/ChatLauncher";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

const CRYPTO_BRAND = {
  title: <>Bybit <span className="text-amber-400">AI</span> Trader</>,
  subtitle: (status) => `Futuros perpetuos USDT · ${status.ai?.label || "IA"}`,
  icon: Zap,
  liveLabel: (status) => `Modo REAL · Bybit ${status.env}`,
  dataLabel: (status) => (status.provider === "bitget" ? "Datos: Bitget (espejo)" : "Datos: Bybit"),
  dataTooltip: (status) => (status.provider === "bitget"
    ? "Bybit bloquea el acceso desde la región del servidor (EE.UU.). El bot usa precios de futuros perpetuos USDT de Bitget, que replican a Bybit con diferencias mínimas. Desde una región permitida conecta Bybit automáticamente."
    : "Conectado directamente a la API pública v5 de Bybit."),
  resetHint: "Se borrarán posiciones, historial, señales y curva de equidad. El balance vuelve al capital inicial configurado (por defecto $500 USDT).",
};

const StatusPill = ({ status }) => {
  const { running, scanning } = status;
  const cls = scanning && running
    ? "bg-purple-500/10 text-purple-300 border-purple-500/30"
    : running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" : "bg-rose-500/10 text-rose-400 border-rose-500/30";
  const dot = scanning && running ? "bg-purple-400" : running ? "bg-emerald-400" : "bg-rose-400";
  const label = scanning && running ? "Escaneando" : running ? "Bot en ejecución" : "Bot detenido";
  return (
    <span data-testid="bot-status-badge" className={`inline-flex items-center gap-2 rounded-sm border px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider ${cls}`}>
      <span className={`pulse-dot ${dot}`} />
      {label}
    </span>
  );
};

export default function Header({ status, onToggle, onScan, onReset, busy, onSettings, onDocs, onDeploy, onLogout, view, onSwitchBot, brand = CRYPTO_BRAND, bot = "crypto", onRefresh, hideChat = false }) {
  const [count, setCount] = useState(status.next_scan_in);
  useEffect(() => setCount(status.next_scan_in), [status.next_scan_in]);
  useEffect(() => {
    const id = setInterval(() => setCount((c) => (c > 0 ? c - 1 : c)), 1000);
    return () => clearInterval(id);
  }, []);
  const Icon = brand.icon;

  return (
    <header data-testid="app-header" className="sticky top-0 z-30 border-b border-[#1E293B] bg-[#07090E]/85 backdrop-blur-xl">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 md:px-6">
        <div className="mr-auto flex items-center gap-3">
          <div className="logo-mark"><Icon className="h-5 w-5" strokeWidth={2.6} /></div>
          <div>
            <h1 className="font-display text-lg font-extrabold uppercase leading-none tracking-tight md:text-xl">
              {brand.title}
            </h1>
            <p data-testid="header-subtitle" className="mt-1 font-mono text-[10px] uppercase tracking-[0.18em] text-slate-500">{brand.subtitle(status)}</p>
          </div>
        </div>

        {status.mode === "REAL" && status.account_kind === "demo" ? (
          <span data-testid="live-demo-badge" title="Servidor demo del bróker: dinero ficticio. El bot opera, aprende y evalúa las fases exactamente igual que con dinero real."
            className="inline-flex items-center gap-1.5 rounded-sm border border-sky-500/50 bg-sky-500/15 px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-wider text-sky-300">
            <span className="pulse-dot bg-sky-400" /> {brand.liveLabel(status)} · <span className="rounded-[2px] bg-sky-400 px-1 text-[9.5px] text-[#07090E]">Cuenta demo</span>
          </span>
        ) : status.mode === "REAL" ? (
          <span data-testid="live-mode-badge" title={status.env} className="inline-flex items-center gap-1.5 rounded-sm border border-rose-500/50 bg-rose-500/15 px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-wider text-rose-300">
            <span className="pulse-dot bg-rose-400" /> {brand.liveLabel(status)}
          </span>
        ) : (
          <span data-testid="paper-mode-badge" className="rounded-sm border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider text-amber-400">
            Paper trading
          </span>
        )}
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span data-testid="data-source-badge" className="inline-flex cursor-help items-center gap-1.5 rounded-sm border border-[#1E293B] bg-[#0D111A] px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-slate-300">
                <Database className="h-3 w-3 text-sky-400" />
                {brand.dataLabel(status)}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs text-xs">{brand.dataTooltip(status)}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <span data-testid="next-scan-timer" className="inline-flex items-center gap-1.5 font-mono text-[11px] text-slate-400" title={status.running ? undefined : "El bot está detenido: el escaneo solo actualiza el mercado en vivo, no abre operaciones"}>
          <Timer className="h-3.5 w-3.5" /> {status.running ? "Próximo escaneo" : "Análisis en"} {count != null ? `${count}s` : "—"}
        </span>
        <StatusPill status={status} />

        <div className="flex flex-wrap items-center gap-2">
          <Button data-testid="scan-now-button" variant="outline" size="sm" onClick={onScan} className="h-8 border-[#1E293B] bg-transparent font-mono text-[11px] uppercase tracking-wider hover:bg-[#111827]">
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Escanear
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button data-testid="reset-account-button" variant="outline" size="sm" className="h-8 border-[#1E293B] bg-transparent font-mono text-[11px] uppercase tracking-wider hover:bg-[#111827]">
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Reiniciar
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent data-testid="reset-account-dialog" className="border-[#1E293B] bg-[#0D111A]">
              <AlertDialogHeader>
                <AlertDialogTitle className="font-display">¿Reiniciar la cuenta paper?</AlertDialogTitle>
                <AlertDialogDescription>{brand.resetHint}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel data-testid="reset-cancel-button">Cancelar</AlertDialogCancel>
                <AlertDialogAction data-testid="reset-confirm-button" onClick={onReset} className="bg-rose-500 text-white hover:bg-rose-600">
                  Reiniciar cuenta
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button
            data-testid="bot-toggle-button"
            onClick={onToggle}
            disabled={busy}
            className={`h-8 font-mono text-[11px] font-bold uppercase tracking-wider transition-colors ${
              status.running ? "border border-rose-500/50 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20" : "bg-amber-500 text-[#07090E] hover:bg-amber-400"
            }`}
          >
            {status.running ? <><Square className="mr-1.5 h-3.5 w-3.5" /> Detener bot</> : <><Play className="mr-1.5 h-3.5 w-3.5" /> Iniciar bot</>}
          </Button>
          {onSettings && <Button data-testid="settings-view-button" variant="outline" size="sm" onClick={onSettings}
            className={`h-8 border-[#1E293B] font-mono text-[11px] uppercase tracking-wider hover:bg-[#111827] ${view === "settings" ? "bg-[#111827] text-amber-300" : "bg-transparent"}`}>
            <Settings2 className="mr-1.5 h-3.5 w-3.5" /> {view === "settings" ? "Volver al panel" : "Ajustes"}
          </Button>}
          {onDocs && (
            <Button data-testid="docs-view-button" size="sm" onClick={onDocs} title="Características, cómo usar el bot y preguntas frecuentes"
              className={`docs-glow h-8 font-mono text-[11px] font-bold uppercase tracking-wider transition-transform hover:-translate-y-px ${
                view === "docs" ? "bg-[#111827] text-amber-300 ring-1 ring-amber-500/60 hover:bg-[#1a2333]" : "bg-gradient-to-r from-amber-400 via-orange-400 to-rose-400 text-[#07090E] hover:from-amber-300 hover:via-orange-300 hover:to-rose-300"}`}>
              <BookOpen className="mr-1.5 h-3.5 w-3.5" /> {view === "docs" ? "Volver al panel" : "Documentación"}
            </Button>
          )}
          {onDeploy && (
            <Button data-testid="deploy-view-button" size="sm" onClick={onDeploy} title="Descarga la app completa e instálala en tu propio servidor, paso a paso"
              className={`vps-glow h-8 font-mono text-[11px] font-bold uppercase tracking-wider transition-transform hover:-translate-y-px ${
                view === "deploy" ? "bg-[#111827] text-emerald-300 ring-1 ring-emerald-500/60 hover:bg-[#1a2333]" : "border border-emerald-400/60 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25"}`}>
              <Server className="mr-1.5 h-3.5 w-3.5" /> {view === "deploy" ? "Volver al panel" : "Instalar en tu VPS"}
            </Button>
          )}
          {!hideChat && <ChatLauncher bot={bot} onApplied={onRefresh} />}
          {onSwitchBot && (
            <Button data-testid="switch-bot-button" variant="outline" size="sm" onClick={onSwitchBot} title="Volver a la selección de bot"
              className="h-8 border-sky-500/40 bg-transparent font-mono text-[11px] uppercase tracking-wider text-sky-300 hover:bg-sky-500/10">
              <LayoutGrid className="mr-1.5 h-3.5 w-3.5" /> Cambiar bot
            </Button>
          )}
          <Button data-testid="logout-button" variant="ghost" size="sm" onClick={onLogout} className="h-8 px-2 font-mono text-[11px] uppercase tracking-wider text-slate-400 hover:bg-[#111827] hover:text-rose-300" title="Cerrar sesión">
            <LogOut className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </header>
  );
}
