from typing import Optional, List, Dict
from pydantic import BaseModel, ConfigDict, Field, field_validator
from models import STRATEGY_KEYS, SIGNAL_MODES
from fx.symbols import AVAILABLE, DEFAULT_SYMBOLS
from fx.market import SESSION_IDS

NEWS_WEIGHTS = ("normal", "bajo")  # weight of market-wide news alerts in the per-signal AI verdict
FX_SLOW_KEYS = ["fix_reversion"]
FX_STRATEGY_KEYS = STRATEGY_KEYS + FX_SLOW_KEYS

FX_TIMEFRAMES = ["5m", "15m", "1h", "4h"]


class FxSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    strategy: str = "all"
    timeframe: str = "1h"
    symbols: List[str] = list(DEFAULT_SYMBOLS)
    risk_per_trade: float = 1.0
    max_positions: int = 5
    leverage: int = 100
    daily_loss_limit: float = 5.0
    ai_enabled: bool = True
    ai_min_confidence: int = 60
    ai_news_weight: str = "normal"
    ai_position_review_enabled: bool = False
    ai_position_review_minutes: int = 30
    ai_position_review_mode: str = "auto"
    ai_position_review_min_confidence: int = 75
    scan_interval_sec: int = 120
    trailing_enabled: bool = True
    htf_filter: bool = True
    symbol_params: Dict[str, Dict[str, float]] = {}
    signal_mode: str = "relaxed"
    disabled_strategies: List[str] = ["supertrend_momentum", "ema_cross"]
    close_before_weekend: bool = True
    min_lot_max_risk: float = 4.0
    sessions_allowed: List[str] = []
    risk_profile: int = 2
    plan_scenario: str = "base"
    plan_target_monthly: float = 1000.0
    plan_monthly_deposit: float = 0.0
    plan_phase: int = 0
    currency_exposure_limit: int = 2
    rollover_guard: bool = True
    vol_filter_enabled: bool = True
    vol_min_pct: int = 20
    vol_high_pct: int = 90
    seasonality_bias: bool = True
    seasonality_block_pairs: List[str] = ["EURUSD"]
    seasonality_size_factor: float = 0.75
    usd_regime_filter: bool = True
    usd_regime_sma: int = 200
    fomc_rule: bool = True
    max_long_positions: int = 2
    be_start_r: float = 1.0
    trail_start_r: float = 1.5
    trail_atr_mult: float = 1.0
    partial_tp_enabled: bool = True
    partial_tp_r: float = 1.5
    partial_tp_pct: int = 50
    bot_running: bool = False

    @field_validator("seasonality_block_pairs")
    @classmethod
    def _season_pairs(cls, v):
        return [s for s in dict.fromkeys(v or []) if s in AVAILABLE]

    @field_validator("usd_regime_sma")
    @classmethod
    def _usd_sma(cls, v):
        if v not in (50, 200):
            raise ValueError("La SMA del régimen USD debe ser 50 o 200")
        return v

    @field_validator("vol_min_pct")
    @classmethod
    def _vol_min(cls, v):
        if not 0 <= v <= 50:
            raise ValueError("Percentil mínimo de volatilidad fuera de rango (0–50)")
        return v

    @field_validator("vol_high_pct")
    @classmethod
    def _vol_high(cls, v):
        if not 60 <= v <= 100:
            raise ValueError("Percentil alto de volatilidad fuera de rango (60–100)")
        return v

    @field_validator("currency_exposure_limit", "max_long_positions")
    @classmethod
    def _small_caps(cls, v):
        if not 1 <= v <= 6:
            raise ValueError("Tope fuera de rango (1–6)")
        return v

    @field_validator("seasonality_size_factor")
    @classmethod
    def _season_factor(cls, v):
        if not 0.25 <= v <= 1.0:
            raise ValueError("Factor de tamaño estacional fuera de rango (0,25–1)")
        return v

    @field_validator("be_start_r", "trail_start_r", "partial_tp_r", "trail_atr_mult")
    @classmethod
    def _r_levels(cls, v):
        if not 0.5 <= v <= 3.0:
            raise ValueError("nivel fuera de rango (0,5–3)")
        return v

    @field_validator("partial_tp_pct")
    @classmethod
    def _partial_pct(cls, v):
        if not 25 <= v <= 75:
            raise ValueError("porcentaje de cierre parcial fuera de rango (25–75)")
        return v

    @field_validator("sessions_allowed")
    @classmethod
    def _sessions(cls, v):
        return [s for s in dict.fromkeys(v or []) if s in SESSION_IDS]

    @field_validator("ai_news_weight")
    @classmethod
    def _news_weight(cls, v):
        if v not in NEWS_WEIGHTS:
            raise ValueError("Peso de noticias inválido (normal o bajo)")
        return v

    @field_validator("signal_mode")
    @classmethod
    def _mode(cls, v):
        if v not in SIGNAL_MODES:
            raise ValueError("Modo de señales inválido")
        return v

    @field_validator("disabled_strategies")
    @classmethod
    def _disabled(cls, v):
        v = [s for s in dict.fromkeys(v or []) if s in FX_STRATEGY_KEYS and s != "all"]
        if len(v) >= len(FX_STRATEGY_KEYS) - 1:
            raise ValueError("Debe quedar al menos una estrategia activa")
        return v

    @field_validator("symbol_params")
    @classmethod
    def _params(cls, v):
        out = {}
        for sym, p in (v or {}).items():
            if sym in AVAILABLE and isinstance(p, dict):
                clean = {k: float(p[k]) for k in ("sl_atr", "tp_atr", "atr_period") if p.get(k) is not None}
                if clean and 0.5 <= clean.get("sl_atr", 1.5) <= 5 and 1 <= clean.get("tp_atr", 3) <= 10 and 5 <= clean.get("atr_period", 14) <= 50:
                    out[sym] = clean
        return out

    @field_validator("strategy")
    @classmethod
    def _strategy(cls, v):
        if v not in FX_STRATEGY_KEYS:
            raise ValueError("Estrategia inválida")
        return v

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v):
        if v not in FX_TIMEFRAMES:
            raise ValueError("Temporalidad inválida")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v):
        v = [s for s in dict.fromkeys(v) if s in AVAILABLE]
        if not v:
            raise ValueError("Selecciona al menos un par")
        return v


