import logging
from datetime import datetime, timezone, timedelta
from models import now_iso
from shadow import AI_KINDS
from performance import excursion_stats
from evolution import AI_SOURCES, SOURCES
import evolution
import notify

logger = logging.getLogger("weekly")
STRATEGY_NAMES = {"trend_ema": "Tendencia EMA", "mean_reversion": "Reversión media", "breakout_turtle": "Turtle",
                  "supertrend_momentum": "Supertrend", "ema_pullback": "Pullback EMA21", "ema_cross": "Cruce EMA 10/20", "multi_horizon": "Multi-Horizonte", "double_divergence": "Divergencia doble"}
KIND_NAMES = {"BLOQUEADA": "riesgo", "NOTICIAS": "noticias", "FUNDING": "funding", "REGIMEN": "régimen BTC", "ROTACION": "rotación", "SESION": "sesiones",
              "ERROR": "error IA", "NO_EJECUTADA": "no ejecutadas"}


def _agg(rows: list) -> dict:
    n = len(rows)
    pnl = sum(r["pnl"] for r in rows)
    wins = [r for r in rows if r["pnl"] > 0]
    gw = sum(r["pnl"] for r in wins)
    gl = sum(r["pnl"] for r in rows if r["pnl"] <= 0)
    return {"trades": n, "pnl": round(pnl, 2), "wins": len(wins), "win_rate": round(len(wins) / n * 100, 1) if n else 0,
            "profit_factor": round(gw / abs(gl), 2) if gl else None, "fees": round(sum(r.get("fees", 0) for r in rows), 2)}


def _by(rows: list, field: str) -> list:
    keys = sorted({r[field] for r in rows})
    return sorted([{"key": k, **_agg([r for r in rows if r[field] == k])} for k in keys], key=lambda x: x["pnl"], reverse=True)


async def build_weekly(engine, db) -> dict:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).isoformat()
    settings = await engine.get_settings()
    s = await engine.account_summary()
    trades = await db.trades.find({"closed_at": {"$gte": since}}, {"_id": 0, "symbol": 1, "pnl": 1, "fees": 1, "strategy": 1, "reason": 1, "side": 1, "horizon": 1, "entry_type": 1}).to_list(5000)
    for t in trades:
        t["horizon"], t["entry_type"] = t.get("horizon") or "short", t.get("entry_type") or "market"
    shadows = await db.shadow_trades.find({"closed_at": {"$gte": since}}, {"_id": 0, "pnl": 1, "kind": 1}).to_list(5000)
    signals = {r["_id"]: r["n"] async for r in db.signals.aggregate([{"$match": {"created_at": {"$gte": since}}}, {"$group": {"_id": "$ai_decision", "n": {"$sum": 1}}}])}
    snap = await db.equity.find_one({"ts": {"$lte": since}}, {"_id": 0, "equity": 1, "net_deposits": 1}, sort=[("ts", -1)]) \
        or await db.equity.find_one({"ts": {"$gte": since}}, {"_id": 0, "equity": 1, "net_deposits": 1}, sort=[("ts", 1)])
    start_equity = snap["equity"] if snap else s["initial_balance"]
    flows = s["net_deposits"] - (float(snap.get("net_deposits") or 0) if snap else 0.0)
    week_pnl = s["equity"] - start_equity - flows
    by_symbol = _by(trades, "symbol")
    ai_shadow = [r for r in shadows if r["kind"] in AI_KINDS]
    checkin = await engine.checkin(settings, s)
    return {
        "from": since, "to": now_iso(), "mode": s["mode"],
        "equity": s["equity"], "start_equity": round(start_equity, 2), "week_pnl": round(week_pnl, 2), "week_flows": round(flows, 2),
        "week_return_pct": round(week_pnl / start_equity * 100, 2) if start_equity else 0,
        "total_return_pct": s["total_return_pct"], "max_drawdown": s["max_drawdown"], "open_positions": s["open_positions"],
        "week": _agg(trades), "by_symbol": by_symbol, "by_strategy": _by(trades, "strategy"), "by_horizon": _by(trades, "horizon"), "by_entry_type": _by(trades, "entry_type"),
        "best": [r for r in by_symbol if r["pnl"] > 0][:3], "worst": [r for r in reversed(by_symbol) if r["pnl"] < 0][:3],
        "signals": {"total": sum(signals.values()), "by_decision": signals},
        "shadow": {"trades": len(shadows), "filter_value": round(-sum(r["pnl"] for r in shadows), 2),
                   "ai_trades": len(ai_shadow), "ai_value": round(-sum(r["pnl"] for r in ai_shadow), 2),
                   "by_kind": {k: {"trades": sum(1 for r in shadows if r["kind"] == k), "value": round(-sum(r["pnl"] for r in shadows if r["kind"] == k), 2)}
                               for k in sorted({r["kind"] for r in shadows})}},
        "plan": {"phase_name": checkin["phase_name"], "message": checkin["message"], "warnings": checkin["warnings"],
                 "ready_to_advance": checkin["ready_to_advance"], "days_running": checkin["days_running"], "last_30d": checkin["last_30d"],
                 "contributions": checkin.get("contributions")},
        "news": engine.news.summary() if engine.news else None,
        "risk_profile": settings.risk_profile,
        "evolution": await evolution.week_summary(engine, since),
        "market": await _market_block(engine, db, settings),
        "excursion": await excursion_stats(db, since) if hasattr(settings, "partial_tp_enabled") else None,
    }


