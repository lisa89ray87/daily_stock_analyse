from datetime import UTC, datetime

from src.daily_stock_analyse.backtest import summarize_backtest


def test_backtest_summary_groups_by_direction_and_catalyst():
    now = datetime.now(UTC)
    rows = [
        {
            "symbol": "AAA",
            "signal": "LONG",
            "status": "TARGET_1",
            "return_pct": 1.2,
            "market_regime_label": "RISK_ON",
            "catalyst_category": "EARNINGS",
            "created_at": now,
        },
        {
            "symbol": "BBB",
            "signal": "SHORT",
            "status": "STOP",
            "return_pct": -1.0,
            "market_regime_label": "RISK_ON",
            "catalyst_category": "REGULATORY",
            "created_at": now,
        },
    ]

    summary = summarize_backtest(rows)
    assert summary["trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert "LONG" in summary["by_direction"]
    assert "SHORT" in summary["by_direction"]
    assert "EARNINGS" in summary["by_catalyst_category"]
