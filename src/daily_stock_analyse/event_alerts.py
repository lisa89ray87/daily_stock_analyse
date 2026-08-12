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
    """Detect session-appropriate early-warning events without changing trade eligibility.

    Regular session uses regular VWAP/opening range. Pre-market, after-hours, and
    overnight use the freshest extended-hours bars plus a session VWAP/range so
    missing regular-session indicators do not automatically invalidate an alert.
    """
    md = analysis.market_data
    session_state = getattr(md, "session_state", "CLOSED")
    extended_session = session_state in {"AFTER_HOURS", "PRE_MARKET", "OVERNIGHT"}
    if extended_session and getattr(md, "extended_intraday_bars", None):
        bars = _bars(md.extended_intraday_bars)
    else:
        bars = _bars(md.intraday_bars)

    if len(bars) < 2:
        return []

    events: list[EventAlert] = []
    last, prev = bars[-1], bars[-2]
    price = float(last["close"])
    prev_close = float(prev["close"])
    session_name = _session_label(session_state)

    price_change = ((price / prev_close) - 1.0) * 100.0 if prev_close else None
    threshold = _float(config, "event_alert_price_change_pct", 2.0)
    if price_change is not None and abs(price_change) >= threshold:
        direction = "BULLISH" if price_change > 0 else "BEARISH"
        events.append(EventAlert(
            analysis.symbol, "PRICE_CHANGE", direction, price,
            f"{session_name} price change {price_change:+.2f}% exceeded {threshold:.2f}%",
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
                f"{session_name} volume {current_volume / avg_volume:.2f}x recent session average",
                "HIGH" if current_volume / avg_volume >= volume_multiplier * 1.5 else "MEDIUM",
                f"VOLUME_SPIKE:{session_state}",
            ))

    closes = [float(b["close"]) for b in bars]
    rsi_period = 14
    rsi_prev = _rsi(closes[:-1], rsi_period)
    rsi_now = _rsi(closes, rsi_period)
    if rsi_prev is not None and rsi_now is not None:
        rsi_high = _float(config, "event_alert_rsi_high", 70.0)
        rsi_low = _float(config, "event_alert_rsi_low", 30.0)
        if rsi_prev < rsi_high <= rsi_now:
            events.append(EventAlert(analysis.symbol, "RSI_THRESHOLD", "BEARISH", price,
                f"{session_name} RSI crossed above {rsi_high:.0f} ({rsi_now:.1f})", "MEDIUM", f"RSI_HIGH_CROSS:{session_state}"))
        elif rsi_prev > rsi_low >= rsi_now:
            events.append(EventAlert(analysis.symbol, "RSI_THRESHOLD", "BULLISH", price,
                f"{session_name} RSI crossed below {rsi_low:.0f} ({rsi_now:.1f})", "MEDIUM", f"RSI_LOW_CROSS:{session_state}"))

    macd_prev, signal_prev = _macd_pair(closes[:-1])
    macd_now, signal_now = _macd_pair(closes)
    if None not in (macd_prev, signal_prev, macd_now, signal_now):
        if macd_prev <= signal_prev and macd_now > signal_now:
            events.append(EventAlert(analysis.symbol, "MACD_CROSS", "BULLISH", price,
                f"{session_name} MACD bullish cross ({macd_now:.4f} > {signal_now:.4f})", "HIGH", f"MACD_BULLISH_CROSS:{session_state}"))
        elif macd_prev >= signal_prev and macd_now < signal_now:
            events.append(EventAlert(analysis.symbol, "MACD_CROSS", "BEARISH", price,
                f"{session_name} MACD bearish cross ({macd_now:.4f} < {signal_now:.4f})", "HIGH", f"MACD_BEARISH_CROSS:{session_state}"))

    for period in (20, 50):
        if len(bars) > period:
            ma_prev = sum(float(b["close"]) for b in bars[-period-1:-1]) / period
            ma_now = sum(float(b["close"]) for b in bars[-period:]) / period
            if prev_close <= ma_prev and price > ma_now:
                events.append(EventAlert(analysis.symbol, "MA_CROSS", "BULLISH", price,
                    f"{session_name} price crossed above SMA{period} ({ma_now:.2f})", "MEDIUM", f"SMA{period}_BULLISH_CROSS:{session_state}"))
            elif prev_close >= ma_prev and price < ma_now:
                events.append(EventAlert(analysis.symbol, "MA_CROSS", "BEARISH", price,
                    f"{session_name} price crossed below SMA{period} ({ma_now:.2f})", "MEDIUM", f"SMA{period}_BEARISH_CROSS:{session_state}"))

    if session_state == "US_REGULAR":
        levels = [("VWAP", md.vwap), ("OPENING_RANGE_HIGH", md.opening_range_high), ("OPENING_RANGE_LOW", md.opening_range_low)]
    else:
        session_vwap = _session_vwap(bars)
        session_high = max(float(b["high"]) for b in bars)
        session_low = min(float(b["low"]) for b in bars)
        levels = [("EXTENDED_VWAP", session_vwap), ("EXTENDED_SESSION_HIGH", session_high), ("EXTENDED_SESSION_LOW", session_low)]

    for name, level in levels:
        if level is None:
            continue
        level = float(level)
        if prev_close <= level < price:
            events.append(EventAlert(analysis.symbol, "PRICE_CROSS", "BULLISH", price,
                f"{session_name} price crossed above {name} {level:.2f}", "HIGH" if "SESSION" in name else "MEDIUM",
                f"{name}_BULLISH_CROSS:{session_state}"))
        elif prev_close >= level > price:
            events.append(EventAlert(analysis.symbol, "PRICE_CROSS", "BEARISH", price,
                f"{session_name} price crossed below {name} {level:.2f}", "HIGH" if "SESSION" in name else "MEDIUM",
                f"{name}_BEARISH_CROSS:{session_state}"))

    return events


def _session_label(session_state: str) -> str:
    return {
        "US_REGULAR": "REGULAR",
        "PRE_MARKET": "PRE-MARKET",
        "AFTER_HOURS": "AFTER-HOURS",
        "OVERNIGHT": "OVERNIGHT",
    }.get(session_state, session_state)


def _bars(raw: list[dict]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for bar in raw or []:
        try:
            out.append({
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
                "high": float(bar.get("high", bar["close"])),
                "low": float(bar.get("low", bar["close"])),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _session_vwap(bars: list[dict[str, float]]) -> float | None:
    total_volume = sum(max(0.0, b["volume"]) for b in bars)
    if total_volume <= 0:
        return None
    total = sum(((b["high"] + b["low"] + b["close"]) / 3.0) * max(0.0, b["volume"]) for b in bars)
    return total / total_volume


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
