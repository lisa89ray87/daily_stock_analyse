from datetime import datetime
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.market_hours import get_market_session_status


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _utc(y: int, m: int, d: int, h: int, minute: int) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=UTC)


def test_midnight_is_overnight_not_premarket(monkeypatch):
    monkeypatch.setenv("LIVE_EXTENDED_CLOSE", "04:00")
    # 00:27 ET on Wednesday is 04:27 UTC.
    status = get_market_session_status(_utc(2026, 8, 12, 4, 27), "America/New_York", "09:30", "16:00")
    assert status.market_now.astimezone(ET).hour == 0
    assert status.session_state == "AFTER_HOURS"


def test_overnight_cutoff_becomes_premarket(monkeypatch):
    monkeypatch.setenv("LIVE_EXTENDED_CLOSE", "04:00")
    status = get_market_session_status(_utc(2026, 8, 12, 8, 1), "America/New_York", "09:30", "16:00")
    assert status.market_now.astimezone(ET).hour == 4
    assert status.market_now.astimezone(ET).minute == 1
    assert status.session_state == "PRE_MARKET"


def test_regular_session_still_wins(monkeypatch):
    monkeypatch.setenv("LIVE_EXTENDED_CLOSE", "04:00")
    status = get_market_session_status(_utc(2026, 8, 12, 14, 0), "America/New_York", "09:30", "16:00")
    assert status.session_state == "US_REGULAR"
