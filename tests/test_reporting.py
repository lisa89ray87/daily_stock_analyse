from datetime import UTC, datetime
from pathlib import Path

from src.daily_stock_analyse.models import (
    BattlePlan,
    CatalystEvent,
    DailyAnalysisReport,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.reporting import render_html, render_markdown


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
    hynix = _sample_stock("SKHY", "SK hynix")
    return DailyAnalysisReport(
        generated_at_utc=datetime.now(UTC),
        generated_at_malaysia="2026-08-10 08:00 +08",
        next_us_market_open_malaysia="2026-08-10 21:30 +08",
        session_label="Session",
        fixed_symbols=["AMD", "SKHY"],
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
        best_long="NONE",
        best_short="NONE",
        closest_long_candidate="AMD | Bias: LONG_BIAS | Status: LONG",
        closest_short_candidate="NONE",
        best_overall="AMD | Bias: LONG_BIAS | Status: WAIT",
    )


def test_html_render_contains_header_and_mobile_table_wrapper():
    report = _sample_report()
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "DAILY STOCK ANALYSIS" in html
    assert "table-wrap" in html
    assert "overflow-x: auto" in html
    assert "Prev Close" in html
    assert "TOP OPPORTUNITIES" in html
    assert "Closest LONG Candidate" in html
    assert "Market Data Session:" in html


def test_html_contains_sk_hynix_labeling():
    report = _sample_report()
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "SKHY" in html
    assert "Dynamic Three" not in html
    assert "Analysis Symbols" in html


def test_reporting_renders_ai_overlay_in_markdown_and_html():
    report = _sample_report()
    ai_overlay = {
        "enabled": True,
        "provider": "gemini",
        "provider_display": "Gemini",
        "status": "Fallback",
        "fallback_used": True,
        "summary": "Risk is elevated but selective long setups remain viable.",
        "action_points": ["Protect entries", "Prioritize liquid names", "Wait for confirmation"],
        "market_bias": "MIXED",
        "market_regime": "Mixed tape with selective momentum leadership.",
        "best_long_candidate": {"symbol": "AMD", "reason": "Best long bias among supplied names."},
        "best_short_candidate": {"symbol": "NONE", "reason": "No clean short candidate in supplied data."},
        "best_day_trade": {
            "symbol": "AMD",
            "direction": "LONG",
            "reason": "Candidate with strongest supplied setup.",
            "status": "Candidate, not confirmed",
        },
        "stocks_to_watch": [{"symbol": "AMD", "reason": "Strong relative volume and trend."}],
        "stocks_to_avoid": [{"symbol": "SKHY", "reason": "Insufficient intraday confirmation."}],
        "key_risks": ["Mixed market regime", "Confirmation still pending"],
        "final_conclusion": "The market is mixed and AMD deserves the most attention, but confirmation is still required before treating it as a confirmed trade.",
        "message": "The market is mixed and AMD deserves the most attention, but confirmation is still required before treating it as a confirmed trade.",
    }
    markdown = render_markdown(report, ai_overlay)
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates", ai_overlay)
    assert "## AI Trading Conclusion" in markdown
    assert "Provider: Gemini (fallback)" in markdown
    assert "Market Bias: MIXED" in markdown
    assert "Best Day-Trade:" in markdown
    assert "Protect entries" in markdown
    assert "AI Trading Conclusion" in html
    assert "Gemini (fallback)" in html
    assert "Market Bias" in html
    assert "Best Long" in html
    assert "Best Short" in html
    assert "Watchlist" in html
    assert "Avoid" in html
    assert "Key Risks" in html
    assert "Wait for confirmation" in html


def test_reporting_does_not_claim_after_hours_is_live_regular_session():
    report = _sample_report()
    report.market_data_session = "AFTER_HOURS"
    report.latest_data_source = "24-Hour / Extended Hours"
    report.live_regular_session = False
    report.analyses[0].market_data.session_state = "AFTER_HOURS"
    report.analyses[0].market_data.selected_data_source = "24-Hour / Extended Hours"
    report.analyses[0].market_data.selected_price_session = "AFTER_HOURS"
    markdown = render_markdown(report)
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "Market Data Session: AFTER_HOURS" in markdown
    assert "Latest Data Source: 24-Hour / Extended Hours" in markdown
    assert "Live Regular Session: No" in markdown
    assert "Extended-hours prices are not U.S. regular-session live prices" in html


def test_closed_report_with_after_hours_data_does_not_show_false_premarket_warning():
    report = _sample_report()
    report.market_data_session = "CLOSED"
    report.latest_data_source = "AFTER_HOURS"
    for analysis in report.analyses:
        analysis.market_data.session_state = "CLOSED"
        analysis.market_data.selected_data_source = "AFTER_HOURS"
        analysis.market_data.selected_price_session = "AFTER_HOURS"
        analysis.market_data.after_hours_price = 101.0
        analysis.market_data.latest_extended_price = 101.0
        analysis.market_data.latest_extended_session = "AFTER_HOURS"
        analysis.data_quality.warnings = []
    markdown = render_markdown(report)
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "PREMARKET_UNAVAILABLE" not in markdown
    assert "AFTER_HOURS" in markdown
    assert "Latest Data Source: AFTER_HOURS" in markdown
    assert "PREMARKET_UNAVAILABLE" not in html


def test_after_hours_report_renders_prices_and_ai_section_normally():
    report = _sample_report()
    report.market_data_session = "AFTER_HOURS"
    report.latest_data_source = "AFTER_HOURS"
    for analysis in report.analyses:
        analysis.market_data.session_state = "AFTER_HOURS"
        analysis.market_data.selected_data_source = "AFTER_HOURS"
        analysis.market_data.selected_price_session = "AFTER_HOURS"
        analysis.market_data.after_hours_price = 101.0
        analysis.market_data.latest_extended_price = 101.0
        analysis.market_data.latest_extended_session = "AFTER_HOURS"
        analysis.data_quality.warnings = []
    ai_overlay = {
        "enabled": True,
        "provider": "openai",
        "provider_display": "OpenAI",
        "status": "Enabled",
        "fallback_used": False,
        "summary": "Summary",
        "action_points": ["A", "B", "C"],
        "market_bias": "MIXED",
        "market_regime": "Tape",
        "best_long_candidate": {"symbol": "AMD", "reason": "Reason"},
        "best_short_candidate": {"symbol": "NONE", "reason": "Reason"},
        "best_day_trade": {"symbol": "NONE", "direction": "NONE", "reason": "Reason", "status": "No trade"},
        "stocks_to_watch": [],
        "stocks_to_avoid": [],
        "key_risks": [],
        "final_conclusion": "Conclusion",
        "message": "Conclusion",
    }
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates", ai_overlay)
    assert "AFTER_HOURS" in html
    assert "101.00" in html
    assert "AI Trading Conclusion" in html


def test_reporting_renders_variable_number_of_analysis_symbols():
    report = _sample_report()
    report.fixed_symbols = ["NOK", "AMD", "NVDA"]
    markdown3 = render_markdown(report)
    html3 = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "## ANALYSIS SYMBOLS" in markdown3
    assert markdown3.count("- NOK") == 1
    assert markdown3.count("- AMD") >= 1
    assert "CONFIGURED ANALYSIS SYMBOLS" in html3

    report.fixed_symbols = ["NOK", "AMD", "NVDA", "INTC", "SNDK", "SKHY"]
    markdown6 = render_markdown(report)
    assert "- SKHY" in markdown6

    report.fixed_symbols = ["NOK", "AMD", "NVDA", "INTC", "SNDK", "SKHY", "AMAT", "PANW", "DDOG"]
    html9 = render_html(report, Path(__file__).resolve().parents[1] / "templates")
    assert "AMAT" in html9
    assert "PANW" in html9
    assert "DDOG" in html9


def test_reporting_renders_real_catalyst_metadata_without_untitled_placeholder():
    report = _sample_report()
    report.news_catalysts = [
        CatalystEvent(
            symbol="AMD",
            headline="AMD beats earnings and raises guidance",
            source="Reuters",
            published_at="2026-08-10T14:00:00+00:00",
            category="EARNINGS",
            importance="HIGH",
            catalyst_direction="BULLISH",
            url="https://example.com/amd",
        )
    ]

    markdown = render_markdown(report)
    html = render_html(report, Path(__file__).resolve().parents[1] / "templates")

    assert "AMD | EARNINGS | BULLISH | HIGH | Reuters" in markdown
    assert "Untitled" not in markdown
    assert "Reuters" in html
    assert "AMD beats earnings and raises guidance" in html
    assert "https://example.com/amd" in html

