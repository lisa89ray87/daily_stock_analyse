from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis_service import (
    analyze_symbol,
    apply_news_lookback,
    build_battle_plan,
    build_data_quality_warnings,
    collect_daily_catalysts,
    display_name,
)
from .ai_analysis import generate_ai_overlay
from .backtest import summarize_backtest
from .config import AppConfig, load_config
from .email_provider import EmailPayload, ResendEmailProvider
from .market import build_market_regime
from .market_hours import get_market_session_status, next_us_market_open_malaysia, utc_now
from .models import BattlePlan, CatalystEvent, DailyAnalysisReport, DataQuality, IntelligenceBlock, StockAnalysis
from .outcomes import evaluate_signal_outcomes
from .providers import create_market_data_provider, create_news_provider
from .reporting import render_html, render_markdown
from .scoring import decide_signal, score_stock
from .selector import select_dynamic_opportunities
from .signal_history import SignalHistoryStore


def run_analysis(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    cfg = load_config(repo_root)
    generated_at_utc = utc_now()
    session = get_market_session_status(
        generated_at_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )

    market_provider = create_market_data_provider(cfg.data_provider)
    news_provider = create_news_provider(cfg.news_provider)

    market_regime = build_market_regime()
    sector_strength = market_regime.indicators.get("semiconductor_etf_change_pct")

    all_symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    analyses: list[StockAnalysis] = []
    errors: list[str] = []

    for symbol in all_symbols:
        try:
            analyses.append(analyze_symbol(symbol, cfg, market_regime.label, sector_strength, market_provider, news_provider, now_utc=generated_at_utc))
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

    generated_at_my = generated_at_utc.astimezone(ZoneInfo(cfg.morning_report_timezone))
    next_open_my = next_us_market_open_malaysia(
        generated_at_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        malaysia_timezone=cfg.morning_report_timezone,
    )

    best_long, closest_long = _best_for_direction(selected_analyses, "LONG")
    best_short, closest_short = _best_for_direction(selected_analyses, "SHORT")

    ai_overlay = generate_ai_overlay(selected_analyses, market_regime, cfg)

    lifecycle_notes: list[str] = []
    historical_performance: dict[str, object] = {}
    daily_catalysts = collect_daily_catalysts(selected_analyses)

    persistence_requested = cfg.enable_outcome_tracking or cfg.enable_backtest
    if persistence_requested:
        if not cfg.database_enabled:
            lifecycle_notes.append("Signal lifecycle disabled: DATABASE_ENABLED=0")
        elif not (cfg.database_url or "").strip():
            lifecycle_notes.append("Signal lifecycle disabled: DATABASE_URL not configured")
        else:
            try:
                store = SignalHistoryStore(cfg.database_url.strip())
                persisted = store.save_signals(
                    selected_analyses,
                    market_regime,
                    generated_at_utc,
                    cfg.signal_expiry_hours,
                    session.session_state,
                    _report_data_source(selected_analyses, session.session_state),
                    ai_overlay.get("provider") if isinstance(ai_overlay, dict) else None,
                )
                lifecycle_notes.append("Signal lifecycle database: PostgreSQL")
                lifecycle_notes.append(f"Signals persisted this run: {persisted}")

                if cfg.enable_outcome_tracking:
                    open_rows = store.open_signals()
                    latest_prices = {item.symbol: item.market_data.price for item in selected_analyses}
                    updates = evaluate_signal_outcomes(open_rows, latest_prices, generated_at_utc)
                    updated_rows = store.apply_outcome_updates(updates, generated_at_utc)
                    lifecycle_notes.append(f"Signal outcomes updated: {updated_rows}")

                if cfg.enable_backtest:
                    backtest_rows = store.load_backtest_rows(limit=5000)
                    historical_performance = summarize_backtest(backtest_rows)
            except Exception as exc:
                lifecycle_notes.append(f"Signal lifecycle disabled for this run: {exc.__class__.__name__}")

    report = DailyAnalysisReport(
        generated_at_utc=generated_at_utc,
        generated_at_malaysia=generated_at_my.strftime("%Y-%m-%d %H:%M %Z"),
        next_us_market_open_malaysia=next_open_my.strftime("%Y-%m-%d %H:%M %Z"),
        session_label="Morning research report",
        market_data_session=session.session_state,
        latest_data_source=_report_data_source(selected_analyses, session.session_state),
        live_regular_session=session.session_state == "US_REGULAR",
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
        notes=errors + lifecycle_notes,
        news_catalysts=daily_catalysts,
        historical_performance=historical_performance,
    )

    output_dir = repo_root / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    md = render_markdown(report, ai_overlay)
    html = render_html(report, repo_root / "templates", ai_overlay)

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
    now_utc: datetime | None = None,
) -> StockAnalysis:
    return analyze_symbol(
        symbol,
        cfg,
        regime_label,
        sector_strength,
        market_provider,
        news_provider,
        now_utc=now_utc,
    )


def _report_data_source(analyses: list[StockAnalysis], session_state: str) -> str:
    if session_state == "US_REGULAR":
        return "Live / Intraday Regular Session"
    data_sources = {x.market_data.selected_data_source for x in analyses if x.market_data.selected_data_source != "UNAVAILABLE"}
    if len(data_sources) == 1:
        return next(iter(data_sources))
    if any(x.market_data.extended_hours_used for x in analyses):
        return "24-Hour / Extended Hours"
    return "UNAVAILABLE"


def _apply_news_lookback(intelligence: IntelligenceBlock, lookback_hours: int) -> IntelligenceBlock:
    return apply_news_lookback(intelligence, lookback_hours)


def _collect_daily_catalysts(analyses: list[StockAnalysis]) -> list[CatalystEvent]:
    return collect_daily_catalysts(analyses)


def _display_name(symbol: str) -> str:
    return display_name(symbol)


def _has_premarket_data(md) -> bool:
    return md.premarket_price is not None or md.latest_extended_session == "PREMARKET"


def _has_after_hours_data(md) -> bool:
    return md.after_hours_price is not None or md.latest_extended_session == "AFTER_HOURS"


def _has_usable_selected_price(md) -> bool:
    return md.price is not None and md.selected_data_source != "UNAVAILABLE"


def _build_data_quality_warnings(symbol: str, md) -> list[str]:
    return build_data_quality_warnings(symbol, md)


def _build_battle_plan(md, signal: str) -> BattlePlan:
    return build_battle_plan(md, signal)


def _unavailable_analysis(symbol: str, reason: str) -> StockAnalysis:
    md = create_market_data_provider("yfinance").get_market_data(symbol)
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
