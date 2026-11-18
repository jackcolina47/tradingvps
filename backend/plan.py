from datetime import datetime, timezone, timedelta

WITHDRAW_PCT = 80
GROWTH_CAP = 5000.0
SCENARIOS = {
    "conservador": {"name": "Prudente", "rate": 2.5, "desc": "≈34% anual · lo que sostienen los fondos sistemáticos consistentes"},
    "base": {"name": "Realista", "rate": 4.0, "desc": "≈60% anual · bot con profit factor > 1.3 operando a 3x"},
    "optimista": {"name": "Ambicioso", "rate": 6.0, "desc": "≈100% anual · solo en rachas de tendencia fuerte, no sostenible"},
}
PHASE_INFO = {
    "semilla": {"name": "Capital semilla", "risk": 1.0,
                "rule": "Reinvertir el 100% de la ganancia. Cero retiros. Riesgo 1% por operación.",
                "advance": "Duplicar el capital inicial manteniendo el drawdown por debajo del 12%."},
    "crecimiento": {"name": "Crecimiento", "risk": 1.0,
                    "rule": "Seguir reinvirtiendo el 100%. Los aportes mensuales aceleran más esta fase que cualquier ajuste.",
                    "advance": "Llegar a $5.000 con profit factor ≥ 1.3 en al menos 100 operaciones."},
    "consolidacion": {"name": "Consolidación", "risk": 1.5,
                      "rule": "Subir el riesgo a 1.5% por operación (métricas ya probadas). Reinvertir el 100%.",
                      "advance": "Alcanzar el capital objetivo que genera la meta mensual."},
    "ingreso": {"name": "Ingreso mensual", "risk": 1.5,
                "rule": f"Retirar el {WITHDRAW_PCT}% de la ganancia de cada mes; el {100 - WITHDRAW_PCT}% se queda como colchón para meses negativos.",
                "advance": "Meta cumplida. Si el drawdown supera el 15%, volver a riesgo 1% y pausar retiros."},
}


def _phase(capital: float, initial: float, target_capital: float) -> str:
    if capital >= target_capital:
        return "ingreso"
    if capital >= max(GROWTH_CAP, 2 * initial):
        return "consolidacion"
    if capital >= 2 * initial:
        return "crecimiento"
    return "semilla"


def project(initial: float, rate_pct: float, target_monthly: float, deposit: float = 0.0, max_months: int = 360) -> dict:
    rate = rate_pct / 100
    base = {"rate_pct": rate_pct, "reachable": False, "months": None, "target_capital": None,
            "income_at_target": None, "total_deposited": 0, "series": [], "timeline": []}
    if rate <= 0:
        return base
    final_rate = rate * PHASE_INFO["ingreso"]["risk"]
    target_capital = target_monthly / (final_rate * WITHDRAW_PCT / 100)
    capital, series, timeline, reached, deposited = initial, [], [], None, 0.0
    for month in range(1, max_months + 1):
        key = _phase(capital, initial, target_capital)
        if not timeline or timeline[-1]["key"] != key:
            timeline.append({"key": key, **PHASE_INFO[key], "start_month": month - 1, "start_capital": round(capital, 2)})
        if key == "ingreso" and reached is None:
            reached = month - 1
        profit = capital * rate * PHASE_INFO[key]["risk"]
        withdraw = profit * WITHDRAW_PCT / 100 if key == "ingreso" else 0.0
        dep = 0.0 if key == "ingreso" else deposit
        capital += profit - withdraw + dep
        deposited += dep
        series.append({"month": month, "capital": round(capital, 2), "profit": round(profit, 2),
                       "withdraw": round(withdraw, 2), "phase": key})
        if reached is not None and month >= reached + 12:
            break
    return {**base, "reachable": reached is not None, "months": reached, "target_capital": round(target_capital, 2),
            "income_at_target": round(target_capital * final_rate * WITHDRAW_PCT / 100, 2),
            "total_deposited": round(deposited, 2), "series": series, "timeline": timeline}


def build_plan(req: dict, scenarios: dict = SCENARIOS) -> dict:
    rates = {k: v["rate"] for k, v in scenarios.items()}
    out = []
    for key, rate in rates.items():
        info = scenarios.get(key, {"name": "Según backtest", "desc": "Mediana Monte Carlo de tus propias estrategias sobre los últimos datos reales"})
        pr = project(req["initial_capital"], rate, req["target_monthly"], req["monthly_deposit"]) if rate is not None else project(req["initial_capital"], 0, req["target_monthly"])
        out.append({"key": key, "name": info["name"], "desc": info["desc"], "rate_pct": rate,
                    "reachable": pr["reachable"], "months": pr["months"], "target_capital": pr["target_capital"],
                    "income_at_target": pr["income_at_target"], "total_deposited": pr["total_deposited"]})
    sel = req["scenario"] if rates.get(req["scenario"]) is not None else "base"
    return {"inputs": req, "scenarios": out, "selected": sel,
            "projection": project(req["initial_capital"], rates[sel], req["target_monthly"], req["monthly_deposit"]),
            "phase_info": PHASE_INFO, "withdraw_pct": WITHDRAW_PCT}


