from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .models import IntelligenceBlock, MarketData, ScoreBreakdown


@dataclass
class SignalDecision:
    signal: str
    confidence: str
    reason: str
    market_alignment: str
    setup_score: int
    trading_horizon: str


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_rsi_for_long(rsi: float | None) -> float:
    if rsi is None:
        return 0.0
    if rsi < 30:
        return 0.55
    if rsi > 70:
        return -0.55
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
        volume_component = _clip((market_data.relative_volume - 1.0) / 1.2)

    rs_component = 0.0
    if market_data.day_change_pct is not None:
        rs_component = _clip(market_data.day_change_pct / 4.0)

    news_component = 0.0
    if intelligence.facts:
        positives = sum(
            1
            for x in intelligence.facts
            if any(k in x.lower() for k in ["beat", "upgrade", "growth", "contract", "expansion", "partnership"])
        )
        negatives = sum(
            1
            for x in intelligence.facts
            if any(k in x.lower() for k in ["miss", "downgrade", "lawsuit", "probe", "delay", "cut"])
        )
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


def decide_signal(
    score: ScoreBreakdown,
    market_data: MarketData,
    cfg: AppConfig,
    market_regime_label: str,
) -> SignalDecision:
    if market_data.price is None:
        return SignalDecision(
            signal="NO_TRADE",
            confidence="LOW",
            reason="Price unavailable",
            market_alignment="UNKNOWN",
            setup_score=0,
            trading_horizon="NO_TRADE",
        )

    long_candidate = score.long_score >= cfg.long_threshold
    short_candidate = score.short_score >= cfg.short_threshold

    if long_candidate and (not short_candidate or score.long_score >= score.short_score):
        direction = "LONG"
        strength = score.long_score
    elif short_candidate:
        direction = "SHORT"
        strength = score.short_score
    else:
        direction = "WAIT"
        strength = max(score.long_score, score.short_score)

    alignment = _market_alignment(direction, market_regime_label)
    setup_score = _setup_score(direction, market_data, score, alignment, cfg)

    min_required = cfg.min_setup_score + (8 if alignment == "MARKET_COUNTERTREND" else 0)

    if direction in {"LONG", "SHORT"} and setup_score >= min_required:
        confidence = "HIGH" if setup_score >= 80 else "MEDIUM"
        horizon = _infer_horizon(market_data, setup_score, cfg)
        reason = "Directional setup has multi-factor confirmation"
        return SignalDecision(direction, confidence, reason, alignment, setup_score, horizon)

    if strength >= min(cfg.long_threshold, cfg.short_threshold) * 0.75:
        return SignalDecision(
            signal="WAIT",
            confidence="LOW",
            reason="Setup is developing but lacks confirmation",
            market_alignment=alignment,
            setup_score=setup_score,
            trading_horizon="SWING" if setup_score >= 50 else "NO_TRADE",
        )

    return SignalDecision(
        signal="NO_TRADE",
        confidence="LOW",
        reason="No high-conviction setup",
        market_alignment=alignment,
        setup_score=setup_score,
        trading_horizon="NO_TRADE",
    )


def _market_alignment(direction: str, regime_label: str) -> str:
    if regime_label == "MIXED" or direction in {"WAIT", "NO_TRADE"}:
        return "MARKET_ALIGNED"
    if regime_label == "RISK_ON" and direction == "LONG":
        return "MARKET_ALIGNED"
    if regime_label == "RISK_OFF" and direction == "SHORT":
        return "MARKET_ALIGNED"
    return "MARKET_COUNTERTREND"


def _setup_score(direction: str, md: MarketData, score: ScoreBreakdown, alignment: str, cfg: AppConfig) -> int:
    if direction not in {"LONG", "SHORT"}:
        base_strength = max(score.long_score, score.short_score)
        return max(0, min(100, int(base_strength * 100)))

    total = 0.0

    trend_ok = (direction == "LONG" and md.trend == "UPTREND") or (direction == "SHORT" and md.trend == "DOWNTREND")
    if trend_ok:
        total += 16

    if md.rsi14 is not None:
        if direction == "LONG" and 48 <= md.rsi14 <= 72:
            total += 10
        elif direction == "SHORT" and (md.rsi14 >= 62 or md.rsi14 <= 42):
            total += 10

    if md.relative_volume is not None and md.relative_volume >= cfg.min_relative_volume:
        total += 12

    if alignment == "MARKET_ALIGNED":
        total += 10

    if direction == "LONG" and score.components.get("fundamentals_news", 0.0) > 0:
        total += 8
    if direction == "SHORT" and score.components.get("fundamentals_news", 0.0) < 0:
        total += 8

    if md.atr14 is not None and md.price is not None and md.price > 0:
        atr_pct = md.atr14 / md.price
        if 0.01 <= atr_pct <= 0.08:
            total += 8

    rr = _risk_reward_ratio(md, direction)
    if rr is not None:
        if rr >= 1.5:
            total += 14
        elif rr >= 1.1:
            total += 8

    if direction == "LONG" and md.breakout_state in {"NEAR BREAKOUT", "BREAKOUT"}:
        total += 12
    if direction == "SHORT" and md.breakout_state in {"NEAR BREAKDOWN", "BREAKDOWN"}:
        total += 12

    if md.vwap is not None and md.price is not None:
        if direction == "LONG" and md.price >= md.vwap:
            total += 6
        if direction == "SHORT" and md.price <= md.vwap:
            total += 6

    return max(0, min(100, int(round(total))))


def _infer_horizon(md: MarketData, setup_score: int, cfg: AppConfig) -> str:
    intraday_ready = md.vwap is not None or (md.opening_range_high is not None and md.opening_range_low is not None)
    volume_ready = md.relative_volume is not None and md.relative_volume >= cfg.min_relative_volume
    if setup_score >= cfg.day_trade_threshold and intraday_ready and volume_ready:
        return "DAY_TRADE"
    if setup_score >= cfg.min_setup_score:
        return "SWING"
    return "NO_TRADE"


def _risk_reward_ratio(md: MarketData, direction: str) -> float | None:
    if md.price is None or md.support is None or md.resistance is None:
        return None

    if direction == "LONG":
        risk = max(0.01, md.price - md.support)
        reward = max(0.01, md.resistance - md.price)
    else:
        risk = max(0.01, md.resistance - md.price)
        reward = max(0.01, md.price - md.support)
    return reward / risk
