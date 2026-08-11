from datetime import UTC, datetime

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.models import IntelligenceBlock, MarketData
from src.daily_stock_analyse.runner import _analyze_symbol, _build_data_quality_warnings


class _FakeMarketProvider:
    def __init__(self, md: MarketData):
        self.md = md

    def get_market_data(self, symbol: str) -> MarketData:
        return self.md


class _FakeNewsProvider:
    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        return IntelligenceBlock(facts=["No recent provider news returned"], interpretation=["x"], upcoming_catalysts=[])


def _cfg() -> AppConfig:
    return AppConfig(
        openai_api_key=None,
        resend_api_key=None,
        email_from=None,
        email_to="raymond87tan@gmail.com",
        send_email=False,
        data_provider="yfinance",
        news_provider="yfinance",
        fixed_watchlist=["SKHY"],
        candidate_universe=["AMD"],
        score_weights={
            "trend": 0.2,
            "momentum": 0.15,
            "volume": 0.1,
            "relative_strength": 0.1,
            "fundamentals_news": 0.2,
            "catalyst_event": 0.1,
            "risk_reward": 0.15,
        },
        schedule_utc_cron="0 23 * * 1-5",
        min_setup_score=70,
        min_relative_volume=1.5,
        day_trade_threshold=75,
        short_threshold=0.70,
        long_threshold=0.70,
        dynamic_count=3,
        day_trade_gap_threshold=3.0,
        day_trade_rvol_threshold=1.5,
        day_trade_min_setup_score=65,
        morning_report_time="08:00",
        morning_report_timezone="Asia/Kuala_Lumpur",
        live_alert_enabled=True,
        live_alert_interval_minutes=5,
        live_market_timezone="America/New_York",
        live_market_open="09:30",
        live_market_close="16:00",
        alert_min_setup_score=70,
        alert_min_rvol=1.5,
        alert_cooldown_minutes=15,
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )


def test_data_quality_flags_and_warnings_present():
    md = MarketData(symbol="AMD", price=100, regular_price=100, previous_close=99, overnight_reference_price=99, volume=1000000, relative_volume=1.2, provider="yfinance", data_timestamp="2026-08-10T00:00:00Z")
    result = _analyze_symbol(
        "AMD",
        _cfg(),
        "MIXED",
        0.0,
        _FakeMarketProvider(md),
        _FakeNewsProvider(),
        now_utc=datetime(2026, 8, 10, 22, 30, tzinfo=UTC),
    )
    assert result.data_quality.price_available is False
    assert "PREMARKET_UNAVAILABLE" in result.data_quality.warnings
    assert "INTRADAY_UNAVAILABLE" in result.data_quality.warnings
    assert "EXTENDED_HOURS_UNAVAILABLE" in result.data_quality.warnings


def test_after_hours_price_is_selected_outside_regular_session():
    md = MarketData(
        symbol="AMD",
        price=100.0,
        regular_price=100.0,
        previous_close=99.0,
        overnight_reference_price=99.0,
        after_hours_price=101.5,
        latest_extended_price=101.5,
        latest_extended_session="AFTER_HOURS",
        provider="yfinance",
        data_timestamp="2026-08-10T00:00:00Z",
    )
    result = _analyze_symbol(
        "AMD",
        _cfg(),
        "MIXED",
        0.0,
        _FakeMarketProvider(md),
        _FakeNewsProvider(),
        now_utc=datetime(2026, 8, 10, 22, 30, tzinfo=UTC),
    )
    assert result.market_data.price == 101.5
    assert result.market_data.session_state == "AFTER_HOURS"
    assert result.market_data.selected_data_source == "24-Hour / Extended Hours"
    assert result.market_data.live_regular_session is False


def test_sk_hynix_labeling():
    md = MarketData(symbol="SKHY", price=42.5, regular_price=42.5, previous_close=42.0, overnight_reference_price=42.0, volume=1, relative_volume=1, provider="yfinance", data_timestamp="2026-08-10T00:00:00Z")
    result = _analyze_symbol("SKHY", _cfg(), "MIXED", 0.0, _FakeMarketProvider(md), _FakeNewsProvider())
    assert result.name == "SK hynix"


def test_no_silent_fallback_from_skhy_to_krx_ticker():
    md = MarketData(symbol="SKHY", price=None, provider="yfinance", data_timestamp="2026-08-10T00:00:00Z")
    result = _analyze_symbol("SKHY", _cfg(), "MIXED", 0.0, _FakeMarketProvider(md), _FakeNewsProvider())
    assert result.symbol == "SKHY"
    assert result.name == "SK hynix"


def test_extended_hours_warning_not_added_when_after_hours_data_exists():
    md = MarketData(
        symbol="AMD",
        price=100.0,
        regular_price=100.0,
        previous_close=99.0,
        overnight_reference_price=99.0,
        volume=1_000_000,
        provider="yfinance",
        data_timestamp="2026-08-10T00:00:00Z",
        premarket_price=None,
        after_hours_price=101.0,
        premarket_volume=None,
        latest_extended_session="AFTER_HOURS",
    )
    warnings = _build_data_quality_warnings("AMD", md)
    assert "PREMARKET_UNAVAILABLE" in warnings
    assert "EXTENDED_HOURS_UNAVAILABLE" not in warnings


def test_extended_hours_warning_added_when_no_extended_prices_or_session():
    md = MarketData(
        symbol="AMD",
        price=100.0,
        regular_price=100.0,
        previous_close=99.0,
        overnight_reference_price=99.0,
        volume=1_000_000,
        provider="yfinance",
        data_timestamp="2026-08-10T00:00:00Z",
        premarket_price=None,
        after_hours_price=None,
        premarket_volume=None,
        latest_extended_session="UNKNOWN",
    )
    warnings = _build_data_quality_warnings("AMD", md)
    assert "PREMARKET_UNAVAILABLE" in warnings
    assert "EXTENDED_HOURS_UNAVAILABLE" in warnings


def test_premarket_availability_uses_session_flag_when_present():
    md = MarketData(
        symbol="AMD",
        price=100.0,
        regular_price=100.0,
        previous_close=99.0,
        overnight_reference_price=99.0,
        volume=1_000_000,
        provider="yfinance",
        data_timestamp="2026-08-10T00:00:00Z",
        premarket_price=None,
        after_hours_price=None,
        premarket_volume=None,
        latest_extended_session="PREMARKET",
    )
    result = _analyze_symbol("AMD", _cfg(), "MIXED", 0.0, _FakeMarketProvider(md), _FakeNewsProvider())
    assert result.data_quality.premarket_available is True
    assert "PREMARKET_UNAVAILABLE" not in result.data_quality.warnings
