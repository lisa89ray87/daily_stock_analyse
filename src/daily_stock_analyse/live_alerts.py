from __future__ import annotations

import json
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import AppConfig, load_config
from .market import build_market_regime
from .market_hours import get_market_session_status, utc_now
from .models import StockAnalysis
from .providers import YFinanceMarketDataProvider, YFinanceNewsProvider
from .runner import _analyze_symbol
from .telegram_provider import TelegramBotProvider


def run_live_alerts(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    cfg = load_config(repo_root)

    if not cfg.live_alert_enabled:
        print("LIVE_ALERT_ENABLED=0, exiting without alerts.")
        return 0

    now = utc_now()
    session = get_market_session_status(
        now,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )

    if not session.market_open:
        print(f"Market closed: {session.reason}")
        _write_live_snapshot(repo_root, {"market_open": False, "reason": session.reason, "alerts": []})
        return 0

    phase = _v4_session_phase(now, cfg)
    print(f"V4 phase: {phase}")

    market_provider = YFinanceMarketDataProvider()
    news_provider = YFinanceNewsProvider()
    regime = build_market_regime()
    sector_strength = regime.indicators.get("semiconductor_etf_change_pct")

    symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    analyses: list[StockAnalysis] = []
    for symbol in symbols:
        try:
            analyses.append(_analyze_symbol(symbol, cfg, regime.label, sector_strength, market_provider, news_provider))
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            print(f"{symbol}: live analysis failed ({exc})")

    output_dir = repo_root / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "alert_state.json"
    state = _load_state(state_path)

    sent_alerts: list[dict] = []
    for analysis in analyses:
        event = _determine_event(analysis, state, cfg, now, session.opening_range_window)
        _update_symbol_state(state, analysis, event, now)
        if event is None:
            continue
        sent_alerts.append(event)

    sent_count = _send_telegram_alerts(sent_alerts, cfg)

    state["updated_at"] = now.isoformat()
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    _write_live_snapshot(
        repo_root,
        {
            "market_open": True,
            "market_reason": session.reason,
            "v4_phase": phase,
            "opening_range_window": session.opening_range_window,
            "market_time": session.market_now.isoformat(),
            "alerts": sent_alerts,
            "alerts_sent": sent_count,
        },
    )
    print(f"Live alert evaluation complete. Alerts generated: {len(sent_alerts)}")
    return 0


def _write_live_snapshot(repo_root: Path, payload: dict) -> None:
    output_dir = repo_root / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "live_alerts.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"symbols": {}, "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"symbols": {}, "updated_at": None}


def _parse_hhmm(raw: str) -> time:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM value: {raw}")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def _v4_session_phase(now_utc: datetime, cfg: AppConfig) -> str:
    market_now = now_utc.astimezone(ZoneInfo(cfg.live_market_timezone))
    opening_start = _parse_hhmm(cfg.v4_opening_start)
    opening_end = _parse_hhmm(cfg.v4_opening_end)
    if opening_start <= market_now.time() < opening_end:
        return "OPENING"
    return "NORMAL"


def _classify_rvol_quality(analysis: StockAnalysis, cfg: AppConfig, now_utc: datetime) -> tuple[str, str]:
    md = analysis.market_data
    if md.relative_volume is None:
        return "UNAVAILABLE", "RVOL missing from provider"

    if md.volume is None or md.avg_volume_20d is None:
        return "DATA_LIMITED", "Volume baseline is incomplete"

    ts = _parse_ts(md.intraday_timestamp or md.data_timestamp)
    if ts is None:
        return "DATA_LIMITED", "Provider timestamp unavailable"
    if now_utc - ts > timedelta(minutes=20):
        return "DATA_LIMITED", "Provider timestamp is stale"

    market_now = now_utc.astimezone(ZoneInfo(cfg.live_market_timezone))
    session_open = _parse_hhmm(cfg.live_market_open)
    session_close = _parse_hhmm(cfg.live_market_close)
    market_session_live = session_open <= market_now.time() <= session_close

    # yfinance RVOL here uses current daily volume vs full-day 20d average,
    # which is not intraday time-normalized during live market hours.
    if md.provider == "yfinance" and market_session_live:
        return "DATA_LIMITED", "Daily-vs-20d RVOL baseline is not intraday-normalized"

    return "RELIABLE", "RVOL baseline and timestamp are valid"


