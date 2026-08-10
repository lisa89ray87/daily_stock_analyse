from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ai_analysis import generate_ai_overlay
from .config import AppConfig, load_config
from .email_provider import EmailPayload, ResendEmailProvider
from .market import build_market_regime
from .market_hours import next_us_market_open_malaysia
from .models import BattlePlan, DailyAnalysisReport, DataQuality, IntelligenceBlock, StockAnalysis
from .providers import YFinanceMarketDataProvider, YFinanceNewsProvider
from .reporting import render_html, render_markdown
from .scoring import decide_signal, score_stock
from .selector import select_dynamic_opportunities


def run_analysis(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    cfg = load_config(repo_root)

    market_provider = YFinanceMarketDataProvider()
    news_provider = YFinanceNewsProvider()

    market_regime = build_market_regime()
    sector_strength = market_regime.indicators.get("semiconductor_etf_change_pct")

    all_symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    analyses: list[StockAnalysis] = []
    errors: list[str] = []

    for symbol in all_symbols:
        try:
            analyses.append(_analyze_symbol(symbol, cfg, market_regime.label, sector_strength, market_provider, news_provider))
        except Exception as exc:
            errors.append(f"{symbol}: analysis failed ({exc})")
            analyses.append(_unavailable_analysis(symbol, f"Provider failure: {exc}"))

    dynamic = select_dynamic_opportunities(
        analyses,
        cfg.fixed_watchlist,
        top_n=cfg.dynamic_count,
        min_setup_score=max(40, cfg.min_setup_score - 10),
        min_relative_volume=cfg.min_relative_volume,
    )

    selected_symbols = {x.symbol for x in dynamic} | set(cfg.fixed_watchlist)
    selected_analyses = [x for x in analyses if x.symbol in selected_symbols]

    bullish_ranked = sorted(selected_analyses, key=lambda x: x.score.long_score, reverse=True)[:3]
    bearish_ranked = sorted(selected_analyses, key=lambda x: x.score.short_score, reverse=True)[:3]

    day_trade_watchlist = sorted(
        [x for x in selected_analyses if x.day_trade_candidate],
        key=lambda x: (x.candidate_score, x.setup_score, x.market_data.relative_volume or 0.0),
        reverse=True,
    )[:5]

    best_long = _best_for_direction(selected_analyses, "LONG")
    best_short = _best_for_direction(selected_analyses, "SHORT")
    best_overall = _best_overall(selected_analyses)

    generated_at_utc = datetime.now(UTC)
    generated_at_my = generated_at_utc.astimezone(ZoneInfo(cfg.morning_report_timezone))
    next_open_my = next_us_market_open_malaysia(
        generated_at_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        malaysia_timezone=cfg.morning_report_timezone,
    )

    best_long, closest_long = _best_for_direction(selected_analyses, "LONG")
    best_short, closest_short = _best_for_direction(selected_analyses, "SHORT")

    report = DailyAnalysisReport(
        generated_at_utc=generated_at_utc,
        generated_at_malaysia=generated_at_my.strftime("%Y-%m-%d %H:%M %Z"),
        next_us_market_open_malaysia=next_open_my.strftime("%Y-%m-%d %H:%M %Z"),
        session_label="Morning research report",
        fixed_symbols=cfg.fixed_watchlist,
        dynamic_symbols=[x.symbol for x in dynamic],
        market_regime=market_regime,
        analyses=selected_analyses,
        day_trading_watchlist=day_trade_watchlist,
        top3_bullish=bullish_ranked,
        top3_bearish=bearish_ranked,
        best_long=best_long,
        best_short=best_short,
        closest_long_candidate=closest_long,
        closest_short_candidate=closest_short,
        best_overall=best_overall,
        notes=errors,
    )

    ai_overlay = generate_ai_overlay(selected_analyses, market_regime, cfg.openai_api_key)

    output_dir = repo_root / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    md = render_markdown(report, ai_overlay)
    html = render_html(report, repo_root / "templates")

    (output_dir / "daily_stock_analysis.md").write_text(md, encoding="utf-8")
    (output_dir / "daily_stock_analysis.html").write_text(html, encoding="utf-8")
    (output_dir / "daily_stock_analysis.json").write_text(
        json.dumps(asdict(report), default=str, indent=2), encoding="utf-8"
    )

    email_sent = False
    if cfg.send_email:
        if not cfg.resend_api_key or not cfg.email_from:
            raise RuntimeError("Email enabled but RESEND_API_KEY or EMAIL_FROM is missing")

        provider = ResendEmailProvider(cfg.resend_api_key)
        payload = EmailPayload(
            subject="Daily Stock Analysis - Morning Report",
            html=html,
            sender=cfg.email_from,
            recipient=cfg.email_to,
        )
        provider.send_html(payload)
        email_sent = True

    print(f"Report generated at {output_dir}")
    print(f"Email sent: {email_sent}")

    if errors:
        print("Completed with partial data errors:")
        for err in errors:
            print(f"- {err}")

    return 0


def _analyze_symbol(
    symbol: str,
    cfg: AppConfig,
    regime_label: str,
    sector_strength: float | None,
    market_provider,
    news_provider,
) -> StockAnalysis:
    md = market_provider.get_market_data(symbol)
    intelligence = news_provider.get_news(symbol)

    if not intelligence.upcoming_catalysts and intelligence.news_available:
        intelligence.upcoming_catalysts = ["Monitor next earnings window and sector headlines"]

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

    battle_plan = _build_battle_plan(md, decision.signal)

    warnings = _build_data_quality_warnings(symbol, md)
    data_quality = DataQuality(
        price_available=md.price is not None,
        intraday_available=md.vwap is not None or md.opening_range_high is not None,
        premarket_available=md.premarket_price is not None,
        volume_available=md.volume is not None,
        timestamp_available=md.data_timestamp is not None,
        provider=md.provider,
        warnings=warnings,
    )

    return StockAnalysis(
        symbol=symbol,
        name=_display_name(symbol),
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
        one_liner=f"{_display_name(symbol)} is rated {decision.signal} based on latest available technical/news inputs.",
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
            "premarket_available": md.premarket_price is not None,
        },
    )


