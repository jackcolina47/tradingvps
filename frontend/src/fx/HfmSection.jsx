import { useState } from "react";
import { Landmark, Loader2, Plug, Trash2, RefreshCw, ListChecks } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Panel } from "@/components/bot/Panel";
import { http, errMsg, fmtDateTime } from "@/lib/api";
import { Field, INPUT, BTN, BTN_OUT } from "@/components/bot/admin/SecuritySection";
import { HfmSwitchAccount } from "./HfmSwitchAccount";
import { useBrand } from "./brand";

const STATE_TONE = { DEPLOYED: "border-emerald-500/40 text-emerald-400", TOKEN_OK: "border-sky-500/40 text-sky-300" };

function SymbolsCheck({ data, bk }) {
  const rows = Object.entries(data.symbols);
  const disabled = data.disabled || [];
  const errors = data.errors || [];
  const resolved = Object.entries(data.resolved || {});
  const bad = data.missing.length + disabled.length;
  const head = bad
    ? [data.missing.length ? `${data.missing.length} par(es) sin cotización en ${bk}: ${data.missing.join(", ")}` : "",
       disabled.length ? `${disabled.length} par(es) cotizan pero NO son operables en esta cuenta: ${disabled.join(", ")}` : ""].filter(Boolean).join(" · ") + " · revisa el sufijo de símbolos y el tipo de cuenta"
    : errors.length ? `El puente MT no respondió para ${errors.join(", ")} · repite la comprobación en unos segundos`
    : `Los ${rows.length} pares activos cotizan y son operables en ${bk}${data.suffix ? ` con sufijo «${data.suffix}»` : " sin sufijo"}`;
  return (
    <div data-testid="hfm-symbols-result" className="rounded-sm border border-[#1E293B] bg-[#0B1220] p-3 font-mono text-[11px] text-slate-300">
      <p data-testid="hfm-symbols-summary" className={bad ? (disabled.length ? "text-rose-300" : "text-amber-300") : errors.length ? "text-amber-300" : "text-emerald-400"}>{head}</p>
      {resolved.length > 0 && (
        <p data-testid="hfm-symbols-resolved" className="mt-1 text-sky-300">
          Nombres resueltos en tu cuenta: {resolved.map(([k, v]) => `${k} → ${v}`).join(", ")}
        </p>
      )}
      <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
        {rows.map(([sym, r]) => (
          <span key={sym} data-testid={`hfm-symbol-${sym}`} className={r.ok ? "text-slate-300" : "text-rose-300"}>
            {r.resolved ? `${sym} → ${r.broker_symbol}` : r.broker_symbol}{r.ok ? ` · ${r.bid} / ${r.ask} · mín ${r.min_lot}` : ` · ${r.error}`}
          </span>
        ))}
      </div>
    </div>
  );
}

const OTHER = "__other__";
const NONE = "__none__";
const toSel = (v) => (v === "" ? NONE : v);
const fromSel = (v) => (v === NONE ? "" : v);
function SuffixField({ value, onChange, suffixes, kind, bk }) {
  const known = suffixes.some((s) => s.suffix === value);
  const [custom, setCustom] = useState(!known);
  const sel = custom ? OTHER : toSel(value);
  const single = suffixes.length <= 1;
  return (
    <Field label={single ? "Sufijo de símbolos" : `Tipo de cuenta ${bk} · sufijo de símbolos`}
      hint={kind ? `Detectado al probar la conexión: cuenta ${kind}` : single ? `En ${bk} los símbolos no llevan sufijo (EURUSD, XAUUSD). Déjalo vacío salvo que tu cuenta use nombres distintos.` : `Cada tipo de cuenta ${bk} usa su propio sufijo (Zero → EURUSDb). Se detecta solo al «Probar conexión».`}>
      <div className="flex gap-2">
        <Select value={sel} onValueChange={(v) => { if (v === OTHER) { setCustom(true); } else { setCustom(false); onChange(fromSel(v)); } }}>
          <SelectTrigger data-testid="hfm-account-kind-select" className={`${INPUT} flex-1`}><SelectValue /></SelectTrigger>
          <SelectContent className="border-[#1E293B] bg-[#0D111A]">
            {suffixes.map((s) => <SelectItem key={s.suffix || "none"} value={toSel(s.suffix)} data-testid={`hfm-account-kind-${s.suffix || "none"}`} className="font-mono text-xs">{s.label} · {s.suffix ? `EURUSD${s.suffix}` : "sin sufijo"}</SelectItem>)}
            <SelectItem value={OTHER} data-testid="hfm-account-kind-other" className="font-mono text-xs">Otro sufijo…</SelectItem>
          </SelectContent>
        </Select>
        {custom && <Input data-testid="hfm-suffix-input" autoComplete="off" value={value} placeholder="p.ej. .r" onChange={(e) => onChange(e.target.value)} className={`${INPUT} w-24`} />}
      </div>
    </Field>
  );
}

