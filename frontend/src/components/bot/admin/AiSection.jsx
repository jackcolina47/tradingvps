import { useEffect, useState } from "react";
import { BrainCircuit, Loader2, Plug, KeyRound, Sparkles, ExternalLink, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Panel } from "../Panel";
import { http, errMsg } from "@/lib/api";
import { Field, INPUT, BTN, BTN_OUT } from "./SecuritySection";
import { ModelPicker, modelLabel } from "./ModelPicker";

const PROVIDER_LABEL = { anthropic: "Anthropic (Claude)", openai: "OpenAI (GPT)", gemini: "Google (Gemini)" };
const SOURCES = [
  { id: "emergent", icon: Sparkles, title: "Clave universal de Emergent", desc: "Sin configurar nada: Claude, GPT o Gemini con el saldo de tu clave Emergent." },
  { id: "gemini", icon: KeyRound, title: "Google Gemini · mi API key", desc: "Usa tu propia clave de Google AI Studio; el consumo se factura en tu cuenta de Google." },
];

const SourceCard = ({ s, active, onClick }) => (
  <button type="button" data-testid={`ai-source-${s.id}`} onClick={onClick} aria-pressed={active}
    className={`rounded-sm border px-3 py-2.5 text-left transition-colors ${active ? "border-amber-500/60 bg-amber-500/10" : "border-[#1E293B] bg-[#07090E] hover:border-slate-600"}`}>
    <span className={`flex items-center gap-2 text-[13px] font-semibold ${active ? "text-amber-200" : "text-slate-200"}`}><s.icon className="h-3.5 w-3.5" /> {s.title}</span>
    <span className="mt-1 block text-[11px] leading-snug text-slate-500">{s.desc}</span>
  </button>
);

const fromConfig = (c) => ({ ai_source: c.ai_source, ai_provider: c.ai_provider, ai_model: c.ai_model, gemini_model: c.gemini_model, ai_fast_model: c.ai_fast_model || "", gemini_api_key: "", emergent_llm_key: "" });
const AUTO = "__auto__";

const emergentKeyLabel = (c) => {
  if (c.emergent_configured) return `Clave universal de Emergent · guardada ${c.emergent_llm_key}`;
  if (c.emergent_key_origin === "env") return `Clave universal de Emergent · del servidor ${c.emergent_env_key}`;
  return "Clave universal de Emergent · sin configurar";
};
const emergentKeyHint = (c) => {
  if (c.emergent_configured) return "Déjala vacía para mantener la guardada; escribe otra para sustituirla.";
  if (c.emergent_key_origin === "env") return "Se está usando la variable EMERGENT_LLM_KEY del servidor. Escribe aquí una clave para usar otra distinta.";
  return "No hay ninguna clave en el servidor: pega aquí tu clave universal (sk-emergent-…) para activar la IA.";
};

