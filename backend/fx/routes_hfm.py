"""/api/fx/* · MetaTrader account through our own bridge: connection test, reconnect, symbol suffix & tradability checks, paper ↔ REAL mode switch."""
import logging
from fastapi import HTTPException

from models import ModeRequest, now_iso
from fx.models import HfmTestRequest
from fx.bridge import MtBridge, BridgeError
from fx.engine_core import HFM_OK_STATES, HFM_OK_CONN

logger = logging.getLogger(__name__)


def register(router, engine, store):
    def _chart_backoff_reset():
        """User tested / reconnected the account: let the chart try the terminal candles again right away."""
        if engine.broker:
            engine.broker.chart_fail_ts = 0.0

    def _bridge(body: HfmTestRequest = None):
        url, token, account_id = store.conn
        use_saved = not (body and body.bridge_token and not body.bridge_token.startswith("••"))
        if not use_saved:
            token = body.bridge_token.strip()
            url = (body.bridge_url or "").strip().rstrip("/") or url
            account_id = (body.bridge_account_id or "").strip() or account_id
        if not token:
            raise HTTPException(400, "Introduce el Token del Puente primero (o define MT_BRIDGE_TOKEN en el servidor)")
        return MtBridge(url, token), use_saved, account_id

    @router.post("/hfm/test")
    async def hfm_test(body: HfmTestRequest = None):
        api, saved, account_id = _bridge(body)
        out = {"ok": True, "token_valid": False, "account": None, "info": None, "positions": [], "saved_credentials": saved, "account_id": account_id, "url": api.url}
        try:
            try:
                acc = await api.account(account_id)
            except BridgeError as e:
                if e.status != 404:
                    raise
                acc = None
            out["token_valid"] = True
            if acc:
                out["account"] = {**acc, "id": account_id}
                if acc["state"] in HFM_OK_STATES and acc["connection"] in HFM_OK_CONN:
                    info = await api.account_info(account_id)
                    info["tradeAllowed"] = info.get("tradeAllowed", acc["trade_allowed"] if acc["trade_allowed"] is not None else True)
                    info["broker"] = info.get("broker") or info.get("name") or engine.bk
                    info["server"] = info.get("server") or acc.get("server") or ""
                    info["login"] = str(info.get("login") or acc.get("login") or "")
                    out["info"] = info
                    out["positions"] = await api.positions(account_id)
        except BridgeError as e:
            raise HTTPException(424, e.msg)
        except Exception as e:
            raise HTTPException(424, f"No se pudo conectar con el puente MT: {str(e)[:160]}")
        finally:
            await api.aclose()
        if saved:
            patch = {"hfm_state": (out["account"] or {}).get("state") or ("TOKEN_OK" if not out["account"] else "DEPLOYED" if out["info"] else ""),
                     "hfm_connection": (out["account"] or {}).get("connection") or ("CONNECTED" if out["info"] else "")}
            if out["info"]:
                info = out["info"]
                patch.update(hfm_verified_at=now_iso(), hfm_verified_equity=info.get("equity"), hfm_currency=info.get("currency", ""), hfm_leverage=info.get("leverage"),
                             hfm_broker=info["broker"], hfm_trade_allowed=bool(info["tradeAllowed"]), mt_server=info["server"], mt_login=info["login"])
                await engine.log("INFO", f"Cuenta {engine.bk} verificada vía puente MT ({account_id}) · {info['broker']} {info['server']} · equidad {info.get('equity')} {info.get('currency', '')}"
                                         + ("" if info["tradeAllowed"] else " · SIN permiso de trading"))
            await store.update(patch)
            _chart_backoff_reset()
        out["hint"] = (None if out["info"] else "La cuenta existe en el puente pero el terminal aún no está conectado al bróker: pulsa «Reconectar» y vuelve a probar" if out["account"]
                       else f"Token válido, pero el puente no conoce la cuenta «{account_id}»: revisa el ID de Cuenta.")
        if out["info"] and not out["info"]["tradeAllowed"]:
            out["hint"] = "La cuenta está conectada pero NO permite operar: activa el trading automático en el terminal del puente (o usa la contraseña master, no investor)."
        if out["info"] and saved and out["info"]["tradeAllowed"]:
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
        except BridgeError as e:
            return {"suffix": None, "hint": f"No se pudo comprobar el sufijo de símbolos: {e.msg[:120]}"}, None
        finally:
            await broker.aclose()

    @router.post("/hfm/reconnect")
    async def hfm_reconnect():
        api, _, account_id = _bridge()
        try:
            await api.reconnect(account_id)
            acc = await api.account(account_id)
        except BridgeError as e:
            raise HTTPException(424, e.msg)
        finally:
            await api.aclose()
        await store.update({"hfm_state": acc["state"], "hfm_connection": acc["connection"]})
        _chart_backoff_reset()
        await engine.log("INFO", f"Reconexión de la cuenta {engine.bk} ({account_id}) solicitada al puente MT · {acc['state'] or '?'} · {acc['connection'] or '?'}")
        return {"ok": True, "account": {**acc, "id": account_id}, "hint": "Reconexión solicitada. Vuelve a «Probar conexión» en unos segundos."}

    @router.post("/hfm/symbols")
    async def hfm_symbols():
        if not store.conn[1]:
            raise HTTPException(400, "Configura primero el Token del Puente")
        broker = engine.broker or engine.new_broker()
        try:
            return await _check_symbols(broker)
        finally:
            if broker is not engine.broker:
                await broker.aclose()

    async def _check_symbols(broker) -> dict:
        """Quote + tradability of every active pair with the configured suffix; the result feeds the «Pasar a REAL» checklist.
        Temporary bridge failures are reported but never persisted as 'not tradable' (that would block the pair in REAL)."""
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
                         + (f" · sin respuesta del puente MT (repite la comprobación): {', '.join(errors)}" if errors else ""))
        return {"suffix": broker.suffix, "symbols": rows, "missing": missing, "disabled": disabled, "errors": errors, "resolved": resolved, "checked_at": store.cfg.hfm_symbols_checked_at}

    @router.post("/hfm/detect-suffix")
    async def hfm_detect_suffix(apply: bool = True):
        if not store.conn[1]:
            raise HTTPException(400, "Configura primero el Token del Puente")
        broker = engine.broker or engine.new_broker()
        try:
            det = await _detect_suffix(broker, apply)
        except BridgeError as e:
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
        connected = c.hfm_state in ("DEPLOYED",) and c.hfm_connection in HFM_OK_CONN
        checks = [
            {"key": "keys", "label": "Puente MetaTrader configurado (URL, token e ID de cuenta)", "ok": bool(store.conn[1]), "required": True,
             "detail": f"{store.conn[0]} · cuenta {store.conn[2]}"},
            {"key": "linked", "label": "Cuenta conectada en el puente", "ok": connected, "required": True,
             "warn": bool(c.hfm_connection and c.hfm_connection != "CONNECTED"),
             "detail": f"{c.hfm_state or 'sin probar'}{' · ' + c.hfm_connection if c.hfm_connection else ''}"},
            {"key": "verified", "label": f"Conexión verificada con {engine.bk} {c.mt_server or ''}".strip(), "ok": bool(c.hfm_verified_at), "required": True,
             "detail": f"equidad {c.hfm_verified_equity:,.2f} {c.hfm_currency}" if c.hfm_verified_equity is not None else None},
            {"key": "trade_allowed", "label": "La cuenta permite operar (trading automático habilitado)", "ok": c.hfm_trade_allowed is not False, "required": True,
             "detail": None if c.hfm_trade_allowed is not False else "trading deshabilitado en el terminal o contraseña investor"},
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
            await engine.log("RISK", f"MODO REAL ACTIVADO · {engine.bk} {store.cfg.mt_server} vía puente MT · " + ("cuenta DEMO: dinero ficticio, el bot opera igual que en real" if demo else "el bot operará con dinero real al iniciarse"))
            await engine.tg(f"🚨 <b>MODO REAL ACTIVADO</b> · {engine.bk} {store.cfg.mt_server}{' · <b>cuenta DEMO</b>' if demo else ''}\nEquidad {store.cfg.hfm_verified_equity or 0:,.2f} {store.cfg.hfm_currency}. "
                            + ("Dinero ficticio del servidor demo; el bot opera y aprende igual que en real." if demo else "El bot operará con dinero real cuando lo inicies."), "orders")
        else:
            await store.update({"trading_mode": "paper", "live_since": None})
            if body.reset_stats:
                await engine.reset_account()
            await engine.log("INFO", "Vuelta a modo PAPER · simulación con precios de Yahoo Finance")
            await engine.tg("📄 <b>Vuelta a modo PAPER</b> · simulación con precios de Yahoo Finance", "orders")
        return await _mode_checks()
