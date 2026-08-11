from __future__ import annotations

from datetime import UTC, datetime

from .config import AppConfig
from .market_hours import apply_session_aware_market_data, utc_now
from .models import BattlePlan, CatalystEvent, DataQuality, IntelligenceBlock, StockAnalysis
from .scoring import decide_signal, score_stock


def analyze_symbol(
    symbol: str,
    cfg: AppConfig,
    regime_label: str,
    sector_strength: float | None,
    market_provider,
    news_provider,
    now_utc: datetime | None = None,
) -> StockAnalysis:
    md = market_provider.get_market_data(symbol)
    apply_session_aware_market_data(
        md,
        now_utc or utc_now(),
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )
    md.data_session = md.session_state
    md.data_source = md.selected_data_source
    md.quote_timestamp = md.data_timestamp
    md.is_extended_hours = md.selected_price_session in {"PREMARKET", "AFTER_HOURS"}
    md.rvol_session = "REGULAR_SESSION" if md.live_regular_session else md.session_state
    md.rvol_context_note = (
        "Regular-session intraday RVOL context"
        if md.live_regular_session
        else f"{md.session_state} volume context is not equivalent to U.S. regular-session RVOL"
    )

    if cfg.enable_news:
        intelligence = news_provider.get_news(symbol)
    else:
        intelligence = IntelligenceBlock(
            facts=["NEWS_DISABLED_BY_CONFIG"],
            interpretation=["Proceed with technical-only analysis"],
            upcoming_catalysts=["NEWS_DISABLED_BY_CONFIG"],
            news_available=False,
            catalyst_status="NEWS_DISABLED",
        )

    intelligence = apply_news_lookback(intelligence, cfg.news_lookback_hours)

    if not intelligence.upcoming_catalysts:
        if intelligence.news_available:
            intelligence.catalyst_status = "NO_MATERIAL_CATALYST"
            intelligence.upcoming_catalysts = ["NO_MATERIAL_CATALYST"]
        else:
            intelligence.catalyst_status = "NO_RECENT_NEWS"
            intelligence.upcoming_catalysts = ["NO_RECENT_NEWS"]

    score = score_stock(md, intelligence, cfg.score_weights)
    decision = decide_signal(score, md, cfg, regime_label, sector_strength)

    risk_class = "UNKNOWN"
    if md.volatility_20d is not None:
        if md.volatility_20d < 0.25:
            risk_class = "LOW"
        elif md.volatility_20d < 0.45:
            risk_class = "MEDIUM"
        else:
            risk_class = "HIGH"

    battle_plan = build_battle_plan(md, decision.signal)

    premarket_available = has_premarket_data(md)

    warnings = build_data_quality_warnings(symbol, md)
    data_quality = DataQuality(
        price_available=md.price is not None,
        intraday_available=md.vwap is not None or md.opening_range_high is not None,
        premarket_available=premarket_available,
        volume_available=md.volume is not None,
        timestamp_available=md.data_timestamp is not None,
        provider=md.provider,
        warnings=warnings,
    )
    md.data_quality = "OK" if not warnings else "WARNINGS"

    return StockAnalysis(
        symbol=symbol,
        name=display_name(symbol),
        signal=decision.signal,
        trading_horizon=decision.trading_horizon,
        direction_bias=decision.direction_bias,
        market_alignment=decision.market_alignment,
        setup_score=decision.setup_score,
        day_trade_candidate=decision.day_trade_candidate,
        candidate_score=decision.candidate_score,
        candidate_status=decision.candidate_status,
        confirmation_needed=decision.confirmation_needed,
        confidence=decision.confidence,
        one_liner=f"{display_name(symbol)} is rated {decision.signal} based on latest available technical/news inputs.",
        main_reason=decision.reason,
        risk_classification=risk_class,
        market_data=md,
        intelligence=intelligence,
        battle_plan=battle_plan,
        score=score,
        data_quality=data_quality,
        source_flags={
            "market_data_available": md.price is not None,
            "news_available": intelligence.news_available,
            "intraday_available": md.vwap is not None or md.opening_range_high is not None,
            "premarket_available": premarket_available,
            "live_data_required": md.live_data_required,
            "extended_hours_used": md.extended_hours_used,
        },
    )


def apply_news_lookback(intelligence: IntelligenceBlock, lookback_hours: int) -> IntelligenceBlock:
    if lookback_hours <= 0 or not intelligence.structured_catalysts:
        return intelligence
    cutoff = datetime.now(UTC).timestamp() - (lookback_hours * 3600)
    filtered: list[CatalystEvent] = []
    for item in intelligence.structured_catalysts:
        if not item.published_at:
            filtered.append(item)
            continue
        try:
            ts = datetime.fromisoformat(item.published_at).timestamp()
        except ValueError:
            filtered.append(item)
            continue
        if ts >= cutoff:
            filtered.append(item)
    filtered = _sort_catalysts(filtered)
    intelligence.structured_catalysts = filtered
    intelligence.facts = [_format_catalyst_fact(x) for x in filtered]

    if not filtered:
        intelligence.news_available = False
        intelligence.facts = ["NO_RECENT_NEWS"]
        intelligence.catalyst_status = "NO_RECENT_NEWS"
        intelligence.upcoming_catalysts = ["NO_RECENT_NEWS"]
        return intelligence

    material = [x for x in filtered if x.category != "NONE"]
    if material:
        intelligence.catalyst_status = "CATALYST_IDENTIFIED"
        intelligence.upcoming_catalysts = [_format_catalyst_summary(x) for x in material[:3]]
    elif intelligence.news_available:
        intelligence.catalyst_status = "NO_MATERIAL_CATALYST"
        intelligence.upcoming_catalysts = ["NO_MATERIAL_CATALYST"]
    return intelligence