def _price_action_confirmation(analysis: StockAnalysis, phase: str, strict: bool = False) -> tuple[bool, str, str]:
    md = analysis.market_data
    signal = analysis.signal

    if md.price is None:
        return False, "Unavailable", "Price unavailable"

    if signal == "LONG":
        if md.opening_range_high is not None and md.price > md.opening_range_high:
            return True, "Break above opening range high", "Opening-range breakout confirmed"
        if md.resistance is not None and md.price > md.resistance:
            return True, "Break above resistance", "Resistance breakout confirmed"
        if strict and md.vwap is not None and md.price > md.vwap and md.breakout_state in {"BREAKOUT", "NEAR BREAKOUT"}:
            return True, "Breakout above VWAP", "Strict bullish VWAP+breakout confirmation"
        if strict and md.trend == "UPTREND" and md.breakout_state in {"BREAKOUT", "NEAR BREAKOUT"}:
            return True, "Uptrend breakout structure", "Strict trend+breakout confirmation"
        if strict:
            return False, "Unavailable", "No bullish price-action confirmation (strict)"
        if md.vwap is not None and md.price > md.vwap:
            return True, "Price above VWAP", "Bullish VWAP confirmation"
        if md.breakout_state in {"BREAKOUT", "NEAR BREAKOUT"}:
            return True, "Bullish breakout structure", "Breakout state confirmation"
        if md.trend == "UPTREND" and md.day_change_pct is not None and md.day_change_pct > 0:
            return True, "Bullish trend continuation", "Positive momentum in uptrend"
        return False, "Unavailable", "No bullish price-action confirmation"

    if signal == "SHORT":
        if md.opening_range_low is not None and md.price < md.opening_range_low:
            return True, "Break below opening range low", "Opening-range breakdown confirmed"
        if md.support is not None and md.price < md.support:
            return True, "Break below support", "Support breakdown confirmed"
        if strict and md.vwap is not None and md.price < md.vwap and md.breakout_state in {"BREAKDOWN", "NEAR BREAKDOWN"}:
            return True, "Breakdown below VWAP", "Strict bearish VWAP+breakdown confirmation"
        if strict and md.trend == "DOWNTREND" and md.breakout_state in {"BREAKDOWN", "NEAR BREAKDOWN"}:
            return True, "Downtrend breakdown structure", "Strict trend+breakdown confirmation"
        if strict:
            return False, "Unavailable", "No bearish price-action confirmation (strict)"
        if md.vwap is not None and md.price < md.vwap:
            return True, "Price below VWAP", "Bearish VWAP confirmation"
        if md.breakout_state in {"BREAKDOWN", "NEAR BREAKDOWN"}:
            return True, "Bearish breakdown structure", "Breakdown state confirmation"
        if md.trend == "DOWNTREND" and md.day_change_pct is not None and md.day_change_pct < 0:
            return True, "Bearish trend continuation", "Negative momentum in downtrend"
        return False, "Unavailable", "No bearish price-action confirmation"

    return False, "Unavailable", "Signal is not LONG/SHORT"


def _is_risk_reward_acceptable(analysis: StockAnalysis) -> tuple[bool, str]:
    bp = analysis.battle_plan
    signal = analysis.signal

    if signal == "LONG" and bp.entry_trigger_price is not None and bp.invalidation_price is not None and bp.target_1 is not None:
        risk = bp.entry_trigger_price - bp.invalidation_price
        reward = bp.target_1 - bp.entry_trigger_price
        if risk <= 0 or reward <= 0:
            return False, "Risk/reward levels are invalid"
        ratio = reward / max(risk, 1e-9)
        return (ratio >= 1.0, f"Risk/reward below minimum ({ratio:.2f})" if ratio < 1.0 else "Risk/reward accepted")

    if signal == "SHORT" and bp.entry_trigger_price is not None and bp.invalidation_price is not None and bp.target_1 is not None:
        risk = bp.invalidation_price - bp.entry_trigger_price
        reward = bp.entry_trigger_price - bp.target_1
        if risk <= 0 or reward <= 0:
            return False, "Risk/reward levels are invalid"
        ratio = reward / max(risk, 1e-9)
        return (ratio >= 1.0, f"Risk/reward below minimum ({ratio:.2f})" if ratio < 1.0 else "Risk/reward accepted")

    matches = re.findall(r"-?\d+(?:\.\d+)?", bp.risk_reward_assessment or "")
    if not matches:
        return False, "Risk/reward unavailable"

    ratio = float(matches[-1])
    if ratio < 1.0:
        return False, f"Risk/reward below minimum ({ratio:.2f})"
    return True, "Risk/reward accepted"


