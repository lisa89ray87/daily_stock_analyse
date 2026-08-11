from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.outcomes import evaluate_signal_outcomes
from src.daily_stock_analyse.signal_history import SignalHistoryStore


def _analysis(symbol: str, signal: str, entry: float, target1: float, target2: float, stop: float) -> StockAnalysis:
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
        market_data=MarketData(symbol=symbol, price=entry),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan("b", "s", "95", "105", "entry", "target", "invalid", "rr", entry_trigger_price=entry, target_1=target1, target_2=target2, invalidation_price=stop),
        score=ScoreBreakdown(total=0.0, long_score=0.0, short_score=0.0, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_outcome_engine_marks_target_and_stop(tmp_path: Path):
    store = SignalHistoryStore(tmp_path / "signals.db")
    regime = MarketRegime("RISK_ON", "BULLISH", "x", "y", "z", {})
    now = datetime.now(UTC)

    store.save_signals([
        _analysis("AAA", "LONG", 100.0, 102.0, 104.0, 98.0),
        _analysis("BBB", "SHORT", 100.0, 98.0, 96.0, 102.0),
    ], regime, now - timedelta(hours=1), expiry_hours=12)

    updates = evaluate_signal_outcomes(
        store.open_signals(),
        latest_prices={"AAA": 104.5, "BBB": 95.0},
        as_of_utc=now,
    )
    store.apply_outcome_updates(updates, now)

    rows = store.load_backtest_rows(limit=10)
    statuses = {str(row["symbol"]): str(row["status"]) for row in rows}
    assert statuses["AAA"] == "TARGET_2"
    assert statuses["BBB"] == "TARGET_2"
