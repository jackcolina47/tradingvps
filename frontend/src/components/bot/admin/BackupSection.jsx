import { useCallback, useEffect, useRef, useState } from "react";
import { DatabaseBackup, Download, Eraser, FileUp, Loader2, Plus, RotateCcw, Send } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Panel } from "../Panel";
import { api, errMsg, fmtDateTime } from "@/lib/api";
import { BTN, BTN_OUT } from "./SecuritySection";
import RestoreBackupDialog from "./RestoreBackupDialog";

const CHUNK = 512 * 1024;
const uid = () => Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
const fmtMb = (mb) => (mb < 0.1 ? `${Math.max(1, Math.round(mb * 1024))} KB` : `${mb.toFixed(1)} MB`);
const SOURCE = { manual: "desde el panel", auto: "automática" };

// Global (both bots): backups live under /api/backup regardless of which dashboard is open.
const EMPTY = { backups: [], last: {}, keep_days: 14, bots_running: { crypto: false, fx: false }, telegram: false, confirm_word: "RESTAURAR" };

function DataCleanup() {
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get(`/maintenance`, { timeout: 20000 }).then((r) => setInfo(r.data)).catch(() => setInfo(null)), []);
  useEffect(() => { load(); }, [load]);
  if (!info) return null;
  const n = (v) => v.toLocaleString("es-ES");
  const nothing = !info.old_logs && !info.old_equity;
  const clean = async () => {
    if (!window.confirm(`Se eliminarán ${n(info.old_logs)} líneas de registro de más de ${info.log_days} días y la curva de equidad de más de ${info.equity_days} días se reducirá a un punto por hora, en todos los bots. Operaciones, señales, sombras y reglas no se tocan. No se puede deshacer. ¿Continuar?`)) return;
    setBusy(true);
    try {
      const { data } = await api.post(`/maintenance/cleanup`, { confirm: true }, { timeout: 300000 });
      toast.success(`Limpieza hecha · ${n(data.logs)} líneas de registro y ${n(data.equity)} puntos de equidad eliminados`);
      await load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <div data-testid="data-cleanup" className="rounded-sm border border-[#1E293B] bg-[#07090E] px-3 py-2.5">
      <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">Datos antiguos · todos los bots</p>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
        <p data-testid="data-cleanup-summary" className="min-w-0 flex-1 text-[11px] leading-relaxed text-slate-400">
          Guardados ahora: {n(info.logs)} líneas de registro · {n(info.equity)} puntos de equidad · {n(info.signals)} señales · {n(info.shadow_trades)} sombras.{" "}
          {nothing ? "No hay nada antiguo que limpiar." : <span className="text-amber-200">Se pueden limpiar {n(info.old_logs)} líneas de registro de más de {info.log_days} días y reducir {n(info.old_equity)} puntos de equidad de más de {info.equity_days} días a uno por hora.</span>}
        </p>
        <Button data-testid="data-cleanup-button" size="sm" variant="outline" onClick={clean} disabled={busy || nothing} className={BTN_OUT}>
          {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Eraser className="mr-1.5 h-3.5 w-3.5" />} Limpiar datos antiguos
        </Button>
      </div>
    </div>
  );
}

