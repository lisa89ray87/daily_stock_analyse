from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.models import IntelligenceBlock, MarketData
from src.daily_stock_analyse.runner import _analyze_symbol


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
    )


def test_data_quality_flags_and_warnings_present():
    md = MarketData(symbol="AMD", price=100, regular_price=100, previous_close=99, overnight_reference_price=99, volume=1000000, relative_volume=1.2, provider="yfinance", data_timestamp="2026-08-10T00:00:00Z")
    result = _analyze_symbol("AMD", _cfg(), "MIXED", 0.0, _FakeMarketProvider(md), _FakeNewsProvider())
    assert result.data_quality.price_available is True
    assert "PREMARKET_UNAVAILABLE" in result.data_quality.warnings
    assert "INTRADAY_UNAVAILABLE" in result.data_quality.warnings


def test_sk_hynix_labeling():
    md = MarketData(symbol="SKHY", price=42.5, regular_price=42.5, previous_close=42.0, overnight_reference_price=42.0, volume=1, relative_volume=1, provider="yfinance", data_timestamp="2026-08-10T00:00:00Z")
    result = _analyze_symbol("SKHY", _cfg(), "MIXED", 0.0, _FakeMarketProvider(md), _FakeNewsProvider())
    assert result.name == "SK hynix"


def test_no_silent_fallback_from_skhy_to_krx_ticker():
    md = MarketData(symbol="SKHY", price=None, provider="yfinance", data_timestamp="2026-08-10T00:00:00Z")
    result = _analyze_symbol("SKHY", _cfg(), "MIXED", 0.0, _FakeMarketProvider(md), _FakeNewsProvider())
    assert result.symbol == "SKHY"
    assert result.name == "SK hynix"
