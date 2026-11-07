import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { http, errMsg } from "@/lib/api";
import SecuritySection from "@/components/bot/admin/SecuritySection";
import AiSection from "@/components/bot/admin/AiSection";
import TelegramSection from "@/components/bot/admin/TelegramSection";
import AccountSection from "@/components/bot/admin/AccountSection";
import HfmSection from "./HfmSection";
import FxNewsSection from "./FxNewsSection";
import LiveModeSection from "@/components/bot/admin/LiveModeSection";
import AutoReviewSection from "@/components/bot/admin/AutoReviewSection";
import LearnedRulesSection from "@/components/bot/admin/LearnedRulesSection";
import BackupSection from "@/components/bot/admin/BackupSection";
import { fmtDateTime } from "@/lib/api";
import { useBrand } from "./brand";

const AUTO_REVIEW_DESC = "La IA revisa el rendimiento real del bot forex, el chequeo del plan y los backtests en lotes y pips, y ajusta pares, estrategias, sesiones y SL/TP dentro de los topes de tu perfil de riesgo forex (riesgo ≤ 1.5 %, apalancamiento ≤ 1:100 o el del perfil, mínimo 2 pares). Se dispara a las 08:00 UTC del día programado, independiente de la revisión del bot cripto, y te avisa por Telegram.";
const WEEKLY_DESC = "PnL y pips netos de la semana, mejores y peores pares, decisiones del filtro IA, evolución (qué cambios mejoraron, cuáles empeoraron y cuáles deshizo la IA) y estado del plan de crecimiento forex. Usa el aviso «Resumen diario y semanal».";

const liveLabels = (bk) => ({
  exchange: bk,
  liveDesc: (d) => <>Operando con dinero real en {bk} <span className="text-slate-200">{d.env}</span> vía MetaApi desde {fmtDateTime(d.live_since)}. Las órdenes a mercado llevan SL/TP adjuntos, el trailing stop se actualiza en el bróker y los cierres por SL/TP se detectan desde el historial de la cuenta.</>,
  paperDesc: () => <>Deja el bot en paper hasta que el historial demuestre expectativa positiva. Cuando los requisitos estén en verde, pasa a real: el bot usará la misma estrategia, el mismo riesgo y la equidad real de tu cuenta {bk} (apalancamiento del bróker) para dimensionar cada orden en lotes.</>,
  dialogDesc: (d) => <>El bot enviará órdenes reales a {bk} <b className="text-slate-200">{d.env}</b> con una equidad verificada de <b className="text-slate-200">{(d.verified_equity || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })} {d.currency}</b>. Riesgo por operación, lote mínimo, límites diarios y filtros de sesión y noticias se aplican tal cual.</>,
  resetLabel: `Reiniciar estadísticas con la equidad real de ${bk}`,
});

export default function FxAdminSettings({ account, refresh }) {
  const brand = useBrand();
  const [config, setConfig] = useState(null);

  const load = useCallback(() => http.get("/config").then((r) => setConfig(r.data)).catch((e) => toast.error(errMsg(e))), []);
  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);

  const save = async (body, okMsg = "Ajustes guardados") => {
    try {
      const { data } = await http.put("/config", body);
      setConfig(data);
      toast.success(okMsg);
      refresh?.();
      return true;
    } catch (e) {
      toast.error(errMsg(e));
      return false;
    }
  };

  if (!config) return <div data-testid="fx-admin-loading" className="flex min-h-[300px] items-center justify-center text-slate-500"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  return (
    <div data-testid="fx-admin-settings" className="space-y-4">
      <div>
        <h2 className="font-display text-base font-bold uppercase tracking-[0.12em] text-slate-100 md:text-lg">Ajustes · {brand.name}</h2>
        <p className="text-[12px] text-slate-500">Cuenta real de {brand.broker} (vía MetaApi) y cambio de modo, agente de noticias y trading por noticias, revisión IA automática, resumen semanal y cuenta paper. La clave de acceso, la IA y Telegram son compartidos con los otros bots. La estrategia y el riesgo se configuran desde el panel principal.</p>
      </div>
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <HfmSection config={config} onSave={save} onRefresh={() => { load(); refresh?.(); }} account={account} />
        <LiveModeSection config={config} refresh={() => { load(); refresh?.(); }} labels={liveLabels(brand.broker)} />
        <FxNewsSection config={config} onSave={save} />
        <AutoReviewSection config={config} onSave={save} onRan={load} desc={AUTO_REVIEW_DESC} />
        <SecuritySection />
        <AiSection config={config} onSave={save} />
        <LearnedRulesSection />
        <TelegramSection config={config} onSave={save} weeklyDesc={WEEKLY_DESC} />
        <AccountSection account={account} refresh={refresh} currency="USD" />
        <BackupSection />
      </div>
    </div>
  );
}