def _is_live_confirmable(analysis: StockAnalysis, cfg: AppConfig, opening_range_window: bool, now_utc: datetime) -> tuple[bool, str, dict]:
    md = analysis.market_data
    phase = _v4_session_phase(now_utc, cfg)
    rvol_quality, rvol_quality_reason = _classify_rvol_quality(analysis, cfg, now_utc)

    thresholds = {
        "min_rvol": cfg.v4_opening_min_rvol if phase == "OPENING" else cfg.v4_normal_min_rvol,
        "min_setup": cfg.v4_opening_min_setup_score if phase == "OPENING" else cfg.v4_normal_min_setup_score,
        "phase": phase,
        "rvol_quality": rvol_quality,
        "rvol_quality_reason": rvol_quality_reason,
        "rvol": md.relative_volume,
    }

    if md.price is None:
        return False, "Price unavailable", thresholds

    if analysis.signal == "LONG" and analysis.direction_bias != "LONG_BIAS":
        return False, "LONG signal requires LONG_BIAS", thresholds
    if analysis.signal == "SHORT" and analysis.direction_bias != "SHORT_BIAS":
        return False, "SHORT signal requires SHORT_BIAS", thresholds

    if analysis.setup_score < thresholds["min_setup"]:
        return False, f"Setup score below {phase} threshold", thresholds

    if rvol_quality == "RELIABLE":
        if md.relative_volume is None or md.relative_volume < thresholds["min_rvol"]:
            return False, f"Relative volume below {phase} threshold", thresholds
        thresholds["rvol_gate"] = "PASSED"
    else:
        thresholds["rvol_gate"] = "BYPASSED_DATA_LIMITED"

    if analysis.market_alignment != "UNKNOWN" and analysis.market_alignment != "MARKET_ALIGNED":
        return False, "Market alignment filter rejected setup", thresholds

    bullish_structure = analysis.signal == "LONG" and (md.trend == "UPTREND" or md.breakout_state in {"BREAKOUT", "NEAR BREAKOUT"})
    bearish_structure = analysis.signal == "SHORT" and (md.trend == "DOWNTREND" or md.breakout_state in {"BREAKDOWN", "NEAR BREAKDOWN"})
    if analysis.signal == "LONG" and not bullish_structure:
        return False, "No valid bullish structure", thresholds
    if analysis.signal == "SHORT" and not bearish_structure:
        return False, "No valid bearish structure", thresholds

    rr_ok, rr_reason = _is_risk_reward_acceptable(analysis)
    if not rr_ok:
        return False, rr_reason, thresholds

    strict_confirmation = rvol_quality in {"DATA_LIMITED", "UNAVAILABLE"}
    price_confirmed, trigger, confirmation = _price_action_confirmation(analysis, phase, strict=strict_confirmation)
    if not price_confirmed:
        return False, confirmation, thresholds

    thresholds["trigger"] = trigger
    thresholds["confirmation"] = confirmation
    thresholds["confirmation_mode"] = "STRICT" if strict_confirmation else "STANDARD"

    if opening_range_window and analysis.market_data.breakout_state not in {"BREAKOUT", "BREAKDOWN"}:
        return False, "OPENING RANGE DEVELOPING", thresholds

    return True, "CONFIRMED", thresholds


