from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import DailyAnalysisReport, StockAnalysis


def render_markdown(report: DailyAnalysisReport, ai_overlay: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("# DAILY STOCK ANALYSIS")
    lines.append("")
    lines.append(f"Generated UTC: {report.generated_at_utc.isoformat()}")
    lines.append(f"Generated: {report.generated_at_malaysia}")
    lines.append(f"Next U.S. Regular Market Open: {report.next_us_market_open_malaysia}")
    lines.append(f"Session: {report.session_label}")
    lines.append("")

    mr = report.market_regime
    lines.append("## Market Regime")
    lines.append(f"- MARKET_REGIME: {mr.label}")
    lines.append(f"- Bias: {mr.bias}")
    lines.append(f"- Futures direction: {mr.indicators.get('market_futures_direction', 'UNAVAILABLE')}")
    lines.append(f"- Main catalyst: {mr.main_catalyst}")
    lines.append(f"- Main risk: {mr.main_risk}")
    lines.append(f"- Data note: {mr.summary}")
    lines.append("")

    lines.append("## DAY TRADING WATCHLIST")
    if not report.day_trading_watchlist:
        lines.append("- NO DAY-TRADE CANDIDATES")
    for x in report.day_trading_watchlist:
        lines.append(f"### {x.symbol} ({x.name})")
        lines.append(f"- Direction Bias: {x.direction_bias}")
        lines.append(f"- Candidate Score: {x.candidate_score}")
        lines.append(f"- Gap: {_fmt(x.market_data.gap_pct)}%")
        lines.append(f"- Relative Volume: {_fmt(x.market_data.relative_volume)}")
        lines.append(f"- Trend: {x.market_data.trend}")
        lines.append(f"- Status: {x.candidate_status}")
        lines.append(f"- Confirmation Needed: {x.confirmation_needed}")
        lines.append(f"- Invalidation: {x.battle_plan.invalidation}")

    lines.append("\n## TOP OPPORTUNITIES")
    lines.append(f"- Best LONG: {report.best_long}")
    lines.append(f"- Closest LONG Candidate: {report.closest_long_candidate}")
    lines.append(f"- Best SHORT: {report.best_short}")
    lines.append(f"- Closest SHORT Candidate: {report.closest_short_candidate}")
    lines.append(f"- Best Overall: {report.best_overall}")

    lines.append("\n## FIXED SIX")
    for sym in report.fixed_symbols:
        lines.append(f"- {sym}")

    lines.append("\n## Overnight / Pre-market")
    for analysis in report.analyses:
        md = analysis.market_data
        lines.append(
            f"- {analysis.symbol}: OVERNIGHT_REFERENCE={_fmt(md.overnight_reference_price)}, "
            f"REGULAR={_fmt(md.regular_price)}, PREMARKET={_fmt(md.premarket_price)}, AFTER_HOURS={_fmt(md.after_hours_price)}, "
            f"LATEST_EXT={_fmt(md.latest_extended_price)} ({md.latest_extended_session}), "
            f"gap_pct={_fmt(md.gap_pct)}, premarket_change_pct={_fmt(md.premarket_change_pct)}, "
            f"premarket_volume={_fmt(md.premarket_volume)}, rel_vol={_fmt(md.relative_volume)}, timestamp={md.data_timestamp or 'UNAVAILABLE'}"
        )

    lines.append("\n## Core Conclusion")
    for analysis in report.analyses:
        lines.extend(_stock_core(analysis))

    lines.append("\n## Data Quality")
    for analysis in report.analyses:
        if analysis.data_quality.warnings:
            lines.append(f"- {analysis.symbol}: {', '.join(analysis.data_quality.warnings)}")
        else:
            lines.append(f"- {analysis.symbol}: OK")

    lines.append("\n## Selection Metadata")
    lines.append(f"- Fixed symbols: {', '.join(report.fixed_symbols)}")
    lines.append(f"- Dynamic symbols selected: {', '.join(report.dynamic_symbols) if report.dynamic_symbols else 'NONE'}")

    if ai_overlay:
        lines.append("\n## AI Trading Conclusion")
        provider_display = ai_overlay.get("provider_display") or "UNAVAILABLE"
        provider_line = provider_display
        if ai_overlay.get("fallback_used"):
            provider_line = f"{provider_display} (fallback)"
        lines.append(f"- Provider: {provider_line}")
        lines.append(f"- Status: {ai_overlay.get('status') or ('Enabled' if ai_overlay.get('enabled') else 'Unavailable')}")
        lines.append(f"- Market Bias: {ai_overlay.get('market_bias') or 'UNAVAILABLE'}")
        lines.append(f"- Market Regime: {ai_overlay.get('market_regime') or 'UNAVAILABLE'}")
        lines.append(f"- Final Conclusion: {ai_overlay.get('final_conclusion') or ai_overlay.get('summary') or ai_overlay.get('message')}")
        best_day_trade = ai_overlay.get("best_day_trade") or {}
        lines.append("- Best Day-Trade:")
        lines.append(f"  - Symbol: {best_day_trade.get('symbol', 'NONE')}")
        lines.append(f"  - Direction: {best_day_trade.get('direction', 'NONE')}")
        lines.append(f"  - Status: {best_day_trade.get('status', 'UNAVAILABLE')}")
        lines.append(f"  - Reason: {best_day_trade.get('reason', 'UNAVAILABLE')}")
        best_long = ai_overlay.get("best_long_candidate") or {}
        best_short = ai_overlay.get("best_short_candidate") or {}
        lines.append(f"- Best Long: {best_long.get('symbol', 'NONE')} - {best_long.get('reason', 'UNAVAILABLE')}")
        lines.append(f"- Best Short: {best_short.get('symbol', 'NONE')} - {best_short.get('reason', 'UNAVAILABLE')}")
        watchlist = ai_overlay.get("stocks_to_watch") or []
        if watchlist:
            lines.append("- Watchlist:")
            for item in watchlist:
                lines.append(f"  - {item.get('symbol', 'NONE')}: {item.get('reason', 'UNAVAILABLE')}")
        avoid = ai_overlay.get("stocks_to_avoid") or []
        if avoid:
            lines.append("- Avoid:")
            for item in avoid:
                lines.append(f"  - {item.get('symbol', 'NONE')}: {item.get('reason', 'UNAVAILABLE')}")
        key_risks = ai_overlay.get("key_risks") or []
        if key_risks:
            lines.append("- Key Risks:")
            for risk in key_risks:
                lines.append(f"  - {risk}")
        action_points = ai_overlay.get("action_points") or []
        if action_points:
            lines.append("- Action Points:")
            for point in action_points:
                lines.append(f"  - {point}")

    lines.append("\n## Risk Warning")
    lines.append("Automated research report only. No brokerage execution. Not investment advice.")

    return "\n".join(lines)


def render_html(report: DailyAnalysisReport, template_dir: Path, ai_overlay: dict | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("daily_report.html")

    fixed_set = set(report.fixed_symbols)
    dynamic_set = set(report.dynamic_symbols)

    fixed_analyses = [x for x in report.analyses if x.symbol in fixed_set]
    dynamic_analyses = [x for x in report.analyses if x.symbol in dynamic_set and x.symbol not in fixed_set]

    watchlist_symbols = {x.symbol for x in report.day_trading_watchlist}
    top_opportunity_symbols: set[str] = set()
    for analysis in report.analyses:
        if analysis.symbol in watchlist_symbols:
            continue
        if analysis.signal in {"LONG", "SHORT"}:
            top_opportunity_symbols.add(analysis.symbol)

    fixed_analyses_prominent = [x for x in fixed_analyses if x.symbol not in watchlist_symbols and x.symbol not in top_opportunity_symbols]

    return template.render(
        report=report,
        ai_overlay=ai_overlay,
        fixed_analyses=fixed_analyses,
        fixed_analyses_prominent=fixed_analyses_prominent,
        dynamic_analyses=dynamic_analyses,
        generated_local=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _stock_core(analysis: StockAnalysis) -> list[str]:
    m = analysis.market_data
    out = [
        f"### {analysis.symbol} - {analysis.name}",
        f"- Signal: {analysis.signal}",
        f"- Direction Bias: {analysis.direction_bias}",
        f"- Trading horizon: {analysis.trading_horizon}",
        f"- Setup score: {analysis.setup_score}/100",
        f"- Candidate score: {analysis.candidate_score}/100",
        f"- Candidate status: {analysis.candidate_status}",
        f"- Market alignment: {analysis.market_alignment}",
        f"- Confidence: {analysis.confidence}",
        f"- Main reason: {analysis.main_reason}",
        "- Setup Breakdown:",
    ]

    points = analysis.score.long_points if analysis.direction_bias == "LONG_BIAS" else analysis.score.short_points
    for key in ["Trend", "Momentum", "Relative Volume", "Gap", "Catalyst", "Risk/Reward", "Total"]:
        if key in points:
            out.append(f"  - {key}: +{points[key]}")

    out.extend(
        [
            "- Data Perspective:",
            f"  regular={_fmt(m.regular_price)}, premarket={_fmt(m.premarket_price)}, after_hours={_fmt(m.after_hours_price)}, "
            f"latest_ext={_fmt(m.latest_extended_price)} ({m.latest_extended_session}), overnight_ref={_fmt(m.overnight_reference_price)}",
            f"  gap={_fmt(m.gap_pct)}, premarket_change={_fmt(m.premarket_change_pct)}, premarket_volume={_fmt(m.premarket_volume)}, "
            f"  trend={m.trend}, RSI={_fmt(m.rsi14)}, MACD={_fmt(m.macd)}, ATR={_fmt(m.atr14)}, VWAP={_fmt(m.vwap)}, "
            f"  ORH={_fmt(m.opening_range_high)}, ORL={_fmt(m.opening_range_low)}, rel_vol={_fmt(m.relative_volume)}",
            "- Data Quality:",
        ]
    )

    if analysis.data_quality.warnings:
        out.extend([f"  - {x}" for x in analysis.data_quality.warnings])
    else:
        out.append("  - None")

    out.append(f"- Entry Trigger: {analysis.battle_plan.entry_area}")
    out.append(
        f"- Entry Trigger Price: {_fmt(analysis.battle_plan.entry_trigger_price)}"
    )
    out.append(f"- Confirmation Level: {_fmt(analysis.battle_plan.confirmation_level)}")
    out.append(f"- Confirmation Needed: {analysis.confirmation_needed}")
    out.append(f"- Target 1: {_fmt(analysis.battle_plan.target_1)}")
    out.append(f"- Target 2: {_fmt(analysis.battle_plan.target_2)}")
    out.append(f"- Invalidation: {analysis.battle_plan.invalidation}")
    out.append(f"- Invalidation Price: {_fmt(analysis.battle_plan.invalidation_price)}")
    if analysis.battle_plan.level_unavailable_reason:
        out.append(f"- Exact entry level: UNAVAILABLE")
        out.append(f"- Reason: {analysis.battle_plan.level_unavailable_reason}")
    out.append("")
    return out


def _fmt(value: float | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    return f"{value:.2f}"
