from __future__ import annotations

import json
import time as time_module
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
            f"New York time {session.market_now.isoformat()}"
        )

        try:
            _run_live_alert_evaluation_cycle(repo_root, cfg, now, session)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            print(f"Evaluation #{evaluation_count} failed: {exc}")

        sleep_seconds = interval_minutes * 60
        print(
            f"Evaluation #{evaluation_count} complete | "
            f"Next evaluation in approximately {interval_minutes} minutes"
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
    print(f"V4 phase: {phase}")
    policy = _live_session_policy(now, cfg)
    print(f"Live session policy | session={policy.session_state} | triggers_enabled={'yes' if policy.allows_regular_session_triggers else 'no'} | reason={policy.reason}")

    market_provider = create_market_data_provider(cfg.live_data_provider)
    news_provider = YFinanceNewsProvider()
    regime = build_market_regime()
    sector_strength = regime.indicators.get("semiconductor_etf_change_pct")

    symbols = list(dict.fromkeys(cfg.fixed_watchlist + cfg.candidate_universe))
    analyses: list[StockAnalysis] = []
    for symbol in symbols:
        try:
            analyses.append(_analyze_symbol(symbol, cfg, regime.label, sector_strength, market_provider, news_provider, now_utc=now))
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
    print(f"Live alert evaluation complete. Alerts generated: {len(sent_alerts)}")
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
            f"Extended-hours volume is not equivalent to regular-session RVOL during {policy.session_state}",
            session_label,
        )

    if md.intraday_rvol is not None and md.intraday_rvol_quality == "RELIABLE":
        return "RELIABLE", md.intraday_rvol_note or "Time-normalized intraday RVOL", session_label

    if md.intraday_rvol_quality in {"DATA_LIMITED", "UNAVAILABLE"}:
        return md.intraday_rvol_quality, md.intraday_rvol_note or "Intraday RVOL quality is limited", session_label

    if md.relative_volume is None:
        return "UNAVAILABLE", "RVOL missing from provider", session_label

    if md.volume is None or md.avg_volume_20d is None:
        return "DATA_LIMITED", "Volume baseline is incomplete", session_label

    ts = _parse_ts(md.intraday_timestamp or md.data_timestamp)
    if ts is None:
        return "DATA_LIMITED", "Provider timestamp unavailable", session_label
    if now_utc - ts > timedelta(minutes=20):
        return "DATA_LIMITED", "Provider timestamp is stale", session_label

    market_now = now_utc.astimezone(ZoneInfo(cfg.live_market_timezone))
    session_open = _parse_hhmm(cfg.live_market_open)
    session_close = _parse_hhmm(cfg.live_market_close)
    market_session_live = session_open <= market_now.time() <= session_close

    if md.provider == "yfinance" and market_session_live:
        return "DATA_LIMITED", "Daily-vs-20d RVOL baseline is not intraday-normalized", session_label

    return "RELIABLE", "RVOL baseline and timestamp are valid", session_label


def _intended_direction(analysis: StockAnalysis) -> str:
    if analysis.signal in {"LONG", "SHORT"}:
        return analysis.signal
    if analysis.direction_bias == "LONG_BIAS":
        return "LONG"
    if analysis.direction_bias == "SHORT_BIAS":
        return "SHORT"
    return "WAIT"


def _intraday_bars(md) -> list[dict]:
    out: list[dict] = []
    for bar in md.intraday_bars or []:
        try:
            out.append(
                {
                    "ts": datetime.fromisoformat(str(bar["ts"])),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                    "volume": float(bar["volume"]),
                }
            )
        except Exception:
            continue
    out.sort(key=lambda x: x["ts"])
    return out


def _ema(values: list[float], span: int) -> float | None:
    if not values:
        return None
    alpha = 2.0 / (span + 1.0)
    ema_value = values[0]
    for v in values[1:]:
        ema_value = (alpha * v) + ((1.0 - alpha) * ema_value)
    return ema_value


def _compute_intraday_context(analysis: StockAnalysis, cfg: AppConfig) -> dict:
    md = analysis.market_data
    bars = _intraday_bars(md)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]

    session_vwap = None
    if closes and volumes and sum(volumes) > 0:
        session_vwap = sum((c * v) for c, v in zip(closes, volumes)) / sum(volumes)

    or_high = md.opening_range_high
    or_low = md.opening_range_low
    if bars:
        first_ts = bars[0]["ts"]
        cutoff = first_ts + timedelta(minutes=max(5, cfg.v4_opening_range_minutes))
        opening_bars = [b for b in bars if b["ts"] <= cutoff]
        if opening_bars:
            or_high = max(b["high"] for b in opening_bars)
            or_low = min(b["low"] for b in opening_bars)

    return {
        "bars": bars,
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "close": closes[-1] if closes else md.price,
        "prev_close": closes[-2] if len(closes) >= 2 else None,
        "last_high": highs[-1] if highs else None,
        "last_low": lows[-1] if lows else None,
        "session_vwap": session_vwap if session_vwap is not None else md.vwap,
        "or_high": or_high,
        "or_low": or_low,
        "ema_fast": _ema(closes, 9) if len(closes) >= 6 else None,
        "ema_slow": _ema(closes, 21) if len(closes) >= 12 else None,
    }


def _risk_reward_ratio_from_analysis(analysis: StockAnalysis) -> float | None:
    bp = analysis.battle_plan
    signal = analysis.signal

    if signal == "LONG" and bp.entry_trigger_price is not None and bp.invalidation_price is not None and bp.target_1 is not None:
        risk = bp.entry_trigger_price - bp.invalidation_price
        reward = bp.target_1 - bp.entry_trigger_price
        if risk <= 0 or reward <= 0:
            return None
        return reward / risk

    if signal == "SHORT" and bp.entry_trigger_price is not None and bp.invalidation_price is not None and bp.target_1 is not None:
        risk = bp.invalidation_price - bp.entry_trigger_price
        reward = bp.entry_trigger_price - bp.target_1
        if risk <= 0 or reward <= 0:
            return None
        return reward / risk

    return None


def _trigger_type_from_label(label: str) -> str:
    mapping = {
        "Breakout above resistance": "RESISTANCE_BREAK",
        "Opening-range breakout": "OPENING_RANGE_BREAKOUT",
        "VWAP reclaim and hold": "RECLAIM",
        "Bullish continuation retest": "RETEST",
        "Breakdown below support": "SUPPORT_BREAK",
        "Opening-range breakdown": "OPENING_RANGE_BREAKDOWN",
        "VWAP rejection and hold": "BREAKDOWN",
        "Bearish continuation retest": "RETEST",
    }
    return mapping.get(label, "BREAKOUT")


def _build_trigger_evidence(
    *,
    confirmed: bool,
    direction: str,
    trigger: str,
    trigger_price: float | None,
    reference_level: float | None,
    current_price: float | None,
    timestamp: str | None,
    observed_at: str | None,
    detail: str,
) -> TriggerEvidence:
    if not confirmed:
        return TriggerEvidence(
            confirmed=False,
            direction=direction,
            trigger_type="",
            trigger_price=trigger_price,
            reference_level=reference_level,
            current_price=current_price,
            timestamp=timestamp,
            observed_at=observed_at,
            detail=detail,
        )

    return TriggerEvidence(
        confirmed=True,
        direction=direction,
        trigger_type=_trigger_type_from_label(trigger),
        trigger_price=trigger_price,
        reference_level=reference_level,
        current_price=current_price,
        timestamp=timestamp,
        observed_at=observed_at,
        detail=detail,
    )


