import { useState } from "react";
import { ArrowLeftRight, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { http, errMsg } from "@/lib/api";

const fmt = (v, cur) => `${Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })} ${cur || "USD"}`;

export function HfmSwitchAccount({ config, account, onDone }) {
  const [busy, setBusy] = useState(false);
  if (config.trading_mode !== "live" || !config.hfm_configured) return null;
  const current = config.mt_server ? `${config.mt_login || config.bridge_effective?.account_id}@${config.mt_server}` : "";
  const changed = !!account?.account_changed;
  const verified = !!config.hfm_verified_at;

  const rebase = async () => {
    setBusy(true);
    try {
      const { data } = await http.post("/account/rebase");
      toast.success(`Cuenta cambiada · capital inicial ${fmt(data.equity, data.currency)} · ${data.tagged} operaciones anteriores conservadas`);
      onDone?.();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };

  return (
    <div data-testid="hfm-switch-account" className={`space-y-2 rounded-sm border p-3 ${changed ? "border-amber-500/40 bg-amber-500/10" : "border-[#1E293B] bg-[#0B1220]"}`}>
      <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-400"><ArrowLeftRight className="h-3.5 w-3.5" /> Cambiar de cuenta (demo → dinero real, u otra cuenta)</p>
      {changed ? (
        <p data-testid="hfm-account-changed" className="text-[12px] leading-relaxed text-amber-200">
          Las estadísticas se basan en la cuenta <b>{account.account_ref}</b> y ahora está vinculada <b>{current}</b>. Re-basa el capital para que el rendimiento,
          el drawdown y el límite diario partan de la equidad de la cuenta nueva sin perder el historial ni los contadores del plan.
        </p>
      ) : (
        <p className="text-[12px] leading-relaxed text-slate-400">
          Tras cambiar el ID de Cuenta del puente y probar la conexión, pulsa aquí: el capital inicial pasa a ser la equidad de la cuenta nueva y se conservan las operaciones
          (etiquetadas como periodo anterior), los contadores de las fases del plan y todo lo aprendido. «Reiniciar» en la cabecera, en cambio, borra el historial.
          {account?.account_ref ? <> Estadísticas actuales: <span className="text-slate-200">{account.account_ref}</span>.</> : ""}
        </p>
      )}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button data-testid="hfm-rebase-button" variant="outline" disabled={busy || !verified} title={verified ? "Requiere bot detenido y sin posiciones abiertas" : "Prueba la conexión de la cuenta nueva primero"}
            className={`h-8 bg-transparent font-mono text-[11px] uppercase tracking-wider ${changed ? "border-amber-500/50 text-amber-200 hover:bg-amber-500/15" : "border-[#1E293B] text-slate-200 hover:bg-[#111827]"}`}>
            {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ArrowLeftRight className="mr-1.5 h-3.5 w-3.5" />} Cambiar de cuenta · re-basar capital
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent data-testid="hfm-rebase-dialog" className="border-amber-500/30 bg-[#0D111A]">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display text-amber-200">Re-basar el capital a la cuenta {current}</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2 text-slate-400">
              <span className="block">Capital inicial, pico de equidad, límite diario y depósitos netos pasan a la equidad actual de la cuenta vinculada{account?.exchange_synced ? <> (<b className="text-slate-200">{fmt(account.equity, account.currency)}</b> ahora mismo)</> : config.hfm_verified_equity ? <> (última verificación: <b className="text-slate-200">{fmt(config.hfm_verified_equity, config.hfm_currency)}</b>)</> : ""}.</span>
              <span className="block">Se conservan: operaciones cerradas (marcadas como «periodo anterior»), operaciones cerradas/días/drawdown de las fases del plan, configuración aprendida, Evolución y walk-forward. La curva de equidad se desplaza para quedar continua.</span>
              <span className="block">Requiere bot detenido y sin posiciones abiertas.</span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="hfm-rebase-cancel" className="border-[#1E293B] bg-transparent font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:bg-[#111827]">Cancelar</AlertDialogCancel>
            <AlertDialogAction data-testid="hfm-rebase-confirm" onClick={rebase} className="bg-amber-500 font-mono text-[11px] font-bold uppercase tracking-wider text-[#07090E] hover:bg-amber-400">Re-basar ahora</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
