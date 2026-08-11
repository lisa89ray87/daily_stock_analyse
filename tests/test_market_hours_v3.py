from datetime import UTC, datetime
from pathlib import Path

from src.daily_stock_analyse.config import load_config
from src.daily_stock_analyse.market_hours import (
    AFTER_HOURS_SOURCE,
    LIVE_INTRADAY_SOURCE,
    LATEST_AVAILABLE_SOURCE,
    PREMARKET_SOURCE,
    get_market_session_status,
    is_weekday_in_timezone,
    next_us_market_open_malaysia,
    select_market_data_for_session,
)
from src.daily_stock_analyse.models import MarketData


def test_market_open_detection():
    # 2026-08-10 14:00 UTC == 10:00 America/New_York (DST)
    now_utc = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is True
    assert status.session_state == "US_REGULAR"


def test_market_closed_detection_after_hours():
    # 2026-08-10 22:30 UTC == 18:30 America/New_York
    now_utc = datetime(2026, 8, 10, 22, 30, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is False
    assert status.session_state == "AFTER_HOURS"


def test_weekend_detection_closed():
    # 2026-08-09 is Sunday
    now_utc = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is False
    assert "Weekend" in status.reason
    assert status.session_state == "CLOSED"


def test_pre_market_detection():
    now_utc = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    status = get_market_session_status(now_utc, "America/New_York", "09:30", "16:00")
    assert status.market_open is False
    assert status.session_state == "PRE_MARKET"


def test_malaysia_weekday_detection_uses_explicit_timezone():
    friday_utc = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
    saturday_utc = datetime(2026, 8, 14, 16, 30, tzinfo=UTC)
    assert is_weekday_in_timezone(friday_utc, "Asia/Kuala_Lumpur") is True
    assert is_weekday_in_timezone(saturday_utc, "Asia/Kuala_Lumpur") is False


def test_extended_hours_selection_when_regular_market_closed():
    md = MarketData(after_hours_price=101.25, latest_extended_price=101.25, latest_extended_session="AFTER_HOURS", symbol="AMD")
    session = get_market_session_status(datetime(2026, 8, 10, 22, 30, tzinfo=UTC), "America/New_York", "09:30", "16:00")
    selected = select_market_data_for_session(md, session)
    assert selected.selected_price == 101.25
    assert selected.selected_data_source == AFTER_HOURS_SOURCE
    assert selected.live_regular_session is False


def test_live_data_selection_during_us_regular_hours():
    md = MarketData(symbol="AMD", price=100.0, regular_price=100.5, intraday_timestamp="2026-08-10T14:00:00Z")
    session = get_market_session_status(datetime(2026, 8, 10, 14, 0, tzinfo=UTC), "America/New_York", "09:30", "16:00")
    selected = select_market_data_for_session(md, session)
    assert selected.selected_price == 100.5
    assert selected.selected_data_source == LIVE_INTRADAY_SOURCE
    assert selected.live_regular_session is True


def test_premarket_prefers_premarket_quote_when_available():
    md = MarketData(symbol="AMD", premarket_price=102.0)
    session = get_market_session_status(datetime(2026, 8, 10, 12, 0, tzinfo=UTC), "America/New_York", "09:30", "16:00")
    selected = select_market_data_for_session(md, session)
    assert selected.selected_price == 102.0
    assert selected.selected_data_source == PREMARKET_SOURCE


def test_premarket_uses_latest_valid_quote_when_no_dedicated_extended_quote_exists():
    md = MarketData(symbol="AMD", regular_price=100.0, price=100.0)
    session = get_market_session_status(datetime(2026, 8, 10, 12, 0, tzinfo=UTC), "America/New_York", "09:30", "16:00")
    selected = select_market_data_for_session(md, session)
    assert selected.selected_price == 100.0
    assert selected.selected_data_source == LATEST_AVAILABLE_SOURCE


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


def test_next_open_no_crash_when_live_market_timezone_missing(monkeypatch):
    monkeypatch.delenv("LIVE_MARKET_TIMEZONE", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    out = next_us_market_open_malaysia(
        datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm="09:30",
        malaysia_timezone="Asia/Kuala_Lumpur",
    )
    assert out.tzinfo is not None


def test_next_open_no_crash_when_live_market_timezone_empty(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_TIMEZONE", "")
    cfg = load_config(Path(__file__).resolve().parents[1])
    out = next_us_market_open_malaysia(
        datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm="09:30",
        malaysia_timezone="Asia/Kuala_Lumpur",
    )
    assert out.tzinfo is not None


def test_next_open_no_crash_when_live_market_open_missing(monkeypatch):
    monkeypatch.delenv("LIVE_MARKET_OPEN", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    out = next_us_market_open_malaysia(
        datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        malaysia_timezone="Asia/Kuala_Lumpur",
    )
    assert out.tzinfo is not None


def test_next_open_no_crash_when_live_market_open_empty(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_OPEN", "")
    cfg = load_config(Path(__file__).resolve().parents[1])
    out = next_us_market_open_malaysia(
        datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        malaysia_timezone="Asia/Kuala_Lumpur",
    )
    assert out.tzinfo is not None
