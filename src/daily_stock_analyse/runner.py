from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .ai_analysis import generate_ai_overlay
from .config import AppConfig, load_config
from .email_provider import EmailPayload, ResendEmailProvider
from .market import build_market_regime
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

    report = DailyAnalysisReport(
        generated_at_utc=datetime.now(UTC),
        session_label="Morning research report (Malaysia timezone target)",
        fixed_symbols=cfg.fixed_watchlist,
        dynamic_symbols=[x.symbol for x in dynamic],
        market_regime=market_regime,
        analyses=selected_analyses,
        day_trading_watchlist=day_trade_watchlist,
        top3_bullish=bullish_ranked,
        top3_bearish=bearish_ranked,
        best_long=best_long,
        best_short=best_short,
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
            subject=f"Daily Stock Analysis - {report.generated_at_utc.date().isoformat()}",
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
    if symbol.upper() == "000660.KS":
        return "SK Hynix (KRX)"
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
    if symbol.upper() == "SNDK":
        warnings.append("TICKER_MAPPING_WARNING")
    return warnings


def _build_battle_plan(md, signal: str) -> BattlePlan:
    support = f"{md.support:.2f}" if md.support is not None else "UNAVAILABLE"
    resistance = f"{md.resistance:.2f}" if md.resistance is not None else "UNAVAILABLE"

    entry = "Wait for confirmation"
    target = "UNAVAILABLE"
    invalidation = "UNAVAILABLE"

    if md.price is not None and md.support is not None and md.resistance is not None:
        if signal == "LONG":
            entry = "Break above resistance with volume confirmation"
            target = f"Toward resistance expansion above {md.resistance:.2f}"
            invalidation = f"Break below {md.support * 0.98:.2f}"
        elif signal == "SHORT":
            entry = "Break below support with increasing selling volume"
            target = f"Toward lower support under {md.support:.2f}"
            invalidation = f"Reclaim above {md.resistance * 1.02:.2f}"
        else:
            entry = "Wait for breakout/breakdown trigger"
            target = "No active target"
            invalidation = "Invalid if setup confirmation never appears"

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


def _best_for_direction(analyses: list[StockAnalysis], direction: str) -> str:
    eligible = [x for x in analyses if x.direction_bias == f"{direction}_BIAS"]
    if not eligible:
        return "NO HIGH-CONVICTION SETUP"
    ranked = sorted(eligible, key=lambda x: (x.candidate_score, x.setup_score), reverse=True)
    best = ranked[0]
    return (
        f"{best.symbol} | Bias: {best.direction_bias} | Status: {best.signal} | Reason: {best.main_reason}"
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
