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
    lines.append(f"- MARKET_REGIME: {mr.label}")
    lines.append(f"- Bias: {mr.bias}")
    lines.append(f"- Main catalyst: {mr.main_catalyst}")
    lines.append(f"- Main risk: {mr.main_risk}")
    lines.append(f"- Futures direction: {mr.indicators.get('market_futures_direction', 'UNAVAILABLE')}")
    lines.append(f"- Data note: {mr.summary}")
    lines.append("")

    lines.append("## Overnight / Pre-market")
    for analysis in report.analyses:
        md = analysis.market_data
        lines.append(
            f"- {analysis.symbol}: prev_close={_fmt(md.previous_close)}, latest_ext={_fmt(md.latest_extended_price)}, "
            f"gap_pct={_fmt(md.gap_pct)}, premarket_change_pct={_fmt(md.premarket_change_pct)}, "
            f"premarket_volume={_fmt(md.premarket_volume)}, rel_vol={_fmt(md.relative_volume)}, "
            f"timestamp={md.premarket_timestamp or 'UNAVAILABLE'}, note={md.delayed_note}"
        )
    lines.append("")

    lines.append("## Core Conclusion")
    for analysis in report.analyses:
        lines.extend(_stock_core(analysis))

    lines.append("## Day Trading Watchlist")
    if not report.day_trading_watchlist:
        lines.append("- NO HIGH-CONVICTION SETUP")
    for x in report.day_trading_watchlist:
        lines.append(f"- Symbol: {x.symbol}")
        lines.append(f"  Direction: {x.signal}")
        lines.append(f"  Trading Horizon: {x.trading_horizon}")
        lines.append(f"  Setup Score: {x.setup_score}")
        lines.append(f"  Why interesting: {x.main_reason}")
        lines.append(f"  Key confirmation: {x.market_data.breakout_state}")
        lines.append(f"  Invalidation: {x.battle_plan.invalidation}")
        lines.append(f"  Risk/Reward: {x.battle_plan.risk_reward_assessment}")
        lines.append("  Cancel condition: Loss of volume confirmation or invalidation breach")

    lines.append("\n## Top Opportunities")
    lines.append(f"- Best LONG setup: {report.best_long}")
    lines.append(f"- Best SHORT setup: {report.best_short}")
    lines.append(f"- Best overall setup: {report.best_overall}")

    lines.append("\n## Ranking")
    lines.append("### Top 3 bullish opportunities")
    for x in report.top3_bullish:
        lines.append(f"- {x.symbol}: {x.signal} ({x.confidence})")
    lines.append("### Top 3 bearish opportunities")
    for x in report.top3_bearish:
        lines.append(f"- {x.symbol}: {x.signal} ({x.confidence})")

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
    fixed_set = set(report.fixed_symbols)
    dynamic_set = set(report.dynamic_symbols)

    fixed_analyses = [x for x in report.analyses if x.symbol in fixed_set]
    dynamic_analyses = [x for x in report.analyses if x.symbol in dynamic_set]

    return template.render(
        report=report,
        bullish=bullish,
        bearish=bearish,
        fixed_analyses=fixed_analyses,
        dynamic_analyses=dynamic_analyses,
        generated_local=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _stock_core(analysis: StockAnalysis) -> list[str]:
    m = analysis.market_data
    out = [
        f"### {analysis.symbol} - {analysis.name}",
        f"- Signal: {analysis.signal}",
        f"- Trading horizon: {analysis.trading_horizon}",
        f"- Setup score: {analysis.setup_score}",
        f"- Market alignment: {analysis.market_alignment}",
        f"- Confidence: {analysis.confidence}",
        f"- Conclusion: {analysis.one_liner}",
        f"- Main reason: {analysis.main_reason}",
        f"- Risk classification: {analysis.risk_classification}",
        "- Data Perspective:",
        f"  price={_fmt(m.price)}, prev_close={_fmt(m.previous_close)}, latest_ext={_fmt(m.latest_extended_price)}, "
        f"gap_pct={_fmt(m.gap_pct)}, premarket_change={_fmt(m.premarket_change_pct)}, premarket_volume={_fmt(m.premarket_volume)}, "
        f"trend={m.trend}, SMA20={_fmt(m.sma20)}, SMA50={_fmt(m.sma50)}, SMA200={_fmt(m.sma200)}, "
        f"RSI={_fmt(m.rsi14)}, MACD={_fmt(m.macd)}, ATR={_fmt(m.atr14)}, VWAP={_fmt(m.vwap)}, "
        f"ORH={_fmt(m.opening_range_high)}, ORL={_fmt(m.opening_range_low)}, volume={_fmt(m.volume)}, rel_vol={_fmt(m.relative_volume)}, "
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
