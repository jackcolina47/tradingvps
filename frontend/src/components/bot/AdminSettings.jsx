import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { http, errMsg } from "@/lib/api";
import SecuritySection from "./admin/SecuritySection";
import AiSection from "./admin/AiSection";
import TelegramSection from "./admin/TelegramSection";
import AutoReviewSection from "./admin/AutoReviewSection";
import LearnedRulesSection from "./admin/LearnedRulesSection";
import AccountSection from "./admin/AccountSection";
import BybitSection from "./admin/BybitSection";
import LiveModeSection from "./admin/LiveModeSection";
import NewsSection from "./admin/NewsSection";
import FundingSection from "./admin/FundingSection";
import BackupSection from "./admin/BackupSection";

export default function AdminSettings({ account, refresh }) {
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

  if (!config) return <div data-testid="admin-loading" className="flex min-h-[300px] items-center justify-center text-slate-500"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  return (
    <div data-testid="admin-settings" className="space-y-4">
      <div>
        <h2 className="font-display text-base font-bold uppercase tracking-[0.12em] text-slate-100 md:text-lg">Ajustes del sistema</h2>
        <p className="text-[12px] text-slate-500">Cuenta real de Bybit y cambio de modo, clave de acceso, inteligencia artificial, alertas de Telegram, revisión automática, agente de noticias, cuenta paper y copias de seguridad. La estrategia y el riesgo se siguen configurando desde el panel principal.</p>
      </div>
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <BybitSection config={config} onSave={save} />
        <LiveModeSection config={config} refresh={refresh} />
        <SecuritySection />
        <AiSection config={config} onSave={save} />
        <LearnedRulesSection />
        <TelegramSection config={config} onSave={save} />
        <AutoReviewSection config={config} onSave={save} onRan={load} />
        <NewsSection config={config} onSave={save} />
        <FundingSection config={config} onSave={save} />
        <AccountSection account={account} refresh={refresh} />
        <BackupSection />
      </div>
    </div>
  );
}
