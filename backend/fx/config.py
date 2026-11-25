"""FX bot configuration (MetaTrader bridge connection, broker account type, auto review); one document per broker (db.fx_config / db.pep_config)."""
import re
import logging
from typing import Optional, List, Dict
from pydantic import BaseModel, ConfigDict, Field
from config import _enc, _dec, mask, _fernet
from fx import bridge
from fx.broker import HFM, BrokerProfile

logger = logging.getLogger(__name__)
SECRET_FIELDS = ("bridge_token",)
# keys of the retired MetaApi integration: a document that still has them is migrated on load (back to paper until the bridge is verified)
LEGACY_KEYS = ("metaapi_token", "metaapi_account_id", "metaapi_region", "metaapi_base_url", "metaapi_plan", "mt_password", "mt_platform", "hfm_account_type", "hfm_reliability")
UNVERIFIED = dict(hfm_state="", hfm_connection="", hfm_broker="", hfm_trade_allowed=None, hfm_verified_at=None, hfm_verified_equity=None, hfm_currency="", hfm_leverage=None,
                  mt_login="", mt_server="")
UNCHECKED = dict(hfm_suffix_kind="", hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None, mt_symbol_map={})
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class FxConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trading_mode: str = "paper"
    live_since: Optional[str] = None
    bridge_url: str = ""
    bridge_token: str = ""
    bridge_account_id: str = ""
    mt_login: str = ""
    mt_server: str = ""
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
    auto_review_enabled: bool = True
    auto_review_frequency: str = "weekly"
    auto_review_day: int = 0
    auto_review_apply: bool = True
    auto_review_last_at: Optional[str] = None
    auto_review_last_summary: str = ""
    auto_review_last_changes: Dict[str, dict] = {}


class FxConfigUpdate(BaseModel):
    bridge_url: Optional[str] = Field(default=None, max_length=200)
    bridge_token: Optional[str] = Field(default=None, max_length=4000)
    bridge_account_id: Optional[str] = Field(default=None, max_length=64)
    mt_symbol_suffix: Optional[str] = Field(default=None, max_length=12)
    account_type: Optional[str] = Field(default=None, max_length=20)
    hfm_alert_after_sec: Optional[int] = Field(default=None, ge=10, le=3600)
    auto_review_enabled: Optional[bool] = None
    auto_review_frequency: Optional[str] = None
    auto_review_day: Optional[int] = Field(default=None, ge=0, le=6)
    auto_review_apply: Optional[bool] = None
    clear_hfm: bool = False


def bridge_conn(cfg: FxConfig, profile: BrokerProfile) -> tuple:
    """(url, token, account_id) in effect: the panel value, else the MT_BRIDGE_URL / MT_BRIDGE_TOKEN environment, else the broker default."""
    return (cfg.bridge_url or bridge.env_url()).rstrip("/"), cfg.bridge_token or bridge.env_token(), cfg.bridge_account_id or profile.bridge_account


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
            doc = {"_id": "main", **FxConfig(mt_symbol_suffix=self.profile.default_suffix).model_dump()}
            await self.col.insert_one(doc)
        elif any(k in doc for k in LEGACY_KEYS):
            doc = await self._migrate_legacy(doc)
        self.cfg = FxConfig.model_validate({k: (_dec(v) if k in SECRET_FIELDS else v) for k, v in doc.items()})
        await self._apply()

    async def _migrate_legacy(self, doc: dict) -> dict:
        """MetaApi → own bridge: the bot goes back to paper until the bridge account is verified; positions of the old account are dropped."""
        reset = {"trading_mode": "paper", "live_since": None, "mt_symbol_suffix": self.profile.default_suffix, **UNVERIFIED, **UNCHECKED}
        await self.col.update_one({"_id": "main"}, {"$set": reset, "$unset": {k: "" for k in LEGACY_KEYS}})
        dropped = (await self.db[f"{self.profile.prefix}positions"].delete_many({"live": True})).deleted_count
        logger.warning(f"{self.profile.broker}: configuración de MetaApi retirada · bot en paper hasta verificar el puente MT · {dropped} posición(es) REAL(es) antiguas descartadas")
        return {**{k: v for k, v in doc.items() if k not in LEGACY_KEYS}, **reset}

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

    @property
    def conn(self) -> tuple:
        return bridge_conn(self.cfg, self.profile)

    def public(self) -> dict:
        d = self.cfg.model_dump()
        for k in SECRET_FIELDS:
            d[k] = mask(d[k])
        url, token, account_id = self.conn
        d["hfm_configured"] = bool(token)
        d["bridge_effective"] = {"url": url, "account_id": account_id, "token_source": "panel" if self.cfg.bridge_token else ("env" if token else "")}
        d["bridge_defaults"] = {"url": bridge.env_url(), "account_id": self.profile.bridge_account}
        d["hfm_suffixes"] = [{"suffix": k, "label": v} for k, v in self.profile.suffixes.items()]
        d["broker"] = self.profile.public(self.cfg.account_type)
        d["secrets_encrypted"] = _fernet is not None
        return d

    def patch_from(self, body: FxConfigUpdate) -> dict:
        patch = {}
        for k, v in body.model_dump(exclude={"clear_hfm"}).items():
            if v is None or (k in SECRET_FIELDS and (v == "" or v.startswith("••"))):
                continue
            patch[k] = v.strip() if isinstance(v, str) else v
        if body.clear_hfm:
            patch.update(bridge_url="", bridge_token="", bridge_account_id="", mt_symbol_suffix=self.profile.default_suffix, **UNVERIFIED, **UNCHECKED)
        if "account_type" in patch and patch["account_type"] not in self.profile.account_types:
            raise ValueError("Tipo de cuenta del bróker inválido")
        if "auto_review_frequency" in patch and patch["auto_review_frequency"] not in ("daily", "weekly"):
            raise ValueError("Frecuencia de revisión inválida (daily o weekly)")
        if patch.get("bridge_url"):
            patch["bridge_url"] = patch["bridge_url"].rstrip("/")
            if not patch["bridge_url"].startswith(("http://", "https://")):
                raise ValueError("La URL del puente debe empezar por http:// o https://")
        if patch.get("bridge_account_id") and not _ACCOUNT_RE.match(patch["bridge_account_id"]):
            raise ValueError("ID de cuenta del puente inválido (letras, números, guion, punto y guion bajo)")
        changed = {k for k in ("bridge_url", "bridge_token", "bridge_account_id") if k in patch and patch[k] != getattr(self.cfg, k)}
        if changed and not body.clear_hfm:
            patch.update(UNVERIFIED)
            if changed - {"bridge_token"}:
                patch.update(UNCHECKED)
        if "mt_symbol_suffix" in patch and patch["mt_symbol_suffix"] != self.cfg.mt_symbol_suffix and not body.clear_hfm:
            patch.update(hfm_symbols_bad=None, hfm_symbols_checked=None, hfm_symbols_checked_at=None, mt_symbol_map={})
        return patch
