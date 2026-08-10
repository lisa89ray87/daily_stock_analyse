from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.models import IntelligenceBlock, MarketData, ScoreBreakdown
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
        fixed_watchlist=["AMD"],
        candidate_universe=["AAPL"],
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


def test_long_bias_and_candidate_classification():
    md = MarketData(
        symbol="TEST",
        price=110,
        sma20=105,
        sma50=100,
        rsi14=58,
        day_change_pct=4.5,
        relative_volume=2.0,
        support=103,
        resistance=124,
        trend="UPTREND",
        breakout_state="BREAKOUT",
        gap_pct=4.2,
        vwap=None,
        atr14=3.0,
    )
    intel = IntelligenceBlock(facts=["Company reports growth and contract wins"], upcoming_catalysts=["Earnings next week"])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_ON", market_sector_strength=1.2)
    assert signal.direction_bias == "LONG_BIAS"
    assert signal.day_trade_candidate is True
    assert signal.trading_horizon == "DAY_TRADE"


def test_short_bias_and_failed_breakout_pattern():
    md = MarketData(
        symbol="TEST",
        price=99,
        sma20=104,
        sma50=108,
        rsi14=49,
        day_change_pct=-4.4,
        relative_volume=1.9,
        support=95,
        resistance=108,
        trend="DOWNTREND",
        breakout_state="NO CLEAR BREAK",
        gap_pct=3.5,
        vwap=101,
        atr14=3.2,
    )
    intel = IntelligenceBlock(facts=["Analyst downgrade and delay concerns"], upcoming_catalysts=[])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_OFF", market_sector_strength=-1.0)
    assert signal.direction_bias == "SHORT_BIAS"
    assert signal.signal in {"SHORT", "WAIT"}


def test_wait_reason_is_specific_for_low_rvol():
    md = MarketData(
        symbol="TEST",
        price=100,
        sma20=98,
        sma50=95,
        rsi14=56,
        day_change_pct=2.4,
        relative_volume=0.8,
        support=96,
        resistance=107,
        trend="UPTREND",
        breakout_state="NEAR BREAKOUT",
        gap_pct=3.4,
        atr14=2.3,
    )
    intel = IntelligenceBlock(facts=["Company reports growth"], upcoming_catalysts=["Event"])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "MIXED", market_sector_strength=0.2)
    assert signal.signal in {"WAIT", "NO_TRADE", "LONG"}
    if signal.signal == "WAIT":
        assert "relative volume is insufficient" in signal.reason.lower()


def test_no_trade_when_price_missing():
    md = MarketData(symbol="TEST", price=None)
    intel = IntelligenceBlock()
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_ON")
    assert signal.signal == "NO_TRADE"


def test_countertrend_alignment_flagged():
    md = MarketData(
        symbol="TEST",
        price=110,
        sma20=105,
        sma50=100,
        rsi14=56,
        day_change_pct=1.5,
        relative_volume=1.7,
        support=104,
        resistance=118,
        trend="UPTREND",
        breakout_state="NEAR BREAKOUT",
        gap_pct=3.2,
        atr14=2.2,
    )
    intel = IntelligenceBlock(facts=["Company reports growth"], upcoming_catalysts=["Event"])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_OFF", market_sector_strength=-1.0)
    assert signal.market_alignment == "MARKET_COUNTERTREND"


def test_score_breakdown_points_present():
    md = MarketData(symbol="TEST", price=100, sma20=99, sma50=97, rsi14=55, relative_volume=1.7, day_change_pct=3.1, support=95, resistance=106, trend="UPTREND", gap_pct=3.3)
    intel = IntelligenceBlock(facts=["growth"], upcoming_catalysts=["event"])
    score = score_stock(md, intel, WEIGHTS)
    assert "Total" in score.long_points
    assert "Trend" in score.long_points


def test_day_trade_candidate_gap_threshold():
    md = MarketData(symbol="TEST", price=100, sma20=99, sma50=97, rsi14=54, relative_volume=1.2, day_change_pct=1.1, support=95, resistance=106, trend="UPTREND", gap_pct=3.1)
    intel = IntelligenceBlock(facts=["growth"], upcoming_catalysts=[])
    score = score_stock(md, intel, WEIGHTS)
    decision = decide_signal(score, md, _cfg(), "RISK_ON")
    assert decision.day_trade_candidate is True


def test_day_trade_candidate_rvol_threshold():
    md = MarketData(symbol="TEST", price=100, sma20=99, sma50=97, rsi14=54, relative_volume=1.6, day_change_pct=0.2, support=95, resistance=106, trend="UPTREND", gap_pct=0.4)
    intel = IntelligenceBlock(facts=[], upcoming_catalysts=[])
    score = score_stock(md, intel, WEIGHTS)
    decision = decide_signal(score, md, _cfg(), "RISK_ON")
    assert decision.day_trade_candidate is True


def test_custom_score_breakdown_still_decides():
    md = MarketData(symbol="TEST", price=100, trend="DOWNTREND", support=95, resistance=105, rsi14=66, relative_volume=1.8, atr14=2.1)
    score = ScoreBreakdown(total=-0.1, long_score=0.2, short_score=0.35, components={}, weights={})
    signal = decide_signal(score, md, _cfg(), "RISK_OFF")
    assert signal.signal in {"SHORT", "WAIT", "NO_TRADE"}
