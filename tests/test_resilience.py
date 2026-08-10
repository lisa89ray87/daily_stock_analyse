from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.models import IntelligenceBlock, MarketData
from src.daily_stock_analyse.scoring import decide_signal, score_stock


WEIGHTS = {
    "trend": 0.2,
    "momentum": 0.15,
    "volume": 0.1,
    "relative_strength": 0.1,
    "fundamentals_news": 0.2,
    "catalyst_event": 0.1,
    "risk_reward": 0.15,
}


def _cfg() -> AppConfig:
    return AppConfig(
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


def test_missing_vwap_allows_day_trade_candidate_wait_for_live_confirmation():
    md = MarketData(
        symbol="NOINTRA",
        price=100,
        sma20=98,
        sma50=95,
        rsi14=55,
        relative_volume=1.8,
        support=96,
        resistance=106,
        gap_pct=4.0,
        day_change_pct=4.2,
        trend="UPTREND",
        breakout_state="NEAR BREAKOUT",
        vwap=None,
        opening_range_high=None,
        opening_range_low=None,
    )
    intel = IntelligenceBlock(facts=["growth"], upcoming_catalysts=["event"])
    score = score_stock(md, intel, WEIGHTS)
    decision = decide_signal(score, md, _cfg(), "RISK_ON")
    assert decision.day_trade_candidate is True
    assert decision.trading_horizon == "DAY_TRADE"


def test_missing_premarket_data_is_tolerated():
    md = MarketData(symbol="NOPRE", price=100, previous_close=100, latest_extended_price=None, premarket_volume=None)
    intel = IntelligenceBlock(news_available=False)
    score = score_stock(md, intel, WEIGHTS)
    assert isinstance(score.long_score, float)
