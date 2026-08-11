from datetime import UTC, datetime, timedelta

from src.daily_stock_analyse.outcomes import evaluate_signal_outcomes


def test_outcome_engine_marks_target_and_stop():
    now = datetime.now(UTC)

    open_rows = [
        {
            "id": 1,
            "symbol": "AAA",
            "signal": "LONG",
            "entry_trigger_price": 100.0,
            "target_1": 102.0,
            "target_2": 104.0,
            "stop_loss": 98.0,
            "invalidation_price": 98.0,
            "triggered": False,
            "triggered_at": None,
            "expiry_at": (now + timedelta(hours=12)).isoformat(),
            "mfe_pct": None,
            "mae_pct": None,
        },
        {
            "id": 2,
            "symbol": "BBB",
            "signal": "SHORT",
            "entry_trigger_price": 100.0,
            "target_1": 98.0,
            "target_2": 96.0,
            "stop_loss": 102.0,
            "invalidation_price": 102.0,
            "triggered": False,
            "triggered_at": None,
            "expiry_at": (now + timedelta(hours=12)).isoformat(),
            "mfe_pct": None,
            "mae_pct": None,
        },
    ]

    updates = evaluate_signal_outcomes(
        open_rows,
        latest_prices={"AAA": 104.5, "BBB": 95.0},
        as_of_utc=now,
    )
    statuses = {item.signal_id: item.status for item in updates}
    assert statuses[1] == "TARGET_2"
    assert statuses[2] == "TARGET_2"
