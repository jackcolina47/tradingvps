"""FX bot configuration (own MetaTrader bridge link, broker account type, forex news agent, news trading); one document per broker (db.fx_config / db.pep_config)."""
from typing import Optional, List, Dict
import re
from pydantic import BaseModel, ConfigDict, Field
from config import _enc, _dec, mask, _fernet
from fx.symbols import CURRENCIES
from fx.bridge import env_defaults
from fx.broker import HFM, BrokerProfile

SECRET_FIELDS = ("bridge_token",)
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


def bridge_settings(cfg, profile: BrokerProfile) -> dict:
    """Effective bridge link of one bot: the panel value when filled in, else MT_BRIDGE_URL / MT_BRIDGE_TOKEN / MT_BRIDGE_ACCOUNT_<BROKER>, else the profile's default account."""
    env = env_defaults(profile.key, profile.bridge_account)
    return {"url": (cfg.bridge_url or env["url"]).rstrip("/"), "token": cfg.bridge_token or env["token"], "account_id": cfg.bridge_account_id or env["account_id"],
            "from_env": {"url": bool(not cfg.bridge_url and env["url"]), "token": bool(not cfg.bridge_token and env["token"]), "account_id": not cfg.bridge_account_id}}


class FxConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trading_mode: str = "paper"
    live_since: Optional[str] = None
    bridge_url: str = ""
    bridge_token: str = ""
    bridge_account_id: str = ""
    mt_login: str = ""      # read from the terminal on «Probar conexión»
    mt_server: str = ""
    mt_platform: str = ""
    mt_symbol_suffix: str = ""
    mt_symbol_map: Dict[str, str] = {}
    account_type: str = ""
    hfm_state: str = ""
    hfm_connection: str = ""
    hfm_broker: str = ""
    hfm_trade_allowed: Optional[bool] = None
    hfm_verified_at: Optional[str] = None
    hfm_verified_equity: Optional[float] = None
    hfm_currency: str = ""
    hfm_leverage: Optional[int] = None
    hfm_suffix_kind: str = ""
    hfm_symbols_bad: Optional[List[str]] = None
    hfm_symbols_checked: Optional[List[str]] = None
    hfm_symbols_checked_at: Optional[str] = None
    hfm_alert_after_sec: int = 180
    news_enabled: bool = True
    news_interval_min: int = 15
    news_countries: List[str] = list(CURRENCIES)
    news_block_events: bool = True
    news_event_before_min: int = 30
    news_event_after_min: int = 15
    news_block_critical: bool = True
    news_reduce_on_high_risk: bool = True
    news_size_factor: float = 0.5
    news_trading_enabled: bool = True
    news_trade_min_confidence: int = 75
    news_trade_confirm_price: bool = True
    news_trade_window_min: int = 12
    news_trade_max_hours: int = 4
    news_trade_risk_factor: float = 1.0
    news_trade_sl_atr: float = 1.5
    news_trade_tp_atr: float = 3.0
    news_trade_max_per_event: int = 2
    wf_auto_remove: bool = True
    auto_review_enabled: bool = True
    auto_review_frequency: str = "weekly"
    auto_review_day: int = 0
    auto_review_apply: bool = True
    auto_review_last_at: Optional[str] = None
    auto_review_last_summary: str = ""
    auto_review_last_changes: Dict[str, dict] = {}


class FxConfigUpdate(BaseModel):
    bridge_url: Optional[str] = Field(default=None, max_length=200)
    bridge_token: Optional[str] = Field(default=None, max_length=400)
    bridge_account_id: Optional[str] = Field(default=None, max_length=40)
    mt_symbol_suffix: Optional[str] = Field(default=None, max_length=12)
    account_type: Optional[str] = Field(default=None, max_length=20)
    hfm_alert_after_sec: Optional[int] = Field(default=None, ge=10, le=3600)
    news_enabled: Optional[bool] = None
    news_interval_min: Optional[int] = Field(default=None, ge=5, le=240)
    news_countries: Optional[List[str]] = None
    news_block_events: Optional[bool] = None
    news_event_before_min: Optional[int] = Field(default=None, ge=0, le=240)
    news_event_after_min: Optional[int] = Field(default=None, ge=0, le=240)
    news_block_critical: Optional[bool] = None
    news_reduce_on_high_risk: Optional[bool] = None
    news_size_factor: Optional[float] = Field(default=None, ge=0.1, le=1.0)
    news_trading_enabled: Optional[bool] = None
    news_trade_min_confidence: Optional[int] = Field(default=None, ge=50, le=100)
    news_trade_confirm_price: Optional[bool] = None
    news_trade_window_min: Optional[int] = Field(default=None, ge=3, le=60)
    news_trade_max_hours: Optional[int] = Field(default=None, ge=1, le=48)
    news_trade_risk_factor: Optional[float] = Field(default=None, ge=0.1, le=1.5)
    news_trade_sl_atr: Optional[float] = Field(default=None, ge=0.5, le=5)
    news_trade_tp_atr: Optional[float] = Field(default=None, ge=1, le=10)
    news_trade_max_per_event: Optional[int] = Field(default=None, ge=1, le=4)
    wf_auto_remove: Optional[bool] = None
    auto_review_enabled: Optional[bool] = None
    auto_review_frequency: Optional[str] = None
    auto_review_day: Optional[int] = Field(default=None, ge=0, le=6)
    auto_review_apply: Optional[bool] = None
    clear_hfm: bool = False