def collect_daily_catalysts(analyses: list[StockAnalysis]) -> list[CatalystEvent]:
    items: list[CatalystEvent] = []
    for analysis in analyses:
        for event in analysis.intelligence.structured_catalysts:
            if event.category == "NONE" or not event.headline.strip():
                continue
            items.append(event)
    return _sort_catalysts(items)[:25]


def _importance_rank(importance: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(importance, 0)


def _published_sort_value(published_at: str | None) -> float:
    if not published_at:
        return 0.0
    try:
        return datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _sort_catalysts(items: list[CatalystEvent]) -> list[CatalystEvent]:
    return sorted(
        items,
        key=lambda item: (
            _importance_rank(item.importance),
            item.category != "OTHER",
            _published_sort_value(item.published_at),
        ),
        reverse=True,
    )


def _format_catalyst_fact(event: CatalystEvent) -> str:
    source = event.source or "Unknown"
    return f"{source}: {event.headline}"


def _format_catalyst_summary(event: CatalystEvent) -> str:
    source = f" | {event.source}" if event.source else ""
    return f"{event.category} | {event.catalyst_direction}{source} | {event.headline}"


def display_name(symbol: str) -> str:
    if symbol.upper() == "SKHY":
        return "SK hynix"
    if symbol.upper() == "SNDK":
        return "SanDisk"
    return symbol


def has_premarket_data(md) -> bool:
    return md.premarket_price is not None or md.latest_extended_session == "PREMARKET"


def has_after_hours_data(md) -> bool:
    return md.after_hours_price is not None or md.latest_extended_session == "AFTER_HOURS"


def has_usable_selected_price(md) -> bool:
    return md.price is not None and md.selected_data_source != "UNAVAILABLE"


def build_data_quality_warnings(symbol: str, md) -> list[str]:
    warnings: list[str] = []
    premarket_available = has_premarket_data(md)
    after_hours_available = has_after_hours_data(md)
    usable_selected_price = has_usable_selected_price(md)

    if md.session_state == "PRE_MARKET" and not premarket_available and not usable_selected_price:
        warnings.append("PREMARKET_UNAVAILABLE")
    if md.live_data_required and md.vwap is None and md.opening_range_high is None:
        warnings.append("INTRADAY_UNAVAILABLE")
    if not usable_selected_price:
        warnings.append("EXTENDED_HOURS_UNAVAILABLE")
    if md.volume is None:
        warnings.append("VOLUME_UNAVAILABLE")
    if md.data_timestamp is None:
        warnings.append("STALE_DATA")
    if symbol.upper() == "SNDK" and (md.price is None or md.data_timestamp is None):
        warnings.append("SNDK_DATA_LIMITATION")
    return warnings


def build_battle_plan(md, signal: str) -> BattlePlan:
    support = f"{md.support:.2f}" if md.support is not None else "UNAVAILABLE"
    resistance = f"{md.resistance:.2f}" if md.resistance is not None else "UNAVAILABLE"

    entry = "Exact entry level: UNAVAILABLE"
    target = "UNAVAILABLE"
    invalidation = "UNAVAILABLE"
    unavailable_reason = "Intraday resistance unavailable from provider. Wait for live market confirmation."

    entry_trigger_price: float | None = None
    confirmation_level: float | None = None
    invalidation_price: float | None = None
    target_1: float | None = None
    target_2: float | None = None

    if md.price is not None and md.support is not None and md.resistance is not None:
        unavailable_reason = None
        if signal == "LONG":
            entry_trigger_price = float(md.resistance)
            confirmation_level = float(md.resistance)
            invalidation_price = float(md.support)
            if md.atr14 is not None:
                target_1 = float(md.resistance + md.atr14)
                target_2 = float(md.resistance + (2 * md.atr14))
            entry = f"Break above {md.resistance:.2f}"
            target = (
                f"Target 1 {target_1:.2f}, Target 2 {target_2:.2f}"
                if target_1 is not None and target_2 is not None
                else "Exact target levels: UNAVAILABLE"
            )
            invalidation = f"Below {md.support:.2f}"
        elif signal == "SHORT":
            entry_trigger_price = float(md.support)
            confirmation_level = float(md.support)
            invalidation_price = float(md.resistance)
            if md.atr14 is not None:
                target_1 = float(md.support - md.atr14)
                target_2 = float(md.support - (2 * md.atr14))
            entry = f"Break below {md.support:.2f}"
            target = (
                f"Target 1 {target_1:.2f}, Target 2 {target_2:.2f}"
                if target_1 is not None and target_2 is not None
                else "Exact target levels: UNAVAILABLE"
            )
            invalidation = f"Above {md.resistance:.2f}"
        else:
            entry = "Exact entry level: UNAVAILABLE"
            target = "No active target"
            invalidation = "Invalid if setup confirmation never appears"
            unavailable_reason = "Signal is not confirmed. Wait for live market confirmation."

    rr = "UNAVAILABLE"
    if md.support is not None and md.resistance is not None and md.price is not None:
        downside = max(0.01, md.price - md.support)
        upside = max(0.01, md.resistance - md.price)
        rr = f"Approx upside/downside ratio {upside/downside:.2f}"

    return BattlePlan(
        bullish_scenario="Price holds support with improving relative volume and trend continuation",
        bearish_scenario="Price loses support or fails at resistance with heavy sell volume",
        key_support=support,
        key_resistance=resistance,
        entry_area=entry,
        target_area=target,
        invalidation=invalidation,
        risk_reward_assessment=rr,
        entry_trigger_price=entry_trigger_price,
        confirmation_level=confirmation_level,
        invalidation_price=invalidation_price,
        target_1=target_1,
        target_2=target_2,
        level_unavailable_reason=unavailable_reason,
    )
