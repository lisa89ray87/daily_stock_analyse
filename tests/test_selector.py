from src.daily_stock_analyse.models import (
    BattlePlan,
    IntelligenceBlock,
    MarketData,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.selector import select_dynamic_opportunities


def _analysis(symbol: str, long_score: float, short_score: float) -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=symbol,
        signal="LONG",
        trading_horizon="SWING",
        market_alignment="MARKET_ALIGNED",
        setup_score=int(max(long_score, short_score) * 100),
        confidence="LOW",
        one_liner="x",
        main_reason="x",
        risk_classification="LOW",
        market_data=MarketData(symbol=symbol, relative_volume=1.3),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan("a", "b", "c", "d", "e", "f", "g", "h"),
        score=ScoreBreakdown(
            total=long_score - short_score,
            long_score=long_score,
            short_score=short_score,
            components={},
            weights={},
        ),
    )


def test_select_dynamic_excludes_fixed_and_keeps_top3():
    fixed = ["AMD", "NVDA"]
    data = [
        _analysis("AMD", 0.9, 0.1),
        _analysis("NVDA", 0.8, 0.2),
        _analysis("AAPL", 0.7, 0.1),
        _analysis("TSLA", 0.6, 0.2),
        _analysis("QCOM", 0.2, 0.7),
        _analysis("META", 0.3, 0.4),
    ]

    data[-1].market_data.relative_volume = 0.6
    out = select_dynamic_opportunities(data, fixed, top_n=3, min_setup_score=45, min_relative_volume=1.0)
    symbols = [x.symbol for x in out]
    assert "AMD" not in symbols
    assert "NVDA" not in symbols
    assert len(symbols) == 3