class FxConfigStore:
    def __init__(self, db, profile: BrokerProfile = HFM):
        self.db = db
        self.profile = profile
        self.col = db[f"{profile.prefix}config"]
        self.cfg = FxConfig()
        self.listeners = []

    async def load(self):
        doc = await self.col.find_one({"_id": "main"})
        if not doc:
            doc = {"_id": "main", **FxConfig().model_dump()}
            await self.col.insert_one(doc)
        if doc.get("metaapi_account_id") and not doc.get("bridge_migrated"):
            # account linked through MetaApi before the own bridge existed: it must be verified again through the bridge (legacy fields are kept, unused)
            reset = {"bridge_migrated": True, "hfm_verified_at": None, "hfm_verified_equity": None, "hfm_state": "", "hfm_connection": "", "hfm_trade_allowed": None}
            await self.col.update_one({"_id": "main"}, {"$set": reset})
            doc.update(reset)
        self.cfg = FxConfig.model_validate({k: (_dec(v) if k in SECRET_FIELDS else v) for k, v in doc.items()})
        await self._apply()

    async def update(self, patch: dict):
        data = self.cfg.model_dump()
        data.update(patch)
        self.cfg = FxConfig.model_validate(data)
        stored = {k: (_enc(v) if k in SECRET_FIELDS else v) for k, v in patch.items()}
        await self.col.update_one({"_id": "main"}, {"$set": stored}, upsert=True)
        await self._apply()

    async def _apply(self):
        for fn in self.listeners:
            await fn(self.cfg)

    def bridge(self) -> dict:
        return bridge_settings(self.cfg, self.profile)

    def bridge_ready(self) -> bool:
        b = self.bridge()
        return bool(b["url"] and b["token"] and b["account_id"])

    def public(self) -> dict:
        d = self.cfg.model_dump()
        for k in SECRET_FIELDS:
            d[k] = mask(d[k])
        b = self.bridge()
        d["hfm_configured"] = self.bridge_ready()
        d["bridge_effective"] = {"url": b["url"], "account_id": b["account_id"], "token_set": bool(b["token"]), "from_env": b["from_env"]}
        d["bridge_default_account"] = self.profile.bridge_account
        d["hfm_suffixes"] = [{"suffix": k, "label": v} for k, v in self.profile.suffixes.items()]
        d["broker"] = self.profile.public(self.cfg.account_type)
        d["news_currencies"] = CURRENCIES
        d["secrets_encrypted"] = _fernet is not None
        return d

    def patch_from(self, body: FxConfigUpdate) -> dict:
        patch = {}
        for k, v in body.model_dump(exclude={"clear_hfm"}).items():
            if v is None or (k in SECRET_FIELDS and (v == "" or v.startswith("••"))):
                continue
            patch[k] = v.strip() if isinstance(v, str) else v
        if body.clear_hfm:
            patch.update(bridge_url="", bridge_token="", bridge_account_id="", mt_login="", mt_server="", mt_platform="", mt_symbol_suffix="", mt_symbol_map={},
                         hfm_state="", hfm_connection="", hfm_broker="",
                         hfm_trade_allowed=None, hfm_verified_at=None, hfm_verified_equity=None, hfm_currency="", hfm_leverage=None,
                         hfm_suffix_kind="", hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None)
        if "account_type" in patch and patch["account_type"] not in self.profile.account_types:
            raise ValueError("Tipo de cuenta del bróker inválido")
        if "auto_review_frequency" in patch and patch["auto_review_frequency"] not in ("daily", "weekly"):
            raise ValueError("Frecuencia de revisión inválida (daily o weekly)")
        if patch.get("bridge_url") and not patch["bridge_url"].startswith(("http://", "https://")):
            raise ValueError("La URL del puente debe empezar por http:// o https://")
        if patch.get("bridge_account_id") and not ACCOUNT_ID_RE.match(patch["bridge_account_id"]):
            raise ValueError("ID de cuenta del puente inválido (letras, números, guion y guion bajo; p. ej. hfm_v1)")
        if "news_countries" in patch:
            patch["news_countries"] = [c for c in dict.fromkeys(patch["news_countries"]) if c in CURRENCIES] or list(CURRENCIES)
        hfm_keys = ("bridge_url", "bridge_token", "bridge_account_id")
        changed = {k for k in hfm_keys if k in patch and patch[k] != getattr(self.cfg, k)}
        if changed and not body.clear_hfm:
            patch.update(hfm_verified_at=None, hfm_verified_equity=None, hfm_state="", hfm_connection="", hfm_trade_allowed=None)
            if changed - {"bridge_token"}:
                patch.update(mt_login="", mt_server="", mt_platform="", hfm_suffix_kind="", hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None, mt_symbol_map={})
        if "mt_symbol_suffix" in patch and patch["mt_symbol_suffix"] != self.cfg.mt_symbol_suffix and not body.clear_hfm:
            patch.update(hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None, mt_symbol_map={})
        return patch
