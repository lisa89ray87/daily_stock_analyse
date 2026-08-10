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

    lines.append("\n## Top Opportunities")
    lines.append(f"- Best LONG: {report.best_long}")
    lines.append(f"- Best SHORT: {report.best_short}")
    lines.append(f"- Best Overall: {report.best_overall}")

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

    if ai_overlay:
        lines.append("\n## AI Overlay")
        lines.append(f"- Enabled: {ai_overlay.get('enabled')}")
        lines.append(f"- Summary: {ai_overlay.get('message')}")

    lines.append("\n## Risk Warning")
    lines.append("Automated research report only. No brokerage execution. Not investment advice.")

    return "\n".join(lines)


def render_html(report: DailyAnalysisReport, template_dir: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("daily_report.html")

    fixed_set = set(report.fixed_symbols)
    dynamic_set = set(report.dynamic_symbols)

    fixed_analyses = [x for x in report.analyses if x.symbol in fixed_set]
    dynamic_analyses = [x for x in report.analyses if x.symbol in dynamic_set]

    return template.render(
        report=report,
        fixed_analyses=fixed_analyses,
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
    out.append(f"- Confirmation Needed: {analysis.confirmation_needed}")
    out.append(f"- Invalidation: {analysis.battle_plan.invalidation}")
    out.append("")
    return out


def _fmt(value: float | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    return f"{value:.2f}"
