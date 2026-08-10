from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Signal = Literal["LONG", "SHORT", "HOLD", "SELL", "NO TRADE"]
RiskClass = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]


@dataclass
class MarketData:
    symbol: str
    price: float | None = None
    day_change_pct: float | None = None
    trend: str = "UNAVAILABLE"
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    volume: float | None = None
    avg_volume_20d: float | None = None
    relative_volume: float | None = None
    volatility_20d: float | None = None
    support: float | None = None
    resistance: float | None = None
    recent_structure: str = "UNAVAILABLE"
    breakout_state: str = "UNAVAILABLE"
    regular_session_timestamp: str | None = None
    overnight_info: str = "UNAVAILABLE"
    premarket_info: str = "UNAVAILABLE"
    regular_session_info: str = "UNAVAILABLE"
    provider: str = "unknown"
    data_timestamp: str | None = None


@dataclass
class IntelligenceBlock:
    facts: list[str] = field(default_factory=list)
    interpretation: list[str] = field(default_factory=list)
    upcoming_catalysts: list[str] = field(default_factory=list)
    news_available: bool = True


@dataclass
class BattlePlan:
    bullish_scenario: str
    bearish_scenario: str
    key_support: str
    key_resistance: str
    entry_area: str
    target_area: str
    invalidation: str
    risk_reward_assessment: str


@dataclass
class ScoreBreakdown:
    total: float
    long_score: float
    short_score: float
    components: dict[str, float]
    weights: dict[str, float]


@dataclass
class StockAnalysis:
    symbol: str
    name: str
    signal: Signal
    confidence: str
    one_liner: str
    main_reason: str
    risk_classification: RiskClass
    market_data: MarketData
    intelligence: IntelligenceBlock
    battle_plan: BattlePlan
    score: ScoreBreakdown
    source_flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class MarketRegime:
    label: str
    bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    main_catalyst: str
    main_risk: str
    summary: str
    indicators: dict[str, Any]


@dataclass
class DailyAnalysisReport:
    generated_at_utc: datetime
    session_label: str
    fixed_symbols: list[str]
    dynamic_symbols: list[str]
    market_regime: MarketRegime
    analyses: list[StockAnalysis]
    top3_bullish: list[StockAnalysis]
    top3_bearish: list[StockAnalysis]
    best_overall: str
    notes: list[str] = field(default_factory=list)
