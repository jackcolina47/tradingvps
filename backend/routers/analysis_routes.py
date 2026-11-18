"""Analysis routes for the crypto bot: growth plan + check-in and the automatic pruning of losing pairs."""
from models import PlanRequest
from plan import build_plan, SCENARIOS
import housekeeping
from deps import db, engine, protected_router

router = protected_router()


@router.get("/housekeeping")
async def housekeeping_status():
    """Pruning state (crypto bot)."""
    return {"prune": await db.meta.find_one({"_id": "prune"}, {"_id": 0}) or {}}


@router.post("/housekeeping/prune")
async def housekeeping_prune():
    """Runs the pruning check now (warns / switches off according to the 30-day stats)."""
    return await housekeeping.prune(engine, await engine.get_settings())


@router.post("/plan")
async def growth_plan(req: PlanRequest):
    settings = await engine.get_settings()
    if req.scenario not in SCENARIOS:
        req.scenario = "base"
    if (settings.plan_scenario, settings.plan_target_monthly, settings.plan_monthly_deposit) != (req.scenario, req.target_monthly, req.monthly_deposit):
        await engine.update_settings({"plan_scenario": req.scenario, "plan_target_monthly": req.target_monthly,
                                      "plan_monthly_deposit": req.monthly_deposit})
    return build_plan(req.model_dump())


@router.get("/plan/checkin")
async def plan_checkin():
    return await engine.checkin()
