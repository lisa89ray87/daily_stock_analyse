from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from ..models import CatalystEvent, IntelligenceBlock, MarketData
from .base import MarketDataProvider, NewsProvider


class YFinanceMarketDataProvider(MarketDataProvider):
    provider_name = "yfinance"
    market_tz = ZoneInfo("America/New_York")

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

        intraday = ticker.history(period="1d", interval="5m", prepost=False, auto_adjust=False)
        intraday_hist = ticker.history(period="30d", interval="5m", prepost=False, auto_adjust=False)
        bars_today = _prepare_regular_session_bars(intraday, self.market_tz)

        if not bars_today.empty:
            md.intraday_bars = _bars_to_payload(bars_today)
            md.intraday_timestamp = datetime.now(UTC).isoformat()

            md.price = float(bars_today["Close"].iloc[-1])
            md.regular_price = md.price

            price_x_vol = bars_today["Close"] * bars_today["Volume"]
            cum_vol = bars_today["Volume"].cumsum()
            vwap_series = price_x_vol.cumsum() / cum_vol.replace(0, pd.NA)
            if not vwap_series.dropna().empty:
                md.vwap = float(vwap_series.dropna().iloc[-1])

            regular = bars_today.between_time("09:30", "10:00")
            if not regular.empty and "High" in regular and "Low" in regular:
                md.opening_range_high = float(regular["High"].max())
                md.opening_range_low = float(regular["Low"].min())

            rvol_value, rvol_quality, rvol_note = _time_normalized_intraday_rvol(bars_today, intraday_hist, self.market_tz)
            md.intraday_rvol = rvol_value
            md.intraday_rvol_quality = rvol_quality
            md.intraday_rvol_note = rvol_note

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
        out.catalyst_status = "UNAVAILABLE"

        try:
            news = ticker.news or []
        except Exception:
            out.news_available = False
            out.facts.append("NO_RECENT_NEWS")
            out.interpretation.append("Proceed with technical-only analysis")
            out.catalyst_status = "NO_RECENT_NEWS"
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            return out

        if not news:
            out.news_available = False
            out.facts.append("NO_RECENT_NEWS")
            out.interpretation.append("Catalyst visibility is low")
            out.catalyst_status = "NO_RECENT_NEWS"
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            return out

        candidates = _extract_valid_news_items(symbol, news, limit=limit)
        if not candidates:
            out.news_available = False
            out.facts.append("NO_RECENT_NEWS")
            out.interpretation.append("No recent usable provider headlines were available")
            out.catalyst_status = "NO_RECENT_NEWS"
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            return out

        for item in candidates:
            event = _classify_catalyst(symbol, item)
            out.facts.append(f"{event.source}: {event.headline}")
            out.structured_catalysts.append(event)

        material = [x for x in out.structured_catalysts if x.category != "NONE"]
        if material:
            out.catalyst_status = "CATALYST_IDENTIFIED"
            out.upcoming_catalysts = [
                f"{x.category} | {x.catalyst_direction} | {x.source} | {x.headline}"
                for x in material[:3]
            ]
        else:
            out.catalyst_status = "NO_MATERIAL_CATALYST"
            out.upcoming_catalysts = ["NO_MATERIAL_CATALYST"]

        out.interpretation.append("News reflects available provider headlines only")
        return out


_PLACEHOLDER_TITLES = {"untitled", "unknown", "n/a", "na", "none"}
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _extract_valid_news_items(symbol: str, news_items: list[dict], limit: int) -> list[dict]:
    normalized: list[tuple[int, int, dict]] = []
    for item in news_items:
        cleaned = _normalize_news_item(item)
        if cleaned is None:
            continue
        title = cleaned["title"]
        publish_ts = cleaned.get("providerPublishTime")
        relevance = 1 if symbol.upper() in title.upper() else 0
        recency = int(publish_ts) if isinstance(publish_ts, (int, float)) else 0
        normalized.append((relevance, recency, cleaned))

    normalized.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _, _, item in normalized[: max(1, limit)]]


def _normalize_news_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = _usable_headline(item.get("title"))
    if title is None:
        return None

    publisher = str(item.get("publisher") or "Unknown").strip() or "Unknown"
    publish_ts = item.get("providerPublishTime")
    cleaned = {
        "title": title,
        "publisher": publisher,
        "providerPublishTime": publish_ts,
    }

    url = _safe_news_url(item)
    if url is not None:
        cleaned["link"] = url
    return cleaned


def _usable_headline(raw_title) -> str | None:
    if not isinstance(raw_title, str):
        return None
    title = " ".join(raw_title.split()).strip()
    if not title:
        return None
    if title.lower() in _PLACEHOLDER_TITLES:
        return None
    return title


def _safe_news_url(item: dict) -> str | None:
    for key in ["link", "canonicalUrl", "clickThroughUrl", "url"]:
        raw = item.get(key)
        if isinstance(raw, dict):
            raw = raw.get("url") or raw.get("link")
        if isinstance(raw, str):
            candidate = raw.strip()
            if _URL_RE.match(candidate):
                return candidate
    return None