def _display_name(symbol: str) -> str:
    if symbol.upper() == "SKHY":
        return "SK hynix"
    if symbol.upper() == "SNDK":
        return "SanDisk"
    return symbol


def _build_data_quality_warnings(symbol: str, md) -> list[str]:
    warnings: list[str] = []
    if md.premarket_price is None:
        warnings.append("PREMARKET_UNAVAILABLE")
    if md.vwap is None and md.opening_range_high is None:
        warnings.append("INTRADAY_UNAVAILABLE")
    if md.premarket_volume is None:
        warnings.append("EXTENDED_HOURS_UNAVAILABLE")
    if md.volume is None:
        warnings.append("VOLUME_UNAVAILABLE")
    if md.data_timestamp is None:
        warnings.append("STALE_DATA")
    if symbol.upper() == "SNDK" and (md.price is None or md.data_timestamp is None):
        warnings.append("SNDK_DATA_LIMITATION")
    return warnings


def _build_battle_plan(md, signal: str) -> BattlePlan:
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


def _unavailable_analysis(symbol: str, reason: str) -> StockAnalysis:
    md = YFinanceMarketDataProvider().get_market_data(symbol)
    intel = IntelligenceBlock(
        facts=["Data unavailable for this symbol"],
        interpretation=[reason],
        upcoming_catalysts=[],
        news_available=False,
    )
    battle = BattlePlan(
        bullish_scenario="UNAVAILABLE",
        bearish_scenario="UNAVAILABLE",
        key_support="UNAVAILABLE",
        key_resistance="UNAVAILABLE",
        entry_area="NO_TRADE",
        target_area="NO_TRADE",
        invalidation="UNAVAILABLE",
        risk_reward_assessment="UNAVAILABLE",
    )
    from .models import ScoreBreakdown

    score = ScoreBreakdown(total=0.0, long_score=0.0, short_score=0.0, components={}, weights={})
    dq = DataQuality(
        price_available=False,
        intraday_available=False,
        premarket_available=False,
        volume_available=False,
        timestamp_available=md.data_timestamp is not None,
        provider=md.provider,
        warnings=["STALE_DATA"],
    )
    return StockAnalysis(
        symbol=symbol,
        name=_display_name(symbol),
        signal="NO_TRADE",
        trading_horizon="NO_TRADE",
        direction_bias="NEUTRAL",
        market_alignment="UNKNOWN",
        setup_score=0,
        day_trade_candidate=False,
        candidate_score=0,
        candidate_status="NO DAY-TRADE CANDIDATES",
        confirmation_needed="Data unavailable",
        confidence="LOW",
        one_liner="Insufficient data",
        main_reason=reason,
        risk_classification="UNKNOWN",
        market_data=md,
        intelligence=intel,
        battle_plan=battle,
        score=score,
        data_quality=dq,
        source_flags={"market_data_available": False, "news_available": False},
    )


def _best_for_direction(analyses: list[StockAnalysis], direction: str) -> tuple[str, str]:
    confirmed = [x for x in analyses if x.signal == direction]
    if confirmed:
        ranked_confirmed = sorted(confirmed, key=lambda x: (x.setup_score, x.candidate_score), reverse=True)
        best = ranked_confirmed[0]
        return (
            f"{best.symbol} | Bias: {best.direction_bias} | Status: {best.signal} | Reason: {best.main_reason}",
            "NONE",
        )

    closest_bias = [x for x in analyses if x.direction_bias == f"{direction}_BIAS"]
    if not closest_bias:
        return "NONE", "NONE"

    ranked_closest = sorted(closest_bias, key=lambda x: (x.candidate_score, x.setup_score), reverse=True)
    closest = ranked_closest[0]
    return (
        "NONE",
        f"{closest.symbol} | Bias: {closest.direction_bias} | Status: {closest.signal} | Reason: {closest.main_reason}",
    )


def _best_overall(analyses: list[StockAnalysis]) -> str:
    eligible = [x for x in analyses if x.day_trade_candidate or x.signal in {"LONG", "SHORT"}]
    if not eligible:
        return "NO HIGH-CONVICTION SETUP"
    ranked = sorted(eligible, key=lambda x: (x.candidate_score, x.setup_score), reverse=True)
    best = ranked[0]
    return f"{best.symbol} | Bias: {best.direction_bias} | Status: {best.signal}"


def main() -> int:
    return run_analysis()
