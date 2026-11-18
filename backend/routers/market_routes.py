"""Crypto bot: pending maker (limit) entries."""
from deps import db, engine, protected_router
import maker

router = protected_router()


@router.get("/market/context")
async def market_context():
    s = await engine.get_settings()
    return {"pending_entries": await maker.pending_view(db), "entry_order_type": s.entry_order_type, "maker_wait_bars": s.maker_wait_bars}


@router.delete("/pending-entries")
async def cancel_pending():
    return {"cancelled": await maker.cancel_all(engine, "cancelación manual")}
