import { useState } from "react";
import { Landmark, Loader2, Plug, Trash2, RefreshCw, ListChecks, ChevronDown } from "lucide-react";
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
const RECONNECT_STATES = ["UNDEPLOYED", "DISCONNECTED", "DISCONNECTED_FROM_BROKER", "DEPLOY_FAILED"];

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
  const defaults = config.bridge_defaults || {};
  const effective = config.bridge_effective || {};
  const blank = () => ({ bridge_url: config.bridge_url || "", bridge_token: "", bridge_account_id: config.bridge_account_id || "", mt_symbol_suffix: config.mt_symbol_suffix || "",
    hfm_alert_after_sec: config.hfm_alert_after_sec ?? 180, account_type: broker.account_type || "" });
  const [form, setForm] = useState(blank);
  const [advanced, setAdvanced] = useState(false);
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
    const { data } = await http.post("/hfm/test", form.bridge_token ? { bridge_token: form.bridge_token, bridge_url: form.bridge_url, bridge_account_id: form.bridge_account_id } : {});
    setResult(data);
    if (data.suffix && data.suffix.suffix !== null && data.suffix.suffix !== undefined) setForm((f) => ({ ...f, mt_symbol_suffix: data.suffix.suffix }));
    if (data.symbols) setSymbols(data.symbols);
    const badSymbols = data.symbols ? data.symbols.missing.length + (data.symbols.disabled || []).length : 0;
    toast[data.info && data.info.tradeAllowed !== false && !badSymbols ? "success" : "warning"](data.info ? (data.info.tradeAllowed === false ? "Conectada, pero sin permiso de trading" : badSymbols ? `Conectada · ${badSymbols} par(es) no operables: revisa el sufijo` : `Cuenta ${bk} conectada vía puente MT`) : data.hint || "Token válido");
    onRefresh?.();
  });
  const reconnect = () => run("reconnect", async () => {
    const { data } = await http.post("/hfm/reconnect");
    setResult((r) => ({ ...(r || {}), account: data.account, hint: data.hint }));
    toast.success("Reconexión solicitada al puente");
    onRefresh?.();
  });
  const checkSymbols = () => run("symbols", async () => {
    const { data } = await http.post("/hfm/symbols");
    setSymbols(data);
    const bad = data.missing.length + (data.disabled || []).length;
    toast[bad ? "warning" : "success"](bad ? `${bad} par(es) no operables en ${bk} · revisa el sufijo` : `Todos los pares cotizan y son operables en ${bk}`);
    onRefresh?.();
  });
  const clear = async () => {
    setBusy("clear");
    const ok = await onSave({ clear_hfm: true }, `Datos del puente de ${bk} eliminados`);
    setBusy(""); setResult(null); setSymbols(null);
    if (ok) setForm({ bridge_url: "", bridge_token: "", bridge_account_id: "", mt_symbol_suffix: "", hfm_alert_after_sec: config.hfm_alert_after_sec ?? 180, account_type: broker.account_type || "" });
  };
  const acc = result?.account;
  const ready = !!config.hfm_configured;
  const hasSaved = !!(config.bridge_token || config.bridge_url || config.bridge_account_id || config.hfm_verified_at);
  const canReconnect = ready && (RECONNECT_STATES.includes(config.hfm_state) || RECONNECT_STATES.includes(config.hfm_connection) || (acc && !result?.info));
  const tokenLabel = config.bridge_token ? ` · guardado ${config.bridge_token}` : effective.token_source === "env" ? " · definido en el servidor (MT_BRIDGE_TOKEN)" : "";

  return (
    <Panel testId="hfm-section" title={`${brand.brokerLong} · cuenta real vía puente MetaTrader`} icon={Landmark} bodyClass="p-4 space-y-3"
      actions={ready && (
        <span data-testid="hfm-state-badge" className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${config.hfm_verified_at ? STATE_TONE.DEPLOYED : STATE_TONE[config.hfm_state] || "border-amber-500/40 text-amber-300"}`}>
          {config.hfm_verified_at ? (config.hfm_trade_allowed === false ? "Solo lectura" : "Verificada") : config.hfm_state || "Sin verificar"}
        </span>
      )}>
      <p className="text-[12px] leading-relaxed text-slate-400">
        {bk} opera con MetaTrader 4/5 y no tiene API REST: el bot se conecta a <span className="text-slate-200">tu propio puente MetaTrader</span> (servidor Windows con el terminal abierto), que ya guarda el login y la contraseña de la cuenta.
        Pasos: 1) indica la URL del puente, el token y el ID de cuenta; 2) <span className="text-slate-200">Guardar</span>, <span className="text-slate-200">Probar conexión</span> y <span className="text-slate-200">Comprobar símbolos</span>;
        3) activa el modo REAL en la tarjeta «Modo de operación». Mientras no haya token, el bot sigue en paper con datos de Yahoo Finance.
        {config.secrets_encrypted ? " El token se guarda cifrado." : ""}
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="URL del Puente" hint={`Vacío = ${defaults.url || "valor por defecto"} (variable MT_BRIDGE_URL del servidor)`}>
          <Input data-testid="hfm-bridge-url-input" autoComplete="off" value={form.bridge_url} placeholder={defaults.url || "http://…:8000"} onChange={set("bridge_url")} className={INPUT} />
        </Field>
        <Field label="ID de Cuenta" hint={`Vacío = ${defaults.account_id || "cuenta por defecto"}`}>
          <Input data-testid="hfm-bridge-account-input" autoComplete="off" value={form.bridge_account_id} placeholder={defaults.account_id || "hfm_v2"} onChange={set("bridge_account_id")} className={INPUT} />
        </Field>
      </div>
      <Field label={`Token del Puente${tokenLabel}`}>
        <Input data-testid="hfm-token-input" type="password" autoComplete="new-password" value={form.bridge_token} placeholder={config.bridge_token || (effective.token_source === "env" ? "Usando MT_BRIDGE_TOKEN del servidor" : "Token Bearer del puente")} onChange={set("bridge_token")} className={INPUT} />
      </Field>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <SuffixField bk={bk} value={form.mt_symbol_suffix} onChange={(v) => { setForm((f) => ({ ...f, mt_symbol_suffix: v })); setResult((r) => (r?.suffix ? { ...r, suffix: null } : r)); }} suffixes={broker.suffixes || config.hfm_suffixes || [{ suffix: "", label: "Premium" }]} kind={config.hfm_suffix_kind} />
        {accountTypes.length > 1 && <AccountTypeField bk={bk} value={form.account_type} onChange={(v) => setForm((f) => ({ ...f, account_type: v }))} types={accountTypes} />}
      </div>
      <button data-testid="hfm-advanced-toggle" onClick={() => setAdvanced((v) => !v)} className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-slate-500 hover:text-slate-300">
        <ChevronDown className={`h-3 w-3 transition-transform ${advanced ? "rotate-180" : ""}`} /> Avanzado · aviso de desconexión
      </button>
      {advanced && (
        <Field label="Aviso de desconexión (segundos)" hint={`En modo REAL, si la cuenta ${bk} pierde la conexión con el puente durante más de este tiempo, recibirás un aviso por Telegram (y otro al restablecerse). Recomendado 180–300 s. 10–3600 s.`}>
          <Input data-testid="hfm-alert-after-input" type="number" min={10} max={3600} step={5} value={form.hfm_alert_after_sec} onChange={set("hfm_alert_after_sec")} className={`${INPUT} num sm:w-1/2`} />
        </Field>
      )}
      <div className="flex flex-wrap gap-2">
        <Button data-testid="hfm-save-button" onClick={save} disabled={busy === "save"} className={BTN}>{busy === "save" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />} Guardar</Button>
        <Button data-testid="hfm-test-button" variant="outline" onClick={test} disabled={busy === "test" || !(form.bridge_token || ready)} className={BTN_OUT} title={form.bridge_token || ready ? undefined : "Introduce primero el Token del Puente"}>
          {busy === "test" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-1.5 h-3.5 w-3.5" />} Probar conexión
        </Button>
        <Button data-testid="hfm-symbols-button" variant="outline" onClick={checkSymbols} disabled={busy === "symbols" || !ready} className={BTN_OUT} title={ready ? `Comprueba que los pares activos cotizan en tu cuenta ${bk}` : "Guarda primero el Token del Puente"}>
          {busy === "symbols" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ListChecks className="mr-1.5 h-3.5 w-3.5" />} Comprobar símbolos
        </Button>
        {canReconnect && (
          <Button data-testid="hfm-reconnect-button" variant="outline" onClick={reconnect} disabled={busy === "reconnect"} className={BTN_OUT}>
            {busy === "reconnect" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />} Reconectar
          </Button>
        )}
        {hasSaved && (
          <Button data-testid="hfm-clear-button" variant="ghost" onClick={clear} disabled={busy === "clear" || config.trading_mode === "live"} title={config.trading_mode === "live" ? "Vuelve a modo paper antes de quitar la cuenta" : undefined}
            className="h-9 font-mono text-[11px] uppercase tracking-wider text-rose-300 hover:bg-rose-500/10 disabled:opacity-40">
            <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Quitar
          </Button>
        )}
      </div>
      {(result || config.hfm_verified_at) && (
        <div data-testid="hfm-test-result" className="space-y-1.5 rounded-sm border border-[#1E293B] bg-[#0B1220] p-3 font-mono text-[11px] text-slate-300">
          {result?.token_valid && <p>Puente <span className="text-slate-100">{result.url}</span> · token <span className="text-emerald-400">válido</span> · cuenta <span data-testid="hfm-bridge-account" className="text-sky-300">{result.account_id}</span></p>}
          {acc && <p>Estado <span className={acc.state === "DEPLOYED" ? "text-emerald-400" : "text-amber-300"}>{acc.state || "—"}</span> · conexión {acc.connection || "—"}{acc.server ? ` · ${acc.server}` : ""}{acc.platform ? ` · ${String(acc.platform).toUpperCase()}` : ""}</p>}
          {result?.info && (
            <p>{result.info.broker} · {result.info.server} · {result.info.currency} · balance <span className="num text-slate-100">{result.info.balance}</span> · equidad <span className="num text-slate-100">{result.info.equity}</span> · margen libre {result.info.freeMargin} · apalancamiento 1:{result.info.leverage} · {result.positions?.length || 0} posiciones abiertas · trading <span className={result.info.tradeAllowed === false ? "text-rose-300" : "text-emerald-400"}>{result.info.tradeAllowed === false ? "NO permitido" : "permitido"}</span></p>
          )}
          {result?.hint && <p className="text-amber-300">{result.hint}</p>}
          {result?.suffix?.hint && <p data-testid="hfm-suffix-detected" className={result.suffix.suffix === null || result.suffix.suffix === undefined ? "text-amber-300" : "text-sky-300"}>{result.suffix.hint}</p>}
          {!result && config.hfm_verified_at && <p>Última verificación {fmtDateTime(config.hfm_verified_at)} · {config.hfm_broker || bk} {config.mt_server} · cuenta {effective.account_id} · equidad {config.hfm_verified_equity} {config.hfm_currency} · apalancamiento 1:{config.hfm_leverage}{config.hfm_trade_allowed === false ? " · SIN permiso de trading" : ""}</p>}
        </div>
      )}
      {symbols && <SymbolsCheck data={symbols} bk={bk} />}
      <HfmSwitchAccount config={config} account={account} onDone={onRefresh} />
    </Panel>
  );
}
