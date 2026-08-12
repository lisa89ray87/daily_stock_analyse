from __future__ import annotations

import html
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, time as dt_time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import yfinance as yf

from .config import load_config
from .event_alerts import EventAlert, detect_event_alerts
from .market_hours import get_market_session_status, is_weekday_in_timezone, utc_now
from .session_windows import is_time_in_window
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


def _hhmm(name: str, default: str) -> dt_time:
    raw = os.getenv(name, default).strip()
    hour, minute = (int(part) for part in raw.split(":", 1))
    return dt_time(hour=hour, minute=minute)


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
        previous = datetime.fromisoformat(sent_at)
        return now - previous < timedelta(minutes=cooldown_minutes)
    except (TypeError, ValueError):
        return False


def _mark_sent(state: dict, event: EventAlert, now) -> None:
    state.setdefault("symbols", {}).setdefault(event.symbol, {})[event.key] = now.isoformat()


def _message(events: list[EventAlert], market_time: str, session_state: str, batch_number: int = 1, batch_count: int = 1) -> str:
    if session_state == "US_REGULAR":
        session_label = "REGULAR"
    elif session_state == "PRE_MARKET":
        session_label = "PRE-MARKET"
    else:
        session_label = "EXTENDED / OVERNIGHT"
    batch_label = f" | Batch {batch_number}/{batch_count}" if batch_count > 1 else ""
    lines = ["<b>⚠️ LIVE EVENT WARNING</b>", f"<i>{html.escape(market_time)} | {session_label}{batch_label}</i>", ""]
    for event in events:
        emoji = "🟢" if event.direction == "BULLISH" else "🔴" if event.direction == "BEARISH" else "🟡"
        price = f"${event.price:.2f}" if event.price is not None else "N/A"
        lines.extend([
            f"<b>{emoji} {html.escape(event.symbol)} — {html.escape(event.event_type)}</b>",
            f"Price: {price}",
            html.escape(str(event.detail)),
            f"Severity: {html.escape(event.severity)}",
            "",
        ])
    lines.append("<i>Early warning only — not a confirmed trade entry.</i>")
    return "\n".join(lines)


