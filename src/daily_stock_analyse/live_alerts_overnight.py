from __future__ import annotations

import os
from datetime import UTC, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

from . import live_alerts_extended_hours as engine


def _overnight_bars(symbol: str, market_tz: ZoneInfo):
    """Return fresh regular + extended 5-minute bars through 04:00 ET."""
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


def run_live_alerts_overnight(base_path=None) -> int:
    # Preserve the existing live-alert engine and its strict risk/trigger gates.
    # Only replace the extended-hours data window and cutoff.
    os.environ["LIVE_EXTENDED_CLOSE"] = "04:00"
    engine._fetch_extended_bars = _overnight_bars
    print("LIVE_OVERNIGHT | enabled=True | window=20:00-04:00 ET | provider=yfinance", flush=True)
    return engine.run_live_alerts_extended_hours(base_path)


if __name__ == "__main__":
    raise SystemExit(run_live_alerts_overnight())
