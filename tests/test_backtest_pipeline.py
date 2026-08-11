from datetime import UTC, datetime
from pathlib import Path

from src.daily_stock_analyse.backtest import summarize_backtest
from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.signal_history import SignalHistoryStore


def _analysis(symbol: str, signal: str, category: str) -> StockAnalysis:
    intel = IntelligenceBlock(catalyst_status="CATALYST_IDENTIFIED")
    if category:
        from src.daily_stock_analyse.models import CatalystEvent

        intel.structured_catalysts = [
            CatalystEvent(
                symbol=symbol,
                headline=f"{symbol} catalyst",
                source="Example",
                published_at=None,
                category=category,
                catalyst_direction="BULLISH" if signal == "LONG" else "BEARISH",
                importance="MEDIUM",
            )
        ]
    return StockAnalysis(
        symbol=symbol,
        name=symbol,
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias="LONG_BIAS" if signal == "LONG" else "SHORT_BIAS",
        market_alignment="MARKET_ALIGNED",
        setup_score=80,
        day_trade_candidate=True,
        candidate_score=85,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="x",
        confidence="MEDIUM",
        one_liner="x",
        main_reason="x",
        risk_classification="MEDIUM",
        market_data=MarketData(symbol=symbol, price=100.0),
        intelligence=intel,
        battle_plan=BattlePlan(
            "b",
            "s",
            "95",
            "105",
            "entry",
            "target",
            "invalid",
            "rr",
            entry_trigger_price=100.0,
            target_1=102.0,
            target_2=104.0,
            invalidation_price=98.0,
        ),
        score=ScoreBreakdown(total=0.0, long_score=0.0, short_score=0.0, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_backtest_summary_groups_by_direction_and_catalyst(tmp_path: Path):
    store = SignalHistoryStore(tmp_path / "signals.db")
    now = datetime.now(UTC)
    regime = MarketRegime("RISK_ON", "BULLISH", "x", "y", "z", {})

    store.save_signals([
        _analysis("AAA", "LONG", "EARNINGS"),
        _analysis("BBB", "SHORT", "REGULATORY"),
    ], regime, now, expiry_hours=24)

    with store._connect() as conn:
        conn.execute("UPDATE signal_history SET status='TARGET_1', return_pct=1.2 WHERE symbol='AAA'")
        conn.execute("UPDATE signal_history SET status='STOP', return_pct=-1.0 WHERE symbol='BBB'")

    summary = summarize_backtest(store.load_backtest_rows(limit=20))
    assert summary["trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert "LONG" in summary["by_direction"]
    assert "SHORT" in summary["by_direction"]
    assert "EARNINGS" in summary["by_catalyst_category"]
