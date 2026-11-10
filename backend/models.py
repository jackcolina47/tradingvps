from datetime import datetime, timezone
from typing import Annotated, Optional, List, Dict, Any
from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, Field, ConfigDict, field_validator

PyObjectId = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, ObjectId) else v)]

AVAILABLE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "ADAUSDT", "NEARUSDT", "SUIUSDT", "LTCUSDT",
    "ZECUSDT", "HYPEUSDT", "ENAUSDT", "ONDOUSDT", "ARBUSDT", "UNIUSDT",
]
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]
STRATEGY_KEYS = ["all", "trend_ema", "mean_reversion", "breakout_turtle", "supertrend_momentum", "ema_pullback", "ema_cross"]
SLOW_STRATEGY_KEYS = ["multi_horizon", "double_divergence"]  # crypto only, long-horizon, own timeframe (1d / 4h)
CRYPTO_STRATEGY_KEYS = STRATEGY_KEYS + SLOW_STRATEGY_KEYS
SIGNAL_MODES = ["strict", "relaxed", "minimal"]
ENTRY_ORDER_TYPES = ["market", "maker"]
REGIME_SMAS = [50, 200]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    @classmethod
    def from_mongo(cls, doc):
        return cls.model_validate(doc) if doc else None

    def to_mongo(self) -> dict:
        data = self.model_dump(exclude={"id"})
        if self.id:
            data["_id"] = ObjectId(self.id)
        return data


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    strategy: str = "all"
    timeframe: str = "1h"
    symbols: List[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT"]
    risk_per_trade: float = 1.0
    max_positions: int = 3
    leverage: int = 3
    daily_loss_limit: float = 5.0
    ai_enabled: bool = True
    ai_min_confidence: int = 60
    ai_position_review_enabled: bool = False
    ai_position_review_minutes: int = 30
    ai_position_review_mode: str = "auto"
    ai_position_review_min_confidence: int = 75
    scan_interval_sec: int = 60
    trailing_enabled: bool = True
    htf_filter: bool = True
    symbol_params: Dict[str, Dict[str, float]] = {}
    signal_mode: str = "strict"
    disabled_strategies: List[str] = ["supertrend_momentum", "ema_pullback", "ema_cross"]
    risk_profile: int = 0
    plan_scenario: str = "base"
    plan_target_monthly: float = 1000.0
    plan_monthly_deposit: float = 0.0
    plan_phase: int = 0
    btc_regime_filter: bool = True
    btc_regime_sma: int = 200
    rotation_enabled: bool = True
    rotation_lookback_days: int = 7
    entry_order_type: str = "maker"
    maker_wait_bars: int = 2
    max_long_positions: int = 2
    be_start_r: float = 1.0
    trail_start_r: float = 1.5
    trail_atr_mult: float = 1.0
    partial_tp_enabled: bool = True
    partial_tp_r: float = 1.5
    partial_tp_pct: int = 50
    bot_running: bool = False

    @field_validator("be_start_r", "trail_start_r", "partial_tp_r")
    @classmethod
    def _r_levels(cls, v):
        if not 0.5 <= v <= 3.0:
            raise ValueError("nivel en R fuera de rango (0,5–3)")
        return v

    @field_validator("trail_atr_mult")
    @classmethod
    def _trail_atr(cls, v):
        if not 0.5 <= v <= 3.0:
            raise ValueError("distancia del trailing fuera de rango (0,5–3 ATR)")
        return v

    @field_validator("partial_tp_pct")
    @classmethod
    def _partial_pct(cls, v):
        if not 25 <= v <= 75:
            raise ValueError("porcentaje de cierre parcial fuera de rango (25–75)")
        return v

    @field_validator("btc_regime_sma")
    @classmethod
    def _regime_sma(cls, v):
        if v not in REGIME_SMAS:
            raise ValueError("SMA de régimen inválida (50 o 200)")
        return v

    @field_validator("entry_order_type")
    @classmethod
    def _entry_type(cls, v):
        if v not in ENTRY_ORDER_TYPES:
            raise ValueError("Tipo de orden de entrada inválido")
        return v

    @field_validator("maker_wait_bars")
    @classmethod
    def _wait(cls, v):
        return min(3, max(1, int(v)))

    @field_validator("max_long_positions")
    @classmethod
    def _max_long(cls, v):
        return min(6, max(1, int(v)))

    @field_validator("rotation_lookback_days")
    @classmethod
    def _lookback(cls, v):
        return min(30, max(3, int(v)))

    @field_validator("signal_mode")
    @classmethod
    def _mode(cls, v):
        if v not in SIGNAL_MODES:
            raise ValueError("Modo de señales inválido")
        return v

    @field_validator("disabled_strategies")
    @classmethod
    def _disabled(cls, v):
        v = [s for s in dict.fromkeys(v or []) if s in CRYPTO_STRATEGY_KEYS and s != "all"]
        if len(v) >= len(CRYPTO_STRATEGY_KEYS) - 1:
            raise ValueError("Debe quedar al menos una estrategia activa")
        return v

    @field_validator("symbol_params")
    @classmethod
    def _params(cls, v):
        out = {}
        for sym, p in (v or {}).items():
            if sym in AVAILABLE_SYMBOLS and isinstance(p, dict):
                clean = {k: float(p[k]) for k in ("sl_atr", "tp_atr", "atr_period") if p.get(k) is not None}
                if clean and 0.5 <= clean.get("sl_atr", 1.5) <= 5 and 1 <= clean.get("tp_atr", 3) <= 10 and 5 <= clean.get("atr_period", 14) <= 50:
                    out[sym] = clean
        return out

    @field_validator("strategy")
    @classmethod
    def _strategy(cls, v):
        if v not in CRYPTO_STRATEGY_KEYS:
            raise ValueError("Estrategia inválida")
        return v

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v):
        if v not in TIMEFRAMES:
            raise ValueError("Temporalidad inválida")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v):
        v = [s for s in dict.fromkeys(v) if s in AVAILABLE_SYMBOLS]
        if not v:
            raise ValueError("Selecciona al menos un símbolo")
        return v


