from __future__ import annotations

from datetime import UTC, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

from . import event_alert_runner as runner


def _overnight_bars(symbol: str, market_tz: ZoneInfo) -> list[dict[str, float | str]]:
    """Return the current regular + post-market + overnight 5-minute bars.

    The overnight window is 20:00-04:00 ET. If the upstream provider does not
    expose overnight bars, the result is empty rather than manufacturing a
    stale price or treating the last 20:00 quote as current.
    """
    frame = yf.Ticker(symbol).history(period="1d", interval="5m", prepost=True, auto_adjust=False)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame.empty or not required.issubset(frame.columns):
        return []

    df = frame.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(UTC)
    df.index = df.index.tz_convert(market_tz)

    latest = df.index.max()
    anchor_date = latest.date()
    if latest.time() < dt_time(9, 30):
        anchor_date -= timedelta(days=1)

    regular_and_evening = (df.index.date == anchor_date) & (df.index.time >= dt_time(9, 30))
    overnight = (df.index.date == anchor_date + timedelta(days=1)) & (df.index.time <= dt_time(4, 0))
    df = df[regular_and_evening | overnight]
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


def _patched_extended_hours_bars(symbol: str, market_tz: ZoneInfo):
    return _overnight_bars(symbol, market_tz)


def run_event_alerts_overnight(base_path: Path | None = None) -> int:
    # The existing runner owns scheduling, state, cooldowns, Telegram delivery,
    # and event detection. This wrapper changes only the extended data source
    # and keeps the implementation isolated from the regular-session engine.
    runner.os.environ["LIVE_EXTENDED_CLOSE"] = "04:00"
    runner._extended_hours_bars = _patched_extended_hours_bars
    print("EVENT_ALERT_OVERNIGHT | enabled=True | window=20:00-04:00 ET | provider=yfinance", flush=True)
    return runner.run_event_alerts(base_path)


if __name__ == "__main__":
    raise SystemExit(run_event_alerts_overnight())
