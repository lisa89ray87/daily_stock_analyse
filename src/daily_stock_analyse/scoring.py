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
    direction_bias: str
    day_trade_candidate: bool
    candidate_score: int
    candidate_status: str
    confirmation_needed: str


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

    total_long = sum(weights.get(key, 0.0) * components.get(key, 0.0) for key in weights)

    short_components = {
        **components,
        "momentum": _normalize_rsi_for_short(market_data.rsi14),
        "trend": -components["trend"],
        "relative_strength": -components["relative_strength"],
        "risk_reward": -components["risk_reward"],
        "fundamentals_news": -components["fundamentals_news"],
    }
    total_short = sum(weights.get(key, 0.0) * short_components.get(key, 0.0) for key in weights)

    long_points = _build_points(market_data, components, direction="LONG")
    short_points = _build_points(market_data, short_components, direction="SHORT")

    net = total_long - total_short
    return ScoreBreakdown(
        total=net,
        long_score=total_long,
        short_score=total_short,
        components=components,
        weights=weights,
        long_points=long_points,
        short_points=short_points,
    )


def decide_signal(
    score: ScoreBreakdown,
    market_data: MarketData,
    cfg: AppConfig,
    market_regime_label: str,
    market_sector_strength: float | None = None,
) -> SignalDecision:
    if market_data.price is None:
        return SignalDecision(
            signal="NO_TRADE",
            confidence="LOW",
            reason="Price unavailable",
            market_alignment="UNKNOWN",
            setup_score=0,
            trading_horizon="NO_TRADE",
            direction_bias="NEUTRAL",
            day_trade_candidate=False,
            candidate_score=0,
            candidate_status="NO DAY-TRADE CANDIDATES",
            confirmation_needed="Provider price unavailable",
        )

    direction_bias = _determine_bias(score)
    alignment = _market_alignment(direction_bias, market_regime_label)

    long_setup_score = _setup_score("LONG", market_data, score, alignment, cfg, market_sector_strength)
    short_setup_score = _setup_score("SHORT", market_data, score, alignment, cfg, market_sector_strength)

    setup_score = max(long_setup_score, short_setup_score)
    preferred_direction = "LONG" if long_setup_score >= short_setup_score else "SHORT"

    day_trade_candidate, candidate_score = _day_trade_candidate(
        preferred_direction,
        market_data,
        score,
        setup_score,
        cfg,
    )

    failed_breakout = _failed_breakout_short_pattern(market_data)
    if failed_breakout and preferred_direction == "SHORT":
        short_setup_score = min(100, short_setup_score + 8)
        setup_score = max(setup_score, short_setup_score)

    min_required = cfg.min_setup_score + (8 if alignment == "MARKET_COUNTERTREND" else 0)

    if preferred_direction == "LONG" and score.long_score >= cfg.long_threshold and long_setup_score >= min_required:
        signal = "LONG"
    elif preferred_direction == "SHORT" and score.short_score >= cfg.short_threshold and short_setup_score >= min_required:
        signal = "SHORT"
    else:
        signal = "WAIT" if day_trade_candidate or setup_score >= max(45, cfg.day_trade_min_setup_score - 10) else "NO_TRADE"

    trading_horizon = _infer_horizon(market_data, setup_score, day_trade_candidate, cfg)

    if signal == "LONG":
        reason = "LONG confirmed by trend, momentum, and risk/reward alignment"
        confidence = "HIGH" if long_setup_score >= 80 else "MEDIUM"
    elif signal == "SHORT":
        reason = "SHORT confirmed by bearish structure and downside confirmation"
        if failed_breakout:
            reason = "SHORT confirmed by failed breakout structure and downside momentum"
        confidence = "HIGH" if short_setup_score >= 80 else "MEDIUM"
    elif signal == "WAIT":
        reason = _wait_reason(preferred_direction, market_data, score, day_trade_candidate)
        confidence = "LOW"
    else:
        reason = "No high-conviction setup"
        confidence = "LOW"

    if day_trade_candidate and signal in {"WAIT", "NO_TRADE"}:
        candidate_status = "DAY_TRADE CANDIDATE - WAIT FOR LIVE CONFIRMATION"
    elif day_trade_candidate:
        candidate_status = "DAY_TRADE CANDIDATE"
    else:
        candidate_status = "NO DAY-TRADE CANDIDATES"

    confirmation_needed = _confirmation_needed(preferred_direction, market_data)

    return SignalDecision(
        signal=signal,
        confidence=confidence,
        reason=reason,
        market_alignment=alignment,
        setup_score=setup_score,
        trading_horizon=trading_horizon,
        direction_bias=direction_bias,
        day_trade_candidate=day_trade_candidate,
        candidate_score=candidate_score,
        candidate_status=candidate_status,
        confirmation_needed=confirmation_needed,
    )


