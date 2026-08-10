from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import yfinance as yf

from ..models import IntelligenceBlock, MarketData
from .base import MarketDataProvider, NewsProvider


class YFinanceMarketDataProvider(MarketDataProvider):
    provider_name = "yfinance"

    def get_market_data(self, symbol: str) -> MarketData:
        ticker = yf.Ticker(symbol)
        md = MarketData(symbol=symbol, provider=self.provider_name)

        hist = ticker.history(period="6mo", interval="1d", auto_adjust=False)
        if hist.empty:
            md.data_timestamp = datetime.now(UTC).isoformat()
            return md

        close = hist["Close"].dropna()
        volume = hist["Volume"].dropna() if "Volume" in hist else pd.Series(dtype=float)
        last_price = float(close.iloc[-1])

        md.price = last_price
        md.sma20 = _safe_series_mean(close, 20)
        md.sma50 = _safe_series_mean(close, 50)
        md.sma200 = _safe_series_mean(close, 200)
        md.rsi14 = _compute_rsi(close, period=14)

        macd_line, macd_signal = _compute_macd(close)
        md.macd = macd_line
        md.macd_signal = macd_signal

        md.day_change_pct = _pct_change(close)
        md.volume = float(volume.iloc[-1]) if not volume.empty else None
        md.avg_volume_20d = _safe_series_mean(volume, 20)
        if md.volume is not None and md.avg_volume_20d:
            md.relative_volume = md.volume / max(1.0, md.avg_volume_20d)

        returns = close.pct_change().dropna()
        if len(returns) >= 20:
            md.volatility_20d = float(returns.tail(20).std() * (252 ** 0.5))

        md.support = _rolling_support(close, 20)
        md.resistance = _rolling_resistance(close, 20)

        if md.sma20 and md.sma50 and md.price:
            if md.price > md.sma20 > md.sma50:
                md.trend = "UPTREND"
                md.recent_structure = "Higher highs / higher lows"
            elif md.price < md.sma20 < md.sma50:
                md.trend = "DOWNTREND"
                md.recent_structure = "Lower highs / lower lows"
            else:
                md.trend = "RANGE"
                md.recent_structure = "Range-bound"

        if md.resistance and md.price and md.price > md.resistance * 0.995:
            md.breakout_state = "NEAR BREAKOUT"
        elif md.support and md.price and md.price < md.support * 1.005:
            md.breakout_state = "NEAR BREAKDOWN"
        else:
            md.breakout_state = "NO CLEAR BREAK"

        info = ticker.fast_info or {}
        premarket = info.get("pre_market_price")
        previous_close = info.get("previous_close")
        day_high = info.get("day_high")
        day_low = info.get("day_low")

        md.overnight_info = (
            f"Previous close: {previous_close:.2f}" if isinstance(previous_close, (int, float)) else "UNAVAILABLE"
        )
        md.premarket_info = (
            f"Pre-market: {premarket:.2f}" if isinstance(premarket, (int, float)) else "UNAVAILABLE"
        )
        if isinstance(day_high, (int, float)) and isinstance(day_low, (int, float)):
            md.regular_session_info = f"Session range: {day_low:.2f} - {day_high:.2f}"
        else:
            md.regular_session_info = "UNAVAILABLE"

        md.regular_session_timestamp = datetime.now(UTC).isoformat()
        md.data_timestamp = datetime.now(UTC).isoformat()
        return md


class YFinanceNewsProvider(NewsProvider):
    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        ticker = yf.Ticker(symbol)
        out = IntelligenceBlock()

        try:
            news = ticker.news or []
        except Exception:
            out.news_available = False
            out.facts.append("News unavailable from provider")
            out.interpretation.append("Proceed with technical-only analysis")
            return out

        if not news:
            out.news_available = False
            out.facts.append("No recent provider news returned")
            out.interpretation.append("Catalyst visibility is low")
            return out

        for item in news[:limit]:
            title = item.get("title") or "Untitled"
            publisher = item.get("publisher") or "Unknown"
            out.facts.append(f"{publisher}: {title}")

        out.interpretation.append("News reflects available provider headlines only")
        return out


def _safe_series_mean(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    return float(series.tail(window).mean())


def _pct_change(series: pd.Series) -> float | None:
    if len(series) < 2:
        return None
    prev = float(series.iloc[-2])
    curr = float(series.iloc[-1])
    if prev == 0:
        return None
    return (curr - prev) / prev * 100.0


def _compute_rsi(series: pd.Series, period: int = 14) -> float | None:
    if len(series) <= period:
        return None
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def _compute_macd(series: pd.Series) -> tuple[float | None, float | None]:
    if len(series) < 35:
        return None, None
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal.iloc[-1])


def _rolling_support(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    return float(series.tail(window).min())


def _rolling_resistance(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    return float(series.tail(window).max())
