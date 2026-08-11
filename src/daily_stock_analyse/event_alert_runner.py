from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from .config import load_config
from .event_alerts import EventAlert, detect_event_alerts
from .market_hours import get_market_session_status, is_weekday_in_timezone, utc_now
from .providers import create_market_data_provider
from .telegram_provider import TelegramBotProvider


def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"symbols": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"symbols": {}}
    except (OSError, json.JSONDecodeError):
        return {"symbols": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _is_suppressed(state: dict, event: EventAlert, now, cooldown_minutes: int) -> bool:
    item = state.setdefault("symbols", {}).setdefault(event.symbol, {})
    sent_at = item.get(event.key)
    if not sent_at:
        return False
    try:
        previous = __import__("datetime").datetime.fromisoformat(sent_at)
        return now - previous < timedelta(minutes=cooldown_minutes)
    except (TypeError, ValueError):
        return False


def _mark_sent(state: dict, event: EventAlert, now) -> None:
    state.setdefault("symbols", {}).setdefault(event.symbol, {})[event.key] = now.isoformat()


def _message(events: list[EventAlert], market_time: str) -> str:
    lines = ["<b>⚠️ LIVE EVENT WARNING</b>", f"<i>{market_time}</i>", ""]
    for event in events:
        emoji = "🟢" if event.direction == "BULLISH" else "🔴" if event.direction == "BEARISH" else "🟡"
        price = f"${event.price:.2f}" if event.price is not None else "N/A"
        lines.extend([
            f"<b>{emoji} {event.symbol} — {event.event_type}</b>",
            f"Price: {price}",
            f"{event.detail}",
            f"Severity: {event.severity}",
            "",
        ])
    lines.append("<i>Early warning only — not a confirmed trade entry.</i>")
    return "\n".join(lines)


def run_event_alerts(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    cfg = load_config(repo_root)
    if not _flag("EVENT_ALERT_ENABLED", True):
        print("EVENT_ALERT_ENABLED=0, exiting without event alerts.")
        return 0

    interval = max(1, _int("EVENT_ALERT_INTERVAL_MINUTES", 5))
    cooldown = max(0, _int("EVENT_ALERT_COOLDOWN_MINUTES", 15))
    state_path = repo_root / "artifacts" / "event_alert_state.json"
    state = _load_state(state_path)
    telegram = TelegramBotProvider(
        enabled=cfg.telegram_enabled,
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
    )
    provider = create_market_data_provider(cfg.live_data_provider)
    symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))

    print("Live event alert service started")
    print(f"Timezone: {cfg.live_market_timezone}")
    print(f"Evaluation interval: {interval} minutes")
    print(f"Event cooldown: {cooldown} minutes")
    print(f"Symbols: {len(symbols)}")

    while True:
        now = utc_now()
        if not is_weekday_in_timezone(now, cfg.live_market_timezone):
            print("Outside Monday-Friday schedule; event alert service stopped cleanly")
            return 0

        session = get_market_session_status(
            now,
            market_timezone=cfg.live_market_timezone,
            market_open_hhmm=cfg.live_market_open,
            market_close_hhmm=cfg.live_market_close,
        )
        print(f"EVENT_ALERT_EVALUATION | session={session.session_state} | New York={session.market_now.isoformat()}")

        if session.session_state == "AFTER_HOURS":
            print("EVENT_ALERT_EVALUATION | regular-session window ended; event alert service stopped cleanly")
            return 0

        if session.session_state != "US_REGULAR":
            print("EVENT_ALERT_EVALUATION | waiting for US_REGULAR session")
            time.sleep(interval * 60)
            continue

        pending: list[EventAlert] = []
        detected = 0
        suppressed = 0
        for symbol in symbols:
            try:
                md = provider.get_market_data(symbol)
                analysis = SimpleNamespace(symbol=symbol, market_data=md)
                events = detect_event_alerts(analysis, cfg)
                detected += len(events)
                for event in events:
                    if _is_suppressed(state, event, now, cooldown):
                        suppressed += 1
                        continue
                    pending.append(event)
            except Exception as exc:
                print(f"EVENT_ALERT_DIAGNOSTIC | symbol={symbol} | status=ERROR | error={exc}")

        print(
            f"EVENT_ALERT_DIAGNOSTIC | detected={detected} | "
            f"pending={len(pending)} | cooldown_suppressed={suppressed} | "
            f"telegram_configured={telegram.is_configured}"
        )

        if pending:
            message = _message(pending, session.market_now.isoformat())
            result = telegram.send_message(message)
            print(
                f"EVENT_ALERT_TELEGRAM | attempted=True | success={result.success} | "
                f"status_code={result.status_code} | error={result.error or 'NONE'}"
            )
            if result.success:
                for event in pending:
                    _mark_sent(state, event, now)
        else:
            print("EVENT_ALERT_TELEGRAM | attempted=False | reason=NO_NEW_EVENTS")

        state["updated_at"] = now.isoformat()
        _save_state(state_path, state)
        time.sleep(interval * 60)


if __name__ == "__main__":
    raise SystemExit(run_event_alerts())
