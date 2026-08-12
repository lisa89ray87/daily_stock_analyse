from __future__ import annotations

import os
from datetime import UTC, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

from . import live_alerts as engine
from .market_hours import get_market_session_status
from .models import MarketData
from .session_windows import is_time_in_window


_ORIGINAL_POLICY = engine._live_session_policy
_ORIGINAL_ANALYZER = engine._analyze_symbol
_ORIGINAL_CONTEXT = engine._compute_intraday_context


def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extended_close() -> dt_time:
    raw = os.getenv("LIVE_EXTENDED_CLOSE", "04:00").strip()
    hour, minute = (int(part) for part in raw.split(":", 1))
    return dt_time(hour=hour, minute=minute)


def _session_state(session, extended_close: dt_time) -> str:
    """Return a precise display/strategy session without changing market calendar logic."""
    if session.session_state == "PRE_MARKET":
        return "PRE_MARKET"
    if session.session_state == "US_REGULAR":
        return "US_REGULAR"
    current = session.market_now.time()
    if is_time_in_window(current, dt_time(20, 0), extended_close):
        return "OVERNIGHT"
    if session.session_state == "AFTER_HOURS":
        return "AFTER_HOURS"
    return session.session_state


def _fetch_extended_bars(symbol: str, market_tz: ZoneInfo) -> list[dict[str, float | str]]:
    """Return fresh 5-minute regular, pre-market, after-hours and overnight bars."""
    frame = yf.Ticker(symbol).history(period="2d", interval="5m", prepost=True, auto_adjust=False)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame.empty or not required.issubset(frame.columns):
        return []
    df = frame.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(UTC)
    df.index = df.index.tz_convert(market_tz)

    latest = df.index.max()
    anchor_date = latest.date()
    if latest.time() < dt_time(4, 0):
        anchor_date -= timedelta(days=1)

    regular_and_after = (df.index.date == anchor_date) & (df.index.time >= dt_time(9, 30))
    overnight = (df.index.date == anchor_date + timedelta(days=1)) & (df.index.time < dt_time(4, 0))
    premarket = (df.index.date == anchor_date + timedelta(days=1)) & (df.index.time >= dt_time(4, 0)) & (df.index.time < dt_time(9, 30))
    df = df[regular_and_after | overnight | premarket]
    if df.empty:
        return []

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return [
        {
            "ts": ts.isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for ts, row in df.iterrows()
    ]


def _session_bars(bars: list[dict[str, float | str]], session_name: str, market_now: datetime) -> list[dict[str, float | str]]:
    """Filter the provider series to the active session for session-specific VWAP/range."""
    result = []
    current_date = market_now.date()
    for bar in bars:
        try:
            ts = datetime.fromisoformat(str(bar["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=market_now.tzinfo)
            local = ts.astimezone(market_now.tzinfo)
        except (KeyError, TypeError, ValueError):
            continue
        t = local.time()
        include = False
        if session_name == "PRE_MARKET":
            include = local.date() == current_date and dt_time(4, 0) <= t < dt_time(9, 30)
        elif session_name == "AFTER_HOURS":
            include = local.date() == current_date and dt_time(16, 0) <= t < dt_time(20, 0)
        elif session_name == "OVERNIGHT":
            include = (local.date() == current_date and t >= dt_time(20, 0)) or (local.date() == current_date and t < dt_time(4, 0))
        if include:
            result.append(bar)
    return result


def _session_vwap_and_range(bars: list[dict[str, float | str]]) -> tuple[float | None, float | None, float | None]:
    if not bars:
        return None, None, None
    total_volume = sum(max(0.0, float(b["volume"])) for b in bars)
    vwap = None
    if total_volume > 0:
        total = sum(((float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0) * max(0.0, float(b["volume"])) for b in bars)
        vwap = total / total_volume
    high = max(float(b["high"]) for b in bars)
    low = min(float(b["low"]) for b in bars)
    return vwap, high, low


def _session_policy(now_utc: datetime, cfg):
    base = _ORIGINAL_POLICY(now_utc, cfg)
    session = get_market_session_status(
        now_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )
    extended_close = _extended_close()
    session_name = _session_state(session, extended_close)

    if session_name == "PRE_MARKET" and _flag("LIVE_PRE_MARKET_ALERTS_ENABLED", True):
        return engine.LiveSessionPolicy(
            session_state="PRE_MARKET",
            allows_regular_session_triggers=True,
            allows_opening_range_confirmation=False,
            allows_vwap_confirmation=True,
            allows_telegram_trade_entry_alerts=True,
            allows_regular_session_candle_confirmation=True,
            reason="Pre-market trigger engine enabled using fresh extended-hours price/candle data",
        )

    if session_name not in {"AFTER_HOURS", "OVERNIGHT"} or not _flag("LIVE_AFTER_HOURS_ALERTS_ENABLED", True):
        return base

    if session_name == "OVERNIGHT":
        return engine.LiveSessionPolicy(
            session_state="OVERNIGHT",
            allows_regular_session_triggers=True,
            allows_opening_range_confirmation=False,
            allows_vwap_confirmation=True,
            allows_telegram_trade_entry_alerts=True,
            allows_regular_session_candle_confirmation=True,
            reason="Overnight trigger engine enabled using fresh extended-hours price/candle data",
        )

    if not is_time_in_window(session.market_now.time(), dt_time(16, 0), extended_close):
        return engine.LiveSessionPolicy(
            session_state="AFTER_HOURS",
            allows_regular_session_triggers=False,
            allows_opening_range_confirmation=False,
            allows_vwap_confirmation=False,
            allows_telegram_trade_entry_alerts=False,
            allows_regular_session_candle_confirmation=False,
            reason=f"Extended-hours window ended at {extended_close.strftime('%H:%M')} ET",
        )

    return engine.LiveSessionPolicy(
        session_state="AFTER_HOURS",
        allows_regular_session_triggers=True,
        allows_opening_range_confirmation=False,
        allows_vwap_confirmation=True,
        allows_telegram_trade_entry_alerts=True,
        allows_regular_session_candle_confirmation=True,
        reason="After-hours trigger engine enabled using fresh extended-hours price/candle data",
    )


def _context_with_extended_hours(symbol_analysis, cfg):
    context = _ORIGINAL_CONTEXT(symbol_analysis, cfg)
    if getattr(symbol_analysis.market_data, "session_state", None) in {"PRE_MARKET", "AFTER_HOURS", "OVERNIGHT"}:
        context["or_high"] = None
        context["or_low"] = None
    return context


def _analyze_symbol_with_extended_hours(symbol, cfg, regime_label, sector_strength, market_provider, news_provider, *, now_utc):
    analysis = _ORIGINAL_ANALYZER(
        symbol,
        cfg,
        regime_label,
        sector_strength,
        market_provider,
        news_provider,
        now_utc=now_utc,
    )
    session = get_market_session_status(
        now_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )
    session_name = _session_state(session, _extended_close())

    extended_session = session_name in {"PRE_MARKET", "AFTER_HOURS", "OVERNIGHT"}
    enabled = (
        (session_name == "PRE_MARKET" and _flag("LIVE_PRE_MARKET_ALERTS_ENABLED", True))
        or (session_name in {"AFTER_HOURS", "OVERNIGHT"} and _flag("LIVE_AFTER_HOURS_ALERTS_ENABLED", True))
    )
    if not extended_session or not enabled:
        return analysis

    bars = _fetch_extended_bars(symbol, ZoneInfo(cfg.live_market_timezone))
    if not bars:
        analysis.market_data.delayed_note = f"{session_name} alert evaluation skipped: fresh extended-hours 5-minute bars unavailable."
        analysis.market_data.session_state = session_name
        return analysis

    md: MarketData = analysis.market_data
    md.session_state = session_name
    md.extended_intraday_bars = bars
    md.intraday_bars = bars
    md.price = float(bars[-1]["close"])
    md.latest_extended_price = md.price
    md.latest_extended_session = "PREMARKET" if session_name == "PRE_MARKET" else "AFTER_HOURS"
    md.selected_price_session = md.latest_extended_session
    md.extended_hours_used = True
    md.is_extended_hours = True
    md.data_session = session_name
    md.data_source = "YFINANCE_EXTENDED_5M"
    md.quote_timestamp = datetime.now(UTC).isoformat()
    md.intraday_timestamp = md.quote_timestamp

    active_bars = _session_bars(bars, session_name, session.market_now)
    session_vwap, session_high, session_low = _session_vwap_and_range(active_bars)
    if session_vwap is not None:
        md.vwap = session_vwap
    md.opening_range_high = None
    md.opening_range_low = None
    if session_high is not None:
        md.resistance = session_high
    if session_low is not None:
        md.support = session_low

    if session_name == "PRE_MARKET":
        md.premarket_price = md.price
        md.latest_extended_session = "PREMARKET"
        md.selected_price_session = "PREMARKET"
    else:
        md.after_hours_price = md.price
        md.latest_extended_session = "AFTER_HOURS"
        md.selected_price_session = "AFTER_HOURS"
    return analysis


def run_live_alerts_extended_hours(base_path=None) -> int:
    original_policy = engine._live_session_policy
    original_analyzer = engine._analyze_symbol
    original_context = engine._compute_intraday_context
    engine._live_session_policy = _session_policy
    engine._analyze_symbol = _analyze_symbol_with_extended_hours
    engine._compute_intraday_context = _context_with_extended_hours
    try:
        print(f"LIVE_EXTENDED | pre_market={_flag('LIVE_PRE_MARKET_ALERTS_ENABLED', True)} | after_hours={_flag('LIVE_AFTER_HOURS_ALERTS_ENABLED', True)} | overnight=True | extended_close={_extended_close().strftime('%H:%M')} ET", flush=True)
        print("LIVE_EXTENDED | strategy=SESSION_AWARE | opening_range_trigger=regular_only | session_vwap=enabled | extended_price_candles=enabled", flush=True)
        return engine.run_live_alerts(base_path)
    finally:
        engine._live_session_policy = original_policy
        engine._analyze_symbol = original_analyzer
        engine._compute_intraday_context = original_context


if __name__ == "__main__":
    raise SystemExit(run_live_alerts_extended_hours())