class SettingsUpdate(BaseModel):
    strategy: Optional[str] = None
    timeframe: Optional[str] = None
    symbols: Optional[List[str]] = None
    risk_per_trade: Optional[float] = Field(default=None, ge=0.1, le=5)
    max_positions: Optional[int] = Field(default=None, ge=1, le=10)
    leverage: Optional[int] = Field(default=None, ge=1, le=20)
    daily_loss_limit: Optional[float] = Field(default=None, ge=1, le=20)
    ai_enabled: Optional[bool] = None
    ai_min_confidence: Optional[int] = Field(default=None, ge=0, le=100)
    ai_position_review_enabled: Optional[bool] = None
    ai_position_review_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    ai_position_review_mode: Optional[str] = Field(default=None, pattern="^(notify|auto)$")
    ai_position_review_min_confidence: Optional[int] = Field(default=None, ge=50, le=100)
    scan_interval_sec: Optional[int] = Field(default=None, ge=30, le=900)
    trailing_enabled: Optional[bool] = None
    htf_filter: Optional[bool] = None
    symbol_params: Optional[Dict[str, Dict[str, float]]] = None
    signal_mode: Optional[str] = None
    disabled_strategies: Optional[List[str]] = None
    risk_profile: Optional[int] = Field(default=None, ge=0, le=4)
    plan_scenario: Optional[str] = None
    plan_target_monthly: Optional[float] = Field(default=None, ge=50, le=10_000_000)
    plan_monthly_deposit: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    plan_phase: Optional[int] = Field(default=None, ge=0, le=4)
    btc_regime_filter: Optional[bool] = None
    btc_regime_sma: Optional[int] = None
    rotation_enabled: Optional[bool] = None
    rotation_lookback_days: Optional[int] = Field(default=None, ge=3, le=30)
    entry_order_type: Optional[str] = None
    maker_wait_bars: Optional[int] = Field(default=None, ge=1, le=3)
    max_long_positions: Optional[int] = Field(default=None, ge=1, le=6)
    be_start_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    trail_start_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    trail_atr_mult: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    partial_tp_enabled: Optional[bool] = None
    partial_tp_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    partial_tp_pct: Optional[int] = Field(default=None, ge=25, le=75)


