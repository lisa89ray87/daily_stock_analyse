from __future__ import annotations

from dataclasses import dataclass

from .models import IntelligenceBlock, MarketData, ScoreBreakdown


@dataclass
class SignalDecision:
    signal: str
    confidence: str
    reason: str


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_rsi_for_long(rsi: float | None) -> float:
    if rsi is None:
        return 0.0
    if rsi < 30:
        return 0.6
    if rsi > 70:
        return -0.6
    return _clip((rsi - 50.0) / 25.0)


def _normalize_rsi_for_short(rsi: float | None) -> float:
    return -_normalize_rsi_for_long(rsi)


def score_stock(
    market_data: MarketData,
    intelligence: IntelligenceBlock,
    weights: dict[str, float],
) -> ScoreBreakdown:
    trend_component = 0.0
    if market_data.sma20 and market_data.sma50 and market_data.price:
        if market_data.price > market_data.sma20 > market_data.sma50:
            trend_component = 0.8
        elif market_data.price < market_data.sma20 < market_data.sma50:
            trend_component = -0.8

    momentum_component = _normalize_rsi_for_long(market_data.rsi14)

    volume_component = 0.0
    if market_data.relative_volume is not None:
        volume_component = _clip((market_data.relative_volume - 1.0) / 1.5)

    rs_component = 0.0
    if market_data.day_change_pct is not None:
        rs_component = _clip(market_data.day_change_pct / 5.0)

    news_component = 0.0
    if intelligence.facts:
        positives = sum(1 for x in intelligence.facts if any(k in x.lower() for k in ["beat", "upgrade", "growth", "contract", "expansion"]))
        negatives = sum(1 for x in intelligence.facts if any(k in x.lower() for k in ["miss", "downgrade", "lawsuit", "probe", "delay"]))
        news_component = _clip((positives - negatives) / max(1, len(intelligence.facts)))

    catalyst_component = 0.3 if intelligence.upcoming_catalysts else 0.0

    rr_component = 0.0
    if market_data.support and market_data.resistance and market_data.price:
        downside = max(0.01, market_data.price - market_data.support)
        upside = max(0.01, market_data.resistance - market_data.price)
        rr_component = _clip((upside / downside - 1.0) / 2.0)

    components = {
        "trend": trend_component,
        "momentum": momentum_component,
        "volume": volume_component,
        "relative_strength": rs_component,
        "fundamentals_news": news_component,
        "catalyst_event": catalyst_component,
        "risk_reward": rr_component,
    }

    total_long = 0.0
    for key, weight in weights.items():
        total_long += weight * components.get(key, 0.0)

    short_components = {
        **components,
        "momentum": _normalize_rsi_for_short(market_data.rsi14),
        "trend": -components["trend"],
        "relative_strength": -components["relative_strength"],
        "risk_reward": -components["risk_reward"],
        "fundamentals_news": -components["fundamentals_news"],
    }

    total_short = 0.0
    for key, weight in weights.items():
        total_short += weight * short_components.get(key, 0.0)

    net = total_long - total_short
    return ScoreBreakdown(
        total=net,
        long_score=total_long,
        short_score=total_short,
        components=components,
        weights=weights,
    )


def decide_signal(score: ScoreBreakdown) -> SignalDecision:
    spread = score.long_score - score.short_score
    strength = max(score.long_score, score.short_score)

    if strength < 0.15:
        return SignalDecision("NO TRADE", "LOW", "Evidence is weak or mixed")

    if spread >= 0.20:
        confidence = "HIGH" if score.long_score >= 0.45 else "MEDIUM"
        return SignalDecision("LONG", confidence, "Bullish factors outweigh bearish factors")

    if spread <= -0.20:
        confidence = "HIGH" if score.short_score >= 0.45 else "MEDIUM"
        return SignalDecision("SHORT", confidence, "Bearish factors outweigh bullish factors")

    if strength >= 0.25:
        return SignalDecision("HOLD", "LOW", "Setup has signal but lacks directional clarity")

    return SignalDecision("NO TRADE", "LOW", "No high-conviction setup")