export default function AiSection({ config, onSave }) {
  const [form, setForm] = useState(fromConfig(config));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [reply, setReply] = useState(null);
  const set = (k, v) => { setDirty(true); setForm((f) => ({ ...f, [k]: v })); };
  const gemini = form.ai_source === "gemini";
  const active = config.ai_active;
  // Keep the form in sync with the saved config (other bot / tab / background job) while the user has no unsaved edits.
  useEffect(() => { if (!dirty) setForm(fromConfig(config)); }, [dirty, config.ai_source, config.ai_provider, config.ai_model, config.gemini_model, config.ai_fast_model]); // eslint-disable-line react-hooks/exhaustive-deps
  const fastProvider = gemini ? "gemini" : form.ai_provider;
  const fastOptions = (config.fast_models || {})[fastProvider] || [];

  const save = async (extra = {}) => {
    setBusy("save");
    const ok = await onSave({ ...form, ...extra }, "Configuración de IA guardada");
    setBusy("");
    if (ok) setDirty(false);
  };
  const discard = () => {
    setForm(fromConfig(config));
    setDirty(false);
    setReply(null);
    toast.info("Cambios descartados · se mantiene la configuración guardada");
  };
  const test = async () => {
    setBusy("test");
    setReply(null);
    try {
      const { data } = await http.post("/ai/test", form);
      setReply(`${data.label} (${data.source === "gemini" ? "tu clave de Google" : "clave Emergent"}${data.key_hint ? ` ${data.key_hint}${data.key_origin_label ? ` · ${data.key_origin_label}` : ""}` : ""}): ${data.reply}`);
      toast.success("La IA respondió correctamente · recuerda guardar para activarla");
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy("");
    }
  };

  return (
    <Panel testId="ai-section" title="Inteligencia artificial" icon={BrainCircuit} bodyClass="p-4 space-y-3">
      <p data-testid="ai-active-label" className={`rounded-sm border px-3 py-2 text-[12px] ${active.health?.ok === false ? "border-rose-500/50 bg-rose-500/10 text-rose-200" : "border-[#1E293B] bg-[#07090E] text-slate-300"}`}>
        Activa ahora en los tres bots: <span className={`font-semibold ${active.health?.ok === false ? "text-rose-300" : "text-amber-300"}`}>{active.label}</span> <span className={active.health?.ok === false ? "text-rose-300/70" : "text-slate-500"}>· {active.source === "gemini" ? "tu API key de Google Gemini" : config.emergent_configured ? "clave Emergent guardada en Ajustes" : "clave universal de Emergent del servidor"} · {active.model}</span>
        {active.key_hint && <span data-testid="ai-active-key" className="mt-1 block font-mono text-[11px] text-slate-400">Clave en uso: <span className="text-slate-200">{active.key_hint}</span>{active.key_origin_label ? ` · ${active.key_origin_label}` : ""}</span>}
        {active.health?.ok === false && <span data-testid="ai-health-error" className="mt-1 block font-mono text-[11px] text-rose-300">⚠ Sin servicio · {active.health.label}: {active.health.error}</span>}
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {SOURCES.map((s) => <SourceCard key={s.id} s={s} active={form.ai_source === s.id} onClick={() => set("ai_source", s.id)} />)}
      </div>
      {gemini ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={`API key de Google Gemini${config.gemini_configured ? ` · guardada ${config.gemini_api_key}` : ""}`}
            hint={<>Déjala vacía para mantener la guardada. <a data-testid="gemini-key-link" href={config.gemini_key_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-300 hover:underline">Obtener una API key en Google AI Studio <ExternalLink className="h-3 w-3" /></a></>}>
            <Input data-testid="gemini-api-key-input" type="password" autoComplete="off" value={form.gemini_api_key} placeholder={config.gemini_configured ? config.gemini_api_key : "AIza…"}
              onChange={(e) => set("gemini_api_key", e.target.value)} className={INPUT} />
          </Field>
          <Field label="Modelo de Gemini" hint="Pro = más razonamiento; Flash = más rápido y barato.">
            <ModelPicker models={config.gemini_models} value={form.gemini_model} onChange={(v) => set("gemini_model", v)} testId="gemini-model" />
          </Field>
        </div>
      ) : (
        <div className="space-y-3">
          <Field label={emergentKeyLabel(config)} hint={emergentKeyHint(config)}>
            <Input data-testid="emergent-llm-key-input" type="password" autoComplete="off" value={form.emergent_llm_key}
              placeholder={config.emergent_configured ? config.emergent_llm_key : config.emergent_key_origin === "env" ? config.emergent_env_key : "sk-emergent-…"}
              onChange={(e) => set("emergent_llm_key", e.target.value)} className={INPUT} />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Proveedor">
              <Select value={form.ai_provider} onValueChange={(v) => { setDirty(true); setForm((f) => ({ ...f, ai_provider: v, ai_model: config.ai_models[v]?.[0] || f.ai_model })); }}>
                <SelectTrigger data-testid="ai-provider-select" className={INPUT}><SelectValue /></SelectTrigger>
                <SelectContent className="border-[#1E293B] bg-[#0D111A]">
                  {config.ai_providers.map((p) => <SelectItem key={p} value={p} className="font-mono text-xs">{PROVIDER_LABEL[p] || p}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Modelo" hint="El consumo se descuenta del saldo de tu clave Emergent.">
              <ModelPicker models={config.ai_models[form.ai_provider] || []} value={form.ai_model} onChange={(v) => set("ai_model", v)} testId="ai-model" />
            </Field>
          </div>
        </div>
      )}
      <Field label="Modelo rápido del embudo (etapa 1)" hint={`Modelo barato que descarta el ruido técnico antes del modelo principal. Automático = ${modelLabel(config.fast_model_auto || "")} (${config.fast_model_auto}). Se activa por bot en Ajustes → «Embudo IA en 2 etapas».`}>
        <Select value={form.ai_fast_model || AUTO} onValueChange={(v) => set("ai_fast_model", v === AUTO ? "" : v)}>
          <SelectTrigger data-testid="ai-fast-model-select" className={INPUT}><SelectValue /></SelectTrigger>
          <SelectContent className="border-[#1E293B] bg-[#0D111A]">
            <SelectItem value={AUTO} className="font-mono text-xs">Automático según proveedor <span className="text-slate-500">· {config.fast_model_auto}</span></SelectItem>
            {fastOptions.map((m) => <SelectItem key={m} value={m} className="font-mono text-xs">{modelLabel(m)} <span className="text-slate-500">· {m}</span></SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      <div className="flex flex-wrap items-center gap-2">
        <Button data-testid="ai-save-button" onClick={() => save()} disabled={!!busy} className={BTN}>{busy === "save" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />} Guardar</Button>
        <Button data-testid="ai-test-button" variant="outline" onClick={test} disabled={!!busy} className={BTN_OUT}>
          {busy === "test" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-1.5 h-3.5 w-3.5" />} Probar conexión
        </Button>
        {dirty && (
          <Button data-testid="ai-discard-button" variant="ghost" onClick={discard} disabled={!!busy} className="h-9 font-mono text-[11px] uppercase tracking-wider text-slate-400 hover:text-slate-100">
            <Undo2 className="mr-1.5 h-3.5 w-3.5" /> Descartar cambios
          </Button>
        )}
        {config.gemini_configured && (
          <Button data-testid="ai-clear-gemini-button" variant="outline" onClick={() => save({ clear_gemini_api_key: true })} disabled={!!busy} className={`${BTN_OUT} text-rose-300 hover:bg-rose-500/10`}>Quitar clave de Gemini</Button>
        )}
        {config.emergent_configured && (
          <Button data-testid="ai-clear-emergent-button" variant="outline" onClick={() => save({ clear_emergent_llm_key: true })} disabled={!!busy} className={`${BTN_OUT} text-rose-300 hover:bg-rose-500/10`}>
            Quitar clave Emergent{config.emergent_key_origin !== "none" && config.emergent_env_key ? " · volver a la del servidor" : ""}
          </Button>
        )}
        {dirty && <span data-testid="ai-dirty-badge" className="rounded-sm border border-amber-500/40 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-amber-300">Cambios sin guardar</span>}
      </div>
      {reply && <p data-testid="ai-test-reply" className="rounded-sm border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-[12px] text-emerald-200">{reply}</p>}
    </Panel>
  );
}
