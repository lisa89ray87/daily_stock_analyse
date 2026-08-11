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
    news_provider = create_news_provider(
        cfg.news_provider,
        news_max_age_hours=cfg.news_max_age_hours,
    )

    market_regime = build_market_regime()
    sector_strength = market_regime.indicators.get("semiconductor_etf_change_pct")

    all_symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    analyses: list[StockAnalysis] = []
    errors: list[str] = []

    for symbol in all_symbols:
        try:
            analyses.append(
                analyze_symbol(
                    symbol,
                    cfg,
                    market_regime.label,
                    sector_strength,
                    market_provider,
                    news_provider,
                    now_utc=generated_at_utc,
                )
            )
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

    best_long, closest_long = _best_for_direction(selected_analyses, "LONG")
    best_short, closest_short = _best_for_direction(selected_analyses, "SHORT")
    best_overall = _best_overall(selected_analyses)

    generated_at_my = generated_at_utc.astimezone(ZoneInfo(cfg.morning_report_timezone))
    next_open_my = next_us_market_open_malaysia(
        generated_at_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        malaysia_timezone=cfg.morning_report_timezone,
    )

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
                )
                lifecycle_notes.append(f"Persisted {persisted} signals")
                outcome = evaluate_signal_outcomes(store, generated_at_utc)
                historical_performance = outcome
                lifecycle_notes.append(f"Outcome tracking: {outcome.get('evaluated', 0)} evaluated")
            except Exception as exc:
                lifecycle_notes.append(f"Signal lifecycle error: {exc}")

    report = DailyAnalysisReport(
        generated_at_utc=generated_at_utc,
        generated_at_malaysia=generated_at_my.isoformat(),
        next_us_market_open_malaysia=next_open_my,
        session_label=session.session_state,
        fixed_symbols=cfg.fixed_watchlist,
        dynamic_symbols=[x.symbol for x in dynamic],
        market_regime=market_regime,
        analyses=analyses,
        day_trading_watchlist=day_trade_watchlist,
        top3_bullish=bullish_ranked,
        top3_bearish=bearish_ranked,
        best_long=best_long,
        best_short=best_short,
        closest_long_candidate=closest_long,
        closest_short_candidate=closest_short,
        best_overall=best_overall,
        notes=errors + lifecycle_notes + ai_overlay.notes,
        market_data_session=session.session_state,
        latest_data_source=_report_data_source(selected_analyses, session.session_state),
        live_regular_session=session.session_state == "US_REGULAR",
        news_catalysts=daily_catalysts,
        historical_performance=historical_performance,
    )

    artifacts = repo_root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "daily_stock_analysis.json").write_text(
        json.dumps(asdict(report), indent=2, default=str), encoding="utf-8"
    )
    (artifacts / "daily_stock_analysis.md").write_text(render_markdown(report), encoding="utf-8")
    (artifacts / "daily_stock_analysis.html").write_text(render_html(report), encoding="utf-8")

    print(f"Report generated at {artifacts}")
    if errors:
        print("Analysis warnings:")
        for error in errors:
            print(f" - {error}")

    if cfg.send_email:
        email = ResendEmailProvider(
            api_key=cfg.resend_api_key,
            sender=cfg.email_from,
            recipient=cfg.email_to,
        )
        payload = EmailPayload(
            subject=f"Daily Stock Analysis — {generated_at_my.strftime('%Y-%m-%d %H:%M %Z')}",
            html=(artifacts / "daily_stock_analysis.html").read_text(encoding="utf-8"),
        )
        result = email.send(payload)
        print(f"Email sent: {result.success}")
    return 0


def _report_data_source(analyses: list[StockAnalysis], session: str) -> str:
    sources = sorted({
        a.market_data.selected_data_source
        for a in analyses
        if a.market_data.selected_data_source and a.market_data.selected_data_source != "UNAVAILABLE"
    })
    if not sources:
        return "UNAVAILABLE"
    return ", ".join(sources)


def _best_for_direction(analyses: list[StockAnalysis], direction: str) -> tuple[str, str]:
    if not analyses:
        return "UNAVAILABLE", "UNAVAILABLE"
    ranked = sorted(
        analyses,
        key=lambda x: x.score.long_score if direction == "LONG" else x.score.short_score,
        reverse=True,
    )
    best = ranked[0]
    return display_name(best.symbol), f"{display_name(best.symbol)} ({best.setup_score}/100)"


def _best_overall(analyses: list[StockAnalysis]) -> str:
    if not analyses:
        return "UNAVAILABLE"
    best = max(analyses, key=lambda x: x.setup_score)
    return display_name(best.symbol)


def _unavailable_analysis(symbol: str, reason: str) -> StockAnalysis:
    md = MarketData(symbol=symbol)
    intelligence = IntelligenceBlock(
        facts=[reason],
        interpretation=["Analysis unavailable"],
        upcoming_catalysts=["UNAVAILABLE"],
        news_available=False,
        catalyst_status="UNAVAILABLE",
    )
    quality = DataQuality(
        price_available=False,
        intraday_available=False,
        premarket_available=False,
        volume_available=False,
        timestamp_available=False,
        provider="unavailable",
        warnings=[reason],
    )
    score = ScoreBreakdown(total=0, long_score=0, short_score=0, components={}, weights={})
    battle = BattlePlan(
        bullish_scenario="UNAVAILABLE",
        bearish_scenario="UNAVAILABLE",
        key_support="UNAVAILABLE",
        key_resistance="UNAVAILABLE",
        entry_area="UNAVAILABLE",
        target_area="UNAVAILABLE",
        invalidation="UNAVAILABLE",
        risk_reward_assessment="UNAVAILABLE",
    )
    return StockAnalysis(
        symbol=symbol,
        name=display_name(symbol),
        signal="NO_TRADE",
        trading_horizon="NO_TRADE",
        direction_bias="NEUTRAL",
        market_alignment="UNKNOWN",
        setup_score=0,
        day_trade_candidate=False,
        candidate_score=0,
        candidate_status="UNAVAILABLE",
        confirmation_needed=reason,
        confidence="LOW",
        one_liner=reason,
        main_reason=reason,
        risk_classification="UNKNOWN",
        market_data=md,
        intelligence=intelligence,
        battle_plan=battle,
        score=score,
        data_quality=quality,
        source_flags={},
    )
