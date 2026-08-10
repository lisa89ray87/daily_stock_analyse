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


def test_long_scoring_bias():
    md = MarketData(
        symbol="TEST",
        price=110,
        sma20=105,
        sma50=100,
        rsi14=58,
        day_change_pct=2.3,
        relative_volume=1.8,
        support=102,
        resistance=120,
    )
    intel = IntelligenceBlock(facts=["Company reports growth and contract wins"], upcoming_catalysts=["Earnings next week"])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score)
    assert score.long_score > score.short_score
    assert signal.signal in {"LONG", "HOLD"}


def test_short_scoring_bias():
    md = MarketData(
        symbol="TEST",
        price=90,
        sma20=95,
        sma50=100,
        rsi14=74,
        day_change_pct=-2.4,
        relative_volume=1.7,
        support=85,
        resistance=96,
    )
    intel = IntelligenceBlock(facts=["Analyst downgrade and delay concerns"], upcoming_catalysts=[])
    score = score_stock(md, intel, WEIGHTS)
    signal = decide_signal(score)
    assert score.short_score >= score.long_score
    assert signal.signal in {"SHORT", "HOLD", "NO TRADE"}
