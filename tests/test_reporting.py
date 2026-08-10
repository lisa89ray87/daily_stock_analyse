from datetime import UTC, datetime
from pathlib import Path

from src.daily_stock_analyse.models import (
    BattlePlan,
    DailyAnalysisReport,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.reporting import render_html


def _sample_stock(symbol: str, name: str) -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=name,
        signal="LONG",
        trading_horizon="DAY_TRADE",
        direction_bias="LONG_BIAS",
        market_alignment="MARKET_ALIGNED",
        setup_score=78,
        day_trade_candidate=True,
        candidate_score=84,
        candidate_status="DAY_TRADE CANDIDATE - WAIT FOR LIVE CONFIRMATION",
        confirmation_needed="Break above resistance with volume expansion",
        confidence="MEDIUM",
        one_liner="Sample",
        main_reason="Sample reason",
        risk_classification="MEDIUM",
        market_data=MarketData(
            symbol=symbol,
            price=100.0,
            trend="UPTREND",
            rsi14=55,
            relative_volume=1.7,
            latest_extended_session="PREMARKET",
            overnight_reference_price=98.0,
            latest_extended_price=101.0,
            gap_pct=3.06,
            premarket_change_pct=3.06,
        ),
        intelligence=IntelligenceBlock(facts=["Fact"], interpretation=["Interp"], upcoming_catalysts=["Catalyst"]),
        battle_plan=BattlePlan("b", "s", "90", "110", "trigger", "108-112", "88", "1.5"),
        score=ScoreBreakdown(total=0.3, long_score=0.5, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, False, True, True, True, "yfinance", ["INTRADAY_UNAVAILABLE"]),
    )


def _sample_report() -> DailyAnalysisReport:
    amd = _sample_stock("AMD", "AMD")
    hynix = _sample_stock("000660.KS", "SK Hynix (KRX)")
    return DailyAnalysisReport(
        generated_at_utc=datetime.now(UTC),
        session_label="Session",
        fixed_symbols=["AMD", "000660.KS"],
        dynamic_symbols=["AAPL"],
        market_regime=MarketRegime(
            label="MIXED",
            bias="NEUTRAL",
            main_catalyst="x",
            main_risk="y",
            summary="z",
            indicators={},
        ),
        analyses=[amd, hynix],
        day_trading_watchlist=[amd],
        top3_bullish=[amd],
        top3_bearish=[amd],
        best_long="AMD | Bias: LONG_BIAS | Status: WAIT",
        best_short="NO HIGH-CONVICTION SETUP",
        best_overall="AMD | Bias: LONG_BIAS | Status: WAIT",
    )


def test_html_render_contains_header_and_mobile_table_wrapper():
    report = _sample_report()
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "DAILY STOCK ANALYSIS" in html
    assert "table-wrap" in html
    assert "Prev Close" in html


def test_html_contains_sk_hynix_labeling():
    report = _sample_report()
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "SK Hynix (KRX)" in html