async def _market_block(engine, db, settings):
    """Crypto only: entry order type and pending maker entries (FX settings lack these fields)."""
    if not hasattr(settings, "entry_order_type"):
        return None
    return {"entry_order_type": settings.entry_order_type, "pending_entries": await db.pending_entries.count_documents({}), "max_long_positions": settings.max_long_positions}


def _sym(k: str) -> str:
    return k.replace("USDT", "")


VERDICT_ICON = {"MEJOR": "🟢 Mejoró", "PEOR": "🔴 Empeoró", "IGUAL": "⚪ Igual", "PENDIENTE": "⏳ Pendiente"}


def _evolution_lines(ev: dict, currency: str) -> list:
    from review import LABELS
    items = ev.get("changes") or []
    if not items:
        return []
    head = f"\n🧬 <b>Evolución</b> · {ev['total']} cambio{'s' if ev['total'] != 1 else ''} esta semana"
    parts = [f"{ev['ai_changes']} por la IA"] if ev["ai_changes"] else []
    parts += [f"{ev['better']} mejoraron"] if ev["better"] else []
    parts += [f"{ev['worse']} empeoraron"] if ev["worse"] else []
    parts += [f"{ev['pending']} pendientes"] if ev["pending"] else []
    parts += [f"{ev['reverted_by_ai']} deshecho{'s' if ev['reverted_by_ai'] != 1 else ''} por la IA"] if ev["reverted_by_ai"] else []
    parts += [f"{ev['reverted_manual']} deshecho{'s' if ev['reverted_manual'] != 1 else ''} a mano"] if ev["reverted_manual"] else []
    lines = [head + (" (" + " · ".join(parts) + ")" if parts else "")]
    for it in sorted(items, key=lambda x: x["ts"]):
        day = datetime.fromisoformat(it["ts"]).strftime("%d/%m")
        keys = ", ".join(LABELS.get(k, k) for k in it["keys"][:4]) + (" …" if len(it["keys"]) > 4 else "")
        if it["is_revert"]:
            who = "por la IA" if it["source"] in AI_SOURCES else "a mano"
            orig = f" del {datetime.fromisoformat(it['revert_of_ts']).strftime('%d/%m')}" if it.get("revert_of_ts") else ""
            lines.append(f"↩️ {day} Deshecho {who}{orig} ({SOURCES.get(it.get('revert_of_source'), it.get('revert_of_source') or 'cambio')}) · {keys}")
            continue
        a = it["after"]
        perf = f" · {a['pnl']:+.2f} {currency} en {a['trades']} ops" if a["trades"] else " · sin operaciones aún"
        tail = ""
        if it["reverted_at"]:
            tail = " → deshecho por la IA" if it.get("reverted_by") in AI_SOURCES else " → deshecho a mano"
        lines.append(f"{VERDICT_ICON.get(it['verdict'], it['verdict'])} · {day} {it['source_label']} · {keys}{perf}{tail}")
    return lines


