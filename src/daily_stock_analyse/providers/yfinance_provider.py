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
        high = hist["High"].dropna() if "High" in hist else pd.Series(dtype=float)
        low = hist["Low"].dropna() if "Low" in hist else pd.Series(dtype=float)

        # Core analytics are based on one internally consistent unadjusted daily series.
        md.price = float(close.iloc[-1])
        md.regular_price = md.price
        md.previous_close = float(close.iloc[-2]) if len(close) >= 2 else None
        md.overnight_reference_price = md.previous_close
        md.latest_extended_price = md.price
        md.latest_extended_session = "REGULAR"

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

        md.atr14 = _compute_atr(high, low, close, 14)

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

        if md.resistance and md.price and md.price > md.resistance * 1.002:
            md.breakout_state = "BREAKOUT"
        elif md.support and md.price and md.price < md.support * 0.998:
            md.breakout_state = "BREAKDOWN"
        elif md.resistance and md.price and md.price > md.resistance * 0.995:
            md.breakout_state = "NEAR BREAKOUT"
        elif md.support and md.price and md.price < md.support * 1.005:
            md.breakout_state = "NEAR BREAKDOWN"
        else:
            md.breakout_state = "NO CLEAR BREAK"

        fast_info = ticker.fast_info or {}
        info = ticker.info or {}

        md.premarket_price = _pick_float(
            _float_or_none(fast_info.get("pre_market_price")),
            _float_or_none(info.get("preMarketPrice")),
        )
        md.after_hours_price = _pick_float(
            _float_or_none(fast_info.get("post_market_price")),
            _float_or_none(info.get("postMarketPrice")),
        )
        md.premarket_volume = _pick_float(
            _float_or_none(fast_info.get("pre_market_volume")),
            _float_or_none(info.get("preMarketVolume")),
        )

        if md.premarket_price is not None:
            md.latest_extended_price = md.premarket_price
            md.latest_extended_session = "PREMARKET"
        elif md.after_hours_price is not None:
            md.latest_extended_price = md.after_hours_price
            md.latest_extended_session = "AFTER_HOURS"

        if md.overnight_reference_price and md.latest_extended_price:
            md.gap_pct = ((md.latest_extended_price - md.overnight_reference_price) / md.overnight_reference_price) * 100.0
        if md.overnight_reference_price and md.premarket_price:
            md.premarket_change_pct = ((md.premarket_price - md.overnight_reference_price) / md.overnight_reference_price) * 100.0

        md.overnight_info = (
            f"Overnight reference (previous regular close): {md.overnight_reference_price:.2f}"
            if isinstance(md.overnight_reference_price, (int, float))
            else "UNAVAILABLE"
        )
        md.premarket_info = (
            f"Premarket: {md.premarket_price:.2f}"
            if isinstance(md.premarket_price, (int, float))
            else "UNAVAILABLE"
        )

        day_high = _pick_float(_float_or_none(fast_info.get("day_high")), _float_or_none(info.get("dayHigh")))
        day_low = _pick_float(_float_or_none(fast_info.get("day_low")), _float_or_none(info.get("dayLow")))
        if isinstance(day_high, (int, float)) and isinstance(day_low, (int, float)):
            md.regular_session_info = f"Session range: {day_low:.2f} - {day_high:.2f}"
        else:
            md.regular_session_info = "UNAVAILABLE"

        intraday = ticker.history(period="1d", interval="5m", prepost=True, auto_adjust=False)
        if not intraday.empty and "Close" in intraday and "Volume" in intraday:
            intraday = intraday.dropna(subset=["Close", "Volume"])
            if not intraday.empty:
                price_x_vol = intraday["Close"] * intraday["Volume"]
                cum_vol = intraday["Volume"].cumsum()
                vwap_series = price_x_vol.cumsum() / cum_vol.replace(0, pd.NA)
                if not vwap_series.dropna().empty:
                    md.vwap = float(vwap_series.dropna().iloc[-1])

                regular = intraday.between_time("09:30", "10:00")
                if not regular.empty and "High" in regular and "Low" in regular:
                    md.opening_range_high = float(regular["High"].max())
                    md.opening_range_low = float(regular["Low"].min())

                md.intraday_timestamp = datetime.now(UTC).isoformat()

        now_ts = datetime.now(UTC).isoformat()
        md.regular_session_timestamp = now_ts
        md.premarket_timestamp = now_ts if md.premarket_price is not None else None
        md.after_hours_timestamp = now_ts if md.after_hours_price is not None else None
        md.data_timestamp = now_ts

        # Detect potential scale inconsistencies from alternate quote sources.
        info_prev_close = _pick_float(_float_or_none(fast_info.get("previous_close")), _float_or_none(info.get("previousClose")))
        if info_prev_close and md.overnight_reference_price:
            rel_diff = abs(info_prev_close - md.overnight_reference_price) / max(1e-9, md.overnight_reference_price)
            if rel_diff > 0.30:
                md.delayed_note = "Latest available provider data may include corporate-action scale differences."

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


def _pick_float(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _float_or_none(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


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


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> float | None:
    if high.empty or low.empty or close.empty or len(close) <= period:
        return None
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean().dropna()
    if atr.empty:
        return None
    return float(atr.iloc[-1])
