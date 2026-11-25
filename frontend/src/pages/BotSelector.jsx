import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap, Landmark, Flame, FlaskConical, ArrowRight, LogOut, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, setApiBase, fmtUsd, fmtPct, pnlClass } from "@/lib/api";

const BOTS = [
  {
    key: "crypto", path: "/bybit", icon: Zap, accent: "amber",
    title: <>Bybit <span className="text-amber-400">AI</span> Trader</>,
    subtitle: "Criptomonedas · Futuros perpetuos USDT",
    desc: "Bot cripto con Embudo IA: filtro técnico, modelo rápido y modelo potente dentro del horario de Nueva York, con bloqueo por eventos rojos y reglas aprendidas. Modo REAL en Bybit.",
    tags: ["BTC · ETH · SOL · XRP…", "Paper / REAL Bybit", "Bitget espejo"],
  },
  {
    key: "fx", path: "/hotforex", icon: Landmark, accent: "sky",
    title: <>HotForex <span className="text-sky-400">AI</span> Trading</>,
    subtitle: "Forex y metales · Oro, plata, EUR, GBP, CHF, CAD, NZD, JPY",
    desc: "Mismo Embudo IA adaptado a divisas: lotes y pips, volumen de ticks del bróker, bloqueo por eventos rojos de cada divisa y cuenta real HFM vía puente MetaTrader propio.",
    tags: ["XAUUSD · XAGUSD · EURUSD…", "Paper trading · 1:100", "Yahoo Finance · HFM"],
  },
  {
    key: "pepper", path: "/pepperstone", icon: Flame, accent: "teal", overview: "/pepper/overview",
    title: <>Pepperstone <span className="text-teal-300">AI</span> trading</>,
    subtitle: "Forex y metales · Pepperstone vía puente MetaTrader propio (MT4 / MT5)",
    desc: "Instancia independiente del motor forex para Pepperstone: cuenta Razor (spread crudo + comisión) o Standard, símbolos sin sufijo, sus propios ajustes, plan, revisión IA y Telegram.",
    tags: ["XAUUSD · XAGUSD · EURUSD…", "Razor / Standard", "Yahoo Finance · Pepperstone"],
  },
];
const ACCENT = {
  fuchsia: { ring: "hover:border-fuchsia-500/60 hover:shadow-[0_0_0_1px_rgba(217,70,239,0.35),0_30px_80px_-30px_rgba(217,70,239,0.35)]", mark: "bg-fuchsia-500 text-[#07090E]", btn: "bg-fuchsia-500 text-[#07090E] hover:bg-fuchsia-400", tag: "border-fuchsia-500/30 text-fuchsia-300" },
  amber: { ring: "hover:border-amber-500/60 hover:shadow-[0_0_0_1px_rgba(247,166,0,0.35),0_30px_80px_-30px_rgba(247,166,0,0.35)]", mark: "bg-amber-500 text-[#07090E]", btn: "bg-amber-500 text-[#07090E] hover:bg-amber-400", tag: "border-amber-500/30 text-amber-300" },
  sky: { ring: "hover:border-sky-500/60 hover:shadow-[0_0_0_1px_rgba(56,189,248,0.35),0_30px_80px_-30px_rgba(56,189,248,0.35)]", mark: "bg-sky-500 text-[#07090E]", btn: "bg-sky-500 text-[#07090E] hover:bg-sky-400", tag: "border-sky-500/30 text-sky-300" },
  teal: { ring: "hover:border-teal-400/60 hover:shadow-[0_0_0_1px_rgba(45,212,191,0.35),0_30px_80px_-30px_rgba(45,212,191,0.35)]", mark: "bg-teal-400 text-[#07090E]", btn: "bg-teal-400 text-[#07090E] hover:bg-teal-300", tag: "border-teal-400/30 text-teal-300" },
};