def _build_points(market_data: MarketData, components: dict[str, float], direction: str) -> dict[str, int]:
    points = {
        "Trend": 18 if (direction == "LONG" and market_data.trend == "UPTREND") or (direction == "SHORT" and market_data.trend == "DOWNTREND") else 0,
        "Momentum": max(0, int(round(abs(components.get("momentum", 0.0)) * 14))),
        "Relative Volume": max(0, int(round(max(0.0, components.get("volume", 0.0)) * 12))),
        "Gap": 10 if market_data.gap_pct is not None and abs(market_data.gap_pct) >= 3.0 else 0,
        "Catalyst": 10 if abs(components.get("fundamentals_news", 0.0)) > 0 else 0,
        "Risk/Reward": max(0, int(round(max(0.0, components.get("risk_reward", 0.0)) * 10))),
    }
    points["Total"] = sum(v for k, v in points.items() if k != "Total")
    return points


def _determine_bias(score: ScoreBreakdown) -> str:
    if score.long_score > score.short_score + 0.05:
        return "LONG_BIAS"
    if score.short_score > score.long_score + 0.05:
        return "SHORT_BIAS"
    return "NEUTRAL"


def _market_alignment(direction_bias: str, regime_label: str) -> str:
    if regime_label == "MIXED" or direction_bias == "NEUTRAL":
        return "MARKET_ALIGNED"
    if regime_label == "RISK_ON" and direction_bias == "LONG_BIAS":
        return "MARKET_ALIGNED"
    if regime_label == "RISK_OFF" and direction_bias == "SHORT_BIAS":
        return "MARKET_ALIGNED"
    return "MARKET_COUNTERTREND"


def _setup_score(
    direction: str,
    md: MarketData,
    score: ScoreBreakdown,
    alignment: str,
    cfg: AppConfig,
    market_sector_strength: float | None,
) -> int:
    total = 0.0

    trend_ok = (direction == "LONG" and md.trend == "UPTREND") or (direction == "SHORT" and md.trend == "DOWNTREND")
    if trend_ok:
        total += 18

    if md.rsi14 is not None:
        if direction == "LONG" and 48 <= md.rsi14 <= 72:
            total += 14
        elif direction == "SHORT" and (md.rsi14 >= 62 or md.rsi14 <= 42):
            total += 14

    if md.relative_volume is not None and md.relative_volume >= cfg.min_relative_volume:
        total += 12

    if md.gap_pct is not None and ((direction == "LONG" and md.gap_pct > 0) or (direction == "SHORT" and md.gap_pct < 0)):
        total += 10

    if alignment == "MARKET_ALIGNED":
        total += 8

    if market_sector_strength is not None and market_sector_strength > 0 and direction == "LONG":
        total += 6
    if market_sector_strength is not None and market_sector_strength < 0 and direction == "SHORT":
        total += 6

    if direction == "LONG" and score.components.get("fundamentals_news", 0.0) > 0:
        total += 10
    if direction == "SHORT" and score.components.get("fundamentals_news", 0.0) < 0:
        total += 10

    rr = _risk_reward_ratio(md, direction)
    if rr is not None:
        if rr >= 1.5:
            total += 10
        elif rr >= 1.1:
            total += 6

    if direction == "LONG" and md.breakout_state in {"NEAR BREAKOUT", "BREAKOUT"}:
        total += 8
    if direction == "SHORT" and md.breakout_state in {"NEAR BREAKDOWN", "BREAKDOWN"}:
        total += 8

    if direction == "SHORT" and _failed_breakout_short_pattern(md):
        total += 8

    return max(0, min(100, int(round(total))))