def _evaluate_trigger_lifecycle(evidence: TriggerEvidence, analysis: StockAnalysis, cfg: AppConfig, now_utc: datetime) -> TriggerLifecycle:
    if not evidence.confirmed:
        return TriggerLifecycle(state="TRIGGER_EXPIRED", detail="Trigger evidence is not confirmed")

    evidence_ts = _parse_ts(evidence.observed_at or evidence.timestamp)
    if evidence_ts is None:
        return TriggerLifecycle(state="TRIGGER_EXPIRED", detail="reason=missing_trigger_timestamp")

    if now_utc - evidence_ts > timedelta(minutes=max(1, cfg.v4_max_trigger_age_minutes)):
        return TriggerLifecycle(state="TRIGGER_EXPIRED", detail="reason=max_trigger_age")

    current_price = evidence.current_price
    if not isinstance(current_price, (int, float)):
        return TriggerLifecycle(state="TRIGGER_EXPIRED", detail="Current price unavailable for trigger lifecycle")

    invalidation = analysis.battle_plan.invalidation_price
    reference = evidence.reference_level
    direction = evidence.direction

    if direction == "LONG":
        if isinstance(invalidation, (int, float)) and current_price <= invalidation:
            return TriggerLifecycle(
                state="TRIGGER_INVALIDATED",
                detail=f"Price {current_price:.2f} <= invalidation {invalidation:.2f}",
            )
        if isinstance(reference, (int, float)) and evidence.trigger_type in {"RESISTANCE_BREAK", "OPENING_RANGE_BREAKOUT", "RECLAIM"} and current_price < reference:
            return TriggerLifecycle(
                state="TRIGGER_EXPIRED",
                detail=f"Price {current_price:.2f} fell below trigger reference {reference:.2f}",
            )
    elif direction == "SHORT":
        if isinstance(invalidation, (int, float)) and current_price >= invalidation:
            return TriggerLifecycle(
                state="TRIGGER_INVALIDATED",
                detail=f"Price {current_price:.2f} >= invalidation {invalidation:.2f}",
            )
        if isinstance(reference, (int, float)) and evidence.trigger_type in {"SUPPORT_BREAK", "OPENING_RANGE_BREAKDOWN", "BREAKDOWN"} and current_price > reference:
            return TriggerLifecycle(
                state="TRIGGER_EXPIRED",
                detail=f"Price {current_price:.2f} rose above trigger reference {reference:.2f}",
            )

    return TriggerLifecycle(state="TRIGGER_STILL_VALID", detail="Trigger remains valid")


def _is_risk_reward_acceptable(analysis: StockAnalysis) -> tuple[bool, str, float | None]:
    ratio = _risk_reward_ratio_from_analysis(analysis)
    if ratio is None:
        return False, "Risk/reward unavailable", None
    if ratio < MIN_RISK_REWARD:
        return False, f"Risk/reward below minimum ({ratio:.2f})", ratio
    return True, "Risk/reward accepted", ratio