def _classify_catalyst(symbol: str, item: dict) -> CatalystEvent:
    title = (item.get("title") or "").strip()
    publisher = (item.get("publisher") or "Unknown").strip()
    publish_ts = item.get("providerPublishTime")
    published_at = None
    if isinstance(publish_ts, (int, float)):
        published_at = datetime.fromtimestamp(float(publish_ts), tz=UTC).isoformat()

    lower = title.lower()
    category = "NONE"
    direction = "UNKNOWN"
    importance = "LOW"

    if any(k in lower for k in ["lawsuit", "court", "legal", "settlement", "complaint"]):
        category = "LEGAL"
        importance = "HIGH"
    elif any(k in lower for k in ["fda", "regulator", "investigation", "probe", "sec", "antitrust", "approval"]):
        category = "REGULATORY"
        importance = "HIGH"
    elif any(k in lower for k in ["earnings", "eps", "quarter", "q1", "q2", "q3", "q4"]):
        category = "EARNINGS"
        importance = "HIGH"
    elif any(k in lower for k in ["guidance", "outlook", "forecast"]):
        category = "GUIDANCE"
        importance = "HIGH"
    elif any(k in lower for k in ["upgrade", "downgrade", "analyst", "price target", "initiated", "reiterated"]):
        category = "ANALYST"
        importance = "MEDIUM"
    elif any(k in lower for k in ["partnership", "partner", "collaboration", "joint venture", "agreement"]):
        category = "PARTNERSHIP"
        importance = "MEDIUM"
    elif any(k in lower for k in ["merger", "acquisition", "acquire", "buyout", "takeover"]):
        category = "ACQUISITION"
        importance = "HIGH"
    elif any(k in lower for k in ["launch", "product", "release", "platform", "gpu", "cpu"]):
        category = "PRODUCT"
        importance = "MEDIUM"
    elif any(k in lower for k in ["offering", "financing", "debt", "notes", "capital raise", "share sale"]):
        category = "FINANCING"
        importance = "MEDIUM"
    elif any(k in lower for k in ["insider", "ceo sells", "director buys", "insider buying", "insider selling"]):
        category = "INSIDER"
        importance = "MEDIUM"
    elif any(k in lower for k in ["sector", "industry", "chip stock", "semiconductor", "memory market", "foundry"]):
        category = "SEMICONDUCTOR"
        importance = "MEDIUM"
    elif any(k in lower for k in ["macro", "fed", "inflation", "rates", "tariff", "economy", "recession"]):
        category = "MACRO"
        importance = "MEDIUM"
    elif title:
        category = "OTHER"

    bullish_terms = ["beat", "raise", "upgrade", "surge", "growth", "win", "strong", "record", "approval"]
    bearish_terms = ["miss", "cut", "downgrade", "lawsuit", "probe", "fall", "weak", "delay", "recall"]
    if any(k in lower for k in bullish_terms) and not any(k in lower for k in bearish_terms):
        direction = "BULLISH"
    elif any(k in lower for k in bearish_terms) and not any(k in lower for k in bullish_terms):
        direction = "BEARISH"
    elif category == "NONE":
        direction = "UNKNOWN"
    else:
        direction = "NEUTRAL"

    return CatalystEvent(
        symbol=symbol,
        headline=title,
        source=publisher,
        published_at=published_at,
        category=category,
        importance=importance,
        catalyst_direction=direction,
        summary=title,
        confidence="MEDIUM" if category not in {"NONE", "OTHER"} else "LOW",
        url=_safe_news_url(item),
    )


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


def _prepare_regular_session_bars(frame: pd.DataFrame, market_tz: ZoneInfo) -> pd.DataFrame:
    if frame.empty or not {"Open", "High", "Low", "Close", "Volume"}.issubset(frame.columns):
        return pd.DataFrame()

    df = frame.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(UTC)
    df.index = df.index.tz_convert(market_tz)
    regular = df.between_time("09:30", "16:00")
    if regular.empty:
        return pd.DataFrame()

    session_date = regular.index.max().date()
    same_day = regular[regular.index.date == session_date]
    return same_day.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def _bars_to_payload(frame: pd.DataFrame) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    for ts, row in frame.iterrows():
        out.append(
            {
                "ts": ts.isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
        )
    return out


def _time_normalized_intraday_rvol(
    today_bars: pd.DataFrame,
    intraday_hist: pd.DataFrame,
    market_tz: ZoneInfo,
) -> tuple[float | None, str, str]:
    if today_bars.empty or "Volume" not in today_bars:
        return None, "UNAVAILABLE", "No regular-session intraday volume"

    current_tod = today_bars.index.max().time()
    current_cum = float(today_bars["Volume"].sum())
    if current_cum <= 0:
        return None, "UNAVAILABLE", "Current intraday cumulative volume is zero"

    if intraday_hist.empty or "Volume" not in intraday_hist:
        return None, "UNAVAILABLE", "Historical intraday data unavailable"

    hist = intraday_hist.copy()
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize(UTC)
    hist.index = hist.index.tz_convert(market_tz)
    hist = hist.between_time("09:30", "16:00")
    if hist.empty:
        return None, "UNAVAILABLE", "No historical regular-session bars"

    grouped = hist.groupby(hist.index.date)
    today_date = today_bars.index.max().date()
    baseline: list[float] = []

    for day, bars in grouped:
        if day == today_date:
            continue
        upto = bars[bars.index.time <= current_tod]
        if upto.empty:
            continue
        cum = float(upto["Volume"].sum())
        if cum > 0:
            baseline.append(cum)

    if len(baseline) < 5:
        return None, "DATA_LIMITED", "Insufficient historical intraday baseline sessions"

    baseline_avg = sum(baseline) / len(baseline)
    if baseline_avg <= 0:
        return None, "DATA_LIMITED", "Historical intraday baseline volume is zero"

    return current_cum / baseline_avg, "RELIABLE", "Time-normalized intraday RVOL"
