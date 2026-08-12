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
    """Return True for the post-close window, including across midnight."""
    current = now_market.time()
    if regular_close < extended_close:
        return regular_close <= current < extended_close
    return current >= regular_close or current < extended_close


def get_market_session_status(now_utc: datetime, market_timezone: str, market_open_hhmm: str, market_close_hhmm: str) -> MarketSessionStatus:
    tz = ZoneInfo(_safe_tz_name(market_timezone, "America/New_York"))
    now_market = now_utc.astimezone(tz)
    if now_market.weekday() >= 5:
        return MarketSessionStatus(False, "Weekend - U.S. regular market closed", False, now_market, None, None, "CLOSED")
    if _is_nyse_holiday(now_market.date()):
        return MarketSessionStatus(False, "NYSE holiday - U.S. regular market closed", False, now_market, None, None, "CLOSED")

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

    return MarketSessionStatus(is_open, reason, opening_range, now_market, open_market, close_market, session_state)


def is_weekday_in_timezone(now_utc: datetime, timezone_name: str) -> bool:
    return now_utc.astimezone(ZoneInfo(timezone_name)).weekday() < 5


def apply_session_aware_market_data(market_data, now_utc: datetime, market_timezone: str, market_open_hhmm: str, market_close_hhmm: str) -> SessionAwareDataSelection:
    session = get_market_session_status(now_utc, market_timezone, market_open_hhmm, market_close_hhmm)
    selection = select_market_data_for_session(market_data, session)
    market_data.session_state = selection.session_state
    market_data.selected_data_source = selection.selected_data_source
    market_data.selected_price_session = selection.selected_price_session
    market_data.live_data_required = selection.live_data_required
    market_data.live_regular_session = selection.live_regular_session
    market_data.extended_hours_used = selection.extended_hours_used
    market_data.price = selection.selected_price
    if selection.live_regular_session and selection.selected_price is None:
        market_data.delayed_note = "Live regular-session price unavailable from provider."
    elif selection.extended_hours_used:
        market_data.delayed_note = "Latest price reflects extended-hours provider data, not U.S. regular-session live trading."
    elif not selection.live_regular_session and selection.selected_price is None:
        market_data.delayed_note = "Extended-hours price unavailable; do not treat prior regular-session data as live."
    return selection


def select_market_data_for_session(market_data, session: MarketSessionStatus) -> SessionAwareDataSelection:
    if session.session_state == "US_REGULAR":
        live_price = _live_intraday_price(market_data)
        return SessionAwareDataSelection(session.session_state, LIVE_INTRADAY_SOURCE if live_price is not None else UNAVAILABLE_SOURCE, live_price, "REGULAR" if live_price is not None else "UNKNOWN", True, True, False)
    extended_price, extended_session = _extended_hours_price(market_data)
    if extended_price is not None:
        source = EXTENDED_HOURS_SOURCE
        if extended_session == "PREMARKET":
            source = PREMARKET_SOURCE
        elif extended_session == "AFTER_HOURS":
            source = AFTER_HOURS_SOURCE
        return SessionAwareDataSelection(session.session_state, source, extended_price, extended_session, False, False, True)
    fallback_price, fallback_session = _latest_valid_quote(market_data)
    return SessionAwareDataSelection(session.session_state, LATEST_AVAILABLE_SOURCE if fallback_price is not None else UNAVAILABLE_SOURCE, fallback_price, fallback_session, False, False, False)


def _live_intraday_price(market_data) -> float | None:
    if market_data.intraday_bars or market_data.intraday_timestamp or market_data.vwap is not None:
        return market_data.regular_price if market_data.regular_price is not None else market_data.price
    return None


def _extended_hours_price(market_data) -> tuple[float | None, str]:
    if market_data.premarket_price is not None:
        return market_data.premarket_price, "PREMARKET"
    if market_data.after_hours_price is not None:
        return market_data.after_hours_price, "AFTER_HOURS"
    if market_data.latest_extended_price is not None and market_data.latest_extended_session in {"PREMARKET", "AFTER_HOURS"}:
        return market_data.latest_extended_price, market_data.latest_extended_session
    return None, "UNKNOWN"


def _latest_valid_quote(market_data) -> tuple[float | None, str]:
    if market_data.regular_price is not None:
        return market_data.regular_price, "REGULAR"
    if market_data.price is not None:
        return market_data.price, market_data.selected_price_session or "REGULAR"
    if market_data.latest_extended_price is not None:
        return market_data.latest_extended_price, market_data.latest_extended_session
    return None, "UNKNOWN"


def next_us_market_open_malaysia(now_utc: datetime, market_timezone: str, market_open_hhmm: str, malaysia_timezone: str) -> datetime:
    market_tz = ZoneInfo(_safe_tz_name(market_timezone, "America/New_York"))
    my_tz = ZoneInfo(malaysia_timezone)
    open_t = _parse_hhmm(market_open_hhmm)
    current_market = now_utc.astimezone(market_tz)
    for i in range(0, 10):
        day = current_market.date() + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        calendar_open_utc, _ = _calendar_schedule_for_day(day)
        open_market = calendar_open_utc.astimezone(market_tz) if calendar_open_utc is not None else datetime.combine(day, open_t, tzinfo=market_tz)
        if open_market <= current_market and i == 0:
            continue
        return open_market.astimezone(my_tz)
    fallback = current_market + timedelta(days=1)
    while fallback.weekday() >= 5:
        fallback += timedelta(days=1)
    return datetime.combine(fallback.date(), open_t, tzinfo=market_tz).astimezone(my_tz)


def utc_now() -> datetime:
    return datetime.now(UTC)