def _build_live_setup_assessment(
    analysis: StockAnalysis,
    cfg: AppConfig,
    phase: str,
    min_setup_score: int,
    session_policy: LiveSessionPolicy,
    rvol_quality: str,
    rr_ratio: float | None,
    now_utc: datetime,
) -> dict:
    md = analysis.market_data
    signal = _intended_direction(analysis)
    ctx = _compute_intraday_context(analysis, cfg)

    def comp(value: float | None, maximum: float, status: str) -> dict:
        return {"value": value, "max": maximum, "status": status}

    trend_value = 0.0
    trend_status = "UNAVAILABLE"
    if signal in {"LONG", "SHORT"} and ctx["ema_fast"] is not None and ctx["ema_slow"] is not None and ctx["close"] is not None:
        trend_status = "INTRADAY_EMA"
        bullish = ctx["ema_fast"] > ctx["ema_slow"] and ctx["close"] > ctx["ema_fast"]
        bearish = ctx["ema_fast"] < ctx["ema_slow"] and ctx["close"] < ctx["ema_fast"]
        if signal == "LONG" and bullish:
            trend_value = 15.0
        elif signal == "SHORT" and bearish:
            trend_value = 15.0
        elif signal == "LONG" and ctx["close"] > ctx["ema_fast"]:
            trend_value = 8.0
        elif signal == "SHORT" and ctx["close"] < ctx["ema_fast"]:
            trend_value = 8.0
    elif signal == "LONG" and md.trend == "UPTREND":
        trend_value = 7.0
        trend_status = "DAILY_CONTEXT"
    elif signal == "SHORT" and md.trend == "DOWNTREND":
        trend_value = 7.0
        trend_status = "DAILY_CONTEXT"
    elif md.trend == "RANGE":
        trend_value = 3.0
        trend_status = "DAILY_CONTEXT"

    momentum_value = 0.0
    momentum_status = "UNAVAILABLE"
    if len(ctx["closes"]) >= 6:
        momentum_status = "INTRADAY"
        c = ctx["closes"]
        ret3 = ((c[-1] - c[-4]) / max(c[-4], 1e-9)) * 100.0
        ret1 = ((c[-1] - c[-2]) / max(c[-2], 1e-9)) * 100.0
        prev1 = ((c[-2] - c[-3]) / max(c[-3], 1e-9)) * 100.0
        accel = ret1 - prev1
        if signal == "LONG":
            if ret3 >= 0.8 and accel >= -0.05:
                momentum_value = 15.0
            elif ret3 > 0:
                momentum_value = 8.0
        elif signal == "SHORT":
            if ret3 <= -0.8 and accel <= 0.05:
                momentum_value = 15.0
            elif ret3 < 0:
                momentum_value = 8.0
    elif md.day_change_pct is not None:
        momentum_status = "DAILY_CONTEXT"
        if signal == "LONG" and md.day_change_pct > 0:
            momentum_value = 4.0
        if signal == "SHORT" and md.day_change_pct < 0:
            momentum_value = 4.0

    price_confirmed = False
    trigger = "Unavailable"
    trigger_reference_level: float | None = None
    confirmation = "No actionable trigger"
    waiting_reason = "waiting for breakout"

    close = ctx["close"]
    prev_close = ctx["prev_close"]
    support = md.support
    resistance = md.resistance
    or_high = ctx["or_high"]
    or_low = ctx["or_low"]
    vwap = ctx["session_vwap"]
    last_low = ctx["last_low"]
    last_high = ctx["last_high"]

    if session_policy.allows_regular_session_triggers and signal == "LONG" and close is not None:
        if resistance is not None and prev_close is not None and prev_close <= resistance < close:
            price_confirmed = True
            trigger = "Breakout above resistance"
            trigger_reference_level = resistance
            confirmation = "Resistance breakout confirmed"
        elif or_high is not None and prev_close is not None and prev_close <= or_high < close:
            price_confirmed = True
            trigger = "Opening-range breakout"
            trigger_reference_level = or_high
            confirmation = "Opening-range breakout confirmed"
        elif vwap is not None and prev_close is not None and prev_close <= vwap < close and last_low is not None and last_low >= vwap * 0.998:
            price_confirmed = True
            trigger = "VWAP reclaim and hold"
            trigger_reference_level = vwap
            confirmation = "VWAP reclaim confirmed"
        elif ctx["ema_fast"] is not None and ctx["ema_slow"] is not None and ctx["ema_fast"] > ctx["ema_slow"] and last_low is not None and close > ctx["ema_fast"]:
            if last_low <= ctx["ema_fast"] * 1.002:
                price_confirmed = True
                trigger = "Bullish continuation retest"
                trigger_reference_level = ctx["ema_fast"]
                confirmation = "EMA retest continuation confirmed"
    elif session_policy.allows_regular_session_triggers and signal == "SHORT" and close is not None:
        if support is not None and prev_close is not None and prev_close >= support > close:
            price_confirmed = True
            trigger = "Breakdown below support"
            trigger_reference_level = support
            confirmation = "Support breakdown confirmed"
        elif or_low is not None and prev_close is not None and prev_close >= or_low > close:
            price_confirmed = True
            trigger = "Opening-range breakdown"
            trigger_reference_level = or_low
            confirmation = "Opening-range breakdown confirmed"
        elif vwap is not None and prev_close is not None and prev_close >= vwap > close and last_high is not None and last_high <= vwap * 1.002:
            price_confirmed = True
            trigger = "VWAP rejection and hold"
            trigger_reference_level = vwap
            confirmation = "VWAP rejection confirmed"
        elif ctx["ema_fast"] is not None and ctx["ema_slow"] is not None and ctx["ema_fast"] < ctx["ema_slow"] and last_high is not None and close < ctx["ema_fast"]:
            if last_high >= ctx["ema_fast"] * 0.998:
                price_confirmed = True
                trigger = "Bearish continuation retest"
                trigger_reference_level = ctx["ema_fast"]
                confirmation = "EMA retest continuation confirmed"

    price_action_value = 0.0
    if price_confirmed:
        price_action_value = 20.0
        waiting_reason = "trigger confirmed"
    elif signal == "LONG" and (md.breakout_state in {"NEAR BREAKOUT", "BREAKOUT"} or (resistance is not None and close is not None and close >= resistance * 0.997)):
        price_action_value = 10.0
        waiting_reason = "waiting for breakout"
    elif signal == "SHORT" and (md.breakout_state in {"NEAR BREAKDOWN", "BREAKDOWN"} or (support is not None and close is not None and close <= support * 1.003)):
        price_action_value = 10.0
        waiting_reason = "waiting for breakdown"

    vwap_value: float | None = None
    vwap_status = "UNAVAILABLE"
    if vwap is not None and close is not None and session_policy.allows_vwap_confirmation:
        vwap_status = "AVAILABLE"
        if signal == "LONG" and close > vwap:
            vwap_value = 10.0
        elif signal == "SHORT" and close < vwap:
            vwap_value = 10.0
        else:
            vwap_value = 0.0
    elif vwap is not None and close is not None:
        vwap_status = "DISABLED_OUTSIDE_US_REGULAR"

    opening_range_value: float | None = None
    opening_range_status = "UNAVAILABLE"
    if or_high is not None and or_low is not None and close is not None and session_policy.allows_opening_range_confirmation:
        opening_range_status = "AVAILABLE"
        if signal == "LONG" and close > or_high:
            opening_range_value = 15.0
        elif signal == "SHORT" and close < or_low:
            opening_range_value = 15.0
        elif signal == "LONG" and close >= or_high * 0.998:
            opening_range_value = 8.0
        elif signal == "SHORT" and close <= or_low * 1.002:
            opening_range_value = 8.0
        else:
            opening_range_value = 0.0
    elif or_high is not None and or_low is not None and close is not None:
        opening_range_status = "DISABLED_OUTSIDE_US_REGULAR"

    if not session_policy.allows_regular_session_triggers and signal in {"LONG", "SHORT"}:
        waiting_reason = session_policy.reason
        confirmation = session_policy.reason
        trigger = f"Regular-session trigger disabled during {session_policy.session_state}"

    alignment_value = 5.0 if analysis.market_alignment == "MARKET_ALIGNED" else (2.0 if analysis.market_alignment == "UNKNOWN" else 0.0)

    risk_reward_value: float | None = None
    risk_reward_status = "UNAVAILABLE"
    if rr_ratio is not None:
        risk_reward_status = "AVAILABLE"
        if rr_ratio >= 2.0:
            risk_reward_value = 10.0
        elif rr_ratio >= 1.5:
            risk_reward_value = 8.0
        elif rr_ratio >= 1.2:
            risk_reward_value = 4.0
        else:
            risk_reward_value = 0.0

    components = {
        "trend": comp(trend_value, 15.0, trend_status),
        "momentum": comp(momentum_value, 15.0, momentum_status),
        "price_action": comp(price_action_value, 20.0, trigger if price_confirmed else waiting_reason),
        "vwap": comp(vwap_value, 10.0, vwap_status),
        "opening_range": comp(opening_range_value, 15.0, opening_range_status),
        "market_alignment": comp(alignment_value, 5.0, analysis.market_alignment),
        "risk_reward": comp(risk_reward_value, 10.0, risk_reward_status),
    }

    available_values = [x["value"] for x in components.values() if x["value"] is not None]
    available_max = [x["max"] for x in components.values() if x["value"] is not None]
    if available_values and available_max:
        raw_score = (sum(available_values) / max(1.0, sum(available_max))) * 100.0
    else:
        raw_score = 0.0

    missing_intraday = int(components["vwap"]["value"] is None) + int(components["opening_range"]["value"] is None)
    final_score = max(0, min(100, int(round(raw_score - (missing_intraday * 3)))))

    if final_score >= min_setup_score and price_confirmed:
        setup_state = "ENTRY_TRIGGERED"
    elif final_score >= max(45, min_setup_score - 15):
        setup_state = "SETUP_DEVELOPING"
    else:
        setup_state = "NO_TRADE"

    state_reason = "entry trigger confirmed" if setup_state == "ENTRY_TRIGGERED" else waiting_reason
    if setup_state == "SETUP_DEVELOPING" and price_confirmed:
        state_reason = f"trigger confirmed but setup score {final_score} below minimum {min_setup_score}"
    if setup_state == "NO_TRADE":
        state_reason = "insufficient intraday confirmation"

    trigger_ts = None
    if ctx.get("bars"):
        try:
            trigger_ts = ctx["bars"][-1]["ts"].isoformat()
        except Exception:
            trigger_ts = md.intraday_timestamp or md.data_timestamp
    else:
        trigger_ts = md.intraday_timestamp or md.data_timestamp

    trigger_evidence = _build_trigger_evidence(
        confirmed=price_confirmed,
        direction=signal,
        trigger=trigger,
        trigger_price=close if isinstance(close, (int, float)) else md.price,
        reference_level=trigger_reference_level,
        current_price=close if isinstance(close, (int, float)) else md.price,
        timestamp=trigger_ts,
        observed_at=now_utc.isoformat() if price_confirmed else None,
        detail=confirmation if price_confirmed else waiting_reason,
    )

    return {
        "phase": phase,
        "components": components,
        "final_setup_score": final_score,
        "setup_state": setup_state,
        "trigger": trigger,
        "confirmation": confirmation,
        "missing_intraday_factors": missing_intraday,
        "price_confirmed": price_confirmed,
        "state_reason": state_reason,
        "vwap_status": vwap_status,
        "opening_range_status": opening_range_status,
        "close": close,
        "rvol_quality": rvol_quality,
        "trigger_evidence": trigger_evidence,
    }


def _fmt_component(component: dict) -> str:
    value = component.get("value")
    if value is None:
        return "UNAVAILABLE"
    return str(int(round(value)))


def _log_setup_components(symbol: str, assessment: dict) -> None:
    c = assessment["components"]
    print(
        f"{symbol}: setup components | Trend {_fmt_component(c['trend'])} | Momentum {_fmt_component(c['momentum'])} | "
        f"PriceAction {_fmt_component(c['price_action'])} | VWAP {_fmt_component(c['vwap'])} | "
        f"OpeningRange {_fmt_component(c['opening_range'])} | MarketAlign {_fmt_component(c['market_alignment'])} | "
        f"RiskReward {_fmt_component(c['risk_reward'])} | Final {assessment['final_setup_score']}"
    )


def _setup_status_summary(analysis: StockAnalysis, assessment: dict) -> str:
    direction = _intended_direction(analysis)
    if assessment["setup_state"] == "SETUP_DEVELOPING":
        return (
            f"{analysis.symbol}: {direction} | SetupState SETUP_DEVELOPING | "
            f"SetupScore {assessment['final_setup_score']} | {assessment.get('state_reason', 'waiting')}"
        )
    if assessment["setup_state"] == "NO_TRADE":
        return (
            f"{analysis.symbol}: {direction} | SetupState NO_TRADE | "
            f"reason={assessment.get('state_reason', 'insufficient evidence')}"
        )
    return (
        f"{analysis.symbol}: {direction} | SetupState ENTRY_TRIGGERED | "
        f"SetupScore {assessment['final_setup_score']}"
    )


