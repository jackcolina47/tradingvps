"""AI routes for the crypto bot: manual auto-review and the growth-plan advisor."""
import logging
from fastapi import HTTPException

from models import AdviseRequest
from ai_advisor import advise, label as ai_label, friendly_error
from review import build_context, apply_ai_changes, run_auto_review, ai_policy
from deps import db, engine, store, protected_router

logger = logging.getLogger(__name__)
router = protected_router()


@router.post("/ai/auto-review")
async def ai_auto_review_now():
    try:
        return await run_auto_review(engine, db, store)
    except Exception as e:
        logger.exception("auto review")
        raise HTTPException(424, f"Error IA: {friendly_error(e)}")


@router.post("/ai/advise")
async def ai_advise(req: AdviseRequest):
    if req.scope != "plan":
        raise HTTPException(400, "Ámbito inválido")
    settings = await engine.get_settings()
    before = settings.model_dump(exclude={"bot_running"})
    acc = await engine.account_summary()
    ctx = await build_context(engine, db, settings, acc, True)
    await engine.log("AI_EVAL", f"{ai_label()} analizando plan y rendimiento real...")
    try:
        res = await advise(req.scope, ctx, ai_policy())
    except Exception as e:
        logger.exception("advise")
        raise HTTPException(424, f"Error IA: {friendly_error(e)}")
    applied, previous = await apply_ai_changes(engine, res, before, settings, source=req.scope)
    await engine.log("AI_EVAL", f"IA: {res['summary'][:160]}" + (f" · cambios: {', '.join(applied)}" if applied else " · sin cambios"))
    return {**res, "applied": applied, "previous": previous}
