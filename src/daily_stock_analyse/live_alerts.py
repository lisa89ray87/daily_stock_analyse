from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


def _is_live_confirmable(analysis: StockAnalysis, cfg: AppConfig, opening_range_window: bool) -> tuple[bool, str]:
    md = analysis.market_data

    if md.price is None:
        return False, "Price unavailable"
    if md.vwap is None and md.opening_range_high is None:
        return False, "Live confirmation unavailable"
    if md.relative_volume is None or md.relative_volume < cfg.alert_min_rvol:
        return False, "Relative volume below live threshold"
    if analysis.setup_score < cfg.alert_min_setup_score:
        return False, "Setup score below live threshold"
    if analysis.battle_plan.entry_trigger_price is None:
        return False, "Exact entry level unavailable"

    if opening_range_window and analysis.market_data.breakout_state not in {"BREAKOUT", "BREAKDOWN"}:
        return False, "OPENING RANGE DEVELOPING"

    return True, "CONFIRMED"


def _determine_event(
    analysis: StockAnalysis,
    state: dict,
    cfg: AppConfig,
    now: datetime,
    opening_range_window: bool,
) -> dict | None:
    symbol_state = state.setdefault("symbols", {}).setdefault(analysis.symbol, {})
    prev_signal = symbol_state.get("last_signal", "WAIT")

    is_confirmable, live_reason = _is_live_confirmable(analysis, cfg, opening_range_window)
    if not is_confirmable:
        print(f"{analysis.symbol}: {analysis.direction_bias} | {live_reason}. No alert generated.")
        return None

    signal = analysis.signal
    price = analysis.market_data.price

    target_1 = analysis.battle_plan.target_1
    target_2 = analysis.battle_plan.target_2
    invalidation = analysis.battle_plan.invalidation_price

    event_type = None
    emoji = "🚨"
    title = "DAY TRADE ALERT"

    if prev_signal in {"WAIT", "NO_TRADE"} and signal == "LONG":
        event_type = "WAIT_TO_LONG"
    elif prev_signal in {"WAIT", "NO_TRADE"} and signal == "SHORT":
        event_type = "WAIT_TO_SHORT"
    elif prev_signal == "LONG" and signal not in {"LONG", "WAIT"}:
        event_type = "LONG_EXIT"
    elif prev_signal == "SHORT" and signal not in {"SHORT", "WAIT"}:
        event_type = "SHORT_EXIT"

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
    rvol_line = f"RVOL: {alert.get('rvol'):.2f}" if isinstance(alert.get("rvol"), (int, float)) else "RVOL: Unavailable"

    header = "🚨 LONG ALERT" if signal == "LONG" else "🚨 SHORT ALERT"
    return (
        f"<b>{header}</b>\n\n"
        f"<b>{symbol}</b>\n\n"
        f"Setup Score: {alert['setup_score']}/100\n"
        f"Price: {_fmt_price(alert.get('price'))}\n\n"
        "Trigger\n"
        f"{'Break above' if signal == 'LONG' else 'Break below'} {_fmt_price(alert.get('entry_trigger'))}\n\n"
        "Confirmation\n"
        f"{confirmation_line}\n\n"
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
