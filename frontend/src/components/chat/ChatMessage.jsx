import { Sparkles, Wrench, Bell, AlertCircle } from "lucide-react";
import { fmtTime } from "@/lib/api";
import { Markdown } from "./Markdown";
import { ProposalCard } from "./ProposalCard";

const TOOL_LABELS = {
  get_performance: "rendimiento", get_trades: "historial", get_signals: "señales", get_logs: "logs", get_options: "opciones", get_market_context: "mercado", get_news: "noticias",
  get_evolution: "evolución", get_plan_checkin: "plan", get_guard: "horario y eventos", get_rules: "reglas aprendidas", run_walk_forward: "walk-forward",
  list_code: "código", read_code: "código", search_code: "código", propose_settings_change: "propuesta", propose_config_change: "propuesta", propose_action: "propuesta", remember: "memoria",
};

export function ChatMessage({ m, onResolve, resolving }) {
  if (m.role === "event") {
    return (
      <div data-testid={`chat-event-${m.id}`} className="flex items-start justify-center gap-1.5 px-6 py-1 text-center font-mono text-[10.5px] leading-snug text-slate-500">
        <Bell className="mt-0.5 h-3 w-3 shrink-0 text-amber-500/70" /> <span>{m.content}</span>
      </div>
    );
  }
  if (m.role === "user") {
    return (
      <div data-testid={`chat-msg-user-${m.id}`} className="flex justify-end">
        <div className="max-w-[88%] rounded-md rounded-br-sm border border-amber-400/30 bg-amber-500/10 px-3.5 py-2.5 text-[13px] leading-relaxed text-amber-50 whitespace-pre-wrap">
          {m.content}
          <div className="mt-1 text-right font-mono text-[9.5px] text-amber-400/60">{fmtTime(m.ts)}</div>
        </div>
      </div>
    );
  }
  const tools = [...new Set((m.tools || []).map((t) => TOOL_LABELS[t] || t))];
  return (
    <div data-testid={`chat-msg-assistant-${m.id}`} className="flex gap-2.5">
      <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-sm border ${m.error ? "border-rose-500/50 bg-rose-500/10 text-rose-300" : "border-amber-400/50 bg-gradient-to-br from-amber-400/20 to-black text-amber-300"}`}>
        {m.error ? <AlertCircle className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className={`rounded-md rounded-tl-sm border px-3.5 py-2.5 ${m.error ? "border-rose-500/40 bg-rose-500/5" : "border-[#1E293B] bg-[#0D111A]"}`}>
          {m.error ? <p data-testid="chat-error-message" className="text-[13px] text-rose-200">{m.content}</p> : <Markdown text={m.content} />}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[9.5px] text-slate-500">
            <span>{fmtTime(m.ts)}</span>
            {tools.length > 0 && (
              <span className="inline-flex items-center gap-1 rounded-sm border border-[#1E293B] px-1.5 py-0.5 text-slate-400" title={`Herramientas: ${(m.tools || []).join(", ")}`}>
                <Wrench className="h-2.5 w-2.5" /> {tools.join(" · ")}
              </span>
            )}
          </div>
        </div>
        {(m.proposals || []).map((p) => <ProposalCard key={p.id} p={p} onResolve={onResolve} busy={resolving} />)}
      </div>
    </div>
  );
}
