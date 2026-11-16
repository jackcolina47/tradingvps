"""Broker profiles for the FX engine instances (HotForex/HFM and Pepperstone share the same engine via MetaApi): names, collections,
MT servers, symbol suffixes, provisioning keywords and trading costs (spread factor + commission per lot)."""
from dataclasses import dataclass, field
from fx.symbols import SPECS


@dataclass(frozen=True)
class Costs:
    """Trading costs of one account type: spread = HFM reference spread × factor; commission in USD per standard lot, round turn."""
    spread_factor: float = 1.0
    commission_lot_usd: float = 0.0
    label: str = ""

    def spread(self, symbol: str) -> float:
        return SPECS[symbol]["spread"] * self.spread_factor

    def side_cost(self, symbol: str, lots: float, tickers: dict) -> float:
        """Cost of ONE side (entry or exit) in USD: half spread + half the round-turn commission."""
        from fx.risk import usd_per_unit
        return self.spread(symbol) / 2 * usd_per_unit(symbol, lots, tickers) + self.commission_lot_usd / 2 * lots


@dataclass(frozen=True)
class BrokerProfile:
    key: str
    prefix: str
    api_prefix: str
    panel: str
    ai_kind: str
    bot_name: str
    tg_name: str
    tg_icon: str
    broker: str
    broker_long: str
    metaapi_name: str
    keywords: tuple
    suffixes: dict
    suffix_hint: str
    server_hint: str
    servers: dict
    account_types: dict
    default_account_type: str
    weekly_prefix: str = field(default="")
    rules_only: bool = False  # entries gated only by risk limits + learned rules (no AI approval), with automatic learning on closed trades

    def costs(self, account_type: str = "") -> Costs:
        t = self.account_types.get(account_type or self.default_account_type) or self.account_types[self.default_account_type]
        return Costs(t["spread_factor"], t["commission_lot_usd"], t["label"])

    def public(self, account_type: str = "") -> dict:
        at = account_type if account_type in self.account_types else self.default_account_type
        return {"key": self.key, "name": self.bot_name, "broker": self.broker, "broker_long": self.broker_long, "panel": self.panel,
                "server_hint": self.server_hint, "servers": self.servers, "suffixes": [{"suffix": k, "label": v} for k, v in self.suffixes.items()],
                "account_types": [{"key": k, **v} for k, v in self.account_types.items()], "account_type": at, "default_account_type": self.default_account_type, "rules_only": self.rules_only}


HFM = BrokerProfile(
    rules_only=True, key="hfm", prefix="fx_", api_prefix="/api/fx", panel="fx", ai_kind="fx", bot_name="HotForex AI Trading", tg_name="HotForex AI", tg_icon="🏦",
    broker="HFM", broker_long="HotForex", metaapi_name="hotforex-ai-trading", keywords=("HFM", "HotForex"),
    suffixes={"": "Premium", "b": "Zero", "r": "Pro", "c": "Cent", "x": "InfinityX"},
    suffix_hint="el símbolo cotiza pero no es operable en esta cuenta: revisa el sufijo de símbolos (HFM: Premium sin sufijo, Zero «b», Pro «r», Cent «c», InfinityX «x»)",
    server_hint="HFMarketsGlobal-Live2", servers={"mt5": ["HFMarketsGlobal-Live", "HFMarketsGlobal-Live2", "HFMarketsGlobal-Demo"], "mt4": ["HFMarketsEurope-Live", "HFMarketsGlobal-Demo"]},
    account_types={"premium": {"label": "Spread HFM · sin comisión", "spread_factor": 1.0, "commission_lot_usd": 0.0, "note": "Spread medio de HFM por par (p. ej. 1,2 pips EURUSD · 0,30 USD oro)."}},
    default_account_type="premium",
)

PEPPERSTONE = BrokerProfile(
    key="pepperstone", prefix="pep_", api_prefix="/api/pepper", panel="pepper", ai_kind="pep", bot_name="Pepperstone AI trading", tg_name="Pepperstone AI", tg_icon="🌶",
    broker="Pepperstone", broker_long="Pepperstone", metaapi_name="pepperstone-ai-trading", keywords=("Pepperstone",),
    suffixes={"": "Standard / Razor (sin sufijo)"},
    suffix_hint="el símbolo cotiza pero no es operable en esta cuenta: en Pepperstone los símbolos no llevan sufijo (EURUSD, XAUUSD); revisa el nombre exacto en MetaTrader",
    server_hint="Pepperstone-MT5-Live01",
    servers={"mt5": ["Pepperstone-Demo", "Pepperstone-MT5-Live01", "Pepperstone-MT5-Live02", "Pepperstone-MT5-Live03"], "mt4": ["Pepperstone-Demo01", "Pepperstone-Live01", "Pepperstone-Edge01"]},
    account_types={
        "razor": {"label": "Razor · spread crudo + comisión", "spread_factor": 0.2, "commission_lot_usd": 7.0,
                  "note": "Spread desde 0,0 pips (≈ 20 % del de HFM) más 7 USD por lote ida y vuelta (3,5 USD por lado). El bot descuenta la comisión en paper, backtest y plan."},
        "standard": {"label": "Standard · solo spread", "spread_factor": 0.9, "commission_lot_usd": 0.0,
                     "note": "Sin comisión; spread medio algo más ajustado que el de HFM Premium (≈ 1,0–1,1 pips EURUSD)."},
    },
    default_account_type="razor",
)

PROFILES = {p.key: p for p in (HFM, PEPPERSTONE)}
BY_PANEL = {p.panel: p for p in PROFILES.values()}
DEFAULT_COSTS = HFM.costs()