def _determine_event(
    analysis: StockAnalysis,
    state: dict,
    cfg: AppConfig,
    now: datetime,
    opening_range_window: bool,
) -> dict | None:
    symbol_state = state.setdefault("symbols", {}).setdefault(analysis.symbol, {})
    prev_signal = symbol_state.get("last_signal", "WAIT")

    phase = _v4_session_phase(now, cfg)
    min_rvol = cfg.v4_opening_min_rvol if phase == "OPENING" else cfg.v4_normal_min_rvol
    rvol_quality, _ = _classify_rvol_quality(analysis, cfg, now)
    rvol_value = analysis.market_data.relative_volume
    rvol_text = f"{rvol_value:.2f}" if isinstance(rvol_value, (int, float)) else "Unavailable"
    if rvol_quality == "RELIABLE":
        volume_note = "passed volume gate" if isinstance(rvol_value, (int, float)) and rvol_value >= min_rvol else "below volume gate"
    else:
        volume_note = "evaluating price confirmation"
    print(f"{analysis.symbol}: {analysis.direction_bias} | Phase {phase} | RVOL {rvol_text} | RVOL {rvol_quality} | {volume_note}")

    signal = analysis.signal
    price = analysis.market_data.price
    target_1 = analysis.battle_plan.target_1
    target_2 = analysis.battle_plan.target_2
    invalidation = analysis.battle_plan.invalidation_price

    event_type = None
    emoji = "🚨"
    title = "DAY TRADE ALERT"

    if prev_signal == "LONG" and invalidation is not None and price is not None and price <= invalidation:
        event_type = "LONG_INVALIDATED"
    if prev_signal == "SHORT" and invalidation is not None and price is not None and price >= invalidation:
        event_type = "SHORT_INVALIDATED"

    alerted_targets = set(symbol_state.get("alerted_targets", []))
    if event_type is None:
        if prev_signal == "LONG" and target_1 is not None and price is not None and price >= target_1 and "LONG_TARGET_1" not in alerted_targets:
            event_type = "LONG_TARGET_1"
            emoji = "🎯"
            title = "TARGET REACHED"
        elif prev_signal == "LONG" and target_2 is not None and price is not None and price >= target_2 and "LONG_TARGET_2" not in alerted_targets:
            event_type = "LONG_TARGET_2"
            emoji = "🎯"
            title = "TARGET REACHED"
        elif prev_signal == "SHORT" and target_1 is not None and price is not None and price <= target_1 and "SHORT_TARGET_1" not in alerted_targets:
            event_type = "SHORT_TARGET_1"
            emoji = "🎯"
            title = "TARGET REACHED"
        elif prev_signal == "SHORT" and target_2 is not None and price is not None and price <= target_2 and "SHORT_TARGET_2" not in alerted_targets:
            event_type = "SHORT_TARGET_2"
            emoji = "🎯"
            title = "TARGET REACHED"

    v4 = {"phase": phase, "rvol_quality": rvol_quality, "rvol": rvol_value}
    if event_type is None:
        is_confirmable, live_reason, v4 = _is_live_confirmable(analysis, cfg, opening_range_window, now)
        if not is_confirmable:
            print(
                f"{analysis.symbol}: {analysis.direction_bias} | Phase {v4['phase']} | "
                f"RVOL {rvol_text} | RVOL {v4.get('rvol_quality', rvol_quality)} | {live_reason}. No alert generated."
            )
            return None

        if prev_signal in {"WAIT", "NO_TRADE"} and signal == "LONG":
            event_type = "WAIT_TO_LONG"
        elif prev_signal in {"WAIT", "NO_TRADE"} and signal == "SHORT":
            event_type = "WAIT_TO_SHORT"
        elif prev_signal == "LONG" and signal not in {"LONG", "WAIT"}:
            event_type = "LONG_EXIT"
        elif prev_signal == "SHORT" and signal not in {"SHORT", "WAIT"}:
            event_type = "SHORT_EXIT"

    if event_type is None:
        return None

    last_alert_type = symbol_state.get("last_alert_type")
    last_alert_ts = _parse_ts(symbol_state.get("last_alert_timestamp"))
    if last_alert_type == event_type and last_alert_ts is not None:
        if now - last_alert_ts < timedelta(minutes=cfg.alert_cooldown_minutes):
            return None

    subject_prefix = "🚨"
    if "TARGET" in event_type:
        subject_prefix = "🎯"
    if "INVALIDATED" in event_type:
        subject_prefix = "⚠"

    return {
        "symbol": analysis.symbol,
        "name": analysis.name,
        "signal": signal,
        "event_type": event_type,
        "subject": f"{subject_prefix} Stock Alert - {analysis.symbol} {signal}",
        "title": f"{emoji} {title}",
        "reason": analysis.main_reason,
        "price": price,
        "setup_score": analysis.setup_score,
        "direction_bias": analysis.direction_bias,
        "market_regime": analysis.market_alignment,
        "entry_trigger": analysis.battle_plan.entry_trigger_price,
        "confirmation_level": analysis.battle_plan.confirmation_level,
        "invalidation": analysis.battle_plan.invalidation_price,
        "target_1": target_1,
        "target_2": target_2,
        "risk_reward": analysis.battle_plan.risk_reward_assessment,
        "timestamp": now.isoformat(),
        "timestamp_market": analysis.market_data.intraday_timestamp or analysis.market_data.data_timestamp,
        "level_unavailable_reason": analysis.battle_plan.level_unavailable_reason,
        "rvol": analysis.market_data.relative_volume,
        "rvol_quality": v4.get("rvol_quality", rvol_quality),
        "phase": v4["phase"],
        "v4_trigger": v4.get("trigger"),
        "v4_confirmation": v4.get("confirmation"),
    }


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _send_telegram_alerts(alerts: list[dict], cfg: AppConfig) -> int:
    sent_count = 0
    telegram = TelegramBotProvider(
        enabled=cfg.telegram_enabled,
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
    )

    if not telegram.is_configured:
        print("Telegram notifications disabled.")

    for alert in alerts:
        if not telegram.is_configured:
            print(f"DRY-RUN ALERT: {alert['event_type']} {alert['symbol']}")
            continue
        try:
            message = _render_telegram_message(alert, cfg.live_market_timezone)
            result = telegram.send_message(message=message, parse_mode="HTML")
        except Exception:
            print("Telegram notification failed.")
            continue
        if result.success:
            sent_count += 1
        elif result.disabled:
            print("Telegram notifications disabled.")
        else:
            print("Telegram notification failed.")

    return sent_count