export default function BackupSection() {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState(0);
  const [file, setFile] = useState(null);
  const [target, setTarget] = useState(null);
  const fileRef = useRef(null);

  // The panel never waits for the list: a new server (no backups yet) or a failing listing must still allow «Restaurar desde archivo».
  const load = useCallback(() => {
    setLoadError("");
    return api.get(`/backup`, { timeout: 20000 }).then((r) => setData(r.data)).catch((e) => setLoadError(errMsg(e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy("create");
    try {
      const { data: rec } = await api.post(`/backup/create`);
      toast.success(`Copia creada · ${rec.filename} · ${fmtMb(rec.size_mb)}`);
      await load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(""); }
  };

  const download = async (filename) => {
    setBusy(`dl:${filename}`);
    try {
      const r = await api.get(`/backup/download/${encodeURIComponent(filename)}`, { responseType: "blob", timeout: 300000 });
      const url = URL.createObjectURL(r.data);
      const a = Object.assign(document.createElement("a"), { href: url, download: filename });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(""); }
  };

  const restore = async (confirm) => {
    setBusy("restore"); setProgress(target.kind === "file" ? 0 : 100);
    try {
      const body = { confirm };
      if (target.kind === "file") {
        const id = uid(); const total = Math.ceil(file.size / CHUNK);
        for (let i = 0; i < total; i++) {
          await api.post(`/backup/upload`, file.slice(i * CHUNK, (i + 1) * CHUNK), { timeout: 120000,
            headers: { "Content-Type": "application/octet-stream", "X-Upload-Id": id, "X-Chunk-Index": i, "X-Chunk-Total": total } });
          setProgress(Math.round(((i + 1) / total) * 100));
        }
        body.upload_id = id;
      } else body.filename = target.filename;
      const { data: out } = await api.post(`/backup/restore`, body, { timeout: 300000 });
      toast.success(`Copia restaurada · ${out.documents} documentos · todos los bots quedan detenidos. Recargando…`);
      setTimeout(() => window.location.reload(), 1800);
    } catch (e) { toast.error(errMsg(e)); setBusy(""); load(); }
  };

  const info = data || EMPTY;
  const running = info.bots_running.crypto || info.bots_running.fx || info.bots_running.pepper;
  const last = info.backups[0];
  const loading = !data && !loadError;
  return (
    <Panel testId="backup-section" title="Copias de seguridad" icon={DatabaseBackup} bodyClass="p-4 space-y-3">
      <p className="text-[11px] leading-relaxed text-slate-500">
        Copia completa de la base de datos de <b className="text-slate-300">los tres bots</b> (ajustes, cuentas, historial, señales, evolución, IA, Telegram y brókers). Se crea sola cada noche a las 03:00 UTC
        (se conservan {info.keep_days} días) y los domingos se envía por Telegram{info.telegram ? "" : " (ahora apagado: activa el aviso «Conexión/sistema»)"}. La clave de acceso actual se conserva al restaurar.
      </p>
      <div data-testid="backup-last" className="rounded-sm border border-[#1E293B] bg-[#07090E] px-3 py-2.5">
        <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-500">Última copia</p>
        {loading ? (
          <p data-testid="backup-loading" className="mt-1 flex items-center gap-2 text-[11px] text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Consultando las copias guardadas…</p>
        ) : loadError ? (
          <div data-testid="backup-load-error" className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11px] text-rose-300">No se pudo consultar la lista de copias: {loadError}. Puedes restaurar igualmente desde un archivo.</p>
            <Button data-testid="backup-retry" size="sm" variant="outline" onClick={load} className={BTN_OUT}><RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Reintentar</Button>
          </div>
        ) : last ? (
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p data-testid="backup-last-name" className="truncate font-mono text-[11px] text-slate-200">{last.filename}</p>
              <p className="text-[11px] text-slate-500">{fmtDateTime(last.created_at)} · {fmtMb(last.size_mb)}{info.last?.filename === last.filename && info.last?.source && ` · ${SOURCE[info.last.source] || info.last.source}`}
                {info.last?.filename === last.filename && info.last?.sent && <span className="ml-1 inline-flex items-center gap-1 text-emerald-400"><Send className="h-3 w-3" /> enviada por Telegram</span>}</p>
            </div>
            <Button data-testid="backup-download-last" size="sm" variant="outline" onClick={() => download(last.filename)} disabled={busy.startsWith("dl:")} className={BTN_OUT}>
              {busy === `dl:${last.filename}` ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1.5 h-3.5 w-3.5" />} Descargar última
            </Button>
          </div>
        ) : <p data-testid="backup-empty" className="mt-1 text-[11px] text-slate-500">Todavía no hay copias guardadas en este servidor. Puedes crear la primera o restaurar una desde un archivo.</p>}
        {info.error && <p data-testid="backup-folder-error" className="mt-1 text-[11px] text-amber-300">{info.error}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button data-testid="backup-create-button" size="sm" onClick={create} disabled={busy === "create"} className={BTN}>
          {busy === "create" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-1.5 h-3.5 w-3.5" />} Crear copia ahora
        </Button>
        <input ref={fileRef} data-testid="backup-file-input" type="file" accept=".gz,.json.gz,application/gzip" className="hidden" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <Button data-testid="backup-pick-file" size="sm" variant="outline" onClick={() => fileRef.current?.click()} className={BTN_OUT}><FileUp className="mr-1.5 h-3.5 w-3.5" /> Restaurar desde archivo…</Button>
      </div>
      {file && (
        <div data-testid="backup-file-selected" className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-amber-500/30 bg-amber-500/5 px-3 py-2">
          <p className="min-w-0 truncate font-mono text-[11px] text-amber-100">{file.name} <span className="text-slate-500">· {fmtMb(file.size / 1048576)}</span></p>
          <div className="flex gap-2">
            <Button data-testid="backup-file-clear" size="sm" variant="ghost" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }} className="h-8 font-mono text-[11px] text-slate-400">Quitar</Button>
            <Button data-testid="backup-file-restore" size="sm" variant="outline" onClick={() => { load(); setTarget({ kind: "file", label: file.name }); }} className="h-8 border-rose-500/40 bg-transparent font-mono text-[11px] uppercase tracking-wider text-rose-300 hover:bg-rose-500/10">
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Restaurar este archivo
            </Button>
          </div>
        </div>
      )}
      {running && <p data-testid="backup-running-note" className="text-[11px] text-amber-300/90">Para restaurar, detén antes todos los bots ({[info.bots_running.crypto && "Bybit", info.bots_running.fx && "HotForex", info.bots_running.pepper && "Pepperstone"].filter(Boolean).join(", ")} en marcha).</p>}
      {info.backups.length > 0 && (
        <div data-testid="backup-list" className="thin-scroll max-h-56 overflow-auto rounded-sm border border-[#1E293B]">
          <table className="w-full text-[11px]">
            <thead><tr className="border-b border-[#1E293B] bg-[#07090E]"><th colSpan={3} className="px-3 py-1.5 text-left font-mono text-[9px] font-normal uppercase tracking-[0.16em] text-slate-500">Copias guardadas en el servidor · {info.backups.length}</th></tr></thead>
            <tbody>
              {info.backups.map((b) => (
                <tr key={b.filename} data-testid={`backup-row-${b.filename}`} className="border-b border-[#1E293B]/60 last:border-0">
                  <td className="px-3 py-1.5 text-slate-300">{fmtDateTime(b.created_at)}</td>
                  <td className="px-2 py-1.5 font-mono text-slate-500">{fmtMb(b.size_mb)}</td>
                  <td className="px-2 py-1.5 text-right">
                    <button data-testid={`backup-download-${b.filename}`} onClick={() => download(b.filename)} className="mr-3 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-slate-300 hover:text-amber-300"><Download className="h-3 w-3" /> Descargar</button>
                    <button data-testid={`backup-restore-${b.filename}`} onClick={() => { load(); setTarget({ kind: "stored", filename: b.filename, label: fmtDateTime(b.created_at) }); }} className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-rose-300/90 hover:text-rose-200"><RotateCcw className="h-3 w-3" /> Restaurar</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <DataCleanup />
      <RestoreBackupDialog target={target} word={info.confirm_word} busy={busy === "restore"} progress={progress} botsRunning={running} onConfirm={restore} onClose={() => setTarget(null)} />
    </Panel>
  );
}
