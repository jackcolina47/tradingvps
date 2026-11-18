"""/api/fx/* · analysis: AI advisor, automatic AI review, growth plan and weekly summary."""
import logging
from fastapi import HTTPException

from models import PlanRequest, AdviseRequest
from ai_advisor import friendly_error
from fx import review as fx_review
from fx import plan as fx_plan
from fx import weekly as fx_weekly

logger = logging.getLogger(__name__)


def register(router, engine, store):
    @router.post("/ai/advise")
    async def ai_advise(req: AdviseRequest):
        if req.scope != "plan":
            raise HTTPException(400, "Ámbito inválido")
        try:
            return await fx_review.advise_scope(engine, req.scope)
        except Exception as e:
            logger.exception("fx advise")
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")

    @router.post("/ai/auto-review")
    async def ai_auto_review_now():
        try:
            return await fx_review.run_auto_review(engine, engine.db, store)
        except Exception as e:
            logger.exception("fx auto review")
            raise HTTPException(424, f"Error IA: {friendly_error(e)}")

    @router.post("/plan")
    async def growth_plan(req: PlanRequest):
        settings = await engine.get_settings()
        if req.scenario not in fx_plan.SCENARIOS:
            req.scenario = "base"
        if (settings.plan_scenario, settings.plan_target_monthly, settings.plan_monthly_deposit) != (req.scenario, req.target_monthly, req.monthly_deposit):
            settings = await engine.update_settings({"plan_scenario": req.scenario, "plan_target_monthly": req.target_monthly, "plan_monthly_deposit": req.monthly_deposit})
        return fx_plan.build_plan(req.model_dump())

    @router.get("/plan/checkin")
    async def plan_checkin():
        return await engine.checkin()

    @router.get("/weekly-summary")
    async def weekly_summary_preview():
        w = await fx_weekly.build(engine, engine.db)
        last = await engine.db.weekly.find_one({"_id": "latest"}, {"_id": 0, "sent_at": 1})
        return {**w, "text": fx_weekly.prefix(engine) + fx_weekly.fmt(w), "last_sent_at": (last or {}).get("sent_at")}

    @router.post("/weekly-summary/send")
    async def weekly_summary_send():
        try:
            return await fx_weekly.send(engine, engine.db, force=True)
        except Exception as e:
            raise HTTPException(424, str(e)[:200])
