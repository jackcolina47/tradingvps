import { useEffect, useState } from "react";
import { Loader2, Scissors } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { http, fmtDateTime } from "@/lib/api";

const short = (s) => s.replace("USDT", "");
const BTN = "h-8 border-[#1E293B] bg-[#0B0F17] font-mono text-[10px] uppercase tracking-wider text-slate-300 hover:bg-[#111827]";

export default function MaintenanceStatus({ settings, refresh }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState("");
  const load = () => http.get("/housekeeping").then((r) => setData(r.data)).catch(() => {});
  useEffect(() => { load(); }, [settings.symbols]);
  const run = async (path, label) => {
    setBusy(path);
    try {
      const r = await http.post(`/housekeeping/${path}`);
      const d = r.data;
      toast.success(d.switched_off?.length ? `Apagados: ${d.switched_off.map(short).join(", ")}` : d.warned?.length ? `Aviso enviado: ${d.warned.map(short).join(", ")}` : "Ningún par cumple el criterio de poda");
      await load();
      refresh?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || `No se pudo ejecutar ${label}`);
    } finally {
      setBusy("");
    }
  };
  if (!data) return null;
  const pr = data.prune || {};
  const warned = Object.keys(pr.warned || {});

  return (
    <div data-testid="maintenance-status" className="grid grid-cols-1 gap-3">
      <div className="rounded-sm border border-[#1E293B] bg-[#0B0F17] p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">
            Poda automática · {settings.auto_prune_enabled ? "activa" : "apagada"} · PF &lt; {settings.auto_prune_pf} con &gt; {settings.auto_prune_min_trades} ops en 30 d
          </p>
          <Button data-testid="prune-run-button" size="sm" variant="outline" className={BTN} disabled={!!busy} onClick={() => run("prune", "la poda")}>
            {busy === "prune" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Scissors className="mr-1 h-3 w-3" />} Comprobar ahora
          </Button>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          {pr.last_at ? `Última comprobación: ${fmtDateTime(pr.last_at)}.` : "Todavía no se ha comprobado."} Primero avisa por Telegram; si al día siguiente el par sigue igual, lo apaga (puedes reactivarlo arriba).
        </p>
        <p data-testid="prune-warned" className="mt-2 text-[12px]">
          {warned.length ? <>En aviso: <span className="text-amber-300">{warned.map(short).join(", ")}</span></> : <span className="text-slate-500">Ningún par en aviso.</span>}
          {pr.last_off?.length > 0 && <> · Últimos apagados: <span className="text-rose-300">{pr.last_off.map(short).join(", ")}</span></>}
        </p>
      </div>
    </div>
  );
}
