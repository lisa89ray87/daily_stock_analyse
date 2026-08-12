from __future__ import annotations

import json
import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import AppConfig, load_config
from .market import build_market_regime
from .market_hours import MarketSessionStatus, get_market_session_status, is_weekday_in_timezone, utc_now
from .models import StockAnalysis
from .providers import YFinanceNewsProvider, create_market_data_provider
from .analysis_service import analyze_symbol as _analyze_symbol
from .telegram_provider import TelegramBotProvider


ALERT_REASON_RVOL_TOO_LOW = "RVOL_TOO_LOW"
ALERT_REASON_RR_TOO_LOW = "RR_TOO_LOW"
ALERT_REASON_ENTRY_NOT_CONFIRMED = "ENTRY_NOT_CONFIRMED"
ALERT_REASON_PHASE_BLOCKED = "PHASE_BLOCKED"
ALERT_REASON_COOLDOWN = "COOLDOWN"
ALERT_REASON_DUPLICATE_POSITION = "DUPLICATE_POSITION"
ALERT_REASON_INVALID_RISK_LEVELS = "INVALID_RISK_LEVELS"
ALERT_REASON_DIRECTION_LEVEL_MISMATCH = "DIRECTION_LEVEL_MISMATCH"
ALERT_REASON_NO_ALERT = "NO_ALERT"
ALERT_REASON_TRIGGER_INVALIDATED = "TRIGGER_INVALIDATED"
ALERT_REASON_TRIGGER_EXPIRED = "TRIGGER_EXPIRED"

VOLUME_STATE_WAIT_FOR_VOLUME = "WAIT_FOR_VOLUME"
VOLUME_STATE_VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
VOLUME_STATE_VOLUME_LOST = "VOLUME_LOST"

MIN_RISK_REWARD = 1.5


@dataclass
class AlertEligibilityResult:
    eligible: bool
    reason: str
    direction: str
    entry: float | None
    stop: float | None
    target1: float | None
    target2: float | None
    risk_reward: float | None
    setup_score: int
    rvol: float | None
    rvol_quality: str
    detail: str | None = None


@dataclass
class TradeLevels:
    direction: str
    entry: float | None
    stop: float | None
    target1: float | None
    target2: float | None
    risk_reward: float | None
    source: str
    detail: str | None = None


@dataclass
class TriggerEvidence:
    confirmed: bool
    direction: str
    trigger_type: str
    trigger_price: float | None
    reference_level: float | None
    current_price: float | None
    timestamp: str | None
    observed_at: str | None
    detail: str


@dataclass
class TriggerLifecycle:
    state: str
    detail: str


@dataclass
class VolumeLifecycle:
    state: str
    rvol: float | None
    required_rvol: float
    detail: str


@dataclass(frozen=True)
class LiveSessionPolicy:
    session_state: str
    allows_regular_session_triggers: bool
    allows_opening_range_confirmation: bool
    allows_vwap_confirmation: bool
    allows_telegram_trade_entry_alerts: bool
    allows_regular_session_candle_confirmation: bool
    reason: str


def _live_session_policy(now_utc: datetime, cfg: AppConfig) -> LiveSessionPolicy:
    session = get_market_session_status(
        now_utc,
        market_timezone=cfg.live_market_timezone,
        market_open_hhmm=cfg.live_market_open,
        market_close_hhmm=cfg.live_market_close,
    )
    state = session.session_state

    if state == "US_REGULAR":
        return LiveSessionPolicy(
            session_state=state,
            allows_regular_session_triggers=True,
            allows_opening_range_confirmation=True,
            allows_vwap_confirmation=True,
            allows_telegram_trade_entry_alerts=True,
            allows_regular_session_candle_confirmation=True,
            reason="Regular-session live trigger engine enabled",
        )

    if state == "PRE_MARKET":
        reason = "Regular-session trigger confirmation disabled until the 09:30 ET U.S. open"
    elif state == "AFTER_HOURS":
        reason = "Regular-session trigger confirmation disabled after the 16:00 ET U.S. close"
    else:
        reason = "Trading triggers are disabled because the U.S. market is closed"

    return LiveSessionPolicy(
        session_state=state,
        allows_regular_session_triggers=False,
        allows_opening_range_confirmation=False,
        allows_vwap_confirmation=False,
        allows_telegram_trade_entry_alerts=False,
        allows_regular_session_candle_confirmation=False,
        reason=reason,
    )