def _entry_transition_event_type(prev_signal: str, signal: str) -> str | None:
    if prev_signal in {"WAIT", "NO_TRADE"} and signal == "LONG":
        return "WAIT_TO_LONG"
    if prev_signal in {"WAIT", "NO_TRADE"} and signal == "SHORT":
        return "WAIT_TO_SHORT"
    return None


def _risk_reward_ratio_from_levels(signal: str, entry: float | None, stop: float | None, target_1: float | None) -> float | None:
    if entry is None or stop is None or target_1 is None:
        return None

    if signal == "LONG":
        risk = entry - stop
        reward = target_1 - entry
    elif signal == "SHORT":
        risk = stop - entry
        reward = entry - target_1
    else:
        return None

    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _is_valid_geometry(direction: str, entry: float | None, stop: float | None, target1: float | None) -> bool:
    if entry is None or stop is None or target1 is None:
        return False
    if direction == "LONG":
        return stop < entry < target1
    if direction == "SHORT":
        return target1 < entry < stop
    return False


def _direction_level_validation(
    direction: str,
    entry: float | None,
    stop: float | None,
    target1: float | None,
) -> tuple[bool, str | None, str | None]:
    if entry is None or stop is None or target1 is None:
        return False, ALERT_REASON_INVALID_RISK_LEVELS, "Missing entry, stop, or target1"

    if direction == "LONG":
        if stop >= entry or target1 <= entry:
            return (
                False,
                ALERT_REASON_DIRECTION_LEVEL_MISMATCH,
                f"Direction LONG requires stop < entry < target1 | Direction {direction} | Entry {entry:.2f} | Stop {stop:.2f} | Target1 {target1:.2f}",
            )
        return True, None, None

    if direction == "SHORT":
        if target1 >= entry or stop <= entry:
            return (
                False,
                ALERT_REASON_DIRECTION_LEVEL_MISMATCH,
                f"Direction SHORT requires target1 < entry < stop | Direction {direction} | Entry {entry:.2f} | Stop {stop:.2f} | Target1 {target1:.2f}",
            )
        return True, None, None

    return False, ALERT_REASON_INVALID_RISK_LEVELS, f"Unsupported direction {direction}"


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _recent_swing_low(ctx: dict) -> float | None:
    lows = list(ctx.get("lows") or [])
    if len(lows) < 3:
        return None
    window = lows[-8:-1] if len(lows) >= 8 else lows[:-1]
    if not window:
        return None
    return min(window)


def _recent_swing_high(ctx: dict) -> float | None:
    highs = list(ctx.get("highs") or [])
    if len(highs) < 3:
        return None
    window = highs[-8:-1] if len(highs) >= 8 else highs[:-1]
    if not window:
        return None
    return max(window)


def _trade_levels_from_intraday_structure(analysis: StockAnalysis, cfg: AppConfig) -> TradeLevels:
    md = analysis.market_data
    direction = _intended_direction(analysis)
    ctx = _compute_intraday_context(analysis, cfg)

    if direction not in {"LONG", "SHORT"}:
        return TradeLevels(direction=direction, entry=None, stop=None, target1=None, target2=None, risk_reward=None, source="none", detail="No trade direction")

    close = ctx.get("close")
    entry_trigger = analysis.battle_plan.entry_trigger_price
    entry = close if isinstance(close, (int, float)) else entry_trigger

    if not isinstance(entry, (int, float)):
        return TradeLevels(
            direction=direction,
            entry=None,
            stop=None,
            target1=None,
            target2=None,
            risk_reward=None,
            source="none",
            detail="Entry unavailable from trigger/current price",
        )

    or_high = ctx.get("or_high")
    or_low = ctx.get("or_low")
    vwap = ctx.get("session_vwap")
    swing_low = _recent_swing_low(ctx)
    swing_high = _recent_swing_high(ctx)
    structure_width = None
    if isinstance(or_high, (int, float)) and isinstance(or_low, (int, float)):
        width = float(or_high) - float(or_low)
        if width > 0:
            structure_width = width

    stop = None
    stop_source = ""
    if direction == "LONG":
        if isinstance(or_low, (int, float)) and or_low < entry:
            stop = float(or_low)
            stop_source = "opening_range"
        elif isinstance(swing_low, (int, float)) and swing_low < entry:
            stop = float(swing_low)
            stop_source = "swing_structure"
        elif isinstance(vwap, (int, float)) and vwap < entry:
            stop = float(vwap)
            stop_source = "vwap_structure"
        elif isinstance(md.support, (int, float)) and md.support < entry:
            stop = float(md.support)
            stop_source = "swing_structure"
    else:
        if isinstance(or_high, (int, float)) and or_high > entry:
            stop = float(or_high)
            stop_source = "opening_range"
        elif isinstance(swing_high, (int, float)) and swing_high > entry:
            stop = float(swing_high)
            stop_source = "swing_structure"
        elif isinstance(vwap, (int, float)) and vwap > entry:
            stop = float(vwap)
            stop_source = "vwap_structure"
        elif isinstance(md.resistance, (int, float)) and md.resistance > entry:
            stop = float(md.resistance)
            stop_source = "swing_structure"

    target1 = None
    target1_source = ""
    target2 = None
    target2_source = ""
    if direction == "LONG":
        target_candidates: list[tuple[float, str]] = []
        if isinstance(analysis.battle_plan.target_1, (int, float)) and analysis.battle_plan.target_1 > entry:
            target_candidates.append((float(analysis.battle_plan.target_1), "swing_structure"))
        if isinstance(md.resistance, (int, float)) and md.resistance > entry:
            target_candidates.append((float(md.resistance), "swing_structure"))
        if isinstance(swing_high, (int, float)) and swing_high > entry:
            target_candidates.append((float(swing_high), "swing_structure"))
        if structure_width is not None and structure_width > 0:
            target_candidates.append((float(entry + structure_width), "opening_range"))
            target_candidates.append((float(entry + (2.0 * structure_width)), "opening_range"))

        target_candidates = sorted(target_candidates, key=lambda x: x[0])
        if isinstance(stop, (int, float)) and stop < entry:
            for value, source in target_candidates:
                if value > entry:
                    rr = _risk_reward_ratio_from_levels("LONG", entry, stop, value)
                    if isinstance(rr, (int, float)) and rr >= MIN_RISK_REWARD:
                        target1 = value
                        target1_source = source
                        break
        if target1 is None:
            for value, source in target_candidates:
                if value > entry:
                    target1 = value
                    target1_source = source
                    break
        if target1 is not None:
            for value, source in target_candidates:
                if value > target1:
                    target2 = value
                    target2_source = source
                    break
            if target2 is None and isinstance(analysis.battle_plan.target_2, (int, float)) and analysis.battle_plan.target_2 > target1:
                target2 = float(analysis.battle_plan.target_2)
                target2_source = "swing_structure"
    else:
        target_candidates_short: list[tuple[float, str]] = []
        if isinstance(analysis.battle_plan.target_1, (int, float)) and analysis.battle_plan.target_1 < entry:
            target_candidates_short.append((float(analysis.battle_plan.target_1), "swing_structure"))
        if isinstance(md.support, (int, float)) and md.support < entry:
            target_candidates_short.append((float(md.support), "swing_structure"))
        if isinstance(swing_low, (int, float)) and swing_low < entry:
            target_candidates_short.append((float(swing_low), "swing_structure"))
        if structure_width is not None and structure_width > 0:
            target_candidates_short.append((float(entry - structure_width), "opening_range"))
            target_candidates_short.append((float(entry - (2.0 * structure_width)), "opening_range"))

        target_candidates_short = sorted(target_candidates_short, key=lambda x: x[0], reverse=True)
        if isinstance(stop, (int, float)) and stop > entry:
            for value, source in target_candidates_short:
                if value < entry:
                    rr = _risk_reward_ratio_from_levels("SHORT", entry, stop, value)
                    if isinstance(rr, (int, float)) and rr >= MIN_RISK_REWARD:
                        target1 = value
                        target1_source = source
                        break
        if target1 is None:
            for value, source in target_candidates_short:
                if value < entry:
                    target1 = value
                    target1_source = source
                    break
        if target1 is not None:
            for value, source in sorted(target_candidates_short, key=lambda x: x[0]):
                if value < target1:
                    target2 = value
                    target2_source = source
                    break
            if target2 is None and isinstance(analysis.battle_plan.target_2, (int, float)) and analysis.battle_plan.target_2 < target1:
                target2 = float(analysis.battle_plan.target_2)
                target2_source = "swing_structure"

    if target2 is not None:
        if direction == "LONG" and target2 <= target1:
            target2 = None
            target2_source = ""
        if direction == "SHORT" and target2 >= target1:
            target2 = None
            target2_source = ""

    geometry_valid, geometry_reason, geometry_detail = _direction_level_validation(direction, entry, stop, target1)
    rr_ratio = _risk_reward_ratio_from_levels(direction, entry, stop, target1) if geometry_valid else None
    source_parts = [x for x in [stop_source, target1_source or target2_source] if x]
    source = "+".join(dict.fromkeys(source_parts)) if source_parts else "none"

    detail = None
    if stop is None:
        detail = "Unable to derive stop from opening range/swing/VWAP"
    elif target1 is None:
        detail = "Unable to derive target1 from structure"
    elif not geometry_valid:
        detail = geometry_detail or "Invalid trade geometry"

    return TradeLevels(
        direction=direction,
        entry=_rounded(entry),
        stop=_rounded(stop),
        target1=_rounded(target1),
        target2=_rounded(target2),
        risk_reward=_rounded(rr_ratio),
        source=source,
        detail=detail,
    )


