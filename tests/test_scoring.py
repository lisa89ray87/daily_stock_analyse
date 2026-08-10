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
        min_setup_score=60,
        min_relative_volume=1.15,
        day_trade_threshold=72,
        short_threshold=0.28,
        long_threshold=0.28,
        dynamic_count=3,
    )


def test_long_classification():
    md = MarketData(
        symbol="TEST",
        price=110,
        sma20=105,
        sma50=100,
        rsi14=58,
        day_change_pct=2.3,
        relative_volume=1.9,
        support=103,
        resistance=124,
        trend="UPTREND",
        breakout_state="NEAR BREAKOUT",
        vwap=109.5,
        atr14=3.0,
    )
    intel = IntelligenceBlock(facts=["Company reports growth and contract wins"], upcoming_catalysts=["Earnings next week"])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_ON")
    assert signal.signal == "LONG"


def test_short_classification():
    md = MarketData(
        symbol="TEST",
        price=90,
        sma20=95,
        sma50=100,
        rsi14=69,
        day_change_pct=-2.4,
        relative_volume=1.8,
        support=82,
        resistance=95,
        trend="DOWNTREND",
        breakout_state="NEAR BREAKDOWN",
        vwap=90.5,
        atr14=3.1,
    )
    intel = IntelligenceBlock(facts=["Analyst downgrade and delay concerns"], upcoming_catalysts=[])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_OFF")
    assert signal.signal == "SHORT"


def test_wait_classification():
    md = MarketData(
        symbol="TEST",
        price=100,
        sma20=100,
        sma50=99,
        rsi14=52,
        day_change_pct=0.4,
        relative_volume=1.0,
        support=97,
        resistance=104,
        trend="RANGE",
        breakout_state="NO CLEAR BREAK",
        atr14=2.0,
    )
    intel = IntelligenceBlock(facts=["No recent provider news returned"], upcoming_catalysts=[])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "MIXED")
    assert signal.signal in {"WAIT", "NO_TRADE"}


def test_no_trade_classification_on_missing_price():
    md = MarketData(symbol="TEST", price=None)
    intel = IntelligenceBlock()
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_ON")
    assert signal.signal == "NO_TRADE"


def test_countertrend_requires_stronger_setup():
    md = MarketData(
        symbol="TEST",
        price=110,
        sma20=105,
        sma50=100,
        rsi14=56,
        day_change_pct=1.5,
        relative_volume=1.3,
        support=104,
        resistance=118,
        trend="UPTREND",
        breakout_state="NEAR BREAKOUT",
        vwap=109,
        atr14=2.2,
    )
    intel = IntelligenceBlock(facts=["Company reports growth"], upcoming_catalysts=["Event"])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score, md, _cfg(), "RISK_OFF")
    assert signal.market_alignment == "MARKET_COUNTERTREND"


def test_decide_short_with_custom_score_breakdown_wait_or_short():
    md = MarketData(symbol="TEST", price=100, trend="DOWNTREND", support=95, resistance=105, rsi14=66, relative_volume=1.4, atr14=2.1)
    score = ScoreBreakdown(total=-0.1, long_score=0.2, short_score=0.35, components={}, weights={})
    signal = decide_signal(score, md, _cfg(), "RISK_OFF")
    assert signal.signal in {"SHORT", "WAIT"}