def _day_trade_candidate(
    preferred_direction: str,
    md: MarketData,
    score: ScoreBreakdown,
    setup_score: int,
    cfg: AppConfig,
) -> tuple[bool, int]:
    unusual_gap = md.gap_pct is not None and abs(md.gap_pct) >= cfg.day_trade_gap_threshold
    unusual_rvol = md.relative_volume is not None and md.relative_volume >= cfg.day_trade_rvol_threshold
    unusual_momentum = md.day_change_pct is not None and abs(md.day_change_pct) >= 3.0
    catalyst = abs(score.components.get("fundamentals_news", 0.0)) > 0

    candidate = unusual_gap or unusual_rvol or unusual_momentum or catalyst

    candidate_score = setup_score
    if unusual_gap:
        candidate_score += 8
    if unusual_rvol:
        candidate_score += 8
    if unusual_momentum:
        candidate_score += 6
    if catalyst:
        candidate_score += 6
    if preferred_direction == "SHORT" and _failed_breakout_short_pattern(md):
        candidate_score += 6

    candidate_score = min(100, candidate_score)
    candidate = candidate and candidate_score >= cfg.day_trade_min_setup_score
    return candidate, candidate_score


def _failed_breakout_short_pattern(md: MarketData) -> bool:
    if md.gap_pct is None or md.price is None:
        return False
    if md.gap_pct < 2.0:
        return False
    if md.breakout_state not in {"NO CLEAR BREAK", "NEAR BREAKDOWN", "BREAKDOWN"}:
        return False
    if md.rsi14 is not None and md.rsi14 > 60:
        return False
    if md.vwap is not None and md.price > md.vwap:
        return False
    return True


def _wait_reason(direction: str, md: MarketData, score: ScoreBreakdown, day_trade_candidate: bool) -> str:
    if direction == "LONG" and (md.relative_volume is None or md.relative_volume < 1.0):
        return "WAIT - bullish trend but relative volume is insufficient."
    if direction == "LONG" and md.breakout_state not in {"NEAR BREAKOUT", "BREAKOUT"}:
        return "WAIT - gap is strong but breakout confirmation is missing."
    if direction == "SHORT" and md.breakout_state not in {"NEAR BREAKDOWN", "BREAKDOWN"}:
        return "WAIT - short setup exists but support has not broken."
    if day_trade_candidate and md.vwap is None and md.opening_range_high is None:
        return "WAIT - data provider does not currently provide intraday confirmation."
    if max(score.long_score, score.short_score) < 0.25:
        return "WAIT - setup strength is below confirmation threshold."
    return "WAIT - setup is developing but lacks confirmation."


def _confirmation_needed(direction: str, md: MarketData) -> str:
    if direction == "LONG":
        if md.vwap is not None:
            return "Break above resistance or reclaim VWAP with sustained volume"
        return "Break above resistance with volume expansion"
    if direction == "SHORT":
        if md.vwap is not None:
            return "Break below support or lose VWAP with increasing sell volume"
        return "Break below support with increasing sell volume"
    return "Monitor for directional breakout or breakdown"


def _infer_horizon(md: MarketData, setup_score: int, day_trade_candidate: bool, cfg: AppConfig) -> str:
    if day_trade_candidate and setup_score >= cfg.day_trade_min_setup_score:
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