PHASE_ORDER = ["validacion", "semilla", "crecimiento", "consolidacion", "ingreso"]
PHASE_NAMES = {"validacion": "Validación en paper", **{k: v["name"] for k, v in PHASE_INFO.items()}}


def _crit(label, value, target, kind="min", fmt="num"):
    met = value >= target if kind == "min" else value <= target
    if kind == "min":
        pct = 100 if met else (value / target * 100 if target else 0)
    else:
        pct = 100 if met else (target / value * 100 if value else 100)
    return {"label": label, "value": round(float(value), 2), "target": target, "kind": kind, "fmt": fmt,
            "met": bool(met), "pct": round(max(0.0, min(100.0, float(pct))), 1)}


def phase_criteria(key: str, s: dict, acc: dict, last30: dict, settings, target_capital: float, days: int, currency: str = "USDT") -> list:
    pf = s["profit_factor"] if s["profit_factor"] is not None else (9.99 if s["realized_pnl"] > 0 else 0)
    initial = acc["initial_balance"]
    if key == "validacion":
        return [_crit("Operaciones cerradas", s["total_trades"], 60), _crit("Profit factor", pf, 1.3),
                _crit("Drawdown máximo (%)", s["max_drawdown"], 10, "max", "pct"), _crit("Días operando", days, 28)]
    if key == "semilla":
        return [_crit(f"Equidad ({currency})", s["equity"], round(2 * initial, 2), fmt="usd"),
                _crit("Drawdown máximo (%)", s["max_drawdown"], 12, "max", "pct")]
    if key == "crecimiento":
        return [_crit(f"Equidad ({currency})", s["equity"], round(max(GROWTH_CAP, 2 * initial), 2), fmt="usd"),
                _crit("Profit factor", pf, 1.3), _crit("Operaciones cerradas", s["total_trades"], 100)]
    if key == "consolidacion":
        return [_crit(f"Equidad ({currency})", s["equity"], round(target_capital, 2), fmt="usd")]
    return [_crit(f"Ganancia últimos 30 días ({currency})", last30["pnl"], settings.plan_target_monthly, fmt="usd"),
            _crit("Drawdown máximo (%)", s["max_drawdown"], 15, "max", "pct")]


async def last30_stats(db, equity: float) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    agg = await db.trades.aggregate([{"$match": {"closed_at": {"$gte": since}}}, {"$group": {
        "_id": None, "n": {"$sum": 1}, "pnl": {"$sum": "$pnl"},
        "wins": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
        "gw": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
        "gl": {"$sum": {"$cond": [{"$lte": ["$pnl", 0]}, "$pnl", 0]}},
    }}]).to_list(1)
    a = agg[0] if agg else {"n": 0, "pnl": 0.0, "wins": 0, "gw": 0.0, "gl": 0.0}
    return {"trades": a["n"], "pnl": round(a["pnl"], 2), "win_rate": round(a["wins"] / a["n"] * 100, 1) if a["n"] else 0,
            "profit_factor": round(a["gw"] / abs(a["gl"]), 2) if a["gl"] else None,
            "return_pct": round(a["pnl"] / (equity - a["pnl"]) * 100, 2) if equity - a["pnl"] > 0 else 0}


