"""Bybit real-account routes for the crypto bot: connection test and paper ⇄ REAL mode switch."""
import time
from fastapi import HTTPException

import notify
from models import BybitTestRequest, ModeRequest, now_iso
from bybit import BybitClient, BybitError
from deps import db, engine, store, protected_router

router = protected_router()


def _bybit_client(body: BybitTestRequest = None):
    c = store.cfg
    use_saved = not (body and body.api_key and body.api_secret and not body.api_key.startswith("••"))
    key, secret = (c.bybit_api_key, c.bybit_api_secret) if use_saved else (body.api_key.strip(), body.api_secret.strip())
    if not key or not secret:
        raise HTTPException(400, "Introduce la API key y el secret de Bybit")
    env = (body.env if body and body.env else c.bybit_env)
    base = body.base_url if body and body.base_url is not None else c.bybit_base_url
    return BybitClient(key, secret, env, base), use_saved


@router.post("/bybit/test")
async def bybit_test(body: BybitTestRequest = None):
    client, saved = _bybit_client(body)
    try:
        t0 = time.time()
        srv = await client.server_time()
        offset_ms = int((srv - t0) * 1000) if srv else None
        wallet = await client.wallet()
        positions = await client.positions()
    except BybitError as e:
        raise HTTPException(424, f"Bybit rechazó la conexión ({e.code}): {e.msg}")
    except Exception as e:
        raise HTTPException(424, f"No se pudo conectar con Bybit: {str(e)[:160]}")
    finally:
        await client.aclose()
    if saved:
        await store.update({"bybit_verified_at": now_iso(), "bybit_verified_equity": wallet["equity"]})
        await engine.log("INFO", f"Conexión Bybit {client.env} verificada · equidad ${wallet['equity']:,.2f}")
    return {"ok": True, "env": client.env, "base_url": client.base_url, "server_offset_ms": offset_ms,
            "wallet": wallet, "open_positions": len(positions), "positions": positions, "saved_credentials": saved}


async def _mode_checks() -> dict:
    c = store.cfg
    open_pos = await db.positions.count_documents({})
    checkin = await engine.checkin()
    phase0 = checkin["phases"][0]
    checks = [
        {"key": "keys", "label": "API key y secret de Bybit guardados", "ok": bool(c.bybit_api_key and c.bybit_api_secret), "required": True},
        {"key": "verified", "label": f"Conexión verificada con Bybit {c.bybit_env}", "ok": bool(c.bybit_verified_at), "required": True,
         "detail": f"equidad ${c.bybit_verified_equity:,.2f}" if c.bybit_verified_equity is not None else None},
        {"key": "stopped", "label": "Bot detenido", "ok": not engine.running, "required": True},
        {"key": "flat", "label": "Sin posiciones abiertas", "ok": open_pos == 0, "required": True, "detail": f"{open_pos} abiertas" if open_pos else None},
        {"key": "plan", "label": "Fase 0 del plan completada (validación en paper)", "ok": phase0["met"] == phase0["total"], "required": False,
         "detail": f"{phase0['met']}/{phase0['total']} criterios"},
    ]
    return {"mode": c.trading_mode, "env": c.bybit_env, "live_since": c.live_since, "checks": checks,
            "can_go_live": all(x["ok"] for x in checks if x["required"]), "verified_equity": c.bybit_verified_equity}


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
        if body.confirm.strip().upper() != "REAL":
            raise HTTPException(400, "Escribe REAL para confirmar el cambio a modo real")
        if not checks["can_go_live"]:
            raise HTTPException(409, "Faltan requisitos: " + " · ".join(x["label"] for x in checks["checks"] if x["required"] and not x["ok"]))
        await store.update({"trading_mode": "live", "live_since": now_iso()})
        if body.reset_stats:
            await engine.reset_account(store.cfg.bybit_verified_equity or None)
        await engine.log("RISK", f"MODO REAL ACTIVADO · Bybit {store.cfg.bybit_env} · el bot operará con dinero real al iniciarse")
        await notify.notify(f"🚨 <b>MODO REAL ACTIVADO</b> · Bybit {store.cfg.bybit_env}\nEquidad ${store.cfg.bybit_verified_equity or 0:,.2f}. El bot operará con dinero real cuando lo inicies.", "orders", bot="bybit")
    else:
        await store.update({"trading_mode": "paper", "live_since": None})
        if body.reset_stats:
            await engine.reset_account()
        await engine.log("INFO", "Vuelta a modo PAPER · simulación con precios reales")
        await notify.notify("📄 <b>Vuelta a modo PAPER</b> · simulación con precios reales", "orders", bot="bybit")
    return await _mode_checks()
