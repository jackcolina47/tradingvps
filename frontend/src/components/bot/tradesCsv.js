import { REASON_LABELS, STRATEGY_LABELS } from "@/lib/api";

const esc = (v) => {
  const s = v == null ? "" : String(v).replace(/\r?\n/g, " ");
  return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const num = (v) => (v == null || v === "" ? "" : String(v).replace(".", ","));
const dt = (iso) => (iso ? new Date(iso).toLocaleString("es-ES", { hour12: false }) : "");

const COLUMNS = [
  ["Apertura", (t) => dt(t.opened_at)], ["Cierre", (t) => dt(t.closed_at)], ["Símbolo", (t) => t.symbol], ["Lado", (t) => (t.side === "long" ? "LONG" : "SHORT")],
  ["Estrategia", (t) => STRATEGY_LABELS[t.strategy] || t.strategy], ["Tipo entrada", (t) => t.entry_type || ""], ["Horizonte", (t) => t.horizon || ""],
  ["Entrada", (t) => num(t.entry_price)], ["Salida", (t) => num(t.exit_price)], ["SL inicial", (t) => num(t.initial_sl)], ["SL final", (t) => num(t.sl)], ["TP", (t) => num(t.tp)], ["ATR", (t) => num(t.atr)],
  ["Cantidad", (t) => num(t.qty)], ["Apalancamiento", (t) => num(t.leverage)], ["Margen", (t) => num(t.margin)], ["Comisiones", (t) => num(t.fees)],
  ["PnL", (t) => num(t.pnl)], ["PnL %", (t) => num(t.pnl_pct)], ["Motivo cierre", (t) => REASON_LABELS[t.reason] || t.reason], ["Duración (min)", (t) => num(t.hold_min)],
  ["MFE (R)", (t) => num(t.mfe_r)], ["MAE (R)", (t) => num(t.mae_r)], ["% camino al TP", (t) => num(t.mfe_tp_pct)], ["Breakeven", (t) => (t.breakeven ? "sí" : "no")], ["Cierre parcial", (t) => (t.partial_tp ? "sí" : "no")],
  ["Modo", (t) => (t.live ? "Real" : "Simulado")], ["Etiquetas", (t) => (t.tags || []).join(", ")], ["Nota", (t) => t.note || ""],
  ["Veredicto IA", (t) => t.ai_review?.verdict || ""], ["Confianza IA", (t) => num(t.ai_review?.confidence)], ["Motivo veredicto IA", (t) => t.ai_review?.verdict_reason || ""],
  ["Por qué se abrió (IA)", (t) => t.ai_review?.why_opened || ""], ["Gráfico (IA)", (t) => t.ai_review?.chart_context || ""], ["Qué pasó (IA)", (t) => t.ai_review?.what_happened || ""],
  ["Decisión mejor (IA)", (t) => t.ai_review?.better_decision || ""], ["Regla sugerida (IA)", (t) => t.ai_review?.lesson || ""], ["ID", (t) => t.id],
];

export function tradesToCsv(trades) {
  const rows = [COLUMNS.map(([h]) => esc(h)).join(";")];
  for (const t of trades) rows.push(COLUMNS.map(([, f]) => esc(f(t))).join(";"));
  return "\uFEFF" + rows.join("\r\n");
}

export function downloadCsv(trades, botLabel = "bot") {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const blob = new Blob([tradesToCsv(trades)], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `historial_${botLabel.toLowerCase().replace(/\s+/g, "_")}_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
