"""Constants, helpers and the collection-prefix wrapper shared by the FX engine and its mixins."""
import logging
from datetime import datetime, timezone
from strategies import STRATEGY_INFO
from fx.strategies_slow import SLOW_INFO as FX_SLOW_INFO

logger = logging.getLogger("fx.engine")
INITIAL_BALANCE = 500.0
LIVE_GRACE_SEC = 20
HFM_HEALTH_SEC = 60
HFM_HEALTH_RETRY_SEC = 15
TRANSIENT_TG_SEC = 30 * 60
PENDING_TTL_SEC = 2 * 3600
HFM_OK_STATES, HFM_OK_CONN = ("DEPLOYED", ""), ("CONNECTED", "")
BLOCK_BY = {"NOTICIAS": "noticias", "BLOQUEADA": "riesgo", "MERCADO": "mercado cerrado", "SESION": "sesión", "EXPOSICION": "exposición por divisa",
            "ROLLOVER": "ventana de rollover", "VOLATILIDAD": "régimen de volatilidad", "ESTACIONAL": "estacionalidad intradía", "REGIMEN_USD": "régimen USD", "FOMC": "regla FOMC",
            "PRECIO_EXTENDIDO": "precio extendido"}
HORIZON_LABEL = {"short": "corto plazo", "long": "largo plazo"}
NEWS_STRATEGY = {"news_event": {"name": "Noticia macro", "rules": "Dato de alto impacto publicado con sorpresa clara frente a la previsión; Claude fija la dirección y la confianza, el precio debe confirmar en los primeros minutos. SL/TP por ATR 15m y cierre máximo por tiempo.", "basis": "Trading de noticias: el flujo posterior a sorpresas macro (tipos, NFP, IPC) tiende a persistir durante horas."}}
FX_STRATEGY_INFO = {**STRATEGY_INFO, **NEWS_STRATEGY, **FX_SLOW_INFO}


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class PrefixedDB:
    """Exposes db.<name> as db.<prefix><name> so the engine code stays collection-agnostic."""

    def __init__(self, db, prefix: str = "fx_"):
        self._db, self._prefix = db, prefix

    def __getattr__(self, name):
        return self._db[f"{self._prefix}{name}"]

    def __getitem__(self, name):
        return self._db[f"{self._prefix}{name}"]
