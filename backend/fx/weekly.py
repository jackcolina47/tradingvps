"""Weekly Telegram summary for the FX bots: the shared weekly builder plus net pips of the week (spread included)."""
import weekly
from fx.symbols import pips
from fx.review import gates_context

PREFIX = "🏦 <b>HotForex AI</b>\n"


def prefix(engine) -> str:
    return f"{engine.profile.tg_icon} <b>{engine.profile.tg_name}</b>\n"


async def build(engine, db) -> dict:
    w = await weekly.build_weekly(engine, db)
    rows = await db.trades.find({"closed_at": {"$gte": w["from"]}}, {"_id": 0, "symbol": 1, "side": 1, "entry_price": 1, "exit_price": 1}).to_list(5000)
    w["pips"] = round(sum(pips(r["symbol"], (r["exit_price"] - r["entry_price"]) * (1 if r["side"] == "long" else -1) - engine.costs.spread(r["symbol"])) for r in rows), 1)
    w["no_tp"] = engine.profile.ema_only
    try:
        w["fx_gates"] = await gates_context(engine, await engine.get_settings())
    except Exception:
        w["fx_gates"] = None
    return w


def fmt(w: dict) -> str:
    parts = [f"Pips netos de la semana: <b>{w['pips']:+.1f}</b>"] if w["week"]["trades"] else []
    g = w.get("fx_gates")
    if g:
        rg = g["regimen_usd"]
        exp = ", ".join(f"{c} {n:+d}" for c, n in sorted(g["exposicion_por_divisa"]["neta_actual"].items()) if n) or "sin posiciones"
        dead = [s for s, v in g["volatilidad"]["por_par"].items() if v["regimen"] == "MUERTO"]
        parts.append(f"<b>Filtros FX</b>: régimen USD {rg.get('state', '—')}" + (f" ({rg['distance_pct']:+.2f} % vs SMA{rg['sma']})" if rg.get("distance_pct") is not None else "")
                     + f" · exposición neta: {exp} (tope {g['exposicion_por_divisa']['tope']})"
                     + (f" · volatilidad muerta en {', '.join(dead)}" if dead else "")
                     + f" · rollover {'activo' if g['rollover']['activo'] else 'apagado'} · estacionalidad {'activa' if g['estacionalidad']['activo'] else 'apagada'} · FOMC {'activa' if g['fomc']['activo'] else 'apagada'}")
    return weekly.format_weekly(w, "USD", "\n".join(parts) if parts else None)


async def send(engine, db, force: bool = False) -> dict:
    return await weekly.send_weekly(engine, db, force, build=build, fmt=fmt, prefix=prefix(engine))