def _message_batches(events: list[EventAlert], market_time: str, session_state: str, max_chars: int = 3800) -> list[list[EventAlert]]:
    """Split event alerts into Telegram-safe batches below the 4096-char API limit."""
    if not events:
        return []
    batches: list[list[EventAlert]] = []
    current: list[EventAlert] = []
    for event in events:
        candidate = current + [event]
        if current and len(_message(candidate, market_time, session_state, 1, 99)) > max_chars:
            batches.append(current)
            current = [event]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _extended_hours_bars(symbol: str, market_tz: ZoneInfo) -> list[dict[str, float | str]]:
    """Fetch 5-minute regular/pre-market/after-hours/overnight bars from yfinance."""
    frame = yf.Ticker(symbol).history(period="2d", interval="5m", prepost=True, auto_adjust=False)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame.empty or not required.issubset(frame.columns):
        return []
    df = frame.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(UTC)
    df.index = df.index.tz_convert(market_tz)

    extended_close = dt_time(4, 0)
    current_latest = df.index.max()
    anchor_date = current_latest.date()
    if current_latest.time() < dt_time(4, 0):
        anchor_date -= timedelta(days=1)

    # Previous regular session plus the current day's pre-market/overnight data.
    regular_and_after = (df.index.date == anchor_date) & (df.index.time >= dt_time(9, 30))
    overnight = (df.index.date == anchor_date + timedelta(days=1)) & (df.index.time < extended_close)
    premarket = (df.index.date == anchor_date + timedelta(days=1)) & (df.index.time >= extended_close) & (df.index.time < dt_time(9, 30))
    df = df[regular_and_after | overnight | premarket]
    if df.empty:
        return []
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return [
        {
            "ts": ts.isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for ts, row in df.iterrows()
    ]


def _evaluate_symbol(symbol: str, provider, cfg, session_state: str) -> tuple[str, list[EventAlert], str | None]:
    print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=start", flush=True)
    try:
        print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=market_data_start", flush=True)
        md = provider.get_market_data(symbol)
        print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=market_data_complete", flush=True)

        md.session_state = session_state
        if session_state in {"AFTER_HOURS", "PRE_MARKET"} and _flag("EVENT_ALERT_AFTER_HOURS_ENABLED", True):
            extended = _extended_hours_bars(symbol, ZoneInfo(cfg.live_market_timezone))
            md.extended_intraday_bars = extended
            if extended:
                md.intraday_bars = extended
                md.price = float(extended[-1]["close"])
                md.selected_price_session = "PREMARKET" if session_state == "PRE_MARKET" else "AFTER_HOURS"
                if session_state == "PRE_MARKET":
                    md.premarket_price = md.price
                else:
                    md.after_hours_price = md.price
                md.latest_extended_price = md.price
                md.latest_extended_session = md.selected_price_session
                md.extended_hours_used = True
                md.is_extended_hours = True
                print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=extended_data_complete | bars={len(extended)} | session={session_state}", flush=True)
            else:
                print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=extended_data_unavailable | status=DATA_LIMITED | session={session_state}", flush=True)

        analysis = SimpleNamespace(symbol=symbol, market_data=md)
        events = detect_event_alerts(analysis, cfg)
        print(f"EVENT_ALERT_SYMBOL | symbol={symbol} | stage=detection_complete | events={len(events)} | session={session_state}", flush=True)
        return symbol, events, None
    except Exception as exc:
        error = repr(exc)
        print(f"EVENT_ALERT_DIAGNOSTIC | symbol={symbol} | status=ERROR | error={error}", flush=True)
        return symbol, [], error


def _evaluate_symbols_concurrently(symbols: list[str], provider, cfg, max_workers: int, session_state: str) -> tuple[list[EventAlert], int, int]:
    worker_count = min(max(1, max_workers), max(1, len(symbols)))
    print(f"EVENT_ALERT_CONCURRENCY | stage=start | symbols={len(symbols)} | max_workers={worker_count} | session={session_state}", flush=True)
    results: dict[str, list[EventAlert]] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="event-alert") as executor:
        futures = {executor.submit(_evaluate_symbol, symbol, provider, cfg, session_state): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result_symbol, events, error = future.result()
            except Exception as exc:
                result_symbol, events, error = symbol, [], repr(exc)
                print(f"EVENT_ALERT_DIAGNOSTIC | symbol={symbol} | status=ERROR | error={error}", flush=True)
            results[result_symbol] = events
            if error is not None:
                errors += 1

    pending_events: list[EventAlert] = []
    detected = 0
    for symbol in symbols:
        events = results.get(symbol, [])
        detected += len(events)
        pending_events.extend(events)
    print(f"EVENT_ALERT_CONCURRENCY | stage=complete | detected={detected} | errors={errors} | session={session_state}", flush=True)
    return pending_events, detected, errors


def run_event_alerts(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    _startup("begin", repo_root=repo_root)

    cfg = load_config(repo_root)
    extended_close = _hhmm("LIVE_EXTENDED_CLOSE", "04:00")
    _startup("config", live_provider=cfg.live_data_provider, timezone=cfg.live_market_timezone, max_workers=cfg.event_alert_max_workers, after_hours_enabled=_flag("EVENT_ALERT_AFTER_HOURS_ENABLED", True), extended_close=extended_close.strftime("%H:%M"))

    if not _flag("EVENT_ALERT_ENABLED", True):
        print("EVENT_ALERT_ENABLED=0, exiting without event alerts.", flush=True)
        return 0

    interval = max(1, _int("EVENT_ALERT_INTERVAL_MINUTES", 5))
    cooldown = max(0, _int("EVENT_ALERT_COOLDOWN_MINUTES", 15))
    state_path = repo_root / "artifacts" / "event_alert_state.json"
    state = _load_state(state_path)
    _startup("state", path=state_path, symbols=len(state.get("symbols", {})))

    telegram = TelegramBotProvider(enabled=cfg.telegram_enabled, bot_token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id)
    _startup("telegram", configured=telegram.is_configured)
    provider = create_market_data_provider(cfg.live_data_provider)
    _startup("market_provider", provider=cfg.live_data_provider)
    symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    _startup("symbols", count=len(symbols), symbols=",".join(symbols))

    print("Live event alert service started", flush=True)
    print(f"Timezone: {cfg.live_market_timezone}", flush=True)
    print(f"Evaluation interval: {interval} minutes", flush=True)
    print(f"Event cooldown: {cooldown} minutes", flush=True)
    print(f"Symbols: {len(symbols)}", flush=True)
    print(f"Max concurrent workers: {cfg.event_alert_max_workers}", flush=True)
    print(f"Extended-hours alerts: {'enabled' if _flag('EVENT_ALERT_AFTER_HOURS_ENABLED', True) else 'disabled'} through {extended_close.strftime('%H:%M')} ET", flush=True)
    print("Pre-market alerts: enabled from 04:00 ET until the 09:30 ET U.S. open", flush=True)
    _startup("ready")

    cycle = 0
    while True:
        cycle += 1
        now = utc_now()
        if not is_weekday_in_timezone(now, cfg.live_market_timezone):
            print("Outside Monday-Friday schedule; event alert service stopped cleanly", flush=True)
            return 0

        session = get_market_session_status(now, market_timezone=cfg.live_market_timezone, market_open_hhmm=cfg.live_market_open, market_close_hhmm=cfg.live_market_close)
        print(f"EVENT_ALERT_EVALUATION | cycle={cycle} | session={session.session_state} | New York={session.market_now.isoformat()}", flush=True)

        if session.session_state == "AFTER_HOURS":
            if not _flag("EVENT_ALERT_AFTER_HOURS_ENABLED", True):
                print("EVENT_ALERT_EVALUATION | extended-hours alerts disabled by configuration", flush=True)
                return 0
            if not is_time_in_window(session.market_now.time(), dt_time(16, 0), extended_close):
                print(f"EVENT_ALERT_EVALUATION | extended-hours window ended at {extended_close.strftime('%H:%M')} ET; stopped cleanly", flush=True)
                return 0
        elif session.session_state == "PRE_MARKET":
            if not _flag("EVENT_ALERT_PRE_MARKET_ENABLED", True):
                print("EVENT_ALERT_EVALUATION | pre-market alerts disabled by configuration", flush=True)
                time.sleep(interval * 60)
                continue
        elif session.session_state != "US_REGULAR":
            print("EVENT_ALERT_EVALUATION | waiting for US_REGULAR, PRE_MARKET, or AFTER_HOURS session", flush=True)
            time.sleep(interval * 60)
            continue

        detected_events, detected, errors = _evaluate_symbols_concurrently(symbols, provider, cfg, cfg.event_alert_max_workers, session.session_state)
        pending: list[EventAlert] = []
        suppressed = 0
        for event in detected_events:
            if _is_suppressed(state, event, now, cooldown):
                suppressed += 1
                continue
            pending.append(event)

        print(f"EVENT_ALERT_DIAGNOSTIC | detected={detected} | pending={len(pending)} | cooldown_suppressed={suppressed} | symbol_errors={errors} | telegram_configured={telegram.is_configured} | session={session.session_state}", flush=True)

        if pending:
            batches = _message_batches(pending, session.market_now.isoformat(), session.session_state)
            print(f"EVENT_ALERT_TELEGRAM_BATCH | batches={len(batches)} | events={len(pending)} | session={session.session_state}", flush=True)
            delivered = 0
            for index, batch in enumerate(batches, start=1):
                message = _message(batch, session.market_now.isoformat(), session.session_state, index, len(batches))
                result = telegram.send_message(message)
                print(f"EVENT_ALERT_TELEGRAM_BATCH | batch={index}/{len(batches)} | events={len(batch)} | chars={len(message)} | success={result.success} | status_code={result.status_code} | error={result.error or 'NONE'} | session={session.session_state}", flush=True)
                if result.success:
                    delivered += len(batch)
                    for event in batch:
                        _mark_sent(state, event, now)
                else:
                    print(f"EVENT_ALERT_TELEGRAM | attempted=True | success=False | failed_batch={index}/{len(batches)} | delivered_events={delivered} | session={session.session_state}", flush=True)
                    break
            print(f"EVENT_ALERT_TELEGRAM | attempted=True | success={delivered == len(pending)} | delivered={delivered}/{len(pending)} | session={session.session_state}", flush=True)
        else:
            print("EVENT_ALERT_TELEGRAM | attempted=False | reason=NO_NEW_EVENTS", flush=True)

        state["updated_at"] = now.isoformat()
        _save_state(state_path, state)
        time.sleep(interval * 60)


if __name__ == "__main__":
    raise SystemExit(run_event_alerts())
