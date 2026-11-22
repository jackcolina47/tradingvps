"""/api/fx/* · HFM account via MetaApi: connection test, link/recreate/redeploy, symbol suffix & tradability checks, paper ↔ REAL mode switch."""
import logging
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException

from models import ModeRequest, now_iso
from fx.models import HfmTestRequest
from fx.metaapi import MetaApi, MetaApiError, PLANS, plan_key, account_opts

logger = logging.getLogger(__name__)


def register(router, engine, store):
    def _chart_backoff_reset():
        """User re-linked / tested / redeployed the account: let the chart try HFM candles again right away."""
        if engine.broker:
            engine.broker.chart_fail_ts = 0.0

    def _metaapi(body: HfmTestRequest = None):
        c = store.cfg
        use_saved = not (body and body.metaapi_token and not body.metaapi_token.startswith("••"))
        token = c.metaapi_token if use_saved else body.metaapi_token.strip()
        if not token:
            raise HTTPException(400, "Introduce el token de MetaApi primero")
        region = (body.metaapi_region if body and body.metaapi_region else c.metaapi_region)
        base = (body.metaapi_base_url.strip() if body and body.metaapi_base_url is not None else c.metaapi_base_url)
        login = (body.mt_login.strip() if body and body.mt_login else c.mt_login)
        server = (body.mt_server.strip() if body and body.mt_server is not None else c.mt_server)
        return MetaApi(token, region, base), use_saved, login, server

    def _opts_text(acc: dict) -> str:
        q = acc.get("quoteStreamingIntervalInSeconds", 2.5)
        return (f" Ajustes en MetaApi: cotizaciones {'en cada tick' if not q else f'cada {q:g} s'} · {acc.get('resourceSlots') or 1} slot(s) de recursos · "
                f"IP dedicada {'IPv4' if (acc.get('allocateDedicatedIp') or 'none') == 'ipv4' else 'no'}.")

    def _acc_view(acc: dict) -> dict:
        return {"id": acc.get("_id") or acc.get("id"), "login": acc.get("login"), "server": acc.get("server"), "state": acc.get("state"),
                "connection": acc.get("connectionStatus"), "region": acc.get("region"), "platform": acc.get("platform") or acc.get("version"),
                "type": acc.get("type") or "", "reliability": acc.get("reliability") or "", "plan": plan_key(acc.get("type"), acc.get("reliability"))}

    CAUSES = {
        "DISCONNECTED": "El terminal de MetaApi está arrancado pero todavía no ha entrado en el bróker. Durante los primeros 5 minutos es normal. Si sigue así más tiempo, "
                        "lo habitual es: contraseña incorrecta o cambiada, plataforma equivocada (MT4 / MT5), nombre de servidor que no corresponde a esta cuenta, "
                        "o cuenta demo caducada.",
        "DISCONNECTED_FROM_BROKER": "El terminal de MetaApi no consigue iniciar sesión en el bróker (o el bróker lo desconectó). Casi siempre es la contraseña, el login o el servidor; "
                                    "también una cuenta demo caducada o deshabilitada. Volver a desplegar no lo arregla: hay que corregir los datos.",
    }

    async def _diagnose(api, acc: dict, accounts: list, login: str) -> dict:
        """Why a linked account is not connected: what MetaApi reports, what does not match the saved data, and what to do. MetaApi gives no explicit reason field."""
        c = store.cfg
        conn, state = acc.get("connectionStatus") or "", acc.get("state") or ""
        checks = [{"ok": True, "text": "Token de MetaApi válido"},
                  {"ok": state == "DEPLOYED", "text": f"Estado en MetaApi: {state or '—'}" + ("" if state == "DEPLOYED" else " · el terminal aún no está arrancado (DEPLOYING tarda 1-3 min; UNDEPLOYED: pulsa «Vincular cuenta»)")},
                  {"ok": conn == "CONNECTED", "text": f"Conexión con el bróker: {conn or '—'}"}]
        age = None
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(acc["createdAt"]).replace("Z", "+00:00"))).total_seconds() / 60
            checks.append({"ok": True, "text": f"Cuenta creada en MetaApi hace {age:.0f} min" if age < 180 else f"Cuenta creada en MetaApi hace {age / 60:.0f} h"})
        except (KeyError, ValueError, TypeError):
            pass
        plat = str(acc.get("platform") or "").lower()
        if plat and plat != c.mt_platform:
            checks.append({"ok": False, "text": f"Plataforma distinta: en MetaApi la cuenta es {plat.upper()} y en el panel tienes {c.mt_platform.upper()}"})
        if acc.get("region") and acc["region"] != c.metaapi_region:
            checks.append({"ok": False, "text": f"Región distinta: la cuenta está en «{acc['region']}» y en el panel tienes «{c.metaapi_region}» (el bot usa la de la cuenta)"})
        if c.metaapi_account_id and c.metaapi_account_id != acc["_id"]:
            checks.append({"ok": False, "text": f"El bot tenía guardada otra cuenta de MetaApi ({c.metaapi_account_id}); ahora usa {acc['_id']}"})
        dups = [a for a in accounts if str(a.get("login")) == str(login) and a.get("_id") != acc["_id"]]
        for a in dups:
            checks.append({"ok": False, "text": f"Cuenta duplicada en MetaApi con el mismo login: {a['_id']} · servidor {a.get('server') or '?'} · {a.get('state')} / {a.get('connectionStatus')} "
                                                "· el bot NO la usa; bórrala en app.metaapi.cloud → Accounts (se factura y puede competir por la misma sesión)"})
        answer = None
        if state == "DEPLOYED" and conn != "CONNECTED":
            try:  # what the terminal itself answers right now
                await api.account_info(acc["_id"], acc.get("region") or api.region)
            except MetaApiError as e:
                answer = e.msg[:260]
        cause = CAUSES.get(conn) if state == "DEPLOYED" else None
        if cause and conn == "DISCONNECTED" and age is not None and age < 5:
            cause = f"La cuenta se creó hace {age:.0f} min: es pronto. Espera hasta 5 minutos y vuelve a pulsar «Probar conexión»."
        steps = [f"Abre MetaTrader ({c.mt_platform.upper()}) en tu móvil o PC con el login {login}, la misma contraseña y el servidor «{acc.get('server')}». Si ahí no entra, el dato equivocado es ese.",
                 "Si la contraseña, el servidor o la plataforma estaban mal: borra esta cuenta en app.metaapi.cloud → Accounts, corrige el dato aquí, pulsa «Guardar» y después «Vincular cuenta» (el panel no modifica una cuenta ya creada en MetaApi).",
                 "Usa la contraseña maestra (trading), no la de inversor (solo lectura).",
                 "En app.metaapi.cloud → Accounts → tu cuenta puedes ver el estado y los avisos que da MetaApi."]
        if dups:
            steps.insert(0, "Borra primero la(s) cuenta(s) duplicada(s) en app.metaapi.cloud y vuelve a probar.")
        return {"checks": checks, "cause": cause, "metaapi_answer": answer, "steps": steps, "account_id": acc["_id"]}

    def _plan_patch(acc: dict) -> dict:
        return {"hfm_account_type": acc.get("type") or "", "hfm_reliability": acc.get("reliability") or ""}

    def _plan_hint(acc: dict) -> str | None:
        current = plan_key(acc.get("type"), acc.get("reliability"))
        if current == store.cfg.metaapi_plan:
            return None
        return (f"La cuenta ya existe en MetaApi como {PLANS[current]['short']} y has elegido {PLANS[store.cfg.metaapi_plan]['short']}: "
                "MetaApi no permite cambiar el tipo, pulsa «Recrear con el nuevo tipo» (bot detenido) o vuelve a seleccionar el tipo actual.")

    @router.post("/hfm/test")
    async def hfm_test(body: HfmTestRequest = None):
        api, saved, login, server = _metaapi(body)
        out = {"ok": True, "token_valid": False, "account": None, "info": None, "positions": [], "saved_credentials": saved, "login": login, "server": server}
        try:
            accounts = await api.list_accounts()
            out["token_valid"] = True
            out["accounts_total"] = len(accounts)
            acc = next((a for a in accounts if str(a.get("login")) == str(login) and (not server or str(a.get("server", "")).lower() == server.lower())), None) if login else None
            if acc:
                out["account"] = _acc_view(acc)
                if acc.get("state") == "DEPLOYED" and acc.get("connectionStatus") == "CONNECTED":
                    out["info"] = await api.account_info(acc["_id"], acc.get("region") or api.region, refresh=True)
                    out["positions"] = await api.positions(acc["_id"], acc.get("region") or api.region)
                else:
                    api.retries = 0
                    out["diagnosis"] = await _diagnose(api, acc, accounts, login)
        except MetaApiError as e:
            raise HTTPException(424, e.msg)
        except Exception as e:
            raise HTTPException(424, f"No se pudo conectar con MetaApi: {str(e)[:160]}")
        finally:
            await api.aclose()
        if saved:
            patch = {"hfm_state": (out["account"] or {}).get("state") or ("TOKEN_OK" if out["token_valid"] else ""),
                     "hfm_connection": (out["account"] or {}).get("connection") or ""}
            if out["account"]:
                patch["metaapi_account_id"] = out["account"]["id"]
                patch.update(hfm_account_type=out["account"]["type"], hfm_reliability=out["account"]["reliability"])
            if out["info"]:
                info = out["info"]
                patch.update(hfm_verified_at=now_iso(), hfm_verified_equity=info.get("equity"), hfm_currency=info.get("currency", ""),
                             hfm_leverage=info.get("leverage"), hfm_broker=info.get("broker", ""), hfm_trade_allowed=bool(info.get("tradeAllowed", True)))
                await engine.log("INFO", f"Cuenta {engine.bk} verificada vía MetaApi · {info.get('broker', engine.bk)} {server} · equidad {info.get('equity')} {info.get('currency', '')}"
                                         + ("" if info.get("tradeAllowed", True) else " · SIN permiso de trading (contraseña investor)"))
            await store.update(patch)
            _chart_backoff_reset()
        out["hint"] = (None if out["info"] else "Cuenta vinculada pero aún no conectada: espera unos minutos y vuelve a probar" if out["account"]
                       else "Token válido. La cuenta MT todavía no está vinculada en MetaApi: pulsa «Vincular cuenta»." if login else f"Token válido. Guarda el login/servidor de {engine.bk} para vincular la cuenta.")
        if out["info"] and not out["info"].get("tradeAllowed", True):
            out["hint"] = "La cuenta está conectada pero NO permite operar: usa la contraseña master (no investor) para el modo REAL."
        if out["account"] and saved and (ph := _plan_hint(out["account"])):
            out["plan_mismatch"] = True
            out["hint"] = ph
        if out["info"] and saved and out["info"].get("tradeAllowed", True):
            out["suffix"], out["symbols"] = await _probe_symbols()
        return out

    async def _probe_symbols():
        """After a successful connection test: detect the account-type suffix, apply it and re-check tradability of every active pair (never fails the test)."""
        broker = engine.new_broker()
        try:
            det = await _detect_suffix(broker, apply=True)
            if det.get("applied"):
                broker.suffix, broker.symbol_map = det["suffix"], {}
            return det, await _check_symbols(broker)
        except MetaApiError as e:
            return {"suffix": None, "hint": f"No se pudo comprobar el sufijo de símbolos: {e.msg[:120]}"}, None
        finally:
            await broker.aclose()

    link_tx = {}  # provisioning transaction in progress (MetaApi answers 202 until it has validated the MT account)

    @router.post("/hfm/link")
    async def hfm_link():
        c = store.cfg
        if not (c.metaapi_token and c.mt_login and c.mt_password and c.mt_server):
            raise HTTPException(400, f"Guarda primero el token de MetaApi, login, contraseña y servidor de {engine.bk}")
        api = MetaApi(c.metaapi_token, c.metaapi_region, c.metaapi_base_url, read_timeout=15.0)
        api.retries = 0  # every step must answer well inside the browser timeout; the panel repeats the step
        try:
            acc = await api.find_account(c.mt_login, c.mt_server)
            opts = lambda existing=None: account_opts(c.metaapi_plan, c.metaapi_quote_interval, c.metaapi_resource_slots, c.metaapi_dedicated_ip, existing)  # noqa: E731
            if acc and acc.get("region") and acc["region"] != c.metaapi_region:
                raise MetaApiError(409, f"En MetaApi ya existe esta cuenta ({acc['_id']}) en la región «{acc['region']}», y aquí has elegido «{c.metaapi_region}». La región no se puede cambiar "
                                        "sobre una cuenta creada: bórrala en app.metaapi.cloud → Accounts y vuelve a pulsar «Vincular cuenta».")
            if not acc:
                key = (c.mt_login, c.mt_server, c.mt_platform, c.metaapi_plan, c.metaapi_region)
                if link_tx.get("key") != key:
                    link_tx.update(key=key, tx=uuid.uuid4().hex[:32])
                created = await api.provision(c.mt_login, c.mt_password, c.mt_server, c.mt_platform, name=engine.profile.metaapi_name, plan=c.metaapi_plan, keywords=engine.profile.keywords,
                                              tx=link_tx["tx"], tries=1, opts=opts())
                if created.get("_retry"):  # MetaApi is still validating the MT credentials: answer now, the panel keeps asking with the same transaction
                    if not link_tx.get("logged"):
                        link_tx["logged"] = True
                        await engine.log("INFO", f"Vinculando la cuenta {engine.bk} {c.mt_login} en {c.mt_server} ({c.mt_platform.upper()}): MetaApi está validando los datos con el bróker…")
                    return {"ok": True, "pending": True, "retry_after": max(2, min(int(created["retry_after"]), 10)),
                            "hint": "MetaApi está validando el login, la contraseña y el servidor con el bróker. Suele tardar entre 10 segundos y 2 minutos."}
                link_tx.clear()
                acc = await api.account(created.get("id") or created.get("_id"))
                await engine.log("INFO", f"Cuenta {engine.bk} {c.mt_login} vinculada en MetaApi ({c.metaapi_region} · {PLANS[c.metaapi_plan]['label']})")
            elif opts(acc):  # existing account with other performance settings (e.g. MetaApi defaults): apply ours and restart the terminal
                changed = opts(acc)
                await api.update_account(acc["_id"], c.mt_password, acc.get("name") or engine.profile.metaapi_name, acc.get("server") or c.mt_server, changed)
                if acc.get("state") == "DEPLOYED":
                    await api.redeploy(acc["_id"])
                acc = await api.account(acc["_id"])
                applied = ", ".join(f"{k}={v}" for k, v in changed.items())
                await engine.log("INFO", f"Ajustes de la cuenta {engine.bk} actualizados en MetaApi ({applied}) · terminal reiniciado")
            if acc.get("state") in ("UNDEPLOYED", "CREATED"):
                await api.deploy(acc["_id"])
                acc = await api.account(acc["_id"])
            try:  # other MetaApi accounts with the same MT login: each one is billed and never used by the bot
                dups = [a for a in await api.list_accounts() if str(a.get("login")) == str(c.mt_login) and a.get("_id") != acc["_id"]]
            except MetaApiError:
                dups = []
        except MetaApiError as e:
            link_tx.clear()
            await engine.log("ERROR", f"No se pudo vincular la cuenta {engine.bk}: {e.msg}")
            raise HTTPException(424, e.msg)
        except Exception as e:
            link_tx.clear()
            await engine.log("ERROR", f"No se pudo vincular la cuenta {engine.bk}: {str(e)[:160]}")
            raise HTTPException(424, f"No se pudo vincular la cuenta: {str(e)[:160]}")
        finally:
            await api.aclose()
        await store.update({"metaapi_account_id": acc["_id"], "hfm_state": acc.get("state", ""), "hfm_connection": acc.get("connectionStatus", ""), **_plan_patch(acc)})
        _chart_backoff_reset()
        hint = _plan_hint(acc)
        dup_hint = ""
        if dups:
            dup_hint = (f" ⚠️ En MetaApi hay {len(dups)} cuenta(s) más con el login {c.mt_login} que el bot NO usa: "
                        + ", ".join(f"{a['_id']} (servidor {a.get('server') or '?'})" for a in dups)
                        + f". El bot usa {acc['_id']}. Bórralas en app.metaapi.cloud → Accounts para que no se facturen.")
            await engine.log("RISK", dup_hint.strip())
        return {"ok": True, "account": _acc_view(acc), "plan_mismatch": bool(hint), "duplicates": [a["_id"] for a in dups],
                "hint": (hint or "Despliegue solicitado: la conexión tarda 1-3 minutos. Pulsa «Probar conexión» para ver el estado.") + _opts_text(acc) + dup_hint}

    @router.post("/hfm/recreate")
    async def hfm_recreate():
        """Delete the MetaApi account and create it again with the selected plan (type/reliability can't be changed in place)."""
        c = store.cfg
        if not (c.metaapi_token and c.mt_login and c.mt_password and c.mt_server):
            raise HTTPException(400, f"Guarda primero el token de MetaApi, login, contraseña y servidor de {engine.bk}")
        if engine.running:
            raise HTTPException(409, "Detén el bot antes de recrear la cuenta en MetaApi")
        plan = PLANS[c.metaapi_plan]
        api = MetaApi(c.metaapi_token, c.metaapi_region, c.metaapi_base_url)
        removed = ""
        try:
            old = await api.find_account(c.mt_login, c.mt_server) or ({"_id": c.metaapi_account_id} if c.metaapi_account_id else None)
            if old and old.get("_id"):
                try:
                    await api.undeploy(old["_id"])
                except MetaApiError:
                    pass
                await api.delete_account(old["_id"])
                removed = f"{PLANS[plan_key(old.get('type'), old.get('reliability'))]['short']} ({old['_id'][:8]}…)" if old.get("type") else old["_id"][:8] + "…"
            created = await api.provision(c.mt_login, c.mt_password, c.mt_server, c.mt_platform, name=engine.profile.metaapi_name, plan=c.metaapi_plan, keywords=engine.profile.keywords,
                                         opts=account_opts(c.metaapi_plan, c.metaapi_quote_interval, c.metaapi_resource_slots, c.metaapi_dedicated_ip))
            acc = await api.account(created.get("id") or created.get("_id"))
            if acc.get("state") in ("UNDEPLOYED", "CREATED"):
                await api.deploy(acc["_id"])
                acc = await api.account(acc["_id"])
        except MetaApiError as e:
            raise HTTPException(424, e.msg)
        except Exception as e:
            raise HTTPException(424, f"No se pudo recrear la cuenta: {str(e)[:160]}")
        finally:
            await api.aclose()
        await store.update({"metaapi_account_id": acc["_id"], "hfm_state": acc.get("state", ""), "hfm_connection": acc.get("connectionStatus", ""),
                               "hfm_verified_at": None, "hfm_verified_equity": None, "hfm_trade_allowed": None, **_plan_patch(acc)})
        await engine.log("INFO", f"Cuenta {engine.bk} {c.mt_login} recreada en MetaApi como {plan['label']}" + (f" · eliminada la anterior {removed}" if removed else ""))
        return {"ok": True, "account": _acc_view(acc), "removed": removed,
                "hint": f"Cuenta recreada como {plan['label']}: la conexión tarda 1-3 minutos. Pulsa «Probar conexión» para verificarla de nuevo."}

    @router.post("/hfm/redeploy")
    async def hfm_redeploy():
        c = store.cfg
        if not (c.metaapi_token and c.metaapi_account_id):
            raise HTTPException(400, "Vincula primero la cuenta en MetaApi")
        api = MetaApi(c.metaapi_token, c.metaapi_region, c.metaapi_base_url)
        try:
            await api.redeploy(c.metaapi_account_id)
            acc = await api.account(c.metaapi_account_id)
        except MetaApiError as e:
            raise HTTPException(424, e.msg)
        finally:
            await api.aclose()
        await store.update({"hfm_state": acc.get("state", ""), "hfm_connection": acc.get("connectionStatus", ""), "hfm_verified_at": None})
        _chart_backoff_reset()
        await engine.log("INFO", f"Redespliegue de la cuenta {engine.bk} en MetaApi solicitado")
        return {"ok": True, "account": _acc_view(acc), "hint": "Redespliegue en curso (1-3 minutos). Vuelve a «Probar conexión»."}

    @router.post("/hfm/symbols")
    async def hfm_symbols():
        c = store.cfg
        if not (c.metaapi_token and c.metaapi_account_id):
            raise HTTPException(400, "Vincula y verifica primero la cuenta en MetaApi")
        broker = engine.broker or engine.new_broker()
        try:
            return await _check_symbols(broker)
        finally:
            if broker is not engine.broker:
                await broker.aclose()

    async def _check_symbols(broker) -> dict:
        """Quote + tradability of every active pair with the configured suffix; the result feeds the «Pasar a REAL» checklist.
        Temporary MetaApi failures are reported but never persisted as 'not tradable' (that would block the pair in REAL)."""
        s = await engine.get_settings()
        rows = {sym: await broker.check_symbol(sym) for sym in s.symbols}
        errors = [sym for sym, r in rows.items() if r.get("transient")]
        missing = [sym for sym, r in rows.items() if not r["quoted"] and not r.get("transient")]
        disabled = [sym for sym, r in rows.items() if r["quoted"] and not r["ok"]]
        resolved = {sym: r["broker_symbol"] for sym, r in rows.items() if r.get("resolved")}
        bad = missing + disabled
        symbol_map = {k: v for k, v in broker.symbol_map.items() if k in s.symbols}
        await store.update({"hfm_symbols_bad": None if errors else bad, "hfm_symbols_checked": None if errors else list(s.symbols), "hfm_symbols_checked_at": now_iso(),
                               "mt_symbol_map": symbol_map})
        await engine.log("RISK" if bad or errors else "INFO", f"Símbolos {engine.bk} comprobados · {len(rows) - len(bad) - len(errors)}/{len(rows)} operables"
                         + (f" · nombres resueltos en la cuenta: {', '.join(f'{k} → {v}' for k, v in resolved.items())}" if resolved else "")
                         + (f" · sin cotización: {', '.join(missing)}" if missing else "") + (f" · NO operables con el sufijo «{broker.suffix}»: {', '.join(disabled)} ({engine.profile.suffix_hint})" if disabled else "")
                         + (f" · sin respuesta de MetaApi (repite la comprobación): {', '.join(errors)}" if errors else ""))
        return {"suffix": broker.suffix, "symbols": rows, "missing": missing, "disabled": disabled, "errors": errors, "resolved": resolved, "checked_at": store.cfg.hfm_symbols_checked_at}

    @router.post("/hfm/detect-suffix")
    async def hfm_detect_suffix(apply: bool = True):
        c = store.cfg
        if not (c.metaapi_token and c.metaapi_account_id):
            raise HTTPException(400, "Vincula y verifica primero la cuenta en MetaApi")
        broker = engine.broker or engine.new_broker()
        try:
            det = await _detect_suffix(broker, apply)
        except MetaApiError as e:
            raise HTTPException(424, e.msg)
        finally:
            if broker is not engine.broker:
                await broker.aclose()
        return det

    async def _detect_suffix(broker, apply: bool) -> dict:
        det = await broker.detect_suffix()
        if det["suffix"] is None:
            det["hint"] = f"Ningún {det['probe']} operable en esta cuenta (¿mercado cerrado o cuenta sin permiso de trading?) · sufijo sin cambios"
            return det
        kind = det["account_type"] or "otro tipo"
        shown = f"«{det['suffix']}»" if det["suffix"] else "sin sufijo"
        if det["matches"]:
            det["hint"] = f"Sufijo de símbolos correcto: {shown} (cuenta {engine.bk} {kind})"
            await store.update({"hfm_suffix_kind": kind})
        elif apply:
            await store.update({"mt_symbol_suffix": det["suffix"], "hfm_suffix_kind": kind, "mt_symbol_map": {}})
            det["applied"] = True
            det["hint"] = f"Sufijo de símbolos detectado y aplicado: {shown} (cuenta {engine.bk} {kind}) · tus pares se enviarán como {det['probe']}{det['suffix']}"
            await engine.log("INFO", f"Sufijo de símbolos {engine.bk} detectado: {shown} · cuenta {kind} · aplicado automáticamente")
        else:
            det["hint"] = f"Sufijo detectado {shown} (cuenta {engine.bk} {kind}); el configurado es {'«' + broker.suffix + '»' if broker.suffix else 'ninguno'}"
        return det

    async def _mode_checks() -> dict:
        c = store.cfg
        open_pos = await engine.db.positions.count_documents({})
        acc = await engine.account_summary()
        active = (await engine.get_settings()).symbols
        unchecked = [sym for sym in active if sym not in (c.hfm_symbols_checked or [])] if c.hfm_symbols_bad is not None else []
        current_plan = plan_key(c.hfm_account_type, c.hfm_reliability) if c.metaapi_account_id and c.hfm_account_type else ""
        plan_mismatch = bool(current_plan and current_plan != c.metaapi_plan)
        checks = [
            {"key": "keys", "label": f"Token de MetaApi y cuenta {engine.bk} guardados", "ok": bool(c.metaapi_token and c.mt_login and c.mt_password and c.mt_server), "required": True},
            {"key": "linked", "label": "Cuenta vinculada y desplegada en MetaApi", "ok": bool(c.metaapi_account_id and c.hfm_state == "DEPLOYED"), "required": True,
             "warn": bool(c.hfm_connection and c.hfm_connection != "CONNECTED") or plan_mismatch,
             "detail": f"{c.hfm_state or 'sin vincular'}{' · ' + c.hfm_connection if c.hfm_connection else ''}"
                       + (f" · {PLANS[current_plan]['short']}" if current_plan else "")
                       + (f" (elegido {PLANS[c.metaapi_plan]['short']}: recrear la cuenta)" if plan_mismatch else "")},
            {"key": "verified", "label": f"Conexión verificada con {engine.bk} {c.mt_server or ''}".strip(), "ok": bool(c.hfm_verified_at), "required": True,
             "detail": f"equidad {c.hfm_verified_equity:,.2f} {c.hfm_currency}" if c.hfm_verified_equity is not None else None},
            {"key": "trade_allowed", "label": "La cuenta permite operar (contraseña master)", "ok": c.hfm_trade_allowed is not False, "required": True,
             "detail": None if c.hfm_trade_allowed is not False else "trading deshabilitado o contraseña investor"},
            {"key": "symbols", "label": f"Pares operables en {engine.bk} con el sufijo de símbolos configurado", "ok": c.hfm_symbols_bad == [] and not unchecked, "required": True,
             "detail": ("sin comprobar · pulsa «Probar conexión» o «Comprobar símbolos»" if c.hfm_symbols_bad is None
                        else f"no operables: {', '.join(c.hfm_symbols_bad)} · revisa el sufijo" if c.hfm_symbols_bad
                        else f"pares nuevos sin comprobar: {', '.join(unchecked)} · pulsa «Comprobar símbolos»" if unchecked
                        else f"sufijo {'«' + c.mt_symbol_suffix + '»' if c.mt_symbol_suffix else 'ninguno'}" + (f" · cuenta {c.hfm_suffix_kind}" if c.hfm_suffix_kind else ""))},
            {"key": "stopped", "label": "Bot detenido", "ok": not engine.running, "required": True},
            {"key": "flat", "label": "Sin posiciones abiertas", "ok": open_pos == 0, "required": True, "detail": f"{open_pos} abiertas" if open_pos else None},
            {"key": "paper", "label": "Validación en paper: ≥ 30 operaciones con profit factor ≥ 1.2", "required": False,
             "ok": acc["total_trades"] >= 30 and (acc["profit_factor"] or 0) >= 1.2, "detail": f"{acc['total_trades']} ops · PF {acc['profit_factor'] or '—'}"},
        ]
        return {"mode": c.trading_mode, "env": c.mt_server or f"{engine.bk}", "account_kind": engine.account_kind(), "live_since": c.live_since, "checks": checks,
                "can_go_live": all(x["ok"] for x in checks if x["required"]), "verified_equity": c.hfm_verified_equity, "currency": c.hfm_currency or "USD"}

    @router.get("/mode")
    async def get_mode():
        return await _mode_checks()

    @router.post("/mode")
    async def set_mode(body: ModeRequest):
        if body.mode not in ("paper", "live"):
            raise HTTPException(400, "Modo inválido")
        checks = await _mode_checks()
        if body.mode == checks["mode"]:
            return checks
        blocked = [x["label"] for x in checks["checks"] if x["required"] and not x["ok"] and x["key"] in ("stopped", "flat")]
        if blocked:
            raise HTTPException(409, "Antes de cambiar de modo: " + " · ".join(blocked))
        if body.mode == "live":
            if await engine.db.lab.find_one({"_id": "main"}):
                raise HTTPException(409, "Desactiva el modo laboratorio (solo Cruce EMA 10/20, pensado para paper) antes de pasar a modo REAL")
            if body.confirm.strip().upper() != "REAL":
                raise HTTPException(400, "Escribe REAL para confirmar el cambio a modo real")
            if not checks["can_go_live"]:
                raise HTTPException(409, "Faltan requisitos: " + " · ".join(x["label"] for x in checks["checks"] if x["required"] and not x["ok"]))
            await store.update({"trading_mode": "live", "live_since": now_iso()})
            if body.reset_stats:
                await engine.reset_account(store.cfg.hfm_verified_equity or None)
            demo = engine.account_kind() == "demo"
            await engine.log("RISK", f"MODO REAL ACTIVADO · {engine.bk} {store.cfg.mt_server} vía MetaApi · " + ("cuenta DEMO: dinero ficticio, el bot opera igual que en real" if demo else "el bot operará con dinero real al iniciarse"))
            await engine.tg(f"🚨 <b>MODO REAL ACTIVADO</b> · {engine.bk} {store.cfg.mt_server}{' · <b>cuenta DEMO</b>' if demo else ''}\nEquidad {store.cfg.hfm_verified_equity or 0:,.2f} {store.cfg.hfm_currency}. "
                            + ("Dinero ficticio del servidor demo; el bot opera y aprende igual que en real." if demo else "El bot operará con dinero real cuando lo inicies."), "orders")
        else:
            await store.update({"trading_mode": "paper", "live_since": None})
            if body.reset_stats:
                await engine.reset_account()
            await engine.log("INFO", "Vuelta a modo PAPER · simulación con precios de Yahoo Finance")
            await engine.tg("📄 <b>Vuelta a modo PAPER</b> · simulación con precios de Yahoo Finance", "orders")
        return await _mode_checks()