function Status({ s }) {
  if (!s) return <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500"><Loader2 className="h-3 w-3 animate-spin" /> Cargando estado</span>;
  if (s.error) return <span data-testid="bot-card-status-error" className="font-mono text-[10px] uppercase tracking-wider text-rose-400">Sin respuesta del motor</span>;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px]">
      <span className={`inline-flex items-center gap-1.5 uppercase tracking-wider ${s.running ? "text-emerald-400" : "text-slate-400"}`}>
        <span className={`pulse-dot ${s.running ? "bg-emerald-400" : "bg-slate-500"}`} /> {s.running ? "Bot en ejecución" : "Bot detenido"}
      </span>
      <span className="text-slate-300">Equidad <span className="num text-slate-100">{fmtUsd(s.equity)}</span></span>
      <span className={`num ${pnlClass(s.total_return_pct)}`}>{fmtPct(s.total_return_pct)}</span>
      <span className="text-slate-400">{s.open_positions} {s.open_positions === 1 ? "posición" : "posiciones"}</span>
      {s.mode && <span className={`rounded-sm border px-1.5 py-px text-[9px] uppercase ${s.mode === "REAL" ? "border-rose-500/50 text-rose-300" : "border-[#1E293B] text-slate-400"}`}>{s.mode}</span>}
      {s.market_open === false && <span className="rounded-sm border border-slate-600 px-1.5 py-px text-[9px] uppercase text-slate-400">Mercado cerrado</span>}
    </div>
  );
}

export default function BotSelector({ onLogout }) {
  const nav = useNavigate();
  const [status, setStatus] = useState({});

  useEffect(() => {
    setApiBase("");
    document.title = "AI Trading · Selecciona un bot";
    const load = async () => {
      const [c, f, p] = await Promise.allSettled([api.get("/overview"), api.get("/fx/overview"), api.get("/pepper/overview")]);
      const v = (r) => (r.status === "fulfilled" ? r.value.data : { error: true });
      setStatus({ crypto: v(c), fx: v(f), pepper: v(p) });
    };
    load();
  }, []);

  return (
    <div data-testid="bot-selector" className="app-bg grain min-h-screen px-6 py-10 md:px-12 md:py-16">
      <div className="mx-auto max-w-6xl">
        <div className="mb-10 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-slate-500">Panel de control · acceso verificado</p>
            <h1 className="font-display mt-2 text-3xl font-extrabold uppercase leading-none tracking-tight text-slate-50 md:text-4xl">¿Qué bot quieres operar?</h1>
            <p className="mt-3 max-w-xl text-sm text-slate-400">Bots independientes, cada uno con su cuenta paper, su historial y sus ajustes. Los tres operan con el mismo embudo de IA, horario operativo y bloqueo por eventos rojos.</p>
          </div>
          <Button data-testid="selector-logout-button" variant="ghost" onClick={onLogout} className="h-9 font-mono text-[11px] uppercase tracking-wider text-slate-400 hover:bg-[#111827] hover:text-rose-300">
            <LogOut className="mr-1.5 h-3.5 w-3.5" /> Cerrar sesión
          </Button>
        </div>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {BOTS.map((b, i) => {
            const a = ACCENT[b.accent];
            const Icon = b.icon;
            return (
              <button key={b.key} data-testid={`bot-card-${b.key}`} onClick={() => nav(b.path)}
                className={`panel rise group flex flex-col text-left transition-[border-color,box-shadow,transform] duration-300 hover:-translate-y-1 ${a.ring}`} style={{ animationDelay: `${i * 90}ms` }}>
                <div className="flex items-start justify-between gap-4 border-b border-[#1E293B] p-6">
                  <div className="flex items-center gap-4">
                    <span className={`flex h-12 w-12 items-center justify-center rounded-md ${a.mark}`}><Icon className="h-6 w-6" strokeWidth={2.4} /></span>
                    <div>
                      <h2 className="font-display text-xl font-extrabold uppercase leading-none tracking-tight text-slate-50">{b.title}</h2>
                      <p className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-slate-500">{b.subtitle}</p>
                    </div>
                  </div>
                  <ArrowRight className="mt-2 h-5 w-5 shrink-0 text-slate-600 transition-transform duration-300 group-hover:translate-x-1 group-hover:text-slate-200" />
                </div>
                <div className="flex flex-1 flex-col gap-5 p-6">
                  <p className="text-[13px] leading-relaxed text-slate-300">{b.desc}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {b.tags.map((t) => <span key={t} className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] ${a.tag}`}>{t}</span>)}
                  </div>
                  <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-[#1E293B] pt-4">
                    <Status s={status[b.key]} />
                    <span className={`inline-flex h-8 items-center rounded-sm px-3 font-mono text-[11px] font-bold uppercase tracking-wider ${a.btn}`}>Entrar</span>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        <p className="mt-10 font-mono text-[10px] uppercase tracking-wider text-slate-600">No es asesoría financiera · El trading con apalancamiento implica alto riesgo</p>
      </div>
    </div>
  );
}
