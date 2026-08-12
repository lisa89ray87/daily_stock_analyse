from __future__ import annotations

import os
from datetime import UTC, datetime, time as dt_time
from zoneinfo import ZoneInfo

import yfinance as yf

from . import live_alerts as engine
from .market_hours import get_market_session_status
from .models import MarketData


def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extended_close() -> dt_time:
    raw = os.getenv("LIVE_EXTENDED_CLOSE", "20:00").strip()
    hour, minute = (int(part) for part in raw.split(":", 1))
    return dt_time(hour=hour, minute=minute)


def _fetch_extended_bars(symbol: str, market_tz: ZoneInfo) -> list[dict[str, float | str]]:
    frame = yf.Ticker(symbol).history(period="1d", interval="5m", prepost=True, auto_adjust=False)
    if frame.empty or not {"Open", "High", "Low", "Close", "Volume"}.issubset(frame.columns):
        return []
    df = frame.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(UTC)
    df.index = df.index.tz_convert(market_tz)
    df = df.between_time("09:30", "20:00")
    if df.empty:
        return []
    session_date = df.index.max().date()
    df = df[df.index.date == session_date].dropna(subset=["Open", "High", "Low", "Close", "Volume"])
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


def _after_hours_policy(now_utc: datetime, cfg):
    base = engine._live_session_policy(now_utc, cfg)
    session = get_market_session_status(
        now_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )
    if session.session_state != "AFTER_HOURS" or not _flag("LIVE_AFTER_HOURS_ALERTS_ENABLED", True):
        return base
    if session.market_now.time() >= _extended_close():
        return engine.LiveSessionPolicy(
            session_state="AFTER_HOURS",
            allows_regular_session_triggers=False,
            allows_opening_range_confirmation=False,
            allows_vwap_confirmation=False,
            allows_telegram_trade_entry_alerts=False,
            allows_regular_session_candle_confirmation=False,
            reason=f"After-hours window ended at {_extended_close().strftime('%H:%M')} ET",
        )
    return engine.LiveSessionPolicy(
        session_state="AFTER_HOURS",
        allows_regular_session_triggers=True,
        allows_opening_range_confirmation=True,
        allows_vwap_confirmation=True,
        allows_telegram_trade_entry_alerts=True,
        allows_regular_session_candle_confirmation=True,
        reason="After-hours trigger engine enabled using extended-hours price/candle data",
    )


def _analyze_symbol_with_extended_hours(symbol, cfg, regime_label, sector_strength, market_provider, news_provider, *, now_utc):
    analysis = engine._analyze_symbol(
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
    if session.session_state != "AFTER_HOURS" or not _flag("LIVE_AFTER_HOURS_ALERTS_ENABLED", True):
        return analysis

    bars = _fetch_extended_bars(symbol, ZoneInfo(cfg.live_market_timezone))
    if not bars:
        analysis.market_data.delayed_note = "After-hours alert evaluation skipped: extended-hours 5-minute bars unavailable."
        analysis.market_data.session_state = "AFTER_HOURS"
        return analysis

    md: MarketData = analysis.market_data
    md.session_state = "AFTER_HOURS"
    md.extended_intraday_bars = bars
    md.intraday_bars = bars
    md.price = float(bars[-1]["close"])
    md.after_hours_price = md.price
    md.latest_extended_price = md.price
    md.latest_extended_session = "AFTER_HOURS"
    md.selected_price_session = "AFTER_HOURS"
    md.extended_hours_used = True
    md.is_extended_hours = True
    md.data_session = "AFTER_HOURS"
    md.data_source = "YFINANCE_EXTENDED_5M"
    md.quote_timestamp = datetime.now(UTC).isoformat()
    md.intraday_timestamp = md.quote_timestamp
    return analysis


def run_live_alerts_extended_hours(base_path=None) -> int:
    if not _flag("LIVE_AFTER_HOURS_ALERTS_ENABLED", True):
        return engine.run_live_alerts(base_path)

    original_policy = engine._live_session_policy
    original_analyzer = engine._analyze_symbol
    engine._live_session_policy = _after_hours_policy
    engine._analyze_symbol = _analyze_symbol_with_extended_hours
    try:
        print(f"LIVE_AFTER_HOURS | enabled=True | extended_close={_extended_close().strftime('%H:%M')} ET")
        return engine.run_live_alerts(base_path)
    finally:
        engine._live_session_policy = original_policy
        engine._analyze_symbol = original_analyzer


if __name__ == "__main__":
    raise SystemExit(run_live_alerts_extended_hours())
