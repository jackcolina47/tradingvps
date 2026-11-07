import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const MODES = { auto: "Actuar automáticamente (cerrar / SL a breakeven)", notify: "Solo avisar (Telegram y panel)" };

export default function AiPositionReviewSettings({ form, set }) {
  const on = !!form.ai_position_review_enabled;
  return (
    <div data-testid="settings-ai-position-review" className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-3">
      <label className="flex cursor-pointer items-center justify-between gap-3">
        <span>
          <span className="block text-[13px] text-slate-200">Revisión IA de posiciones abiertas</span>
          <span className="block text-[11px] text-slate-500">Cada cierto tiempo la IA relee el gráfico de cada posición (velas recientes, swings, velas extendidas) y decide MANTENER, mover el SL a BREAKEVEN o CERRAR si el mercado se ha vuelto en contra.</span>
        </span>
        <Switch data-testid="settings-ai-position-review-enabled-switch" checked={on} onCheckedChange={(v) => set("ai_position_review_enabled", v)} disabled={!form.ai_enabled} />
      </label>
      {!form.ai_enabled && <p className="mt-1 text-[11px] text-amber-300/80">Requiere «Confirmación con IA» activada.</p>}
      {on && (
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Cada (minutos)</p>
            <Input data-testid="settings-ai-position-review-minutes-input" type="number" step={5} min={5} max={240} value={form.ai_position_review_minutes ?? 30}
              onChange={(e) => set("ai_position_review_minutes", e.target.value)} className="num h-9 border-[#1E293B] bg-[#07090E] text-xs" />
          </div>
          <div>
            <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Qué puede hacer</p>
            <Select value={form.ai_position_review_mode || "auto"} onValueChange={(v) => set("ai_position_review_mode", v)}>
              <SelectTrigger data-testid="settings-ai-position-review-mode-select" className="h-9 border-[#1E293B] bg-[#07090E] font-mono text-xs"><SelectValue /></SelectTrigger>
              <SelectContent className="border-[#1E293B] bg-[#0D111A]">
                {Object.entries(MODES).map(([k, v]) => <SelectItem key={k} value={k} className="font-mono text-xs">{v}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Confianza mín. para actuar (%)</p>
            <Input data-testid="settings-ai-position-review-min-confidence-input" type="number" step={5} min={50} max={100} value={form.ai_position_review_min_confidence ?? 75}
              onChange={(e) => set("ai_position_review_min_confidence", e.target.value)} disabled={form.ai_position_review_mode === "notify"} className="num h-9 border-[#1E293B] bg-[#07090E] text-xs" />
          </div>
        </div>
      )}
      {on && <p className="mt-2 text-[11px] text-slate-500">Consume una llamada de IA por posición abierta en cada ciclo. Cerrar o proteger solo ocurre con confianza ≥ al mínimo; con «Solo avisar» nunca toca la posición.</p>}
    </div>
  );
}