def _update_symbol_state(state: dict, analysis: StockAnalysis, event: dict | None, now: datetime) -> None:
    symbol_state = state.setdefault("symbols", {}).setdefault(analysis.symbol, {})
    symbol_state["last_signal"] = analysis.signal

    if event is None:
        return

    symbol_state["last_alert_type"] = event["event_type"]
    symbol_state["last_alert_timestamp"] = now.isoformat()

    alerted_targets = set(symbol_state.get("alerted_targets", []))
    if "TARGET" in event["event_type"]:
        alerted_targets.add(event["event_type"])
    symbol_state["alerted_targets"] = sorted(alerted_targets)


def _fmt_price(v: float | None) -> str:
    return f"${v:.2f}" if isinstance(v, (int, float)) else "Unavailable"


def _fmt_time_in_tz(ts_raw: str | None, tz_name: str) -> str:
    if not ts_raw:
        return "Unavailable"
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return "Unavailable"
    local = ts.astimezone(ZoneInfo(tz_name))
    return local.strftime("%H:%M %Z")


def _render_telegram_message(alert: dict, market_tz: str) -> str:
    event_type = alert.get("event_type", "")
    symbol = alert["symbol"]
    signal = alert["signal"]
    phase = alert.get("phase", "NORMAL")
    time_label = _fmt_time_in_tz(alert.get("timestamp_market") or alert.get("timestamp"), market_tz)

    if "TARGET" in event_type:
        target_label = "Target 1" if event_type.endswith("_1") else "Target 2"
        target_value = alert.get("target_1") if event_type.endswith("_1") else alert.get("target_2")
        return (
            "<b>🎯 TARGET REACHED</b>\n\n"
            f"<b>{symbol} - {signal}</b>\n\n"
            f"{target_label} reached:\n{_fmt_price(target_value)}\n\n"
            f"Time:\n{time_label}"
        )

    if event_type == "LONG_INVALIDATED":
        return (
            "<b>⚠️ LONG SETUP INVALIDATED</b>\n\n"
            f"<b>{symbol}</b>\n\n"
            f"Invalidation:\n{_fmt_price(alert.get('invalidation'))}\n\n"
            "Reason:\nPrice lost the setup invalidation level.\n\n"
            f"Time:\n{time_label}"
        )

    if event_type == "SHORT_INVALIDATED":
        return (
            "<b>⚠️ SHORT SETUP INVALIDATED</b>\n\n"
            f"<b>{symbol}</b>\n\n"
            f"Invalidation:\n{_fmt_price(alert.get('invalidation'))}\n\n"
            "Reason:\nPrice lost the setup invalidation level.\n\n"
            f"Time:\n{time_label}"
        )

    confirmation_line = (
        "5-minute close above resistance with volume expansion."
        if signal == "LONG"
        else "5-minute close below support with selling-volume expansion."
    )
    rvol_quality = alert.get("rvol_quality")
    if isinstance(alert.get("rvol"), (int, float)):
        if rvol_quality:
            rvol_line = f"RVOL: {alert.get('rvol'):.2f} ({rvol_quality})"
        else:
            rvol_line = f"RVOL: {alert.get('rvol'):.2f}"
    elif rvol_quality in {"DATA_LIMITED", "UNAVAILABLE"}:
        rvol_line = f"RVOL: {rvol_quality}"
    else:
        rvol_line = "RVOL: Unavailable"

    header = "🚨 V4 LONG ALERT" if signal == "LONG" else "🚨 V4 SHORT ALERT"
    return (
        f"<b>{header}</b>\n\n"
        f"Symbol: <b>{symbol}</b>\n"
        f"Phase: {phase}\n"
        f"Bias: {'LONG' if signal == 'LONG' else 'SHORT'}\n\n"
        f"Setup Score: {alert['setup_score']}/100\n"
        f"Price: {_fmt_price(alert.get('price'))}\n\n"
        "Trigger\n"
        f"{alert.get('v4_trigger') or ('Break above ' + _fmt_price(alert.get('entry_trigger')) if signal == 'LONG' else 'Break below ' + _fmt_price(alert.get('entry_trigger')))}\n\n"
        "Confirmation\n"
        f"{alert.get('v4_confirmation') or confirmation_line}\n\n"
        "Invalidation\n"
        f"{_fmt_price(alert.get('invalidation'))}\n\n"
        "Target 1\n"
        f"{_fmt_price(alert.get('target_1'))}\n\n"
        "Target 2\n"
        f"{_fmt_price(alert.get('target_2'))}\n\n"
        f"Market: {alert.get('market_regime', 'Unavailable')}\n"
        f"{rvol_line}"
    ) + f"\n\nTime: {time_label}"


