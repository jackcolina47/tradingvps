"""Growth plan for the FX bots: the same compounding phases as crypto with forex-realistic scenarios."""
import plan as core

CURRENCY = "USD"
SCENARIOS = {
    "conservador": {"name": "Prudente", "rate": 1.5, "desc": "≈20% anual · lo que sostienen los sistemas forex consistentes con riesgo bajo"},
    "base": {"name": "Realista", "rate": 3.0, "desc": "≈43% anual · bot con profit factor > 1.3 arriesgando 1% por operación"},
    "optimista": {"name": "Ambicioso", "rate": 5.0, "desc": "≈80% anual · solo en rachas de tendencia fuerte en oro y mayores, no sostenible"},
}


def build_plan(req: dict) -> dict:
    return {**core.build_plan(req, SCENARIOS), "currency": CURRENCY}


def _rate(settings) -> float:
    return SCENARIOS.get(settings.plan_scenario, SCENARIOS["base"])["rate"]


async def contributions(db, acc: dict, settings) -> dict:
    return await core.contributions(db, acc, settings, _rate(settings), CURRENCY)


def build_checkin(s: dict, acc: dict, last30: dict, settings, days: int, contrib: dict = None) -> dict:
    return core.build_checkin(s, acc, last30, settings, _rate(settings), days, CURRENCY, contrib)
