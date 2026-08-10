from datetime import UTC, datetime
from pathlib import Path

from src.daily_stock_analyse.models import (
    BattlePlan,
    DailyAnalysisReport,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.reporting import render_html


def _sample_report() -> DailyAnalysisReport:
    analysis = StockAnalysis(
        symbol="AMD",
        name="AMD",
        signal="LONG",
        confidence="MEDIUM",
        one_liner="Sample",
        main_reason="Sample reason",
        risk_classification="MEDIUM",
        market_data=MarketData(symbol="AMD", price=100.0, trend="UPTREND", rsi14=55, relative_volume=1.2),
        intelligence=IntelligenceBlock(facts=["Fact"], interpretation=["Interp"], upcoming_catalysts=["Catalyst"]),
        battle_plan=BattlePlan("b", "s", "90", "110", "95-100", "108-112", "88", "1.5"),
        score=ScoreBreakdown(total=0.3, long_score=0.5, short_score=0.2, components={}, weights={}),
    )
    return DailyAnalysisReport(
        generated_at_utc=datetime.now(UTC),
        session_label="Session",
        fixed_symbols=["AMD"],
        dynamic_symbols=["AAPL"],
        market_regime=MarketRegime(
            label="MIXED",
            bias="NEUTRAL",
            main_catalyst="x",
            main_risk="y",
            summary="z",
            indicators={},
        ),
        analyses=[analysis],
        top3_bullish=[analysis],
        top3_bearish=[analysis],
        best_overall="AMD (LONG)",
    )


def test_html_render_contains_header():
    report = _sample_report()
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "DAILY STOCK ANALYSIS" in html
    assert "AMD" in html
