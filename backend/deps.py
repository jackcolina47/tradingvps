"""Shared application state (db, engine, config store, news agent, auth) used by server.py and the routers."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import APIRouter, Depends  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from engine import TradingEngine  # noqa: E402
from config import ConfigStore  # noqa: E402
from news import NewsAgent  # noqa: E402
from auth import make_require_auth  # noqa: E402
from ai_usage import AiUsage  # noqa: E402
import ai_advisor  # noqa: E402

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
engine = TradingEngine(db)
store = ConfigStore(db)
news = NewsAgent(db, engine)
engine.news = news
require_auth = make_require_auth(lambda: store.cfg.token_version)
ai_usage = AiUsage(db)
ai_advisor.set_usage_hook(ai_usage.record)

from fx.engine import FxEngine  # noqa: E402
from fx.config import FxConfigStore  # noqa: E402
from fx.news import FxNewsAgent  # noqa: E402
from fx.market import FxMarket  # noqa: E402
from fx.broker import HFM, PEPPERSTONE  # noqa: E402

fx_market = FxMarket()  # one Yahoo Finance client shared by every MetaApi/MetaTrader bot (same symbols, half the requests)


def _fx_bot(profile):
    eng = FxEngine(db, profile, fx_market)
    st = FxConfigStore(db, profile)
    eng.store = st
    nw = FxNewsAgent(eng.db, eng)
    eng.news = nw
    return eng, st, nw


fx_engine, fx_store, fx_news = _fx_bot(HFM)
pep_engine, pep_store, pep_news = _fx_bot(PEPPERSTONE)
fx_market._shared_owner = fx_engine
FX_BOTS = ((fx_engine, fx_store, fx_news), (pep_engine, pep_store, pep_news))
ALL_ENGINES = (engine, fx_engine, pep_engine)

from lab import LabEngine  # noqa: E402

lab_engine = LabEngine(db, engine)  # paper-only research clone of the Bybit bot (test cruce EMA)
lab4h_engine = LabEngine(db, engine, key="lab4h", name="Bybit Lab 4H", source=lab_engine, timeframe="4h", capital=1000.0)
LABS = (lab_engine, lab4h_engine)


async def _ai_health_event(event: str, h: dict):
    """AI provider down/up → log in both bots (they share the AI config) + Telegram 'system' alert."""
    import notify
    from fx.engine import fmt_duration
    from datetime import datetime, timezone
    model = ai_advisor.label()
    if event == "down":
        text = f"IA sin servicio ({h['label']}) · {model} · {h['error']}"
        tg = (f"🧠⚠️ <b>IA sin servicio · {h['label']}</b>\n{model}: {h['error']}\n"
              "Las señales nuevas quedan sin evaluar (se registran como ERROR y no se operan) y el agente de noticias no se actualiza hasta que la IA responda.")
    else:
        dur = fmt_duration((datetime.now(timezone.utc) - datetime.fromisoformat(h["down_since"])).total_seconds()) if h.get("down_since") else "?"
        text = f"IA operativa de nuevo · {model} · estuvo sin servicio {dur}"
        tg = f"✅ <b>IA operativa de nuevo</b> · {model} · estuvo sin servicio {dur}. El bot vuelve a evaluar señales con normalidad."
    for eng in ALL_ENGINES:
        await eng.log("ERROR" if event == "down" else "INFO", text)
    await notify.notify(tg, "system")


ai_advisor.set_health_hook(_ai_health_event)


def protected_router() -> APIRouter:
    return APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