function AccountTypeField({ value, onChange, types, bk }) {
  const cur = types.find((t) => t.key === value) || types[0];
  return (
    <Field label={`Tipo de cuenta ${bk} · costes`} hint={cur?.note || "Determina el spread y la comisión que el bot descuenta en paper, backtests y plan de crecimiento."}>
      <Select value={cur?.key} onValueChange={onChange}>
        <SelectTrigger data-testid="hfm-account-type-select" className={INPUT}><SelectValue /></SelectTrigger>
        <SelectContent className="border-[#1E293B] bg-[#0D111A]">
          {types.map((t) => <SelectItem key={t.key} value={t.key} data-testid={`hfm-account-type-${t.key}`} className="font-mono text-xs">{t.label}{t.commission_lot_usd ? ` · ${t.commission_lot_usd} USD/lote` : ""}</SelectItem>)}
        </SelectContent>
      </Select>
    </Field>
  );
}

export default function HfmSection({ config, onSave, onRefresh, account }) {
  const brand = useBrand();
  const bk = brand.broker;
  const broker = config.broker || {};
  const accountTypes = broker.account_types || [];
  const eff = config.bridge_effective || { from_env: {} };
  const [form, setForm] = useState({ bridge_url: config.bridge_url || "", bridge_token: "", bridge_account_id: config.bridge_account_id || "",
    mt_symbol_suffix: config.mt_symbol_suffix || "", hfm_alert_after_sec: config.hfm_alert_after_sec ?? 180, account_type: broker.account_type || "" });
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [symbols, setSymbols] = useState(null);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const run = async (key, fn) => {
    setBusy(key);
    try { await fn(); } catch (e) { toast.error(errMsg(e)); } finally { setBusy(""); }
  };

  const save = async () => {
    setBusy("save");
    const body = Object.fromEntries(Object.entries(form).filter(([k, v]) => v !== "" || !["bridge_token", "account_type"].includes(k)));
    body.hfm_alert_after_sec = Math.min(3600, Math.max(10, Number(form.hfm_alert_after_sec) || 180));
    const ok = await onSave(body, `Puente de ${bk} guardado · prueba la conexión`);
    setBusy("");
    if (ok) {
      setForm((f) => ({ ...f, bridge_token: "", hfm_alert_after_sec: body.hfm_alert_after_sec }));
      if (body.mt_symbol_suffix !== config.mt_symbol_suffix) setSymbols(null);
    }
  };
  const test = () => run("test", async () => {
    setResult(null);
    const typed = form.bridge_token || form.bridge_url !== (config.bridge_url || "") || form.bridge_account_id !== (config.bridge_account_id || "");
    const { data } = await http.post("/hfm/test", typed ? { bridge_url: form.bridge_url, bridge_token: form.bridge_token, bridge_account_id: form.bridge_account_id } : {}, { timeout: 90000 });
    setResult(data);
    if (data.suffix && data.suffix.suffix !== null && data.suffix.suffix !== undefined) setForm((f) => ({ ...f, mt_symbol_suffix: data.suffix.suffix }));
    if (data.symbols) setSymbols(data.symbols);
    const badSymbols = data.symbols ? data.symbols.missing.length + (data.symbols.disabled || []).length : 0;
    const warn = !data.info || data.info.tradeAllowed === false || badSymbols;
    toast[warn ? "warning" : "success"](data.info ? (data.info.tradeAllowed === false ? "Conectada, pero sin permiso de trading" : badSymbols ? `Conectada · ${badSymbols} par(es) no operables: revisa el sufijo` : `Cuenta ${bk} conectada vía puente MT`) : data.hint || "El puente responde");
    if (!data.saved_credentials && data.info) toast.info("Pulsa «Guardar» para que el bot use estos datos");
    onRefresh?.();
  });
  const reconnect = () => run("reconnect", async () => {
    const { data } = await http.post("/hfm/reconnect", null, { timeout: 120000 });
    setResult((r) => ({ ...(r || {}), account: data.account, hint: data.hint, diagnosis: data.diagnosis, info: null }));
    toast.success("Reconexión solicitada al puente");
    onRefresh?.();
  });
  const checkSymbols = () => run("symbols", async () => {
    const { data } = await http.post("/hfm/symbols", null, { timeout: 120000 });
    setSymbols(data);
    const bad = data.missing.length + (data.disabled || []).length;
    toast[bad ? "warning" : "success"](bad ? `${bad} par(es) no operables en ${bk} · revisa el sufijo` : `Todos los pares cotizan y son operables en ${bk}`);
    onRefresh?.();
  });
  const clear = async () => {
    setBusy("clear");
    const ok = await onSave({ clear_hfm: true }, `Datos del puente de ${bk} eliminados del panel`);
    setBusy(""); setResult(null); setSymbols(null);
    if (ok) setForm({ bridge_url: "", bridge_token: "", bridge_account_id: "", mt_symbol_suffix: "", hfm_alert_after_sec: config.hfm_alert_after_sec ?? 180, account_type: broker.account_type || "" });
  };
  const acc = result?.account;
  const envNote = (k, label) => (eff.from_env?.[k] ? ` · usando ${label}` : "");

  return (
    <Panel testId="hfm-section" title={`${brand.brokerLong} · cuenta real vía puente MT propio`} icon={Landmark} bodyClass="p-4 space-y-3"
      actions={config.hfm_configured && (
        <span data-testid="hfm-state-badge" className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${config.hfm_verified_at ? STATE_TONE.DEPLOYED : STATE_TONE[config.hfm_state] || "border-amber-500/40 text-amber-300"}`}>
          {config.hfm_verified_at ? (config.hfm_trade_allowed === false ? "Solo lectura" : "Verificada") : config.hfm_state === "DEPLOYED" ? "Terminal activo" : config.hfm_state === "UNDEPLOYED" ? "Terminal parado" : "Sin verificar"}
        </span>
      )}>
      <p className="text-[12px] leading-relaxed text-slate-400">
        {bk} opera con MetaTrader y no tiene API REST, así que el bot se conecta a <span className="text-slate-200">tu propio puente</span> (mt_bridge.py) en el VPS Windows, donde corre el terminal MetaTrader de esta cuenta con Algo Trading activo.
        El login y la contraseña de MetaTrader se quedan en ese terminal: aquí solo indicas la URL del puente, su token y el identificador de la cuenta (por defecto <span className="num text-slate-200">{config.bridge_default_account}</span>).
        Pasos: 1) guarda; 2) <span className="text-slate-200">Probar conexión</span>; 3) <span className="text-slate-200">Comprobar símbolos</span>. Después activa el modo REAL en la tarjeta «Modo de operación». Empieza con una cuenta demo de {bk}.
        {config.secrets_encrypted ? " El token se guarda cifrado." : ""}
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={`URL del puente MT${envNote("url", "MT_BRIDGE_URL")}`} hint="Dirección de mt_bridge.py en tu VPS Windows. Vacío = variable de entorno MT_BRIDGE_URL.">
          <Input data-testid="hfm-bridge-url-input" autoComplete="off" value={form.bridge_url} placeholder={eff.from_env?.url ? eff.url : "http://94.72.105.219:8000"} onChange={set("bridge_url")} className={INPUT} />
        </Field>
        <Field label={`Token del puente${config.bridge_token ? ` · guardado ${config.bridge_token}` : envNote("token", "MT_BRIDGE_TOKEN")}`} hint="El mismo token de config_bridge.json. Vacío = variable de entorno MT_BRIDGE_TOKEN.">
          <Input data-testid="hfm-bridge-token-input" type="password" autoComplete="new-password" value={form.bridge_token} placeholder={config.bridge_token || (eff.token_set ? "definido en el servidor" : "token del puente")} onChange={set("bridge_token")} className={INPUT} />
        </Field>
        <Field label="ID de cuenta en el puente" hint={`Nombre de la cuenta en config_bridge.json. Vacío = ${eff.account_id || config.bridge_default_account}.`}>
          <Input data-testid="hfm-bridge-account-input" autoComplete="off" value={form.bridge_account_id} placeholder={eff.account_id || config.bridge_default_account} onChange={set("bridge_account_id")} className={`${INPUT} num`} />
        </Field>
        <SuffixField bk={bk} value={form.mt_symbol_suffix} onChange={(v) => { setForm((f) => ({ ...f, mt_symbol_suffix: v })); setResult((r) => (r?.suffix ? { ...r, suffix: null } : r)); }} suffixes={broker.suffixes || config.hfm_suffixes || [{ suffix: "", label: "Premium" }]} kind={config.hfm_suffix_kind} />
        {accountTypes.length > 1 && <AccountTypeField bk={bk} value={form.account_type} onChange={(v) => setForm((f) => ({ ...f, account_type: v }))} types={accountTypes} />}
        <Field label="Aviso de desconexión (segundos)" hint={`En modo REAL, si el terminal ${bk} o el puente dejan de responder durante más de este tiempo, recibirás un aviso por Telegram (y otro al restablecerse). Recomendado 180–300 s. 10–3600 s.`}>
          <Input data-testid="hfm-alert-after-input" type="number" min={10} max={3600} step={5} value={form.hfm_alert_after_sec} onChange={set("hfm_alert_after_sec")} className={`${INPUT} num`} />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button data-testid="hfm-save-button" onClick={save} disabled={busy === "save"} className={BTN}>{busy === "save" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />} Guardar</Button>
        <Button data-testid="hfm-test-button" variant="outline" onClick={test} disabled={busy === "test" || !(config.hfm_configured || (form.bridge_url && form.bridge_token))} className={BTN_OUT}>
          {busy === "test" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-1.5 h-3.5 w-3.5" />} Probar conexión
        </Button>
        <Button data-testid="hfm-symbols-button" variant="outline" onClick={checkSymbols} disabled={busy === "symbols" || !config.hfm_verified_at} className={BTN_OUT} title={config.hfm_verified_at ? `Comprueba que los pares activos cotizan en tu cuenta ${bk}` : "Prueba primero la conexión"}>
          {busy === "symbols" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ListChecks className="mr-1.5 h-3.5 w-3.5" />} Comprobar símbolos
        </Button>
        {config.hfm_configured && (
          <Button data-testid="hfm-reconnect-button" variant="outline" onClick={reconnect} disabled={busy === "reconnect"} className={BTN_OUT} title="Pide al puente que vuelva a engancharse al terminal MetaTrader">
            {busy === "reconnect" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />} Reconectar
          </Button>
        )}
        {(config.bridge_url || config.bridge_token || config.bridge_account_id) && (
          <Button data-testid="hfm-clear-button" variant="ghost" onClick={clear} disabled={busy === "clear" || config.trading_mode === "live"} title={config.trading_mode === "live" ? "Vuelve a modo paper antes de quitar la cuenta" : undefined}
            className="h-9 font-mono text-[11px] uppercase tracking-wider text-rose-300 hover:bg-rose-500/10 disabled:opacity-40">
            <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Quitar
          </Button>
        )}
      </div>
      {(result || config.hfm_verified_at) && (
        <div data-testid="hfm-test-result" className="space-y-1.5 rounded-sm border border-[#1E293B] bg-[#0B1220] p-3 font-mono text-[11px] text-slate-300">
          {result?.token_valid && <p data-testid="hfm-bridge-ok">Puente MT: <span className="text-emerald-400">accesible y token válido</span> · cuentas definidas: {(result.bridge_accounts || []).join(", ") || "ninguna"}</p>}
          {acc && <p data-testid="hfm-account-line">Cuenta {acc.id} · {String(acc.platform || "").toUpperCase()} · login {acc.login || "—"} · {acc.server || "—"} · terminal <span className={acc.state === "DEPLOYED" ? "text-emerald-400" : "text-amber-300"}>{acc.state === "DEPLOYED" ? "activo" : "parado"}</span> · conexión con el bróker {acc.connection || "—"}</p>}
          {result?.info && (
            <p>Bróker {result.info.broker} · {result.info.currency} · balance <span className="num text-slate-100">{result.info.balance}</span> · equidad <span className="num text-slate-100">{result.info.equity}</span> · margen libre {result.info.freeMargin} · apalancamiento 1:{result.info.leverage} · {result.positions?.length || 0} posiciones abiertas · trading <span className={result.info.tradeAllowed === false ? "text-rose-300" : "text-emerald-400"}>{result.info.tradeAllowed === false ? "NO permitido" : "permitido"}</span></p>
          )}
          {result?.hint && <p data-testid="hfm-test-hint" className="text-amber-300">{result.hint}</p>}
          {result?.diagnosis && (
            <div data-testid="hfm-diagnosis" className="mt-2 space-y-1.5 rounded-sm border border-amber-500/30 bg-amber-500/5 p-2.5">
              <p className="font-display text-[12px] font-semibold uppercase tracking-wide text-amber-200">Diagnóstico de conexión</p>
              {result.diagnosis.checks.map((c, i) => (
                <p key={i} data-testid={`hfm-diagnosis-check-${i}`} className="break-words"><span className={c.ok ? "text-emerald-400" : "text-rose-400"}>{c.ok ? "✓" : "✗"}</span> {c.text}</p>
              ))}
              {result.diagnosis.cause && <p data-testid="hfm-diagnosis-cause" className="text-amber-200">Causa más probable: {result.diagnosis.cause}</p>}
              <p className="pt-1 text-slate-400">Qué hacer:</p>
              <ol className="list-decimal space-y-1 pl-5 text-slate-300">{result.diagnosis.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </div>
          )}
          {result?.suffix?.hint && <p data-testid="hfm-suffix-detected" className={result.suffix.suffix === null || result.suffix.suffix === undefined ? "text-amber-300" : "text-sky-300"}>{result.suffix.hint}</p>}
          {!result && config.hfm_verified_at && <p>Última verificación {fmtDateTime(config.hfm_verified_at)} · {config.hfm_broker || bk} · login {config.mt_login} · {config.mt_server} · {String(config.mt_platform || "").toUpperCase()} · equidad {config.hfm_verified_equity} {config.hfm_currency} · apalancamiento 1:{config.hfm_leverage}{config.hfm_trade_allowed === false ? " · SIN permiso de trading" : ""}</p>}
        </div>
      )}
      {symbols && <SymbolsCheck data={symbols} bk={bk} />}
      <HfmSwitchAccount config={config} account={account} onDone={onRefresh} />
    </Panel>
  );
}