def _resolve_volume_lifecycle(symbol_state: dict, rvol: float | None, required_rvol: float) -> VolumeLifecycle:
    previous = symbol_state.get("volume_lifecycle", {}) if isinstance(symbol_state.get("volume_lifecycle"), dict) else {}
    previous_state = str(previous.get("state") or "")

    if not isinstance(rvol, (int, float)):
        return VolumeLifecycle(
            state=VOLUME_STATE_WAIT_FOR_VOLUME,
            rvol=None,
            required_rvol=required_rvol,
            detail="Reliable RVOL value unavailable",
        )

    if rvol >= required_rvol:
        if previous_state in {VOLUME_STATE_WAIT_FOR_VOLUME, VOLUME_STATE_VOLUME_LOST}:
            return VolumeLifecycle(
                state=VOLUME_STATE_VOLUME_CONFIRMED,
                rvol=rvol,
                required_rvol=required_rvol,
                detail="RVOL reached configured gate",
            )
        return VolumeLifecycle(
            state=VOLUME_STATE_VOLUME_CONFIRMED,
            rvol=rvol,
            required_rvol=required_rvol,
            detail="RVOL remains above configured gate",
        )

    if previous_state == VOLUME_STATE_VOLUME_CONFIRMED:
        return VolumeLifecycle(
            state=VOLUME_STATE_VOLUME_LOST,
            rvol=rvol,
            required_rvol=required_rvol,
            detail="RVOL dropped below configured gate after confirmation",
        )

    return VolumeLifecycle(
        state=VOLUME_STATE_WAIT_FOR_VOLUME,
        rvol=rvol,
        required_rvol=required_rvol,
        detail="RVOL below configured gate",
    )


def _is_live_confirmable(analysis: StockAnalysis, cfg: AppConfig, opening_range_window: bool, now_utc: datetime) -> tuple[bool, str, dict]:
    md = analysis.market_data
    phase = _v4_session_phase(now_utc, cfg)
    session_policy = _live_session_policy(now_utc, cfg)
    rvol_quality, rvol_quality_reason, rvol_session = _classify_rvol_quality(analysis, cfg, now_utc)
    rvol_value = md.intraday_rvol if md.intraday_rvol is not None else md.relative_volume

    thresholds = {
        "min_rvol": cfg.v4_opening_min_rvol if phase == "OPENING" else cfg.v4_normal_min_rvol,
        "min_setup": cfg.v4_opening_min_setup_score if phase == "OPENING" else cfg.v4_normal_min_setup_score,
        "phase": phase,
        "session_state": session_policy.session_state,
        "session_reason": session_policy.reason,
        "session_triggers_enabled": session_policy.allows_regular_session_triggers,
        "rvol_quality": rvol_quality,
        "rvol_quality_reason": rvol_quality_reason,
        "rvol_session": rvol_session,
        "rvol": rvol_value,
    }

    if md.price is None:
        return False, "Price unavailable", thresholds

    if analysis.signal == "LONG" and analysis.direction_bias != "LONG_BIAS":
        thresholds["setup_state"] = "NO_TRADE"
        return False, "LONG signal requires LONG_BIAS", thresholds
    if analysis.signal == "SHORT" and analysis.direction_bias != "SHORT_BIAS":
        thresholds["setup_state"] = "NO_TRADE"
        return False, "SHORT signal requires SHORT_BIAS", thresholds

    rr_ratio = _risk_reward_ratio_from_analysis(analysis)
    setup_assessment = _build_live_setup_assessment(
        analysis,
        cfg,
        phase,
        thresholds["min_setup"],
        session_policy,
        rvol_quality=rvol_quality,
        rr_ratio=rr_ratio,
        now_utc=now_utc,
    )
    thresholds.update(
        {
            "setup_components": setup_assessment["components"],
            "setup_score": setup_assessment["final_setup_score"],
            "setup_state": setup_assessment["setup_state"],
            "trigger": setup_assessment["trigger"],
            "confirmation": setup_assessment["confirmation"],
            "state_reason": setup_assessment.get("state_reason"),
            "vwap_status": setup_assessment.get("vwap_status"),
            "opening_range_status": setup_assessment.get("opening_range_status"),
            "trigger_evidence": setup_assessment.get("trigger_evidence"),
        }
    )

    if not session_policy.allows_regular_session_triggers:
        if thresholds.get("setup_state") == "ENTRY_TRIGGERED":
            thresholds["setup_state"] = "SETUP_DEVELOPING"
        thresholds["session_gate_blocked"] = True
        return False, session_policy.reason, thresholds

    if analysis.market_alignment != "UNKNOWN" and analysis.market_alignment != "MARKET_ALIGNED":
        return False, "Market alignment filter rejected setup", thresholds

    bullish_structure = analysis.signal == "LONG" and (md.trend == "UPTREND" or md.breakout_state in {"BREAKOUT", "NEAR BREAKOUT"})
    bearish_structure = analysis.signal == "SHORT" and (md.trend == "DOWNTREND" or md.breakout_state in {"BREAKDOWN", "NEAR BREAKDOWN"})
    if analysis.signal == "LONG" and not bullish_structure:
        return False, "No valid bullish structure", thresholds
    if analysis.signal == "SHORT" and not bearish_structure:
        return False, "No valid bearish structure", thresholds

    if setup_assessment.get("setup_state") != "ENTRY_TRIGGERED":
        return False, setup_assessment.get("state_reason") or setup_assessment.get("confirmation", "No actionable trigger"), thresholds

    thresholds["confirmation_mode"] = "STRICT" if rvol_quality in {"DATA_LIMITED", "UNAVAILABLE"} else "STANDARD"
    thresholds["setup_state"] = "ENTRY_TRIGGERED"

    return True, "CONFIRMED", thresholds


