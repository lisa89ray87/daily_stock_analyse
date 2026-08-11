from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Signal = Literal["LONG", "SHORT", "WAIT", "NO_TRADE"]
RiskClass = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
TradingHorizon = Literal["DAY_TRADE", "SWING", "NO_TRADE"]
MarketAlignment = Literal["MARKET_ALIGNED", "MARKET_COUNTERTREND", "UNKNOWN"]
DirectionBias = Literal["LONG_BIAS", "SHORT_BIAS", "NEUTRAL"]
SessionLabel = Literal["REGULAR", "PREMARKET", "AFTER_HOURS", "OVERNIGHT_REFERENCE", "UNKNOWN"]


@dataclass
class CatalystEvent:
    symbol: str
    headline: str
    source: str
    published_at: str | None
    category: Literal[
        "EARNINGS",
        "GUIDANCE",
        "ANALYST",
        "PRODUCT",
        "PARTNERSHIP",
        "REGULATORY",
        "LEGAL",
        "ACQUISITION",
        "SEMICONDUCTOR",
        "MACRO",
        "FINANCING",
        "INSIDER",
        "OTHER",
        "NONE",
    ] = "NONE"
    importance: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    catalyst_direction: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"] = "UNKNOWN"
    summary: str = "UNAVAILABLE"
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    url: str | None = None


@dataclass
class MarketData:
    symbol: str
    price: float | None = None
    session_state: str = "CLOSED"
    selected_data_source: str = "UNAVAILABLE"
    selected_price_session: SessionLabel = "UNKNOWN"
    live_regular_session: bool = False
    live_data_required: bool = False
    extended_hours_used: bool = False
    previous_close: float | None = None
    latest_extended_price: float | None = None
    latest_extended_session: SessionLabel = "UNKNOWN"
    gap_pct: float | None = None
    premarket_change_pct: float | None = None
    premarket_volume: float | None = None
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
    atr14: float | None = None
    vwap: float | None = None
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    support: float | None = None
    resistance: float | None = None
    recent_structure: str = "UNAVAILABLE"
    breakout_state: str = "UNAVAILABLE"
    regular_session_timestamp: str | None = None
    premarket_timestamp: str | None = None
    after_hours_timestamp: str | None = None
    intraday_timestamp: str | None = None
    regular_price: float | None = None
    premarket_price: float | None = None
    after_hours_price: float | None = None
    overnight_reference_price: float | None = None
    overnight_info: str = "UNAVAILABLE"
    premarket_info: str = "UNAVAILABLE"
    regular_session_info: str = "UNAVAILABLE"
    provider: str = "unknown"
    data_timestamp: str | None = None
    delayed_note: str = "Latest available provider data (may be delayed)."
    intraday_bars: list[dict[str, float | str]] = field(default_factory=list)
    intraday_rvol: float | None = None
    intraday_rvol_quality: str = "UNAVAILABLE"
    intraday_rvol_note: str | None = None
    rvol_session: str = "UNKNOWN"
    rvol_context_note: str | None = None
    data_session: str = "CLOSED"
    data_source: str = "UNAVAILABLE"
    quote_timestamp: str | None = None
    is_extended_hours: bool = False
    data_quality: str = "UNKNOWN"


@dataclass
class IntelligenceBlock:
    facts: list[str] = field(default_factory=list)
    interpretation: list[str] = field(default_factory=list)
    upcoming_catalysts: list[str] = field(default_factory=list)
    news_available: bool = True
    structured_catalysts: list[CatalystEvent] = field(default_factory=list)
    catalyst_status: str = "UNAVAILABLE"


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
    entry_trigger_price: float | None = None
    confirmation_level: float | None = None
    invalidation_price: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    level_unavailable_reason: str | None = None


@dataclass
class ScoreBreakdown:
    total: float
    long_score: float
    short_score: float
    components: dict[str, float]
    weights: dict[str, float]
    long_points: dict[str, int] = field(default_factory=dict)
    short_points: dict[str, int] = field(default_factory=dict)


@dataclass
class DataQuality:
    price_available: bool
    intraday_available: bool
    premarket_available: bool
    volume_available: bool
    timestamp_available: bool
    provider: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class StockAnalysis:
    symbol: str
    name: str
    signal: Signal
    trading_horizon: TradingHorizon
    direction_bias: DirectionBias
    market_alignment: MarketAlignment
    setup_score: int
    day_trade_candidate: bool
    candidate_score: int
    candidate_status: str
    confirmation_needed: str
    confidence: str
    one_liner: str
    main_reason: str
    risk_classification: RiskClass
    market_data: MarketData
    intelligence: IntelligenceBlock
    battle_plan: BattlePlan
    score: ScoreBreakdown
    data_quality: DataQuality
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
    generated_at_malaysia: str
    next_us_market_open_malaysia: str
    session_label: str
    fixed_symbols: list[str]
    dynamic_symbols: list[str]
    market_regime: MarketRegime
    analyses: list[StockAnalysis]
    day_trading_watchlist: list[StockAnalysis]
    top3_bullish: list[StockAnalysis]
    top3_bearish: list[StockAnalysis]
    best_long: str
    best_short: str
    closest_long_candidate: str
    closest_short_candidate: str
    best_overall: str
    notes: list[str] = field(default_factory=list)
    market_data_session: str = "CLOSED"
    latest_data_source: str = "UNAVAILABLE"
    live_regular_session: bool = False
    news_catalysts: list[CatalystEvent] = field(default_factory=list)
    historical_performance: dict[str, Any] = field(default_factory=dict)
