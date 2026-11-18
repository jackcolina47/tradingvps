"""FX bot configuration (MetaApi/MT credentials, broker account type, auto review); one document per broker (db.fx_config / db.pep_config)."""
from typing import Optional, List, Dict
from pydantic import BaseModel, ConfigDict, Field
from config import _enc, _dec, mask, _fernet
from fx.metaapi import PLANS, PLAN_COMMON, plan_key
from fx.broker import HFM, BrokerProfile

SECRET_FIELDS = ("metaapi_token", "mt_password")
REGIONS = ["new-york", "london"]
PLATFORMS = ["mt5", "mt4"]


class FxConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trading_mode: str = "paper"
    live_since: Optional[str] = None
    metaapi_token: str = ""
    mt_login: str = ""
    mt_password: str = ""
    mt_server: str = ""
    mt_platform: str = "mt5"
    mt_symbol_suffix: str = ""
    mt_symbol_map: Dict[str, str] = {}
    account_type: str = ""
    metaapi_region: str = "new-york"
    metaapi_base_url: str = ""
    metaapi_account_id: str = ""
    metaapi_plan: str = "g2-high"
    hfm_account_type: str = ""
    hfm_reliability: str = ""
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
    auto_review_enabled: bool = True
    auto_review_frequency: str = "weekly"
    auto_review_day: int = 0
    auto_review_apply: bool = True
    auto_review_last_at: Optional[str] = None
    auto_review_last_summary: str = ""
    auto_review_last_changes: Dict[str, dict] = {}


class FxConfigUpdate(BaseModel):
    metaapi_token: Optional[str] = Field(default=None, max_length=4000)
    mt_login: Optional[str] = Field(default=None, max_length=40)
    mt_password: Optional[str] = Field(default=None, max_length=200)
    mt_server: Optional[str] = Field(default=None, max_length=80)
    mt_platform: Optional[str] = None
    mt_symbol_suffix: Optional[str] = Field(default=None, max_length=12)
    account_type: Optional[str] = Field(default=None, max_length=20)
    metaapi_region: Optional[str] = None
    metaapi_base_url: Optional[str] = Field(default=None, max_length=200)
    metaapi_plan: Optional[str] = None
    hfm_alert_after_sec: Optional[int] = Field(default=None, ge=10, le=3600)
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

    def public(self) -> dict:
        d = self.cfg.model_dump()
        for k in SECRET_FIELDS:
            d[k] = mask(d[k])
        d["hfm_configured"] = bool(self.cfg.metaapi_token and self.cfg.mt_login and self.cfg.mt_password and self.cfg.mt_server)
        d["metaapi_regions"] = REGIONS
        d["mt_platforms"] = PLATFORMS
        d["metaapi_plans"] = [{"key": k, **p, "month_usd": round(p["hour_usd"] * 24 * 30, 2)} for k, p in PLANS.items()]
        d["metaapi_plan_common"] = PLAN_COMMON
        d["hfm_suffixes"] = [{"suffix": k, "label": v} for k, v in self.profile.suffixes.items()]
        d["broker"] = self.profile.public(self.cfg.account_type)
        d["hfm_current_plan"] = plan_key(self.cfg.hfm_account_type, self.cfg.hfm_reliability) if self.cfg.metaapi_account_id and self.cfg.hfm_account_type else ""
        d["hfm_plan_mismatch"] = bool(d["hfm_current_plan"] and d["hfm_current_plan"] != self.cfg.metaapi_plan)
        d["secrets_encrypted"] = _fernet is not None
        return d

    def patch_from(self, body: FxConfigUpdate) -> dict:
        patch = {}
        for k, v in body.model_dump(exclude={"clear_hfm"}).items():
            if v is None or (k in SECRET_FIELDS and (v == "" or v.startswith("••"))):
                continue
            patch[k] = v.strip() if isinstance(v, str) else v
        if body.clear_hfm:
            patch.update(metaapi_token="", mt_login="", mt_password="", mt_server="", metaapi_account_id="", metaapi_base_url="", mt_symbol_suffix="", mt_symbol_map={},
                         hfm_state="", hfm_connection="", hfm_broker="", hfm_account_type="", hfm_reliability="",
                         hfm_trade_allowed=None, hfm_verified_at=None, hfm_verified_equity=None, hfm_currency="", hfm_leverage=None,
                         hfm_suffix_kind="", hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None)
        if "account_type" in patch and patch["account_type"] not in self.profile.account_types:
            raise ValueError("Tipo de cuenta del bróker inválido")
        if "mt_platform" in patch and patch["mt_platform"] not in PLATFORMS:
            raise ValueError("Plataforma inválida (mt4 o mt5)")
        if "metaapi_plan" in patch and patch["metaapi_plan"] not in PLANS:
            raise ValueError("Tipo de cuenta MetaApi inválido")
        if "metaapi_region" in patch and patch["metaapi_region"] not in REGIONS:
            raise ValueError("Región de MetaApi inválida")
        if "auto_review_frequency" in patch and patch["auto_review_frequency"] not in ("daily", "weekly"):
            raise ValueError("Frecuencia de revisión inválida (daily o weekly)")
        if "metaapi_base_url" in patch and patch["metaapi_base_url"] and not patch["metaapi_base_url"].startswith(("http://", "https://")):
            raise ValueError("El endpoint de MetaApi debe empezar por http:// o https://")
        if "mt_login" in patch and patch["mt_login"] and not patch["mt_login"].isdigit():
            raise ValueError("El login de MetaTrader debe ser numérico")
        hfm_keys = ("metaapi_token", "mt_login", "mt_password", "mt_server", "mt_platform", "metaapi_region", "metaapi_base_url")
        changed = {k for k in hfm_keys if k in patch and patch[k] != getattr(self.cfg, k)}
        if changed and not body.clear_hfm:
            patch.update(hfm_verified_at=None, hfm_verified_equity=None, hfm_state="", hfm_connection="", hfm_trade_allowed=None)
            if changed - {"metaapi_token", "mt_password"}:
                patch.update(metaapi_account_id="", hfm_account_type="", hfm_reliability="", hfm_suffix_kind="", hfm_symbols_bad=None, hfm_symbols_checked=None,
                             hfm_symbols_checked_at=None, mt_symbol_map={})
        if "mt_symbol_suffix" in patch and patch["mt_symbol_suffix"] != self.cfg.mt_symbol_suffix and not body.clear_hfm:
            patch.update(hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None, mt_symbol_map={})
        return patch