def run_live_alerts(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    cfg = load_config(repo_root)

    if not cfg.live_alert_enabled:
        print("LIVE_ALERT_ENABLED=0, exiting without alerts.")
        return 0

    interval_minutes = max(1, int(cfg.live_alert_interval_minutes or 5))
    print("Live alert service started")
    print(f"Timezone: {cfg.live_market_timezone}")
    print(f"Market open: {cfg.live_market_open}")
    print(f"Market close: {cfg.live_market_close}")
    print(f"Evaluation interval: {interval_minutes} minutes")

    evaluation_count = 0
    while True:
        now = utc_now()
        if not is_weekday_in_timezone(now, cfg.morning_report_timezone):
            print(f"Outside Monday-Friday Malaysia schedule | now={now.astimezone(ZoneInfo(cfg.morning_report_timezone)).isoformat()}")
            _write_live_snapshot(repo_root, {"market_open": False, "reason": "Outside Monday-Friday Malaysia schedule", "alerts": []})
            print("Live alert service stopped cleanly")
            return 0

        session = get_market_session_status(
            now,
            market_timezone=cfg.live_market_timezone,
            market_open_hhmm=cfg.live_market_open,
            market_close_hhmm=cfg.live_market_close,
        )

        evaluation_count += 1
        print(
            f"Evaluation #{evaluation_count} started | session={session.session_state} | "
            f"New York time {session.market_now.isoformat()}",
            flush=True,
        )

        try:
            _run_live_alert_evaluation_cycle(repo_root, cfg, now, session)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            print(f"Evaluation #{evaluation_count} failed: {exc}", flush=True)

        sleep_seconds = interval_minutes * 60
        print(
            f"Evaluation #{evaluation_count} complete | "
            f"Next evaluation in approximately {interval_minutes} minutes",
            flush=True,
        )
        time_module.sleep(sleep_seconds)


def _is_pre_market_wait_state(session: MarketSessionStatus) -> bool:
    if session.market_open:
        return False
    if session.market_open_time is None:
        return False
    return session.market_now < session.market_open_time


def _seconds_until_market_open_or_interval(session: MarketSessionStatus, interval_minutes: int) -> int:
    if session.market_open_time is None:
        return max(30, interval_minutes * 60)

    seconds_to_open = int((session.market_open_time - session.market_now).total_seconds())
    if seconds_to_open <= 0:
        return max(5, interval_minutes * 60)

    return max(5, min(seconds_to_open, interval_minutes * 60))


def _run_live_alert_evaluation_cycle(
    repo_root: Path,
    cfg: AppConfig,
    now: datetime,
    session: MarketSessionStatus,
) -> int:
    phase = _v4_session_phase(now, cfg)
    print(f"V4 phase: {phase}", flush=True)
    policy = _live_session_policy(now, cfg)
    print(f"Live session policy | session={policy.session_state} | triggers_enabled={'yes' if policy.allows_regular_session_triggers else 'no'} | reason={policy.reason}", flush=True)

    market_provider = create_market_data_provider(cfg.live_data_provider)
    news_provider = YFinanceNewsProvider()
    regime = build_market_regime()
    sector_strength = regime.indicators.get("semiconductor_etf_change_pct")

    symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    print(f"LIVE_ANALYSIS | symbols={len(symbols)} | max_workers={cfg.event_alert_max_workers}", flush=True)

    def analyze_one(symbol: str) -> tuple[str, StockAnalysis | None, str | None]:
        print(f"LIVE_SYMBOL | symbol={symbol} | stage=start", flush=True)
        try:
            analysis = _analyze_symbol(symbol, cfg, regime.label, sector_strength, market_provider, news_provider, now_utc=now)
            print(f"LIVE_SYMBOL | symbol={symbol} | stage=complete | setup_score={analysis.setup_score} | signal={analysis.signal}", flush=True)
            return symbol, analysis, None
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            print(f"LIVE_SYMBOL | symbol={symbol} | stage=error | error={exc}", flush=True)
            return symbol, None, str(exc)

    analyses: list[StockAnalysis] = []
    worker_count = max(1, min(int(cfg.event_alert_max_workers or 6), len(symbols) or 1))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="live-analysis") as executor:
        futures = {executor.submit(analyze_one, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            _, analysis, _ = future.result()
            if analysis is not None:
                analyses.append(analysis)

    print(f"LIVE_ANALYSIS | stage=complete | analyzed={len(analyses)} | errors={len(symbols) - len(analyses)}", flush=True)

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
            "market_open": getattr(session, "market_open", session.session_state == "US_REGULAR"),
            "market_reason": session.reason,
            "market_session": session.session_state,
            "live_session_policy": {
                "session_state": policy.session_state,
                "allows_regular_session_triggers": policy.allows_regular_session_triggers,
                "reason": policy.reason,
            },
            "v4_phase": phase,
            "opening_range_window": session.opening_range_window,
            "market_time": session.market_now.isoformat(),
            "alerts": sent_alerts,
            "alerts_sent": sent_count,
        },
    )
    print(f"Live alert evaluation complete. Alerts generated: {len(sent_alerts)}", flush=True)
    return len(sent_alerts)


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


def _classify_rvol_quality(analysis: StockAnalysis, cfg: AppConfig, now_utc: datetime) -> tuple[str, str, str]:
    md = analysis.market_data
    policy = _live_session_policy(now_utc, cfg)
    session_label = "REGULAR_SESSION" if policy.session_state == "US_REGULAR" else policy.session_state

    if policy.session_state != "US_REGULAR":
        if md.intraday_rvol is not None:
            return (
                "DATA_LIMITED",
                f"Regular-session intraday RVOL is stale during {policy.session_state}",
                session_label,
            )
        if md.relative_volume is None:
            return "UNAVAILABLE", "RVOL missing from provider", session_label
        return (
            "DATA_LIMITED",
            f"Using daily relative volume proxy during {policy.session_state}",
            session_label,
        )

    if md.intraday_rvol is not None:
        return md.intraday_rvol_quality or "AVAILABLE", md.intraday_rvol_note or "Intraday RVOL available", session_label
    if md.relative_volume is not None:
        return "DATA_LIMITED", "Using daily relative volume proxy; intraday RVOL unavailable", session_label
    return "UNAVAILABLE", "RVOL missing from provider", session_label


# The remainder of the live-alert decision, lifecycle, Telegram, and snapshot helpers
# intentionally remain unchanged from the existing implementation.