def format_weekly(w: dict, currency: str = "USDT", extra: str = None) -> str:
    wk, sh, pl = w["week"], w["shadow"], w["plan"]
    arrow = "📈" if w["week_pnl"] >= 0 else "📉"
    lines = [f"{arrow} <b>Resumen semanal · {w['mode']}</b>",
             f"Equidad <b>${w['equity']:,.2f}</b> · semana {w['week_pnl']:+.2f} {currency} ({w['week_return_pct']:+.2f}%) · total {w['total_return_pct']:+.2f}% · DD máx {w['max_drawdown']}%"]
    if wk["trades"]:
        lines.append(f"{wk['trades']} operaciones · acierto {wk['win_rate']}% · PF {wk['profit_factor'] if wk['profit_factor'] is not None else '—'} · comisiones {wk['fees']:.2f} {currency}")
    else:
        lines.append("Sin operaciones cerradas esta semana.")
    if w.get("week_flows"):
        lines.append(f"💰 Aportes netos de la semana {w['week_flows']:+.2f} {currency} (excluidos del PnL)")
    if extra:
        lines.append(extra)
    if w["best"]:
        lines.append("\n<b>Mejores pares</b>\n" + "\n".join(f"🟢 {_sym(r['key'])} {r['pnl']:+.2f} {currency} · {r['trades']} ops · {r['win_rate']}%" for r in w["best"]))
    if w["worst"]:
        lines.append("\n<b>Peores pares</b>\n" + "\n".join(f"🔴 {_sym(r['key'])} {r['pnl']:+.2f} {currency} · {r['trades']} ops · {r['win_rate']}%" for r in w["worst"]))
    if w["by_strategy"]:
        top, bottom = w["by_strategy"][0], w["by_strategy"][-1]
        strat = f"\n<b>Estrategias</b>: mejor {STRATEGY_NAMES.get(top['key'], top['key'])} {top['pnl']:+.2f}"
        if bottom is not top:
            strat += f" · peor {STRATEGY_NAMES.get(bottom['key'], bottom['key'])} {bottom['pnl']:+.2f}"
        lines.append(strat)
    lp = next((r for r in w.get("by_horizon") or [] if r["key"] == "long"), None)
    if lp:
        lines.append(f"Largo plazo (Multi-Horizonte/Divergencia): {lp['trades']} ops · {lp['pnl']:+.2f} {currency} · acierto {lp['win_rate']}%")
    mkt = w.get("market")
    exc = w.get("excursion")
    if exc and exc.get("tracked"):
        tr = next((r for r in exc["by_reason"] if r["key"] == "TRAILING"), None)
        lines.append(f"Recorrido: {exc['tracked']} ops con MFE medio +{exc['avg_mfe_r']:.2f} R (MAE {exc['avg_mae_r']:.2f} R)"
                     + ("" if w.get("no_tp") else f" · {exc['near_miss']} rozaron el TP (≥70 %) sin tocarlo ({exc['near_miss_pct']}%)")
                     + (f" · salidas por trailing: {tr['trades']} con MFE {tr['avg_mfe_r']:.2f} R" if tr else "")
                     + (f" · cierres parciales: {exc['partial']['trades']} ({exc['partial']['pnl']:+.2f} {currency})" if exc["partial"]["trades"] else ""))
    if mkt:
        lines.append(f"\n<b>Entradas</b>: {'límite maker' if mkt['entry_order_type'] == 'maker' else 'a mercado'}" + (f" · {mkt['pending_entries']} en espera" if mkt["pending_entries"] else ""))
    mk = {r["key"]: r for r in w.get("by_entry_type") or []}
    if mk.get("maker") or mk.get("market_fallback"):
        m, f = mk.get("maker", {"trades": 0}), mk.get("market_fallback", {"trades": 0})
        tot = m["trades"] + f["trades"]
        lines.append(f"Entradas límite maker: {m['trades']}/{tot} llenadas ({round(m['trades'] / tot * 100) if tot else 0}%) · {f['trades']} a mercado tras esperar")
    sig = w["signals"]
    if sig["total"]:
        d = sig["by_decision"]
        lines.append(f"\n<b>Filtro IA</b>: {sig['total']} señales · aprobadas {d.get('APROBADA', 0)} · rechazadas {d.get('RECHAZADA', 0)} · bloqueadas {d.get('BLOQUEADA', 0) + d.get('NOTICIAS', 0)}")
    if sh["trades"]:
        verb = "evitó pérdidas por" if sh["filter_value"] >= 0 else "dejó escapar"
        lines.append(f"Valor del filtro: {verb} <b>{abs(sh['filter_value']):.2f} {currency}</b> en {sh['trades']} operaciones sombra"
                     + (f" (IA: {sh['ai_value']:+.2f} {currency} en {sh['ai_trades']})" if sh["ai_trades"] else ""))
        detail = [f"{KIND_NAMES.get(k, k)} {v['value']:+.2f}" for k, v in sh.get("by_kind", {}).items() if k not in AI_KINDS and v["trades"]]
        if detail:
            lines.append("Por filtro: " + " · ".join(detail))
    elif sig["total"] and w.get("shadow_supported", True):
        lines.append("Valor del filtro: aún sin operaciones sombra cerradas esta semana.")
    if w.get("evolution"):
        lines.extend(_evolution_lines(w["evolution"], currency))
    lines.append(f"\n📋 <b>Plan</b> · {pl['message']}")
    l30 = pl["last_30d"]
    lines.append(f"Últimos 30 días: {l30['pnl']:+.2f} {currency} · {l30['trades']} ops · PF {l30['profit_factor'] if l30['profit_factor'] is not None else '—'} · día {pl['days_running']}")
    ct = pl.get("contributions")
    if ct and (ct["target"] > 0 or ct["total_deposited"] or ct["total_withdrawn"]):
        vs = ct.get("capital_vs_plan")
        lines.append(f"💰 Aportes: {ct['message']}" + (f" · capital {vs:+.2f} {currency} vs plan" if vs is not None and ct["target"] > 0 else ""))
    for wn in pl["warnings"]:
        lines.append(f"⚠️ {wn}")
    if w["news"] and w["news"].get("ready"):
        lines.append(f"\n📰 Noticias: riesgo {w['news']['risk']} · sentimiento {w['news']['sentiment']:+d}")
    return "\n".join(lines)


async def send_weekly(engine, db, force: bool = False, *, build=None, fmt=format_weekly, prefix: str = "") -> dict:
    w = await (build or build_weekly)(engine, db)
    text = prefix + fmt(w)
    bot = notify.bot_of(engine)
    sent = bool(await notify.send(text, bot=bot) if force else await notify.notify(text, "daily", bot=bot))
    if sent:
        await db.weekly.update_one({"_id": "latest"}, {"$set": {**w, "text": text, "sent_at": now_iso()}}, upsert=True)
        await engine.log("PLAN", f"Resumen semanal enviado: {w['week_pnl']:+.2f} ({w['week_return_pct']:+.2f}%) · {w['week']['trades']} ops"
                                 + (f" · filtro {w['shadow']['filter_value']:+.2f}" if w["shadow"]["trades"] else ""))
    else:
        await engine.log("INFO", "Resumen semanal generado pero no enviado (Telegram desactivado o aviso 'daily' apagado)")
    return {**w, "text": text, "sent": sent}
