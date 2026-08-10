from src.daily_stock_analyse.models import IntelligenceBlock, MarketData
from src.daily_stock_analyse.scoring import decide_signal, score_stock
from src.daily_stock_analyse.config import AppConfig


WEIGHTS = {
    "trend": 0.2,
    "momentum": 0.15,
    "volume": 0.1,
    "relative_strength": 0.1,
    "fundamentals_news": 0.2,
    "catalyst_event": 0.1,
    "risk_reward": 0.15,
}


def test_missing_market_data_does_not_crash_scoring():
    md = MarketData(symbol="MISS")
    intel = IntelligenceBlock(news_available=False)
    score = score_stock(md, intel, WEIGHTS)
    assert score.long_score == 0.0


def test_invalid_api_like_content_is_tolerated():
    md = MarketData(symbol="BROKEN", day_change_pct=None, rsi14=None)
    intel = IntelligenceBlock(facts=["No recent provider news returned"], news_available=False)
    score = score_stock(md, intel, WEIGHTS)
    assert isinstance(score.total, float)


def test_missing_intraday_data_does_not_break_decision():
    md = MarketData(
        symbol="NOINTRA",
        price=100,
        sma20=98,
        sma50=95,
        rsi14=55,
        relative_volume=1.3,
        support=96,
        resistance=106,
        vwap=None,
        opening_range_high=None,
        opening_range_low=None,
    )
    intel = IntelligenceBlock(facts=["growth"], upcoming_catalysts=["event"])
    score = score_stock(md, intel, WEIGHTS)
    cfg = AppConfig(
        openai_api_key=None,
        resend_api_key=None,
        email_from=None,
        email_to="raymond87tan@gmail.com",
        send_email=False,
        data_provider="yfinance",
        news_provider="yfinance",
        fixed_watchlist=[],
        candidate_universe=[],
        score_weights=WEIGHTS,
        schedule_utc_cron="0 23 * * 1-5",
        min_setup_score=60,
        min_relative_volume=1.15,
        day_trade_threshold=72,
        short_threshold=0.28,
        long_threshold=0.28,
        dynamic_count=3,
    )
    decision = decide_signal(score, md, cfg, "RISK_ON")
    assert decision.trading_horizon in {"SWING", "NO_TRADE", "DAY_TRADE"}


def test_missing_premarket_data_is_tolerated():
    md = MarketData(symbol="NOPRE", price=100, previous_close=100, latest_extended_price=None, premarket_volume=None)
    intel = IntelligenceBlock(news_available=False)
    score = score_stock(md, intel, WEIGHTS)
    assert isinstance(score.long_score, float)