class Position(BaseDocument):
    symbol: str
    side: str
    strategy: str
    entry_price: float
    qty: float
    leverage: int
    margin: float
    sl: float
    tp: float
    initial_sl: float
    atr: float
    fees: float
    mark_price: float
    unrealized_pnl: float = 0.0
    breakeven: bool = False
    signal_id: Optional[str] = None
    live: bool = False
    order_id: Optional[str] = None
    link_id: Optional[str] = None
    expires_at: Optional[str] = None
    horizon: str = "short"
    entry_type: str = "market"
    initial_qty: Optional[float] = None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    partial_done: bool = False
    partial_gross: float = 0.0
    partial_fees: float = 0.0
    ai_review: Optional[Dict[str, Any]] = None
    opened_at: str = Field(default_factory=now_iso)


class Trade(BaseDocument):
    symbol: str
    side: str
    strategy: str
    entry_price: float
    exit_price: float
    qty: float
    leverage: int
    margin: float
    pnl: float
    pnl_pct: float
    fees: float
    reason: str
    opened_at: str
    live: bool = False
    period: str = ""
    horizon: str = "short"
    entry_type: str = "market"
    mfe_r: float = 0.0
    mae_r: float = 0.0
    mfe_tp_pct: float = 0.0
    partial_tp: bool = False
    hold_min: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    initial_sl: Optional[float] = None
    atr: Optional[float] = None
    breakeven: bool = False
    signal_id: Optional[str] = None
    order_id: Optional[str] = None
    note: str = ""
    tags: List[str] = []
    ai_review: Optional[Dict[str, Any]] = None
    closed_at: str = Field(default_factory=now_iso)


class Signal(BaseDocument):
    symbol: str
    side: str
    strategy: str
    timeframe: str
    price: float
    sl: float
    tp: float
    score: int
    reasons: List[str]
    indicators: Dict[str, Any]
    ai_decision: str
    ai_confidence: int = 0
    ai_min_confidence: Optional[int] = None  # threshold in force when the AI evaluated it (the card shows «58 % (mín. 60 %)»)
    ai_risk: str = "-"
    ai_reasoning: str = ""
    executed: bool = False
    note: str = ""
    key: str = ""
    context: Dict[str, Any] = {}
    created_at: str = Field(default_factory=now_iso)


class LogEntry(BaseDocument):
    level: str
    message: str
    ts: str = Field(default_factory=now_iso)


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "all"
    timeframe: str = "15m"
    risk_per_trade: float = Field(default=1.0, ge=0.1, le=5)
    leverage: int = Field(default=3, ge=1, le=20)


class ResetRequest(BaseModel):
    initial_balance: Optional[float] = Field(default=None, ge=10, le=10_000_000)


class PlanRequest(BaseModel):
    initial_capital: float = Field(default=500, ge=50, le=10_000_000)
    monthly_deposit: float = Field(default=0, ge=0, le=1_000_000)
    target_monthly: float = Field(default=1000, ge=50, le=10_000_000)
    scenario: str = "base"


class OptimizeRequest(BaseModel):
    symbol: str


class AdviseRequest(BaseModel):
    scope: str
    optimizer_results: Optional[List[Dict[str, Any]]] = None


class ProfileRequest(BaseModel):
    level: int = Field(ge=0, le=4)


class BybitTestRequest(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    env: Optional[str] = None
    base_url: Optional[str] = None


class ModeRequest(BaseModel):
    mode: str
    confirm: str = ""
    reset_stats: bool = True
