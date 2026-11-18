"""Weekly Telegram summary for the FX bots: the shared weekly builder plus net pips of the week (spread included)."""
import weekly
from fx.symbols import pips

PREFIX = "🏦 <b>HotForex AI</b>\n"


def prefix(engine) -> str:
    return f"{engine.profile.tg_icon} <b>{engine.profile.tg_name}</b>\n"


async def build(engine, db) -> dict:
    w = await weekly.build_weekly(engine, db)
    rows = await db.trades.find({"closed_at": {"$gte": w["from"]}}, {"_id": 0, "symbol": 1, "side": 1, "entry_price": 1, "exit_price": 1}).to_list(5000)
    w["pips"] = round(sum(pips(r["symbol"], (r["exit_price"] - r["entry_price"]) * (1 if r["side"] == "long" else -1) - engine.costs.spread(r["symbol"])) for r in rows), 1)
    return w


def fmt(w: dict) -> str:
    parts = [f"Pips netos de la semana: <b>{w['pips']:+.1f}</b>"] if w["week"]["trades"] else []
    return weekly.format_weekly(w, "USD", "\n".join(parts) if parts else None)


async def send(engine, db, force: bool = False) -> dict:
    return await weekly.send_weekly(engine, db, force, build=build, fmt=fmt, prefix=prefix(engine))
