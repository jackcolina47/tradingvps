import { Ghost } from "lucide-react";
import { TH, TD } from "../PositionsTable";
import { fmtSignedUsd, pnlClass, STRATEGY_LABELS, SESSION_NAMES } from "@/lib/api";

const NEWS_RISK = { ALTO: "Alto (alertas generales)", MEDIO: "Medio", BAJO: "Bajo", SIN_DATO: "Sin dato de noticias" };
const KIND = { RECHAZADA: "Rechazadas por IA", BAJA_CONFIANZA: "IA aprobó, confianza baja", BLOQUEADA: "Bloqueadas por riesgo", NOTICIAS: "Bloqueadas por noticias", FUNDING: "Bloqueadas por funding", REGIMEN: "Bloqueadas por régimen BTC", ROTACION: "Bloqueadas por rotación", EXPOSICION: "Bloqueadas por exposición", ROLLOVER: "Bloqueadas por rollover", VOLATILIDAD: "Bloqueadas por volatilidad", ESTACIONAL: "Bloqueadas por estacionalidad", REGIMEN_USD: "Bloqueadas por régimen USD", FOMC: "Bloqueadas por regla FOMC", PRECIO_EXTENDIDO: "Bloqueadas por precio extendido", FILTRO_IA: "Descartadas por el filtro rápido IA", RR_BAJO: "Bloqueadas por R:R insuficiente", SPREAD: "Bloqueadas por spread", SESION: "Fuera de sesión", ERROR: "Error de IA", NO_EJECUTADA: "Aprobadas, no abiertas" };
const sessionLabel = (k) => k.split("+").map((s) => SESSION_NAMES[s] || (s === "ninguna" ? "Sin sesión" : s)).join(" + ");

const Card = ({ label, value, hint, cls = "text-slate-100", testId }) => (
  <div data-testid={testId} className="min-w-[150px] flex-1 rounded-sm border border-[#1E293B] bg-[#0D111A] px-3 py-2.5">
    <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
    <p className={`num mt-1 text-base font-semibold ${cls}`}>{value}</p>
    {hint && <p className="text-[10px] text-slate-500">{hint}</p>}
  </div>
);

const Table = ({ title, label, rows, render = (k) => k, testId }) => (
  <div data-testid={testId} className="overflow-hidden rounded-sm border border-[#1E293B]">
    <p className="border-b border-[#1E293B] bg-[#0D111A] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">{title}</p>
    <table className="w-full text-sm">
      <thead><tr><TH>{label}</TH><TH right>Ops</TH><TH right>Acierto</TH><TH right>Evitó</TH><TH right>Se perdió</TH><TH right>PnL sombra</TH></tr></thead>
      <tbody className="num text-[12px]">
        {rows.filter((r) => r.trades > 0).map((r) => (
          <tr key={r.key} className="border-t border-[#1E293B]/60">
            <TD className="font-display text-[13px] font-semibold text-slate-100">{render(r.key)}</TD>
            <TD right>{r.trades}</TD>
            <TD right>{r.win_rate}%</TD>
            <TD right className="text-emerald-400">{fmtSignedUsd(r.avoided)}</TD>
            <TD right className="text-rose-400">{fmtSignedUsd(-r.missed)}</TD>
            <TD right className={`font-semibold ${pnlClass(r.pnl)}`}>{fmtSignedUsd(r.pnl)}</TD>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

export default function ShadowStats({ data }) {
  if (!data) return null;
  const t = data.total;
  const fv = data.filter_value;
  return (
    <div data-testid="shadow-stats" className="space-y-3 rounded-md border border-fuchsia-500/25 bg-fuchsia-500/[0.04] p-3.5">
      <div>
        <p className="flex items-center gap-1.5 font-display text-[14px] font-bold text-fuchsia-200"><Ghost className="h-4 w-4" /> Operaciones sombra · ¿acierta el filtro?</p>
        <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
          Cada señal que no se operó (rechazada por la IA, fuera de sesión, bloqueada por riesgo o noticias, con error o sin poder abrirse) se simula igual que una operación real
          (mismo tamaño, SL, TP y trailing). «Evitó» son las pérdidas que el filtro ahorró; «se perdió» las ganancias que dejó escapar. Valor neto positivo = el filtro suma.
          La IA recibe estos datos en la revisión automática semanal y en «Optimizar todo con IA».
        </p>
      </div>
      {t.trades === 0 ? (
        <p data-testid="shadow-empty" className="text-[12px] text-slate-500">Todavía no se ha cerrado ninguna operación sombra{data.open ? ` (${data.open} en seguimiento)` : ""}.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <Card label="Simuladas" value={t.trades} hint={`${data.open} abiertas${data.undersized ? ` · ${data.undersized} con lote mínimo forzado` : ""}${t.pips != null ? ` · ${t.pips > 0 ? "+" : ""}${t.pips} pips netos` : ""}`} testId="shadow-total" />
            <Card label="Acierto de las descartadas" value={`${t.win_rate}%`} testId="shadow-winrate" />
            <Card label="Pérdidas evitadas" value={fmtSignedUsd(t.avoided)} cls="text-emerald-400" testId="shadow-avoided" />
            <Card label="Ganancias perdidas" value={fmtSignedUsd(-t.missed)} cls="text-rose-400" testId="shadow-missed" />
            <Card label="Valor neto del filtro" value={fmtSignedUsd(fv)} cls={fv >= 0 ? "text-emerald-400" : "text-rose-400"}
              hint={fv >= 0 ? "El filtro está protegiendo la cuenta" : "El filtro está costando dinero"} testId="shadow-filter-value" />
            <Card label="Solo filtro IA" value={fmtSignedUsd(data.ai.filter_value)} cls={data.ai.filter_value >= 0 ? "text-emerald-400" : "text-rose-400"} hint={`${data.ai.trades} señales rechazadas por la IA`} testId="shadow-ai-value" />
          </div>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Table title="Por motivo del descarte" label="Motivo" rows={data.by_kind} render={(k) => KIND[k] || k} testId="shadow-by-kind" />
            <Table title="Rechazos de la IA por confianza" label="Confianza IA" rows={data.by_confidence} testId="shadow-by-confidence" />
            <Table title="Por estrategia" label="Estrategia" rows={data.by_strategy} render={(k) => STRATEGY_LABELS[k] || k} testId="shadow-by-strategy" />
            <Table title="Por par" label="Par" rows={data.by_symbol} testId="shadow-by-symbol" />
            {data.by_session?.length > 0 && <Table title="Por sesión del mercado" label="Sesión" rows={data.by_session} render={sessionLabel} testId="shadow-by-session" />}
            {data.ai_by_news_risk?.length > 0 && <Table title="Rechazos de la IA por riesgo de noticias" label="Riesgo de mercado" rows={data.ai_by_news_risk} render={(k) => NEWS_RISK[k] || k} testId="shadow-ai-by-news-risk" />}
          </div>
        </>
      )}
    </div>
  );
}
