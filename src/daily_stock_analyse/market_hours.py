from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)


class _NyseHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        Holiday("NewYearsDay", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, observance=nearest_workday, start_date="2021-01-01"),
        Holiday("IndependenceDay", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


mcal = None

LIVE_INTRADAY_SOURCE = "Live / Intraday Regular Session"
EXTENDED_HOURS_SOURCE = "24-Hour / Extended Hours"
PREMARKET_SOURCE = "PRE_MARKET"
AFTER_HOURS_SOURCE = "AFTER_HOURS"
LATEST_AVAILABLE_SOURCE = "Latest Available Quote"
UNAVAILABLE_SOURCE = "UNAVAILABLE"


@dataclass
class MarketSessionStatus:
    market_open: bool
    reason: str
    opening_range_window: bool
    market_now: datetime
    market_open_time: datetime | None
    market_close_time: datetime | None
    session_state: str = "CLOSED"


@dataclass
class SessionAwareDataSelection:
    session_state: str
    selected_data_source: str
    selected_price: float | None
    selected_price_session: str
    live_data_required: bool
    live_regular_session: bool
    extended_hours_used: bool


def _safe_tz_name(raw: str | None, default: str) -> str:
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


def _parse_hhmm(raw: str) -> time:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM value: {raw}")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def _calendar_schedule_for_day(day: date) -> tuple[datetime | None, datetime | None]:
    if _is_nyse_holiday(day):
        return None, None

    return None, None


def _is_nyse_holiday(day: date) -> bool:
    holidays = _NyseHolidayCalendar().holidays(start=day.isoformat(), end=day.isoformat())
    return len(holidays) > 0


def _is_overnight_window(now_market: datetime, regular_close: time, extended_close: time) -> bool:
    """Return True for the post-close window, including across midnight.

    The production overnight policy is 16:00-04:00 ET: regular-session close
    through the configured extended close. This must be checked before the
    normal ``now < open`` pre-market classification because 00:00-04:00 is
    chronologically before the same day's 09:30 open but belongs to the prior
    trading day's overnight session.
    """
    current = now_market.time()
    if regular_close < extended_close:
        return regular_close <= current < extended_close
    return current >= regular_close or current < extended_close


def get_market_session_status(
    now_utc: datetime,
    market_timezone: str,
    market_open_hhmm: str,
    market_close_hhmm: str,
) -> MarketSessionStatus:
    tz = ZoneInfo(_safe_tz_name(market_timezone, "America/New_York"))
    now_market = now_utc.astimezone(tz)

    if now_market.weekday() >= 5:
        return MarketSessionStatus(
            market_open=False,
            reason="Weekend - U.S. regular market closed",
            opening_range_window=False,
            market_now=now_market,
            market_open_time=None,
            market_close_time=None,
            session_state="CLOSED",
        )

    if _is_nyse_holiday(now_market.date()):
        return MarketSessionStatus(
            market_open=False,
            reason="NYSE holiday - U.S. regular market closed",
            opening_range_window=False,
            market_now=now_market,
            market_open_time=None,
            market_close_time=None,
            session_state="CLOSED",
        )

    calendar_open_utc, calendar_close_utc = _calendar_schedule_for_day(now_market.date())

    if calendar_open_utc is not None and calendar_close_utc is not None:
        open_market = calendar_open_utc.astimezone(tz)
        close_market = calendar_close_utc.astimezone(tz)
    else:
        open_t = _parse_hhmm(market_open_hhmm)
        close_t = _parse_hhmm(market_close_hhmm)
        open_market = datetime.combine(now_market.date(), open_t, tzinfo=tz)
        close_market = datetime.combine(now_market.date(), close_t, tzinfo=tz)

    is_open = open_market <= now_market < close_market
    opening_range = is_open and now_market < (open_market + timedelta(minutes=30))
    extended_close = _parse_hhmm(os.getenv("LIVE_EXTENDED_CLOSE", "04:00"))
    overnight = not is_open and _is_overnight_window(now_market, close_market.time(), extended_close)

    if is_open:
        session_state = "US_REGULAR"
    elif overnight:
        session_state = "AFTER_HOURS"
    elif now_market < open_market:
        session_state = "PRE_MARKET"
    elif now_market >= close_market:
        session_state = "AFTER_HOURS"
    else:
        session_state = "CLOSED"

    reason = "Market open" if is_open else "Outside regular market hours"
    if overnight:
        reason = f"Extended-hours / overnight window through {extended_close.strftime('%H:%M')} ET"
    if calendar_open_utc is None or calendar_close_utc is None:
        reason = f"{reason} (calendar fallback mode)"

    return MarketSessionStatus(
        market_open=is_open,
        reason=reason,
        opening_range_window=opening_range,
        market_now=now_market,
        market_open_time=open_market,
        market_close_time=close_market,
        session_state=session_state,
    )


def is_weekday_in_timezone(now_utc: datetime, timezone_name: str) -> bool:
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    return local_now.weekday() < 5


def apply_session_aware_market_data(
    market_data,
    now_utc: datetime,
    market_timezone: str,
    market_open_hhmm: str,
    market_close_hhmm: str,
) -> SessionAwareDataSelection:
    session = get_market_session_status(now_utc, market_timezone, market_open_hhmm, market_close_hhmm)
    selection = select_market_data_for_session(market_data, session)

    market_data.session_state = selection.session_state
    market_data.selected_data_source = selection.selected_data_source
    market_data.selected_price_session = selection.selected_price_session
    market_data.live_data_required = selection.live_data_required
    market_data.live_regular_session = selection.live_regular_session
    market_data.extended_hours_used = selection.extended_hours_used
    if selection.selected_price is not None:
        market_data.price = selection.selected_price
    return selection
