"""Crypto bot: pending maker entries."""
from deps import engine, protected_router
import maker

router = protected_router()


@router.delete("/pending-entries")
async def cancel_pending():
    return {"cancelled": await maker.cancel_all(engine, "cancelación manual")}
