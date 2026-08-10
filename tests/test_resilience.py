from src.daily_stock_analyse.models import IntelligenceBlock, MarketData
from src.daily_stock_analyse.scoring import score_stock


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
