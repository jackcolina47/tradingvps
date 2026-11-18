import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, TrendingUp } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { http, errMsg } from "@/lib/api";
import PlanScenarios from "./plan/PlanScenarios";
import PlanProjection from "./plan/PlanProjection";
import PlanPhases from "./plan/PlanPhases";
import PlanCheckin from "./plan/PlanCheckin";

const FIELDS = (cur) => [
  { k: "initial_capital", label: `Capital inicial (${cur})`, min: 50, step: 50 },
  { k: "monthly_deposit", label: `Aporte mensual (${cur})`, min: 0, step: 25 },
  { k: "target_monthly", label: `Meta mensual (${cur})`, min: 50, step: 100 },
];
const DEFAULT_INTRO = "Plan de crecimiento compuesto: cuándo reinvertir, cuándo subir el riesgo y cuándo empezar a retirar. Los escenarios Prudente / Realista / Ambicioso son rentabilidades mensuales supuestas para la proyección para la proyección.";

export default function GrowthPlan({ account, settings, checkin, onSave, refresh, currency = "USDT", intro = DEFAULT_INTRO }) {
  const [form, setForm] = useState({
    initial_capital: account.initial_balance, monthly_deposit: settings.plan_monthly_deposit,
    target_monthly: settings.plan_target_monthly, scenario: settings.plan_scenario,
  });
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  const started = useRef(false);

  const run = useCallback(async (body) => {
    setLoading(true);
    try {
      const { data } = await http.post("/plan", body);
      if (!alive.current) return;
      setRes(data);
      refresh?.();
    } catch (e) {
      if (alive.current) toast.error(`Plan falló: ${errMsg(e)}`);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [refresh]);

  useEffect(() => {
    alive.current = true;
    if (!started.current) {
      started.current = true;
      run(form);
    }
    return () => { alive.current = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectScenario = (scenario) => {
    const next = { ...form, scenario };
    setForm(next);
    run(next);
  };

  return (
    <div data-testid="growth-plan-panel" className="space-y-5 p-4">
      {checkin && <PlanCheckin checkin={checkin} onRefresh={refresh} onAdvance={(phase, risk) => onSave({ plan_phase: phase, ...(risk ? { risk_per_trade: risk } : {}) })} />}
      <p className="text-[12px] leading-relaxed text-slate-400">{intro}</p>
      <div className="flex flex-wrap items-end gap-3">
        {FIELDS(currency).map((f) => (
          <div key={f.k} className="min-w-[150px] flex-1">
            <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{f.label}</p>
            <Input data-testid={`plan-${f.k.replace(/_/g, "-")}-input`} type="number" min={f.min} step={f.step} value={form[f.k]}
              onChange={(e) => setForm((s) => ({ ...s, [f.k]: Number(e.target.value) }))}
              className="h-9 border-[#1E293B] bg-[#07090E] font-mono text-xs" />
          </div>
        ))}
        <Button data-testid="plan-run-button" onClick={() => run(form)} disabled={loading} className="h-9 bg-amber-500 font-mono text-[11px] font-bold uppercase tracking-wider text-[#07090E] hover:bg-amber-400">
          {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <TrendingUp className="mr-1.5 h-3.5 w-3.5" />} Calcular
        </Button>
      </div>
      {!res && loading && <p className="font-mono text-[11px] text-slate-500">Calculando la proyección...</p>}
      {res && (
        <>
          <PlanScenarios scenarios={res.scenarios} selected={res.selected} onSelect={selectScenario} target={res.inputs.target_monthly} />
          <PlanProjection projection={res.projection} />
          <PlanPhases projection={res.projection} />
          <p className="font-mono text-[10px] uppercase tracking-wider text-slate-600">
            Proyección matemática con rendimientos promedio · los meses reales varían · no es asesoría financiera
          </p>
        </>
      )}
    </div>
  );
}
