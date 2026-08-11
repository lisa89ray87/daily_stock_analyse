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


def _startup(stage: str, status: str = "OK", **fields: object) -> None:
    suffix = "".join(f" | {key}={value}" for key, value in fields.items())
    print(f"EVENT_ALERT_STARTUP | stage={stage} | status={status}{suffix}", flush=True)


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
    _startup("begin", repo_root=repo_root)

    try:
        cfg = load_config(repo_root)
        _startup("config", live_provider=cfg.live_data_provider, timezone=cfg.live_market_timezone)
    except Exception as exc:
        _startup("config", "ERROR", error=repr(exc))
        raise

    if not _flag("EVENT_ALERT_ENABLED", True):
        print("EVENT_ALERT_ENABLED=0, exiting without event alerts.", flush=True)
        return 0

    interval = max(1, _int("EVENT_ALERT_INTERVAL_MINUTES", 5))
    cooldown = max(0, _int("EVENT_ALERT_COOLDOWN_MINUTES", 15))
    state_path = repo_root / "artifacts" / "event_alert_state.json"

    try:
        state = _load_state(state_path)
        _startup("state", path=state_path, symbols=len(state.get("symbols", {})))
    except Exception as exc:
        _startup("state", "ERROR", error=repr(exc))
        raise

    try:
        telegram = TelegramBotProvider(
            enabled=cfg.telegram_enabled,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
        )
        _startup("telegram", configured=telegram.is_configured)
    except Exception as exc:
        _startup("telegram", "ERROR", error=repr(exc))
        raise

    try:
        provider = create_market_data_provider(cfg.live_data_provider)
        _startup("market_provider", provider=cfg.live_data_provider)
    except Exception as exc:
        _startup("market_provider", "ERROR", error=repr(exc))
        raise

    symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    _startup("symbols", count=len(symbols), symbols=",".join(symbols))

    print("Live event alert service started", flush=True)
    print(f"Timezone: {cfg.live_market_timezone}", flush=True)
    print(f"Evaluation interval: {interval} minutes", flush=True)
    print(f"Event cooldown: {cooldown} minutes", flush=True)
    print(f"Symbols: {len(symbols)}", flush=True)
    _startup("ready")

    cycle = 0
    while True:
        cycle += 1
        now = utc_now()
        if not is_weekday_in_timezone(now, cfg.live_market_timezone):
            print("Outside Monday-Friday schedule; event alert service stopped cleanly", flush=True)
            return 0

        session = get_market_session_status(
            now,
            market_timezone=cfg.live_market_timezone,
            market_open_hhmm=cfg.live_market_open,
            market_close_hhmm=cfg.live_market_close,
        )
        print(
            f"EVENT_ALERT_EVALUATION | cycle={cycle} | session={session.session_state} | "
            f"New York={session.market_now.isoformat()}",
            flush=True,
        )

        if session.session_state == "AFTER_HOURS":
            print("EVENT_ALERT_EVALUATION | regular-session window ended; event alert service stopped cleanly", flush=True)
            return 0

        if session.session_state != "US_REGULAR":
            print("EVENT_ALERT_EVALUATION | waiting for US_REGULAR session", flush=True)
            time.sleep(interval * 60)
            continue

        pending: list[EventAlert] = []
        detected = 0
        suppressed = 0
        for symbol in symbols:
            print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=start", flush=True)
            try:
                print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=market_data_start", flush=True)
                md = provider.get_market_data(symbol)
                print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=market_data_complete", flush=True)
                analysis = SimpleNamespace(symbol=symbol, market_data=md)
                events = detect_event_alerts(analysis, cfg)
                print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=detection_complete | events={len(events)}", flush=True)
                detected += len(events)
                for event in events:
                    if _is_suppressed(state, event, now, cooldown):
                        suppressed += 1
                        continue
                    pending.append(event)
            except Exception as exc:
                print(f"EVENT_ALERT_DIAGNOSTIC | symbol={symbol} | status=ERROR | error={exc}", flush=True)

        print(
            f"EVENT_ALERT_DIAGNOSTIC | detected={detected} | "
            f"pending={len(pending)} | cooldown_suppressed={suppressed} | "
            f"telegram_configured={telegram.is_configured}",
            flush=True,
        )

        if pending:
            message = _message(pending, session.market_now.isoformat())
            result = telegram.send_message(message)
            print(
                f"EVENT_ALERT_TELEGRAM | attempted=True | success={result.success} | "
                f"status_code={result.status_code} | error={result.error or 'NONE'}",
                flush=True,
            )
            if result.success:
                for event in pending:
                    _mark_sent(state, event, now)
        else:
            print("EVENT_ALERT_TELEGRAM | attempted=False | reason=NO_NEW_EVENTS", flush=True)

        state["updated_at"] = now.isoformat()
        _save_state(state_path, state)
        time.sleep(interval * 60)


if __name__ == "__main__":
    raise SystemExit(run_event_alerts())
