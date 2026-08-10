from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.runner import _best_for_direction, _build_battle_plan, _build_data_quality_warnings


def _analysis(symbol: str, signal: str, direction_bias: str, setup_score: int = 70, candidate_score: int = 70) -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=symbol,
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias=direction_bias,
        market_alignment="MARKET_ALIGNED",
        setup_score=setup_score,
        day_trade_candidate=True,
        candidate_score=candidate_score,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="x",
        confidence="MEDIUM",
        one_liner="x",
        main_reason="x",
        risk_classification="MEDIUM",
        market_data=MarketData(symbol=symbol, price=100.0, support=95.0, resistance=105.0, atr14=2.0),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan("b", "s", "95", "105", "entry", "target", "invalid", "rr"),
        score=ScoreBreakdown(total=0.0, long_score=0.0, short_score=0.0, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_best_long_requires_confirmed_long_signal():
    analyses = [
        _analysis("AAA", "NO_TRADE", "LONG_BIAS", setup_score=90, candidate_score=95),
        _analysis("BBB", "WAIT", "LONG_BIAS", setup_score=85, candidate_score=90),
    ]
    best_long, closest = _best_for_direction(analyses, "LONG")
    assert best_long == "NONE"
    assert "Status: NO_TRADE" in closest or "Status: WAIT" in closest


def test_best_short_requires_confirmed_short_signal():
    analyses = [
        _analysis("AAA", "NO_TRADE", "SHORT_BIAS", setup_score=92, candidate_score=96),
        _analysis("BBB", "WAIT", "SHORT_BIAS", setup_score=84, candidate_score=88),
    ]
    best_short, closest = _best_for_direction(analyses, "SHORT")
    assert best_short == "NONE"
    assert "Status: NO_TRADE" in closest or "Status: WAIT" in closest


def test_best_long_best_short_none_when_no_confirmed_signal():
    analyses = [_analysis("AAA", "WAIT", "NEUTRAL")]
    best_long, _ = _best_for_direction(analyses, "LONG")
    best_short, _ = _best_for_direction(analyses, "SHORT")
    assert best_long == "NONE"
    assert best_short == "NONE"


def test_entry_level_unavailable_when_required_levels_missing():
    md = MarketData(symbol="X", price=100.0, support=None, resistance=None, atr14=None)
    battle = _build_battle_plan(md, "LONG")
    assert battle.entry_trigger_price is None
    assert battle.confirmation_level is None
    assert battle.invalidation_price is None
    assert battle.target_1 is None
    assert battle.target_2 is None
    assert battle.level_unavailable_reason is not None


def test_sndk_mapping_warning_removed_for_valid_data():
    md = MarketData(symbol="SNDK", price=45.0, data_timestamp="2026-08-10T00:00:00Z")
    warnings = _build_data_quality_warnings("SNDK", md)
    assert "TICKER_MAPPING_WARNING" not in warnings
    assert "SNDK_DATA_LIMITATION" not in warnings


def test_sndk_warning_present_when_data_not_reliable():
    md = MarketData(symbol="SNDK", price=None, data_timestamp=None)
    warnings = _build_data_quality_warnings("SNDK", md)
    assert "SNDK_DATA_LIMITATION" in warnings
