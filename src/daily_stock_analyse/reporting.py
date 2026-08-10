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
    lines.append("## Market Analysis")
    lines.append(f"- Regime: {mr.label}")
    lines.append(f"- Bias: {mr.bias}")
    lines.append(f"- Main catalyst: {mr.main_catalyst}")
    lines.append(f"- Main risk: {mr.main_risk}")
    lines.append(f"- Data note: {mr.summary}")
    lines.append("")

    lines.append("## Core Conclusion")
    for analysis in report.analyses:
        lines.extend(_stock_core(analysis))

    lines.append("## Ranking")
    lines.append("### Top 3 bullish opportunities")
    for x in report.top3_bullish:
        lines.append(f"- {x.symbol}: {x.signal} ({x.confidence})")
    lines.append("### Top 3 bearish opportunities")
    for x in report.top3_bearish:
        lines.append(f"- {x.symbol}: {x.signal} ({x.confidence})")
    lines.append(f"### Best overall opportunity\n- {report.best_overall}")

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

    bullish = report.top3_bullish
    bearish = report.top3_bearish

    return template.render(
        report=report,
        bullish=bullish,
        bearish=bearish,
        generated_local=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _stock_core(analysis: StockAnalysis) -> list[str]:
    m = analysis.market_data
    out = [
        f"### {analysis.symbol} - {analysis.name}",
        f"- Signal: {analysis.signal}",
        f"- Confidence: {analysis.confidence}",
        f"- Conclusion: {analysis.one_liner}",
        f"- Main reason: {analysis.main_reason}",
        f"- Risk classification: {analysis.risk_classification}",
        "- Data Perspective:",
        f"  price={_fmt(m.price)}, daily_change_pct={_fmt(m.day_change_pct)}, trend={m.trend}, "
        f"SMA20={_fmt(m.sma20)}, SMA50={_fmt(m.sma50)}, SMA200={_fmt(m.sma200)}, "
        f"RSI={_fmt(m.rsi14)}, MACD={_fmt(m.macd)}, volume={_fmt(m.volume)}, rel_vol={_fmt(m.relative_volume)}, "
        f"volatility={_fmt(m.volatility_20d)}, support={_fmt(m.support)}, resistance={_fmt(m.resistance)}, "
        f"structure={m.recent_structure}, breakout={m.breakout_state}",
        "- Intelligence (FACT):",
    ]
    out.extend([f"  - {x}" for x in analysis.intelligence.facts] or ["  - UNAVAILABLE"])
    out.append("- Intelligence (INTERPRETATION):")
    out.extend([f"  - {x}" for x in analysis.intelligence.interpretation] or ["  - UNAVAILABLE"])
    out.append("- Battle Plan:")
    out.append(f"  - Bullish scenario: {analysis.battle_plan.bullish_scenario}")
    out.append(f"  - Bearish scenario: {analysis.battle_plan.bearish_scenario}")
    out.append(f"  - Entry area: {analysis.battle_plan.entry_area}")
    out.append(f"  - Target area: {analysis.battle_plan.target_area}")
    out.append(f"  - Invalidation: {analysis.battle_plan.invalidation}")
    out.append("")
    return out


def _fmt(value: float | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    return f"{value:.2f}"