def _evaluate_alert_eligibility(
    analysis: StockAnalysis,
    symbol_state: dict,
    cfg: AppConfig,
    now: datetime,
    opening_range_window: bool,
) -> tuple[AlertEligibilityResult, dict, str | None, TradeLevels | None, VolumeLifecycle | None]:
    signal = analysis.signal
    direction = signal if signal in {"LONG", "SHORT"} else _intended_direction(analysis)

    technical_ok, technical_reason, v4 = _is_live_confirmable(analysis, cfg, opening_range_window, now)
    setup_score = int(v4.get("setup_score", analysis.setup_score))
    rvol_value = v4.get("rvol")
    rvol_quality = v4.get("rvol_quality", "UNAVAILABLE")

    if v4.get("setup_state") != "ENTRY_TRIGGERED":
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_ENTRY_NOT_CONFIRMED,
                direction=direction,
                entry=None,
                stop=None,
                target1=None,
                target2=None,
                risk_reward=None,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=technical_reason,
            ),
            v4,
            None,
            None,
            None,
        )

    trigger_evidence = v4.get("trigger_evidence")
    if not isinstance(trigger_evidence, TriggerEvidence):
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_ENTRY_NOT_CONFIRMED,
                direction=direction,
                entry=None,
                stop=None,
                target1=None,
                target2=None,
                risk_reward=None,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail="Trigger evidence missing for ENTRY_TRIGGERED setup",
            ),
            v4,
            None,
            None,
            None,
        )

    if not trigger_evidence.confirmed:
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_ENTRY_NOT_CONFIRMED,
                direction=direction,
                entry=None,
                stop=None,
                target1=None,
                target2=None,
                risk_reward=None,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail="TriggerEvidence.confirmed is False",
            ),
            v4,
            None,
            None,
            None,
        )

    candidate_event_type = "WAIT_TO_LONG" if direction == "LONG" else "WAIT_TO_SHORT"

    trade_levels = _trade_levels_from_intraday_structure(analysis, cfg)
    entry = trade_levels.entry
    stop = trade_levels.stop
    target_1 = trade_levels.target1
    target_2 = trade_levels.target2
    rr_ratio = trade_levels.risk_reward

    lifecycle = _evaluate_trigger_lifecycle(trigger_evidence, analysis, cfg, now)
    v4["trigger_lifecycle"] = lifecycle
    if lifecycle.state == "TRIGGER_INVALIDATED":
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_TRIGGER_INVALIDATED,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=lifecycle.detail,
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )
    if lifecycle.state == "TRIGGER_EXPIRED":
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_TRIGGER_EXPIRED,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=lifecycle.detail,
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )

    geometry_valid, geometry_reason, geometry_detail = _direction_level_validation(direction, entry, stop, target_1)
    if not geometry_valid:
        detail = geometry_detail or trade_levels.detail or "Invalid risk levels"
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=geometry_reason or ALERT_REASON_INVALID_RISK_LEVELS,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=detail,
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )

    if opening_range_window and v4.get("trigger") == "Opening-range breakout":
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_PHASE_BLOCKED,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail="Opening range still developing",
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )

    min_setup = cfg.v4_opening_min_setup_score if v4.get("phase") == "OPENING" else cfg.v4_normal_min_setup_score
    if setup_score < min_setup:
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_ENTRY_NOT_CONFIRMED,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=f"Setup score below {v4.get('phase')} threshold ({setup_score} < {min_setup})",
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )

    if not isinstance(rr_ratio, (int, float)):
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_INVALID_RISK_LEVELS,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail="Unable to compute RR from generated levels",
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )

    if rr_ratio < MIN_RISK_REWARD:
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_RR_TOO_LOW,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=f"RR {rr_ratio:.2f} < minimum {MIN_RISK_REWARD:.2f}",
            ),
            v4,
            candidate_event_type,
            trade_levels,
            None,
        )

    min_rvol = cfg.v4_opening_min_rvol if v4.get("phase") == "OPENING" else cfg.v4_normal_min_rvol
    volume_lifecycle: VolumeLifecycle | None = None
    if rvol_quality == "RELIABLE":
        volume_lifecycle = _resolve_volume_lifecycle(symbol_state, rvol_value if isinstance(rvol_value, (int, float)) else None, min_rvol)
        if volume_lifecycle.state in {VOLUME_STATE_WAIT_FOR_VOLUME, VOLUME_STATE_VOLUME_LOST}:
            return (
                AlertEligibilityResult(
                    eligible=False,
                    reason=ALERT_REASON_RVOL_TOO_LOW,
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    target1=target_1,
                    target2=target_2,
                    risk_reward=rr_ratio,
                    setup_score=setup_score,
                    rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                    rvol_quality=rvol_quality,
                    detail=f"RVOL {rvol_value:.2f} < {min_rvol:.2f}" if isinstance(rvol_value, (int, float)) else "RVOL unavailable",
                ),
                v4,
                candidate_event_type,
                trade_levels,
                volume_lifecycle,
            )
    elif rvol_quality in {"DATA_LIMITED", "UNAVAILABLE"}:
        volume_lifecycle = VolumeLifecycle(
            state=VOLUME_STATE_VOLUME_CONFIRMED,
            rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
            required_rvol=min_rvol,
            detail="RVOL quality is not reliable; using existing strict trigger path",
        )

    last_alert_type = symbol_state.get("last_alert_type")
    last_alert_ts = _parse_ts(symbol_state.get("last_alert_timestamp"))
    if last_alert_type == candidate_event_type and last_alert_ts is not None:
        if now - last_alert_ts < timedelta(minutes=cfg.alert_cooldown_minutes):
            return (
                AlertEligibilityResult(
                    eligible=False,
                    reason=ALERT_REASON_COOLDOWN,
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    target1=target_1,
                    target2=target_2,
                    risk_reward=rr_ratio,
                    setup_score=setup_score,
                    rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                    rvol_quality=rvol_quality,
                    detail=f"Cooldown active ({cfg.alert_cooldown_minutes}m)",
                ),
                v4,
                candidate_event_type,
                trade_levels,
                volume_lifecycle,
            )

    if symbol_state.get("position_state") == "IN_POSITION" and symbol_state.get("active_direction") == signal:
        return (
            AlertEligibilityResult(
                eligible=False,
                reason=ALERT_REASON_DUPLICATE_POSITION,
                direction=direction,
                entry=entry,
                stop=stop,
                target1=target_1,
                target2=target_2,
                risk_reward=rr_ratio,
                setup_score=setup_score,
                rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
                rvol_quality=rvol_quality,
                detail=f"Already IN_POSITION {signal}",
            ),
            v4,
            candidate_event_type,
            trade_levels,
            volume_lifecycle,
        )

    return (
        AlertEligibilityResult(
            eligible=True,
            reason="ALERT_ELIGIBLE",
            direction=direction,
            entry=entry,
            stop=stop,
            target1=target_1,
            target2=target_2,
            risk_reward=rr_ratio,
            setup_score=setup_score,
            rvol=rvol_value if isinstance(rvol_value, (int, float)) else None,
            rvol_quality=rvol_quality,
            detail="Execution and risk gates passed",
        ),
        v4,
        candidate_event_type,
        trade_levels,
        volume_lifecycle,
    )


