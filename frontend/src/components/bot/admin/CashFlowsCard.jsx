import { useCallback, useEffect, useState } from "react";
import { ArrowDownToLine, ArrowUpFromLine, Landmark, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { http, errMsg, fmtSignedUsd, fmtDateTime, pnlClass } from "@/lib/api";
import { Field, INPUT, BTN_OUT } from "./SecuritySection";

const SOURCE = { paper: "cuenta paper", bybit: "Bybit", hfm: "bróker (puente MT)" };

// Deposits / withdrawals: manual in paper (simulate the plan's monthly contributions), auto-detected from the broker in REAL mode.
export default function CashFlowsCard({ account, refresh, currency = "USDT" }) {
  const [data, setData] = useState(null);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const live = account.mode === "REAL";

  const load = useCallback(() => http.get("/account/cashflows").then((r) => setData(r.data)).catch(() => {}), []);
  useEffect(() => { load(); }, [load, account.net_deposits]);

  const submit = async (sign) => {
    const v = Math.abs(Number(amount));
    if (!(v > 0)) return;
    setBusy(sign > 0 ? "dep" : "wd");
    try {
      const { data: out } = await http.post("/account/cashflow", { amount: sign * v, note });
      toast.success(`${sign > 0 ? "Depósito" : "Retiro"} registrado ${fmtSignedUsd(out.flow.amount)} · aportes netos ${fmtSignedUsd(out.account.net_deposits)}`);
      setAmount(""); setNote("");
      load(); refresh?.();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(""); }
  };

  return (
    <div data-testid="cashflows-card" className="rounded-sm border border-[#1E293B] bg-[#0D111A] p-3">
      <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-300"><Landmark className="h-3.5 w-3.5 text-amber-300" /> {live ? "Depósitos y retiros detectados" : "Depositar / Retirar"}</p>
      <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
        {live
          ? "En modo REAL el bot detecta las transferencias de la cuenta del bróker cada minuto (y al instante si la equidad salta). Se excluyen del PnL diario, del límite de pérdida diaria, del retorno total y del drawdown; el tamaño de las órdenes ya usa la nueva equidad."
          : "Simula los aportes mensuales del plan de crecimiento (o un retiro). El importe entra en el balance y se excluye del PnL, del límite diario y del drawdown, igual que un depósito real."}
      </p>
      {!live && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <div className="w-[130px]">
            <Field label={`Importe (${currency})`}>
              <Input data-testid="cashflow-amount-input" type="number" min={0.01} step={50} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="100" className={INPUT} />
            </Field>
          </div>
          <div className="min-w-[140px] flex-1">
            <Field label="Nota (opcional)">
              <Input data-testid="cashflow-note-input" value={note} maxLength={120} onChange={(e) => setNote(e.target.value)} placeholder="Aporte mensual" className={INPUT} />
            </Field>
          </div>
          <Button data-testid="cashflow-deposit-button" variant="outline" size="sm" onClick={() => submit(1)} disabled={!!busy || !(Number(amount) > 0)} className={`${BTN_OUT} border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/10`}>
            {busy === "dep" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ArrowDownToLine className="mr-1.5 h-3.5 w-3.5" />} Depositar
          </Button>
          <Button data-testid="cashflow-withdraw-button" variant="outline" size="sm" onClick={() => submit(-1)} disabled={!!busy || !(Number(amount) > 0)} className={`${BTN_OUT} border-rose-500/40 text-rose-300 hover:bg-rose-500/10`}>
            {busy === "wd" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ArrowUpFromLine className="mr-1.5 h-3.5 w-3.5" />} Retirar
          </Button>
        </div>
      )}
      {data && data.items.length > 0 ? (
        <div data-testid="cashflows-list" className="thin-scroll mt-3 max-h-40 overflow-auto rounded-sm border border-[#1E293B]">
          <table className="w-full text-[11px]">
            <tbody>
              {data.items.map((f) => (
                <tr key={f.id} data-testid={`cashflow-row-${f.id}`} className="border-b border-[#1E293B]/60 last:border-0">
                  <td className="px-3 py-1.5 text-slate-400">{fmtDateTime(f.ts)}</td>
                  <td className={`num px-2 py-1.5 text-right font-semibold ${pnlClass(f.amount)}`}>{fmtSignedUsd(f.amount)}</td>
                  <td className="px-2 py-1.5 text-slate-500">{f.kind === "deposit" ? "Depósito" : "Retiro"} · {SOURCE[f.source] || f.source}{f.note ? ` · ${f.note}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : data && <p data-testid="cashflows-empty" className="mt-2 text-[11px] text-slate-600">Sin depósitos ni retiros registrados.</p>}
    </div>
  );
}
