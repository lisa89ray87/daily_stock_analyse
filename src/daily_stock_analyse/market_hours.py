from __future__ import annotations

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

    if is_open:
        session_state = "US_REGULAR"
    elif now_market < open_market:
        session_state = "PRE_MARKET"
    elif now_market >= close_market:
        session_state = "AFTER_HOURS"
    else:
        session_state = "CLOSED"

    reason = "Market open" if is_open else "Outside regular market hours"
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
        return SessionAwareDataSelection(
            session_state=session.session_state,
            selected_data_source=LIVE_INTRADAY_SOURCE if live_price is not None else UNAVAILABLE_SOURCE,
            selected_price=live_price,
            selected_price_session="REGULAR" if live_price is not None else "UNKNOWN",
            live_data_required=True,
            live_regular_session=True,
            extended_hours_used=False,
        )

    extended_price, extended_session = _extended_hours_price(market_data)
    return SessionAwareDataSelection(
        session_state=session.session_state,
        selected_data_source=EXTENDED_HOURS_SOURCE if extended_price is not None else UNAVAILABLE_SOURCE,
        selected_price=extended_price,
        selected_price_session=extended_session,
        live_data_required=False,
        live_regular_session=False,
        extended_hours_used=extended_price is not None,
    )


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


def next_us_market_open_malaysia(
    now_utc: datetime,
    market_timezone: str,
    market_open_hhmm: str,
    malaysia_timezone: str,
) -> datetime:
    market_tz = ZoneInfo(_safe_tz_name(market_timezone, "America/New_York"))
    my_tz = ZoneInfo(malaysia_timezone)
    open_t = _parse_hhmm(market_open_hhmm)

    current_market = now_utc.astimezone(market_tz)

    # Search next 10 days to safely skip weekends/holidays.
    for i in range(0, 10):
        day = current_market.date() + timedelta(days=i)
        if day.weekday() >= 5:
            continue

        calendar_open_utc, _ = _calendar_schedule_for_day(day)
        if calendar_open_utc is not None:
            open_market = calendar_open_utc.astimezone(market_tz)
        else:
            open_market = datetime.combine(day, open_t, tzinfo=market_tz)

        if open_market <= current_market and i == 0:
            continue

        return open_market.astimezone(my_tz)

    # Defensive fallback.
    fallback = current_market + timedelta(days=1)
    while fallback.weekday() >= 5:
        fallback += timedelta(days=1)
    return datetime.combine(fallback.date(), open_t, tzinfo=market_tz).astimezone(my_tz)


def utc_now() -> datetime:
    return datetime.now(UTC)