class FxSettingsUpdate(BaseModel):
    strategy: Optional[str] = None
    timeframe: Optional[str] = None
    symbols: Optional[List[str]] = None
    risk_per_trade: Optional[float] = Field(default=None, ge=0.1, le=5)
    max_positions: Optional[int] = Field(default=None, ge=1, le=12)
    leverage: Optional[int] = Field(default=None, ge=1, le=500)
    daily_loss_limit: Optional[float] = Field(default=None, ge=1, le=20)
    ai_enabled: Optional[bool] = None
    ai_min_confidence: Optional[int] = Field(default=None, ge=0, le=100)
    ai_news_weight: Optional[str] = Field(default=None, pattern="^(normal|bajo)$")
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
    close_before_weekend: Optional[bool] = None
    min_lot_max_risk: Optional[float] = Field(default=None, ge=0, le=10)
    sessions_allowed: Optional[List[str]] = None
    plan_scenario: Optional[str] = None
    plan_target_monthly: Optional[float] = Field(default=None, ge=50, le=10_000_000)
    plan_monthly_deposit: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    plan_phase: Optional[int] = Field(default=None, ge=0, le=4)
    currency_exposure_limit: Optional[int] = Field(default=None, ge=1, le=6)
    rollover_guard: Optional[bool] = None
    vol_filter_enabled: Optional[bool] = None
    vol_min_pct: Optional[int] = Field(default=None, ge=0, le=50)
    vol_high_pct: Optional[int] = Field(default=None, ge=60, le=100)
    seasonality_bias: Optional[bool] = None
    seasonality_block_pairs: Optional[List[str]] = None
    seasonality_size_factor: Optional[float] = Field(default=None, ge=0.25, le=1.0)
    usd_regime_filter: Optional[bool] = None
    usd_regime_sma: Optional[int] = None
    fomc_rule: Optional[bool] = None
    max_long_positions: Optional[int] = Field(default=None, ge=1, le=6)
    be_start_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    trail_start_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    trail_atr_mult: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    partial_tp_enabled: Optional[bool] = None
    partial_tp_r: Optional[float] = Field(default=None, ge=0.5, le=3.0)
    partial_tp_pct: Optional[int] = Field(default=None, ge=25, le=75)


class HfmTestRequest(BaseModel):
    metaapi_token: str = ""
    mt_login: str = ""
    mt_password: str = ""
    mt_server: Optional[str] = None
    mt_platform: Optional[str] = None
    metaapi_region: Optional[str] = None
    metaapi_base_url: Optional[str] = None


class FxBacktestRequest(BaseModel):
    symbol: str
    strategy: str = "all"
    timeframe: str = "1h"
    risk_per_trade: float = Field(default=1.0, ge=0.1, le=5)
    leverage: int = Field(default=100, ge=1, le=500)


class FxProfileRequest(BaseModel):
    level: int = Field(ge=0, le=4)