def _render_alert_html(alert: dict) -> str:
    def fmt_price(v: float | None) -> str:
        return f"${v:.2f}" if isinstance(v, (int, float)) else "UNAVAILABLE"

    lines = [
        f"<h2>{alert['title']}</h2>",
        f"<p><strong>{alert['symbol']}</strong> ({alert['name']})</p>",
        f"<p><strong>Signal:</strong> {alert['signal']}</p>",
        f"<p><strong>Price:</strong> {fmt_price(alert['price'])}</p>",
        f"<p><strong>Trigger:</strong> {'Break above' if alert['signal'] == 'LONG' else 'Break below'} {fmt_price(alert['entry_trigger'])}</p>",
        f"<p><strong>Confirmation:</strong> 5-minute close above/below {fmt_price(alert['confirmation_level'])} with volume expansion.</p>",
        f"<p><strong>Stop / Invalidation:</strong> {fmt_price(alert['invalidation'])}</p>",
        f"<p><strong>Target 1:</strong> {fmt_price(alert['target_1'])}</p>",
        f"<p><strong>Target 2:</strong> {fmt_price(alert['target_2'])}</p>",
        f"<p><strong>Setup Score:</strong> {alert['setup_score']}/100</p>",
        f"<p><strong>Market:</strong> {alert['market_regime']}</p>",
        f"<p><strong>Reason:</strong> {alert['reason']}</p>",
        f"<p><strong>Data timestamp:</strong> {alert['timestamp_market'] or 'UNAVAILABLE'}</p>",
    ]

    if alert.get("level_unavailable_reason"):
        lines.append("<p><strong>Entry level:</strong> UNAVAILABLE</p>")
        lines.append(f"<p><strong>Reason:</strong> {alert['level_unavailable_reason']}</p>")

    return "\n".join(lines)


def main() -> int:
    return run_live_alerts()


if __name__ == "__main__":
    raise SystemExit(main())
