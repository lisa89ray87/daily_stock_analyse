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


@dataclass
class MarketSessionStatus:
    market_open: bool
    reason: str
    opening_range_window: bool
    market_now: datetime
    market_open_time: datetime | None
    market_close_time: datetime | None


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
        )

    if _is_nyse_holiday(now_market.date()):
        return MarketSessionStatus(
            market_open=False,
            reason="NYSE holiday - U.S. regular market closed",
            opening_range_window=False,
            market_now=now_market,
            market_open_time=None,
            market_close_time=None,
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
    )


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
