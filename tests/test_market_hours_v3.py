from datetime import UTC, datetime

from src.daily_stock_analyse.market_hours import (
    get_market_session_status,
    next_us_market_open_malaysia,
)


def test_market_open_detection():
    # 2026-08-10 14:00 UTC == 10:00 America/New_York (DST)
    now_utc = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is True


def test_market_closed_detection_after_hours():
    # 2026-08-10 22:30 UTC == 18:30 America/New_York
    now_utc = datetime(2026, 8, 10, 22, 30, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is False


def test_weekend_detection_closed():
    # 2026-08-09 is Sunday
    now_utc = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is False
    assert "Weekend" in status.reason


def test_dst_handling_for_next_open_in_malaysia_changes_hour():
    jan = next_us_market_open_malaysia(
        datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
        market_timezone="America/New_York",
        market_open_hhmm="09:30",
        malaysia_timezone="Asia/Kuala_Lumpur",
    )
    jul = next_us_market_open_malaysia(
        datetime(2026, 7, 15, 0, 0, tzinfo=UTC),
        market_timezone="America/New_York",
        market_open_hhmm="09:30",
        malaysia_timezone="Asia/Kuala_Lumpur",
    )
    assert {jan.hour, jul.hour} == {21, 22}


def test_holiday_detection_when_calendar_available():
    # Christmas Day (NYSE holiday) during would-be market hours.
    now_utc = datetime(2026, 12, 25, 17, 0, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is False
    assert "holiday" in status.reason.lower()