def _determine_event(
    analysis: StockAnalysis,
    state: dict,
    cfg: AppConfig,
    now: datetime,
    opening_range_window: bool,
) -> dict | None:
    symbol_state = state.setdefault("symbols", {}).setdefault(analysis.symbol, {})
    prev_signal = symbol_state.get("last_signal", "WAIT")
    symbol_state.setdefault("position_state", "WATCHING")

    phase = _v4_session_phase(now, cfg)
    session_policy = _live_session_policy(now, cfg)
    min_rvol = cfg.v4_opening_min_rvol if phase == "OPENING" else cfg.v4_normal_min_rvol
    rvol_quality, _, rvol_session = _classify_rvol_quality(analysis, cfg, now)
    rvol_value = analysis.market_data.intraday_rvol if analysis.market_data.intraday_rvol is not None else analysis.market_data.relative_volume
    rvol_text = f"{rvol_value:.2f}" if isinstance(rvol_value, (int, float)) else "DATA_LIMITED"
    if rvol_quality == "RELIABLE":
        volume_note = "passed volume gate" if isinstance(rvol_value, (int, float)) and rvol_value >= min_rvol else "below volume gate"
    else:
        volume_note = "evaluating price confirmation"
    print(
        f"{analysis.symbol}: session={session_policy.session_state} | {analysis.direction_bias} | Phase {phase} | "
        f"RVOL {rvol_text} | RVOL {rvol_quality} | RVOL_SESSION {rvol_session} | {volume_note}"
    )

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
    eligibility: AlertEligibilityResult | None = None
    trade_levels: TradeLevels | None = None
    volume_lifecycle: VolumeLifecycle | None = None
    if event_type is None:
        eligibility, v4, candidate_event_type, trade_levels, volume_lifecycle = _evaluate_alert_eligibility(
            analysis,
            symbol_state,
            cfg,
            now,
            opening_range_window,
        )
        symbol_state["last_setup_state"] = v4.get("setup_state", "NO_TRADE")
        symbol_state["last_session_state"] = v4.get("session_state", session_policy.session_state)
        if "setup_components" in v4:
            _log_setup_components(analysis.symbol, {"components": v4["setup_components"], "final_setup_score": v4.get("setup_score", 0)})
            print(
                _setup_status_summary(
                    analysis,
                    {
                        "setup_state": v4.get("setup_state", "NO_TRADE"),
                        "final_setup_score": v4.get("setup_score", 0),
                        "state_reason": v4.get("state_reason"),
                    },
                )
            )

        if v4.get("setup_state") == "ENTRY_TRIGGERED":
            trigger_evidence = v4.get("trigger_evidence")
            if isinstance(trigger_evidence, TriggerEvidence) and trigger_evidence.confirmed:
                trigger_price_text = f"{trigger_evidence.trigger_price:.2f}" if isinstance(trigger_evidence.trigger_price, (int, float)) else "UNAVAILABLE"
                reference_text = f"{trigger_evidence.reference_level:.2f}" if isinstance(trigger_evidence.reference_level, (int, float)) else "UNAVAILABLE"
                print(
                    f"{analysis.symbol}: TRIGGER_CONFIRMED | Direction {trigger_evidence.direction} | "
                    f"Type {trigger_evidence.trigger_type} | TriggerPrice {trigger_price_text} | "
                    f"Reference {reference_text} | {trigger_evidence.detail}"
                )

            lifecycle = v4.get("trigger_lifecycle")
            if isinstance(lifecycle, TriggerLifecycle) and lifecycle.state == "TRIGGER_INVALIDATED":
                trigger_type = trigger_evidence.trigger_type if isinstance(trigger_evidence, TriggerEvidence) else "UNAVAILABLE"
                print(f"{analysis.symbol}: TRIGGER_INVALIDATED | Direction {eligibility.direction} | Type {trigger_type} | {lifecycle.detail}")
            elif isinstance(lifecycle, TriggerLifecycle) and lifecycle.state == "TRIGGER_EXPIRED":
                trigger_type = trigger_evidence.trigger_type if isinstance(trigger_evidence, TriggerEvidence) else "UNAVAILABLE"
                print(f"{analysis.symbol}: TRIGGER_EXPIRED | Direction {eligibility.direction} | Type {trigger_type} | {lifecycle.detail}")

            rvol_log = f"{eligibility.rvol:.2f}" if isinstance(eligibility.rvol, (int, float)) else eligibility.rvol_quality
            print(f"{analysis.symbol}: ENTRY_TRIGGERED | SetupScore {eligibility.setup_score} | RVOL {rvol_log}")

            entry_text = f"{trade_levels.entry:.2f}" if trade_levels and isinstance(trade_levels.entry, (int, float)) else "UNAVAILABLE"
            stop_text = f"{trade_levels.stop:.2f}" if trade_levels and isinstance(trade_levels.stop, (int, float)) else "UNAVAILABLE"
            t1_text = f"{trade_levels.target1:.2f}" if trade_levels and isinstance(trade_levels.target1, (int, float)) else "UNAVAILABLE"
            t2_text = f"{trade_levels.target2:.2f}" if trade_levels and isinstance(trade_levels.target2, (int, float)) else "UNAVAILABLE"
            rr_text = f"{trade_levels.risk_reward:.2f}" if trade_levels and isinstance(trade_levels.risk_reward, (int, float)) else "UNAVAILABLE"
            src_text = trade_levels.source if trade_levels is not None else "none"
            print(
                f"{analysis.symbol}: TRADE_LEVELS | Direction {eligibility.direction} | Entry {entry_text} | "
                f"Stop {stop_text} | Target1 {t1_text} | Target2 {t2_text} | RR {rr_text} | Source {src_text}"
            )

            direction_valid, _, direction_detail = _direction_level_validation(eligibility.direction, trade_levels.entry, trade_levels.stop, trade_levels.target1)
            direction_status = "DIRECTION_LEVEL_VALID" if direction_valid else "DIRECTION_LEVEL_MISMATCH"
            direction_detail_text = f" | {direction_detail}" if direction_detail else ""
            print(
                f"{analysis.symbol}: {direction_status} | Direction {eligibility.direction} | Entry {entry_text} | "
                f"Stop {stop_text} | Target1 {t1_text}{direction_detail_text}"
            )

            if isinstance(volume_lifecycle, VolumeLifecycle):
                rvol_live = f"{volume_lifecycle.rvol:.2f}" if isinstance(volume_lifecycle.rvol, (int, float)) else "UNAVAILABLE"
                if volume_lifecycle.state == VOLUME_STATE_WAIT_FOR_VOLUME:
                    print(f"{analysis.symbol}: WAIT_FOR_VOLUME | RVOL {rvol_live} | Required {volume_lifecycle.required_rvol:.2f}")
                elif volume_lifecycle.state == VOLUME_STATE_VOLUME_CONFIRMED:
                    print(f"{analysis.symbol}: VOLUME_CONFIRMED | RVOL {rvol_live} | Required {volume_lifecycle.required_rvol:.2f}")
                elif volume_lifecycle.state == VOLUME_STATE_VOLUME_LOST:
                    print(f"{analysis.symbol}: VOLUME_LOST | RVOL {rvol_live} | Required {volume_lifecycle.required_rvol:.2f}")

            if isinstance(trade_levels, TradeLevels) and isinstance(v4.get("trigger_evidence"), TriggerEvidence):
                symbol_state["volume_candidate"] = {
                    "direction": eligibility.direction,
                    "trigger_evidence": {
                        "confirmed": v4["trigger_evidence"].confirmed,
                        "direction": v4["trigger_evidence"].direction,
                        "trigger_type": v4["trigger_evidence"].trigger_type,
                        "trigger_price": v4["trigger_evidence"].trigger_price,
                        "reference_level": v4["trigger_evidence"].reference_level,
                        "current_price": v4["trigger_evidence"].current_price,
                        "timestamp": v4["trigger_evidence"].timestamp,
                        "detail": v4["trigger_evidence"].detail,
                    },
                    "trade_levels": {
                        "entry": trade_levels.entry,
                        "stop": trade_levels.stop,
                        "target1": trade_levels.target1,
                        "target2": trade_levels.target2,
                        "risk_reward": trade_levels.risk_reward,
                        "source": trade_levels.source,
                    },
                }

            if isinstance(volume_lifecycle, VolumeLifecycle):
                symbol_state["volume_lifecycle"] = {
                    "state": volume_lifecycle.state,
                    "rvol": volume_lifecycle.rvol,
                    "required_rvol": volume_lifecycle.required_rvol,
                    "detail": volume_lifecycle.detail,
                    "updated_at": now.isoformat(),
                }

        if not eligibility.eligible:
            if isinstance(volume_lifecycle, VolumeLifecycle) and volume_lifecycle.state in {VOLUME_STATE_WAIT_FOR_VOLUME, VOLUME_STATE_VOLUME_LOST}:
                symbol_state["last_alert_decision"] = volume_lifecycle.state
            else:
                symbol_state["last_alert_decision"] = "ALERT_BLOCKED" if v4.get("setup_state") == "ENTRY_TRIGGERED" else "NO_ALERT"
            symbol_state["last_alert_reason"] = eligibility.reason
            if v4.get("setup_state") == "ENTRY_TRIGGERED":
                if isinstance(volume_lifecycle, VolumeLifecycle) and volume_lifecycle.state == VOLUME_STATE_WAIT_FOR_VOLUME:
                    return None

                detail = eligibility.detail or ""
                if detail:
                    print(f"{analysis.symbol}: ALERT_BLOCKED | {eligibility.reason} | {detail}")
                else:
                    print(f"{analysis.symbol}: ALERT_BLOCKED | {eligibility.reason}")

                if eligibility.reason in {ALERT_REASON_TRIGGER_INVALIDATED, ALERT_REASON_TRIGGER_EXPIRED}:
                    symbol_state.pop("volume_candidate", None)
            else:
                detail = eligibility.detail or eligibility.reason
                print(
                    f"{analysis.symbol}: session={v4.get('session_state', session_policy.session_state)} | {analysis.direction_bias} | Phase {v4['phase']} | "
                    f"RVOL {rvol_text} | RVOL {v4.get('rvol_quality', rvol_quality)} | RVOL_SESSION {v4.get('rvol_session', rvol_session)} | {detail}. No alert generated."
                )
            return None

        event_type = candidate_event_type
        symbol_state["last_alert_decision"] = "ALERT_ELIGIBLE"
        symbol_state["last_alert_reason"] = "ALERT_ELIGIBLE"
        symbol_state.pop("volume_candidate", None)
        rr_log = f"{eligibility.risk_reward:.2f}" if isinstance(eligibility.risk_reward, (int, float)) else "UNAVAILABLE"
        target2_log = f"{eligibility.target2:.2f}" if isinstance(eligibility.target2, (int, float)) else "UNAVAILABLE"
        print(
            f"{analysis.symbol}: ALERT_ELIGIBLE | {eligibility.direction} | "
            f"Entry {eligibility.entry:.2f} | Stop {eligibility.stop:.2f} | "
            f"Target1 {eligibility.target1:.2f} | Target2 {target2_log} | RR {rr_log}"
        )

        if event_type is None:
            print(
                f"{analysis.symbol}: {analysis.direction_bias} | Phase {v4['phase']} | "
                f"RVOL {rvol_text} | RVOL {v4.get('rvol_quality', rvol_quality)} | No entry transition. No alert generated."
            )
            return None

    if event_type is None:
        return None

    last_alert_type = symbol_state.get("last_alert_type")
    last_alert_ts = _parse_ts(symbol_state.get("last_alert_timestamp"))
    if event_type not in {"WAIT_TO_LONG", "WAIT_TO_SHORT"} and last_alert_type == event_type and last_alert_ts is not None:
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
        "setup_score": eligibility.setup_score if eligibility is not None else v4.get("setup_score", analysis.setup_score),
        "setup_state": v4.get("setup_state", "ENTRY_TRIGGERED"),
        "session_state": v4.get("session_state", session_policy.session_state),
        "direction_bias": analysis.direction_bias,
        "market_regime": analysis.market_alignment,
        "entry_trigger": eligibility.entry if eligibility is not None else analysis.battle_plan.entry_trigger_price,
        "confirmation_level": analysis.battle_plan.confirmation_level,
        "invalidation": eligibility.stop if eligibility is not None else analysis.battle_plan.invalidation_price,
        "target_1": eligibility.target1 if eligibility is not None else target_1,
        "target_2": eligibility.target2 if eligibility is not None else target_2,
        "risk_reward": analysis.battle_plan.risk_reward_assessment,
        "timestamp": now.isoformat(),
        "timestamp_market": analysis.market_data.intraday_timestamp or analysis.market_data.data_timestamp,
        "level_unavailable_reason": analysis.battle_plan.level_unavailable_reason,
        "rvol": rvol_value,
        "rvol_quality": v4.get("rvol_quality", rvol_quality),
        "rvol_session": v4.get("rvol_session", rvol_session),
        "phase": v4["phase"],
        "v4_trigger": v4.get("trigger"),
        "v4_confirmation": v4.get("confirmation"),
        "vwap_status": v4.get("vwap_status") or ("AVAILABLE" if analysis.market_data.vwap is not None else "UNAVAILABLE"),
        "opening_range_status": (
            v4.get("opening_range_status")
            or ("AVAILABLE" if analysis.market_data.opening_range_high is not None and analysis.market_data.opening_range_low is not None else "UNAVAILABLE")
        ),
        "risk_reward_ratio": eligibility.risk_reward if eligibility is not None else _risk_reward_ratio_from_analysis(analysis),
        "trade_level_source": trade_levels.source if trade_levels is not None else "none",
        "volume_lifecycle_state": volume_lifecycle.state if isinstance(volume_lifecycle, VolumeLifecycle) else None,
        "trigger_type": (
            v4.get("trigger_evidence").trigger_type
            if isinstance(v4.get("trigger_evidence"), TriggerEvidence)
            else None
        ),
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
    symbol_state.setdefault("position_state", "WATCHING")

    if event is None:
        if symbol_state.get("position_state") == "EXITED" and analysis.signal in {"WAIT", "NO_TRADE"}:
            symbol_state["position_state"] = "WATCHING"
        return

    symbol_state["last_alert_type"] = event["event_type"]
    symbol_state["last_alert_timestamp"] = now.isoformat()

    if event["event_type"] == "WAIT_TO_LONG":
        symbol_state["position_state"] = "IN_POSITION"
        symbol_state["active_direction"] = "LONG"
    elif event["event_type"] == "WAIT_TO_SHORT":
        symbol_state["position_state"] = "IN_POSITION"
        symbol_state["active_direction"] = "SHORT"
    elif event["event_type"] in {"LONG_EXIT", "SHORT_EXIT", "LONG_INVALIDATED", "SHORT_INVALIDATED"}:
        symbol_state["position_state"] = "EXITED"
        symbol_state["active_direction"] = None

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
            rvol_line = f"RVOL: {alert.get('rvol'):.2f} {rvol_quality}"
        else:
            rvol_line = f"RVOL: {alert.get('rvol'):.2f}"
    elif rvol_quality in {"DATA_LIMITED", "UNAVAILABLE"}:
        rvol_line = f"RVOL: {rvol_quality}"
    else:
        rvol_line = "RVOL: Unavailable"

    header = "🟢 " + symbol + " LONG - ENTRY TRIGGERED" if signal == "LONG" else "🔴 " + symbol + " SHORT - ENTRY TRIGGERED"
    rr_ratio = alert.get("risk_reward_ratio")
    rr_line = f"Risk/Reward: {rr_ratio:.2f}" if isinstance(rr_ratio, (int, float)) else "Risk/Reward: UNAVAILABLE"
    vwap_relation = "Above" if signal == "LONG" else "Below"
    if alert.get("vwap_status") != "AVAILABLE":
        vwap_relation = "UNAVAILABLE"
    return (
        f"<b>{header}</b>\n\n"
        f"Phase: {phase}\n"
        f"Price: {_fmt_price(alert.get('price'))}\n"
        f"Setup Score: {alert['setup_score']}/100\n"
        f"{rvol_line}\n"
        f"VWAP: {vwap_relation}\n"
        f"Trigger: {alert.get('v4_trigger') or confirmation_line}\n"
        f"{rr_line}\n"
        f"Opening Range: {alert.get('opening_range_status', 'UNAVAILABLE')}\n"
        f"Market: {alert.get('market_regime', 'Unavailable')}\n"
        "\n"
        f"Entry: {_fmt_price(alert.get('entry_trigger'))}\n"
        f"Stop: {_fmt_price(alert.get('invalidation'))}\n"
        f"Target 1: {_fmt_price(alert.get('target_1'))}\n"
        f"Target 2: {_fmt_price(alert.get('target_2'))}"
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
