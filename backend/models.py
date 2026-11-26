from datetime import datetime, timezone
from typing import Annotated, Optional, List, Dict, Any
from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, Field, ConfigDict, field_validator, model_validator
from fx.symbols import SPECS as FX_SPECS


def pips_for(symbol: str, side: str, entry: float, price: float):
    """FX: real pips (SPECS pip size). Crypto: 1 pip = 0,01 % of the entry price (comparable across pairs, like a major FX pip)."""
    if not entry or price is None:
        return None
    d = (price - entry) if side == "long" else (entry - price)
    pip = FX_SPECS.get(symbol, {}).get("pip")
    return round(d / pip, 1) if pip else round(d / entry * 10000, 1)

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
    min_rr: float = 1.5
    atr_levels_enabled: bool = True
    atr_sl_mult: float = 2.0
    atr_tp_mult: float = 4.0
    ema_test_partial_enabled: bool = False
    ema_test_partial_atr: float = 1.5
    rules_auto_learn: bool = True
    ai_position_review_enabled: bool = False
    ai_position_review_minutes: int = 30
    ai_position_review_mode: str = "auto"
    ai_position_review_min_confidence: int = 75
    btc_shock_pct: float = 1.5
    auto_prune_enabled: bool = True
    auto_prune_pf: float = 0.8
    auto_prune_min_trades: int = 10
    scan_interval_sec: int = 60
    trailing_enabled: bool = True
    breakeven_enabled: bool = True
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

    @field_validator("ai_min_confidence", mode="before")
    @classmethod
    def _confidence_level(cls, v):
        """«Confianza IA para abrir operación»: 10 %…90 % in steps of 10 (older free values are snapped)."""
        try:
            return min(90, max(10, int(round(float(v) / 10) * 10)))
        except (TypeError, ValueError):
            return 60

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
    min_rr: Optional[float] = Field(default=None, ge=0, le=5)
    atr_levels_enabled: Optional[bool] = None
    atr_sl_mult: Optional[float] = Field(default=None, ge=0.5, le=10)
    atr_tp_mult: Optional[float] = Field(default=None, ge=0.5, le=20)
    ema_test_partial_enabled: Optional[bool] = None
    ema_test_partial_atr: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    rules_auto_learn: Optional[bool] = None
    ai_position_review_enabled: Optional[bool] = None
    ai_position_review_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    ai_position_review_mode: Optional[str] = Field(default=None, pattern="^(notify|auto)$")
    ai_position_review_min_confidence: Optional[int] = Field(default=None, ge=50, le=100)
    btc_shock_pct: Optional[float] = Field(default=None, ge=0, le=10)
    auto_prune_enabled: Optional[bool] = None
    auto_prune_pf: Optional[float] = Field(default=None, ge=0.3, le=1.5)
    auto_prune_min_trades: Optional[int] = Field(default=None, ge=3, le=100)
    scan_interval_sec: Optional[int] = Field(default=None, ge=30, le=900)
    trailing_enabled: Optional[bool] = None
    breakeven_enabled: Optional[bool] = None
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
    news_guard: Optional[str] = None  # key of the red event this position was already protected for
    rr_target: Optional[float] = None  # exact reward:risk of the trade; 1.0 = range trade, closes whole at its target (no partial close)
    exit_mode: Optional[str] = None  # "cross": no SL/TP, closes on the opposite EMA cross (Test cruce EMA 10/20)
    pips: Optional[float] = None

    @model_validator(mode="after")
    def _pips(self):
        self.pips = pips_for(self.symbol, self.side, self.entry_price, self.mark_price)
        return self
    entry_type: str = "market"
    initial_qty: Optional[float] = None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    partial_done: bool = False
    partial_gross: float = 0.0
    partial_fees: float = 0.0
    ai_review: Optional[Dict[str, Any]] = None
    rules: Optional[Dict[str, Any]] = None  # exit rules of this trade only (manual order): override the bot settings
    ai_review_minutes: Optional[int] = None  # AI review of this trade only, every N minutes (manual order)
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
    exit_mode: Optional[str] = None  # "cross": no SL/TP, closes on the opposite EMA cross (Test cruce EMA 10/20)
    pips: Optional[float] = None

    @model_validator(mode="after")
    def _pips(self):
        if self.pips is None:
            self.pips = pips_for(self.symbol, self.side, self.entry_price, self.exit_price)
        return self
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
    ai_stage: Optional[int] = None  # funnel stage that decided: 0 pre-filter, 1 quick filter, 2 final evaluation
    ai_model: Optional[str] = None
    ai_ms: Optional[int] = None
    ai_adjusted_sl: Optional[float] = None  # proposed by the AI; applied (within safe bounds) only when ai_levels_applied
    ai_adjusted_tp: Optional[float] = None
    ai_levels_applied: bool = False
    default_sl: Optional[float] = None
    default_tp: Optional[float] = None
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


class ManualOrder(BaseModel):
    symbol: str
    side: str = Field(pattern="^(long|short)$")
    sl: float = Field(gt=0)
    tp: float = Field(gt=0)
    atr: float = Field(gt=0)
    risk_pct: float = Field(ge=0.1, le=5)
    breakeven_enabled: Optional[bool] = None
    be_start_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    trailing_enabled: Optional[bool] = None
    trail_start_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    trail_atr_mult: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    partial_tp_enabled: Optional[bool] = None
    partial_tp_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    partial_tp_pct: Optional[int] = Field(default=None, ge=25, le=75)
    ai_review_minutes: Optional[int] = Field(default=None, ge=5, le=30, multiple_of=5)

    def rules(self) -> dict:
        """Exit rules chosen for this order only (fields left empty follow the bot settings)."""
        keys = ("breakeven_enabled", "be_start_r", "trailing_enabled", "trail_start_r", "trail_atr_mult", "partial_tp_enabled", "partial_tp_r", "partial_tp_pct")
        return {k: getattr(self, k) for k in keys if getattr(self, k) is not None}


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