async def contributions(db, acc: dict, settings, rate_pct: float, currency: str = "USDT") -> dict:
    """Real deposits/withdrawals (cash_flows) against the monthly contribution committed in the plan, per calendar month since the account
    started, plus the capital the projection expected by now."""
    target = round(float(settings.plan_monthly_deposit or 0), 2)
    flows = await db.cash_flows.find({}, {"_id": 0, "ts": 1, "amount": 1}).to_list(5000)
    start, now = datetime.fromisoformat(acc["created_at"]), datetime.now(timezone.utc)
    months, y, m = [], start.year, start.month
    while (y, m) <= (now.year, now.month):
        key = f"{y:04d}-{m:02d}"
        dep = sum(f["amount"] for f in flows if f["amount"] > 0 and f["ts"][:7] == key)
        wd = sum(-f["amount"] for f in flows if f["amount"] < 0 and f["ts"][:7] == key)
        months.append({"month": key, "deposited": round(dep, 2), "withdrawn": round(wd, 2), "target": target,
                       "met": target <= 0 or dep >= target - 0.005, "current": (y, m) == (now.year, now.month)})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    months = months[-12:]
    cur = months[-1]
    total_dep = round(sum(f["amount"] for f in flows if f["amount"] > 0), 2)
    total_wd = round(sum(-f["amount"] for f in flows if f["amount"] < 0), 2)
    months_met = sum(1 for mo in months if mo["met"]) if target > 0 else 0
    elapsed = max(0, (now - start).days // 30)
    pr = project(acc["initial_balance"], rate_pct, settings.plan_target_monthly, target) if rate_pct and rate_pct > 0 else None
    expected = pr["series"][elapsed - 1]["capital"] if pr and pr["series"] and elapsed >= 1 else acc["initial_balance"] + (target * elapsed)
    expected = round(expected, 2)
    if target <= 0:
        msg = (f"El plan no incluye aporte mensual · aportes netos reales {acc.get('net_deposits', 0):+,.2f} {currency}" if flows
               else "El plan no incluye aporte mensual.")
    elif cur["met"]:
        msg = f"Aporte de este mes cumplido ({fmt_usd(cur['deposited'])} de {fmt_usd(target)}) · {months_met} de {len(months)} meses cumplidos"
    else:
        msg = (f"Este mes llevas {fmt_usd(cur['deposited'])} de {fmt_usd(target)} (faltan {fmt_usd(target - cur['deposited'])}) · "
               f"{months_met} de {len(months)} meses cumplidos")
    return {"target": target, "currency": currency, "months": months, "this_month": cur, "months_met": months_met, "months_total": len(months),
            "total_deposited": total_dep, "total_withdrawn": total_wd, "net_deposits": round(float(acc.get("net_deposits") or 0), 2),
            "expected_to_date": round(target * len(months), 2), "elapsed_months": elapsed, "expected_capital": expected, "message": msg}


def build_checkin(s: dict, acc: dict, last30: dict, settings, rate_pct: float, days: int, currency: str = "USDT", contributions: dict = None) -> dict:
    rate = rate_pct / 100 if rate_pct and rate_pct > 0 else SCENARIOS["base"]["rate"] / 100
    target_capital = settings.plan_target_monthly / (rate * PHASE_INFO["ingreso"]["risk"] * WITHDRAW_PCT / 100)
    cur = PHASE_ORDER[min(settings.plan_phase, len(PHASE_ORDER) - 1)]
    phases = []
    for i, key in enumerate(PHASE_ORDER):
        crit = phase_criteria(key, s, acc, last30, settings, target_capital, days, currency)
        status = "done" if i < settings.plan_phase else "current" if i == settings.plan_phase else "pending"
        phases.append({"key": key, "index": i, "name": PHASE_NAMES[key], "status": status, "criteria": crit,
                       "met": sum(c["met"] for c in crit), "total": len(crit),
                       "risk": PHASE_INFO.get(key, {}).get("risk", 1.0)})
    current = phases[settings.plan_phase]
    ready = current["met"] == current["total"] and settings.plan_phase < len(PHASE_ORDER) - 1
    goal_met = current["met"] == current["total"] and settings.plan_phase == len(PHASE_ORDER) - 1
    warnings = []
    if s["max_drawdown"] > 15:
        warnings.append(f"Drawdown {s['max_drawdown']:.1f}% supera el 15%: reduce el riesgo a 1% y pausa retiros.")
    if s["total_trades"] >= 30 and s["profit_factor"] is not None and s["profit_factor"] < 1:
        warnings.append(f"Profit factor {s['profit_factor']} < 1 tras {s['total_trades']} operaciones: el sistema pierde dinero, revisa estrategias y pares.")
    if s["daily_paused"]:
        warnings.append("Límite de pérdida diaria alcanzado hoy: el bot no abrirá nuevas posiciones hasta mañana.")
    nxt = PHASE_NAMES[PHASE_ORDER[settings.plan_phase + 1]] if ready else None
    if goal_met:
        message = f"Meta cumplida: {fmt_usd(last30['pnl'])} en los últimos 30 días. Mantén el plan de retiros del {WITHDRAW_PCT}%."
    elif ready:
        message = f"Cumples los {current['total']} criterios de «{current['name']}». Puedes avanzar a «{nxt}»."
    else:
        message = f"Fase actual: {current['name']} · {current['met']} de {current['total']} criterios cumplidos."
    if contributions:
        contributions = {**contributions, "capital_vs_plan": round(s["equity"] - contributions["expected_capital"], 2)}
    return {"phase": settings.plan_phase, "phase_key": cur, "phase_name": current["name"], "ready_to_advance": ready,
            "goal_met": goal_met, "next_phase_name": nxt, "next_phase_risk": phases[settings.plan_phase + 1]["risk"] if ready else None,
            "message": message, "phases": phases, "warnings": warnings, "days_running": days,
            "target_capital": round(target_capital, 2), "last_30d": last30, "contributions": contributions}


def fmt_usd(v: float) -> str:
    return f"${v:,.2f}"
