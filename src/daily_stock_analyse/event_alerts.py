from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import StockAnalysis


@dataclass(frozen=True)
class EventAlert:
    symbol: str
    event_type: str
    direction: str
    price: float | None
    detail: str
    severity: str = "MEDIUM"
    key: str = ""


def detect_event_alerts(analysis: StockAnalysis, config: Any) -> list[EventAlert]:
    """Detect early-warning technical events without changing trade eligibility.

    Regular, pre-market, after-hours, and overnight sessions use the freshest
    provider bars available for event detection. Regular-session VWAP/opening
    range remain reference levels when available; extended bars provide fresh
    price/volume/momentum signals outside regular hours.
    """
    md = analysis.market_data
    session_state = getattr(md, "session_state", "CLOSED")
    if session_state in {"AFTER_HOURS", "PRE_MARKET"} and getattr(md, "extended_intraday_bars", None):
        bars = _bars(md.extended_intraday_bars)
    else:
        bars = _bars(md.intraday_bars)

    if len(bars) < 2:
        return []

    events: list[EventAlert] = []
    last, prev = bars[-1], bars[-2]
    price = float(last["close"])
    prev_close = float(prev["close"])

    price_change = ((price / prev_close) - 1.0) * 100.0 if prev_close else None
    threshold = _float(config, "event_alert_price_change_pct", 2.0)
    if price_change is not None and abs(price_change) >= threshold:
        direction = "BULLISH" if price_change > 0 else "BEARISH"
        events.append(EventAlert(
            analysis.symbol, "PRICE_CHANGE", direction, price,
            f"Intrabar price change {price_change:+.2f}% exceeded {threshold:.2f}%",
            "HIGH" if abs(price_change) >= threshold * 1.5 else "MEDIUM",
            f"PRICE_CHANGE:{direction}:{round(price_change / threshold)}",
        ))

    volumes = [float(b["volume"]) for b in bars[:-1] if float(b["volume"]) > 0]
    volume_multiplier = _float(config, "event_alert_volume_spike_multiplier", 2.0)
    if volumes:
        lookback = volumes[-20:]
        avg_volume = sum(lookback) / len(lookback)
        current_volume = float(last["volume"])
        if avg_volume > 0 and current_volume / avg_volume >= volume_multiplier:
            events.append(EventAlert(
                analysis.symbol, "VOLUME_SPIKE", "NEUTRAL", price,
                f"Volume {current_volume / avg_volume:.2f}x recent intraday average",
                "HIGH" if current_volume / avg_volume >= volume_multiplier * 1.5 else "MEDIUM",
                "VOLUME_SPIKE",
            ))

    rsi_period = 14
    rsi_prev = _rsi([float(b["close"]) for b in bars[:-1]], rsi_period)
    rsi_now = _rsi([float(b["close"]) for b in bars], rsi_period)
    if rsi_prev is not None and rsi_now is not None:
        rsi_high = _float(config, "event_alert_rsi_high", 70.0)
        rsi_low = _float(config, "event_alert_rsi_low", 30.0)
        if rsi_prev < rsi_high <= rsi_now:
            events.append(EventAlert(analysis.symbol, "RSI_THRESHOLD", "BEARISH", price,
                f"RSI crossed above {rsi_high:.0f} ({rsi_now:.1f})", "MEDIUM", "RSI_HIGH_CROSS"))
        elif rsi_prev > rsi_low >= rsi_now:
            events.append(EventAlert(analysis.symbol, "RSI_THRESHOLD", "BULLISH", price,
                f"RSI crossed below {rsi_low:.0f} ({rsi_now:.1f})", "MEDIUM", "RSI_LOW_CROSS"))

    macd_prev, signal_prev = _macd_pair([float(b["close"]) for b in bars[:-1]])
    macd_now, signal_now = _macd_pair([float(b["close"]) for b in bars])
    if None not in (macd_prev, signal_prev, macd_now, signal_now):
        if macd_prev <= signal_prev and macd_now > signal_now:
            events.append(EventAlert(analysis.symbol, "MACD_CROSS", "BULLISH", price,
                f"MACD bullish cross ({macd_now:.4f} > {signal_now:.4f})", "HIGH", "MACD_BULLISH_CROSS"))
        elif macd_prev >= signal_prev and macd_now < signal_now:
            events.append(EventAlert(analysis.symbol, "MACD_CROSS", "BEARISH", price,
                f"MACD bearish cross ({macd_now:.4f} < {signal_now:.4f})", "HIGH", "MACD_BEARISH_CROSS"))

    for period in (20, 50):
        if len(bars) > period:
            ma_prev = sum(float(b["close"]) for b in bars[-period-1:-1]) / period
            ma_now = sum(float(b["close"]) for b in bars[-period:]) / period
            if prev_close <= ma_prev and price > ma_now:
                events.append(EventAlert(analysis.symbol, "MA_CROSS", "BULLISH", price,
                    f"Price crossed above SMA{period} ({ma_now:.2f})", "MEDIUM", f"SMA{period}_BULLISH_CROSS"))
            elif prev_close >= ma_prev and price < ma_now:
                events.append(EventAlert(analysis.symbol, "MA_CROSS", "BEARISH", price,
                    f"Price crossed below SMA{period} ({ma_now:.2f})", "MEDIUM", f"SMA{period}_BEARISH_CROSS"))

    levels = [("VWAP", md.vwap), ("OPENING_RANGE_HIGH", md.opening_range_high), ("OPENING_RANGE_LOW", md.opening_range_low)]
    for name, level in levels:
        if level is None:
            continue
        level = float(level)
        if prev_close <= level < price:
            events.append(EventAlert(analysis.symbol, "PRICE_CROSS", "BULLISH", price,
                f"Price crossed above {name} {level:.2f}", "HIGH" if name != "VWAP" else "MEDIUM",
                f"{name}_BULLISH_CROSS"))
        elif prev_close >= level > price:
            events.append(EventAlert(analysis.symbol, "PRICE_CROSS", "BEARISH", price,
                f"Price crossed below {name} {level:.2f}", "HIGH" if name != "VWAP" else "MEDIUM",
                f"{name}_BEARISH_CROSS"))

    return events


def _bars(raw: list[dict]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for bar in raw or []:
        try:
            out.append({
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _float(config: Any, name: str, default: float) -> float:
    value = getattr(config, name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rsi(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for before, after in zip(values[-period-1:-1], values[-period:]):
        delta = after - before
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _macd_pair(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 35:
        return None, None
    macd_values: list[float] = []
    for i in range(26, len(values)):
        window = values[: i + 1]
        macd_values.append(_ema(window, 12) - _ema(window, 26))
    if len(macd_values) < 9:
        return None, None
    signal = _ema(macd_values[-9:], 9)
    return macd_values[-1], signal
