from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import (
    _determine_event,
    _is_live_confirmable,
    _render_telegram_message,
    _send_telegram_alerts,
    _trade_levels_from_intraday_structure,
    TradeLevels,
    TriggerEvidence,
)
from src.daily_stock_analyse.models import BattlePlan, DataQuality, IntelligenceBlock, MarketData, ScoreBreakdown, StockAnalysis
from src.daily_stock_analyse.telegram_provider import TelegramSendResult


def _cfg(*, telegram_enabled: bool = False) -> AppConfig:
    return AppConfig(
        openai_api_key=None,
        resend_api_key=None,
        email_from=None,
        email_to="x@example.com",
        send_email=False,
        data_provider="yfinance",
        news_provider="yfinance",
        fixed_watchlist=["AMD"],
        candidate_universe=["AAPL"],
        score_weights={
            "trend": 0.2,
            "momentum": 0.15,
            "volume": 0.1,
            "relative_strength": 0.1,
            "fundamentals_news": 0.2,
            "catalyst_event": 0.1,
            "risk_reward": 0.15,
        },
        schedule_utc_cron="0 0 * * 1-5",
        min_setup_score=70,
        min_relative_volume=1.5,
        day_trade_threshold=75,
        short_threshold=0.7,
        long_threshold=0.7,
        dynamic_count=3,
        day_trade_gap_threshold=3.0,
        day_trade_rvol_threshold=1.5,
        day_trade_min_setup_score=65,
        morning_report_time="08:00",
        morning_report_timezone="Asia/Kuala_Lumpur",
        live_alert_enabled=True,
        live_alert_interval_minutes=5,
        live_market_timezone="America/New_York",
        live_market_open="09:30",
        live_market_close="16:00",
        alert_min_setup_score=70,
        alert_min_rvol=1.5,
        alert_cooldown_minutes=15,
        telegram_enabled=telegram_enabled,
        telegram_bot_token="bot-token" if telegram_enabled else None,
        telegram_chat_id="chat-id" if telegram_enabled else None,
        v4_opening_start="09:30",
        v4_opening_end="10:00",
        v4_opening_min_rvol=1.20,
        v4_opening_min_setup_score=75,
        v4_normal_min_rvol=1.50,
        v4_normal_min_setup_score=70,
        v4_opening_range_minutes=30,
    )


def _bars(direction: str, n: int = 18, start: float = 100.0) -> list[dict[str, float | str]]:
    base = datetime(2026, 8, 10, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    out: list[dict[str, float | str]] = []
    price = start
    for i in range(n):
        step = 0.30 if direction == "up" else -0.30
        open_p = price
        close_p = price + step
        high_p = max(open_p, close_p) + 0.12
        low_p = min(open_p, close_p) - 0.08
        out.append(
            {
                "ts": (base + timedelta(minutes=5 * i)).isoformat(),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": 120_000 + (i * 3000),
            }
        )
        price = close_p
    return out


def _analysis(
    signal: str,
    bias: str,
    *,
    bars: list[dict[str, float | str]],
    trend: str,
    breakout_state: str,
    support: float,
    resistance: float,
    intraday_rvol: float | None = 1.8,
    intraday_rvol_quality: str = "RELIABLE",
    day_change_pct: float = 1.0,
) -> StockAnalysis:
    price = float(bars[-1]["close"])
    return StockAnalysis(
        symbol="PLTR",
        name="PLTR",
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias=bias,
        market_alignment="MARKET_ALIGNED",
        setup_score=80,
        day_trade_candidate=True,
        candidate_score=80,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="x",
        confidence="HIGH",
        one_liner="x",
        main_reason="Strong setup",
        risk_classification="MEDIUM",
        market_data=MarketData(
            symbol="PLTR",
            price=price,
            relative_volume=1.2,
            intraday_rvol=intraday_rvol,
            intraday_rvol_quality=intraday_rvol_quality,
            intraday_rvol_note="intraday",
            trend=trend,
            breakout_state=breakout_state,
            vwap=price - 0.4 if signal == "LONG" else price + 0.4,
            opening_range_high=resistance,
            opening_range_low=support,
            resistance=resistance,
            support=support,
            day_change_pct=day_change_pct,
            volume=2_000_000,
            avg_volume_20d=1_000_000,
            intraday_timestamp="2026-08-10T14:35:00Z",
            data_timestamp="2026-08-10T14:35:00Z",
            provider="yfinance",
            intraday_bars=bars,
        ),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan(
            bullish_scenario="b",
            bearish_scenario="s",
            key_support="99",
            key_resistance="101",
            entry_area="entry",
            target_area="target",
            invalidation="invalid",
            risk_reward_assessment="",
            entry_trigger_price=price,
            confirmation_level=price,
            invalidation_price=support if signal == "LONG" else resistance,
            target_1=(price + 1.6) if signal == "LONG" else (price - 1.6),
            target_2=(price + 3.2) if signal == "LONG" else (price - 3.2),
        ),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def _triggered_long() -> StockAnalysis:
    bars = _bars("up")
    resistance = float(bars[-2]["close"]) + 0.05
    bars[-1]["close"] = resistance + 0.30
    bars[-1]["high"] = resistance + 0.35
    entry = float(bars[-1]["close"])
    support = entry - 0.70
    a = _analysis("LONG", "LONG_BIAS", bars=bars, trend="UPTREND", breakout_state="BREAKOUT", support=support, resistance=resistance)
    a.battle_plan.entry_trigger_price = resistance
    a.battle_plan.invalidation_price = support
    a.battle_plan.target_1 = entry + 9.00
    a.battle_plan.target_2 = entry + 12.00
    return a


def _triggered_short() -> StockAnalysis:
    bars = _bars("down", start=102.2)
    support = float(bars[-2]["close"]) - 0.05
    bars[-1]["close"] = support - 0.30
    bars[-1]["low"] = support - 0.35
    entry = float(bars[-1]["close"])
    resistance = entry + 0.70
    a = _analysis(
        "SHORT",
        "SHORT_BIAS",
        bars=bars,
        trend="DOWNTREND",
        breakout_state="BREAKDOWN",
        support=support,
        resistance=resistance,
        day_change_pct=-1.0,
    )
    a.battle_plan.entry_trigger_price = support
    a.battle_plan.invalidation_price = resistance
    a.battle_plan.target_1 = entry - 1.60
    a.battle_plan.target_2 = entry - 3.20
    return a


def _patched_levels(
    direction: str,
    entry: float,
    stop: float,
    target1: float,
    *,
    target2: float | None = None,
    risk_reward: float | None = 2.0,
    source: str = "swing_structure",
    detail: str | None = None,
) -> TradeLevels:
    return TradeLevels(
        direction=direction,
        entry=entry,
        stop=stop,
        target1=target1,
        target2=target2,
        risk_reward=risk_reward,
        source=source,
        detail=detail,
    )


def test_trade_level_generation_valid_long_levels():
    cfg = _cfg()
    levels = _trade_levels_from_intraday_structure(_triggered_long(), cfg)
    assert levels.direction == "LONG"
    assert levels.entry is not None
    assert levels.stop is not None
    assert levels.target1 is not None
    assert levels.stop < levels.entry < levels.target1


def test_trade_level_generation_valid_short_levels():
    cfg = _cfg()
    levels = _trade_levels_from_intraday_structure(_triggered_short(), cfg)
    assert levels.direction == "SHORT"
    assert levels.entry is not None
    assert levels.stop is not None
    assert levels.target1 is not None
    assert levels.target1 < levels.entry < levels.stop


def test_trade_level_generation_invalid_long_geometry():
    cfg = _cfg()
    a = _triggered_long()
    entry = float(a.market_data.price or 0.0)
    for bar in a.market_data.intraday_bars or []:
        bar["open"] = entry + 0.20
        bar["high"] = entry + 0.20
        bar["low"] = entry + 0.20
        bar["close"] = entry + 0.20
    a.market_data.price = entry + 0.20
    a.market_data.opening_range_low = entry + 0.20
    a.market_data.support = entry + 0.20
    a.market_data.vwap = entry + 0.20
    a.battle_plan.target_1 = entry
    a.market_data.resistance = entry
    a.market_data.opening_range_high = entry + 0.20
    levels = _trade_levels_from_intraday_structure(a, cfg)
    assert levels.risk_reward is None
    assert levels.detail is not None


def test_trade_level_generation_invalid_short_geometry():
    cfg = _cfg()
    a = _triggered_short()
    entry = float(a.market_data.price or 0.0)
    for bar in a.market_data.intraday_bars or []:
        bar["open"] = entry - 0.20
        bar["high"] = entry - 0.20
        bar["low"] = entry - 0.20
        bar["close"] = entry - 0.20
    a.market_data.price = entry - 0.20
    a.market_data.opening_range_high = entry - 0.20
    a.market_data.resistance = entry - 0.20
    a.market_data.vwap = entry - 0.20
    a.battle_plan.target_1 = entry
    a.market_data.support = entry - 0.20
    a.market_data.opening_range_low = entry - 0.20
    levels = _trade_levels_from_intraday_structure(a, cfg)
    assert levels.risk_reward is None
    assert levels.detail is not None


def test_trade_level_generation_missing_stop():
    cfg = _cfg()
    a = _triggered_long()
    entry = float(a.market_data.price or 0.0)
    for bar in a.market_data.intraday_bars or []:
        bar["open"] = entry + 0.20
        bar["high"] = entry + 0.20
        bar["low"] = entry + 0.20
        bar["close"] = entry + 0.20
    a.market_data.price = entry + 0.20
    a.market_data.opening_range_low = None
    a.market_data.support = None
    a.market_data.vwap = entry + 0.20
    levels = _trade_levels_from_intraday_structure(a, cfg)
    assert levels.stop is None


def test_trade_level_generation_missing_target1():
    cfg = _cfg()
    a = _triggered_long()
    entry = float(a.market_data.price or 0.0)
    a.battle_plan.target_1 = entry - 1.0
    a.market_data.resistance = entry - 0.5
    for bar in a.market_data.intraday_bars or []:
        bar["open"] = entry - 0.10
        bar["high"] = entry - 0.10
        bar["low"] = entry - 0.10
        bar["close"] = entry - 0.10
    a.market_data.price = entry - 0.10
    a.market_data.vwap = entry - 0.10
    a.market_data.opening_range_high = entry
    a.market_data.opening_range_low = entry
    levels = _trade_levels_from_intraday_structure(a, cfg)
    assert levels.target1 is None


def test_trade_level_generation_rr_calculation():
    cfg = _cfg()
    levels = _trade_levels_from_intraday_structure(_triggered_long(), cfg)
    assert isinstance(levels.risk_reward, float)
    assert levels.risk_reward > 0


def test_trade_level_generation_target2_optional_handling():
    cfg = _cfg()
    a = _triggered_long()
    a.battle_plan.target_2 = None
    levels = _trade_levels_from_intraday_structure(a, cfg)
    assert levels.target1 is not None
    assert levels.target2 is None or levels.target2 > levels.target1


def test_entry_triggered_valid_levels_and_rr_is_eligible():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_entry_triggered_rr_too_low_blocks():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    entry = float(a.market_data.price or 0.0)
    a.battle_plan.target_1 = entry + 0.40
    a.battle_plan.target_2 = entry + 0.80
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RR_TOO_LOW"


def test_entry_triggered_rvol_too_low_blocks():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.10
    a.market_data.intraday_rvol_quality = "RELIABLE"
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RVOL_TOO_LOW"


def test_entry_triggered_invalid_risk_levels_blocks():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels(
            direction="LONG",
            entry=105.0,
            stop=None,
            target1=108.0,
            target2=None,
            risk_reward=None,
            source="none",
            detail="Missing stop",
        )
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "INVALID_RISK_LEVELS"


def test_duplicate_in_position_blocks():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "position_state": "IN_POSITION", "active_direction": "LONG"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DUPLICATE_POSITION"


def test_cooldown_blocks():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {
        "symbols": {
            "PLTR": {
                "last_signal": "WAIT",
                "last_alert_type": "WAIT_TO_LONG",
                "last_alert_timestamp": (now - timedelta(minutes=5)).isoformat(),
            }
        }
    }
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "COOLDOWN"


def test_new_trigger_reaches_eligibility_before_duplicate_state_block():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "LONG", "position_state": "WATCHING"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert state["symbols"]["PLTR"]["last_alert_reason"] != "NO_ALERT"
    assert event is not None


def test_telegram_eligible_alert_contains_entry():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    msg = _render_telegram_message(event, "America/New_York")
    assert "Entry:" in msg


def test_telegram_eligible_alert_contains_stop():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    msg = _render_telegram_message(event, "America/New_York")
    assert "Stop:" in msg


def test_telegram_eligible_alert_contains_target1():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    msg = _render_telegram_message(event, "America/New_York")
    assert "Target 1:" in msg


def test_telegram_eligible_alert_contains_target2():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    msg = _render_telegram_message(event, "America/New_York")
    assert "Target 2:" in msg


def test_telegram_eligible_alert_contains_rr():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    msg = _render_telegram_message(event, "America/New_York")
    assert "Risk/Reward:" in msg


def test_blocked_alert_not_dispatched_to_telegram():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.0
    a.market_data.intraday_rvol_quality = "RELIABLE"
    blocked = _determine_event(a, state, cfg, now, opening_range_window=False)
    alerts = [x for x in [blocked] if x is not None]

    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        sent = _send_telegram_alerts(alerts, cfg)

    assert send_mock.call_count == 0
    assert sent == 0


def test_trigger_evidence_creation_contains_structure():
    cfg = _cfg()
    a = _triggered_long()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    ok, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    evidence = meta.get("trigger_evidence")
    assert ok is True
    assert evidence is not None
    assert evidence.confirmed is True
    assert evidence.direction == "LONG"
    assert isinstance(evidence.trigger_type, str)


def test_long_trigger_confirmation_evidence():
    cfg = _cfg()
    a = _triggered_long()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    evidence = meta["trigger_evidence"]
    assert evidence.confirmed is True
    assert evidence.direction == "LONG"
    assert evidence.trigger_price is not None


def test_short_trigger_confirmation_evidence():
    cfg = _cfg()
    a = _triggered_short()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    evidence = meta["trigger_evidence"]
    assert evidence.confirmed is True
    assert evidence.direction == "SHORT"
    assert evidence.trigger_price is not None


def test_trigger_evidence_passed_into_eligibility():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    with patch("src.daily_stock_analyse.live_alerts._evaluate_trigger_lifecycle") as lifecycle_mock:
        from src.daily_stock_analyse.live_alerts import TriggerLifecycle

        lifecycle_mock.return_value = TriggerLifecycle(state="TRIGGER_STILL_VALID", detail="ok")
        _determine_event(a, state, cfg, now, opening_range_window=False)

    assert lifecycle_mock.call_count == 1
    evidence_arg = lifecycle_mock.call_args[0][0]
    assert evidence_arg.confirmed is True
    assert evidence_arg.direction == "LONG"


def test_entry_triggered_confirmed_trigger_reaches_eligibility_not_entry_not_confirmed():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    assert state["symbols"]["PLTR"]["last_setup_state"] == "ENTRY_TRIGGERED"
    assert state["symbols"]["PLTR"]["last_alert_reason"] != "ENTRY_NOT_CONFIRMED"


def test_entry_triggered_confirmed_then_invalidated_returns_trigger_invalidated():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.battle_plan.invalidation_price = float(a.market_data.price or 0.0) + 0.05
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_setup_state"] == "ENTRY_TRIGGERED"
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "TRIGGER_INVALIDATED"


def test_trigger_expiry_returns_trigger_expired():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    with patch("src.daily_stock_analyse.live_alerts._evaluate_trigger_lifecycle") as lifecycle_mock:
        from src.daily_stock_analyse.live_alerts import TriggerLifecycle

        lifecycle_mock.return_value = TriggerLifecycle(state="TRIGGER_EXPIRED", detail="reference drift")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "TRIGGER_EXPIRED"


def test_trigger_direction_consistency():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    _, _, meta_long = _is_live_confirmable(_triggered_long(), cfg, opening_range_window=False, now_utc=now)
    _, _, meta_short = _is_live_confirmable(_triggered_short(), cfg, opening_range_window=False, now_utc=now)
    assert meta_long["trigger_evidence"].direction == "LONG"
    assert meta_short["trigger_evidence"].direction == "SHORT"


def test_trigger_price_consistency():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    evidence = meta["trigger_evidence"]
    assert isinstance(evidence.trigger_price, float)
    assert abs(evidence.trigger_price - float(a.market_data.price or 0.0)) < 1e-6


def test_reference_level_consistency():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    _, _, meta = _is_live_confirmable(_triggered_long(), cfg, opening_range_window=False, now_utc=now)
    evidence = meta["trigger_evidence"]
    assert evidence.reference_level is not None


def test_pre_market_cannot_generate_entry_triggered_event():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 10, 33, tzinfo=UTC)
    analysis = _triggered_long()

    ok, reason, meta = _is_live_confirmable(analysis, cfg, opening_range_window=False, now_utc=now)
    event = _determine_event(analysis, state, cfg, now, opening_range_window=False)

    assert ok is False
    assert meta["session_state"] == "PRE_MARKET"
    assert meta["session_gate_blocked"] is True
    assert meta["setup_state"] != "ENTRY_TRIGGERED"
    assert meta["opening_range_status"] == "DISABLED_OUTSIDE_US_REGULAR"
    assert meta["vwap_status"] == "DISABLED_OUTSIDE_US_REGULAR"
    assert "disabled" in reason.lower()
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ENTRY_NOT_CONFIRMED"


def test_after_hours_cannot_generate_entry_triggered_event():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 22, 30, tzinfo=UTC)
    analysis = _triggered_long()

    ok, _, meta = _is_live_confirmable(analysis, cfg, opening_range_window=False, now_utc=now)
    event = _determine_event(analysis, state, cfg, now, opening_range_window=False)

    assert ok is False
    assert meta["session_state"] == "AFTER_HOURS"
    assert meta["session_gate_blocked"] is True
    assert meta["setup_state"] != "ENTRY_TRIGGERED"
    assert event is None


def test_closed_cannot_generate_entry_triggered_event():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    analysis = _triggered_long()

    ok, reason, meta = _is_live_confirmable(analysis, cfg, opening_range_window=False, now_utc=now)
    event = _determine_event(analysis, state, cfg, now, opening_range_window=False)

    assert ok is False
    assert meta["session_state"] == "CLOSED"
    assert meta["session_gate_blocked"] is True
    assert meta["setup_state"] != "ENTRY_TRIGGERED"
    assert "market is closed" in reason.lower()
    assert event is None


def test_us_regular_still_allows_entry_triggered_event():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    analysis = _triggered_long()

    ok, _, meta = _is_live_confirmable(analysis, cfg, opening_range_window=False, now_utc=now)
    event = _determine_event(analysis, state, cfg, now, opening_range_window=False)

    assert ok is True
    assert meta["session_state"] == "US_REGULAR"
    assert meta.get("session_gate_blocked") is not True
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"


def test_premarket_rvol_is_not_labeled_as_regular_session_reliable():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 10, 33, tzinfo=UTC)
    _, _, meta = _is_live_confirmable(_triggered_long(), cfg, opening_range_window=False, now_utc=now)
    assert meta["rvol_session"] == "PRE_MARKET"
    assert meta["rvol_quality"] == "DATA_LIMITED"


def test_valid_trigger_and_levels_and_rr_is_alert_eligible():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels(
            direction="SHORT",
            entry=100.0,
            stop=101.0,
            target1=98.0,
            target2=97.0,
            risk_reward=2.0,
            source="swing_structure",
            detail=None,
        )
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_valid_long_direction_levels_are_eligible_with_rr_2():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        levels_mock.return_value = _patched_levels("LONG", 100.0, 95.0, 110.0)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is not None
    assert event["risk_reward_ratio"] == 2.0
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_valid_short_direction_levels_are_eligible_with_rr_2():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        levels_mock.return_value = _patched_levels("SHORT", 100.0, 105.0, 90.0)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is not None
    assert event["risk_reward_ratio"] == 2.0
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_short_with_long_geometry_is_blocked_with_direction_level_mismatch():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        levels_mock.return_value = _patched_levels("SHORT", 100.0, 95.0, 110.0, risk_reward=2.0, detail="mismatch")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DIRECTION_LEVEL_MISMATCH"


def test_long_with_short_geometry_is_blocked_with_direction_level_mismatch():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        levels_mock.return_value = _patched_levels("LONG", 100.0, 105.0, 90.0, risk_reward=2.0, detail="mismatch")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DIRECTION_LEVEL_MISMATCH"


def test_direction_mismatch_cannot_pass_volume_gate():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()
    a.market_data.intraday_rvol = 2.0
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        levels_mock.return_value = _patched_levels("SHORT", 100.0, 95.0, 110.0, risk_reward=2.0, detail="mismatch")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DIRECTION_LEVEL_MISMATCH"


def test_direction_mismatch_never_becomes_alert_eligible_or_dispatches_telegram():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()
    a.market_data.intraday_rvol = 2.0
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        levels_mock.return_value = _patched_levels("SHORT", 100.0, 95.0, 110.0, risk_reward=2.0, detail="mismatch")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    alerts = [x for x in [event] if x is not None]
    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        sent = _send_telegram_alerts(alerts, cfg)

    assert event is None
    assert sent == 0
    assert send_mock.call_count == 0
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DIRECTION_LEVEL_MISMATCH"


def test_v47_directional_regression_valid_long_uber_style_case_still_confirms():
    cfg = _cfg()
    state = {"symbols": {"UBER": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.symbol = "UBER"
    a.name = "UBER"
    a.market_data.symbol = "UBER"
    a.market_data.price = 77.77
    a.market_data.intraday_rvol = 1.80
    a.market_data.intraday_rvol_quality = "RELIABLE"
    a.battle_plan.entry_trigger_price = 77.77
    a.battle_plan.confirmation_level = 77.77
    a.battle_plan.invalidation_price = 75.39
    a.battle_plan.target_1 = 82.39
    a.battle_plan.target_2 = None
    a.market_data.opening_range_low = 75.39
    a.market_data.support = 75.39
    a.market_data.opening_range_high = 82.39
    a.market_data.resistance = 82.39

    trigger_evidence = TriggerEvidence(
        confirmed=True,
        direction="LONG",
        trigger_type="EMA retest continuation",
        trigger_price=77.77,
        reference_level=77.77,
        current_price=77.77,
        timestamp=now.isoformat(),
        detail="confirmed",
    )

    v4 = {
        "phase": "NORMAL",
        "rvol_quality": "RELIABLE",
        "rvol": 1.80,
        "setup_state": "ENTRY_TRIGGERED",
        "setup_score": 87,
        "trigger": "EMA retest continuation",
        "confirmation": "confirmed",
        "state_reason": "confirmed",
        "vwap_status": "AVAILABLE",
        "opening_range_status": "AVAILABLE",
        "trigger_evidence": trigger_evidence,
    }

    confirmable_result = (True, "CONFIRMED", v4)

    with patch("src.daily_stock_analyse.live_alerts._is_live_confirmable", return_value=confirmable_result):
        with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
            levels_mock.return_value = _patched_levels("LONG", 77.77, 75.39, 82.39, target2=None, risk_reward=1.94)
            event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is not None
    assert state["symbols"]["UBER"]["last_alert_reason"] == "ALERT_ELIGIBLE"
    assert state["symbols"]["UBER"]["volume_lifecycle"]["state"] == "VOLUME_CONFIRMED"


def test_valid_trigger_low_rvol_is_blocked_rvol_too_low():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()
    a.market_data.intraday_rvol = 1.0
    a.market_data.intraday_rvol_quality = "RELIABLE"
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("SHORT", 100.0, 101.0, 98.0, 97.0, 2.0, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RVOL_TOO_LOW"


def test_valid_trigger_low_rr_is_blocked_rr_too_low():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()
    entry = float(a.market_data.price or 0.0)
    a.battle_plan.target_1 = entry - 0.30
    a.battle_plan.target_2 = entry - 0.60
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RR_TOO_LOW"


def test_valid_trigger_cooldown_blocked():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {
        "symbols": {
            "PLTR": {
                "last_signal": "WAIT",
                "last_alert_type": "WAIT_TO_SHORT",
                "last_alert_timestamp": (now - timedelta(minutes=5)).isoformat(),
            }
        }
    }
    a = _triggered_short()
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("SHORT", 100.0, 101.0, 98.0, 97.0, 2.0, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "COOLDOWN"


def test_valid_trigger_duplicate_position_blocked():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "position_state": "IN_POSITION", "active_direction": "SHORT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_short()
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("SHORT", 100.0, 101.0, 98.0, 97.0, 2.0, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DUPLICATE_POSITION"


def test_v47_entry_triggered_valid_rr_low_reliable_rvol_wait_for_volume():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 0.80
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, 99.0, 102.0, 103.0, 2.0, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_decision"] == "WAIT_FOR_VOLUME"
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RVOL_TOO_LOW"


def test_v47_wait_for_volume_never_dispatches_telegram():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 0.80
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, 99.0, 102.0, 103.0, 2.0, "swing_structure", None)
        blocked = _determine_event(a, state, cfg, now, opening_range_window=False)

    alerts = [x for x in [blocked] if x is not None]
    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        sent = _send_telegram_alerts(alerts, cfg)
    assert sent == 0
    assert send_mock.call_count == 0


def test_v47_wait_for_volume_preserves_trigger_evidence():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 0.80
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, 99.0, 102.0, 103.0, 2.0, "swing_structure", None)
        _determine_event(a, state, cfg, now, opening_range_window=False)

    candidate = state["symbols"]["PLTR"].get("volume_candidate")
    assert isinstance(candidate, dict)
    assert candidate["trigger_evidence"]["confirmed"] is True


def test_v47_volume_cross_wait_to_confirmed_revalidates_to_eligible():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {
        "symbols": {
            "PLTR": {
                "last_signal": "WAIT",
                "volume_lifecycle": {"state": "WAIT_FOR_VOLUME", "rvol": 0.8, "required_rvol": 1.5},
            }
        }
    }
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.60
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, 99.0, 102.0, 103.0, 2.0, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is not None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"
    assert state["symbols"]["PLTR"]["volume_lifecycle"]["state"] == "VOLUME_CONFIRMED"


def test_v47_volume_lost_after_confirmed_does_not_dispatch():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {
        "symbols": {
            "PLTR": {
                "last_signal": "WAIT",
                "volume_lifecycle": {"state": "VOLUME_CONFIRMED", "rvol": 1.6, "required_rvol": 1.5},
            }
        }
    }
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.20
    a.market_data.intraday_rvol_quality = "RELIABLE"

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, 99.0, 102.0, 103.0, 2.0, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_decision"] == "VOLUME_LOST"


def test_v47_wait_then_trigger_invalidated_has_priority():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "volume_lifecycle": {"state": "WAIT_FOR_VOLUME"}}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    with patch("src.daily_stock_analyse.live_alerts._evaluate_trigger_lifecycle") as lifecycle_mock:
        from src.daily_stock_analyse.live_alerts import TriggerLifecycle

        lifecycle_mock.return_value = TriggerLifecycle("TRIGGER_INVALIDATED", "manual invalidation")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "TRIGGER_INVALIDATED"


def test_v47_wait_then_trigger_expired_has_priority():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "volume_lifecycle": {"state": "WAIT_FOR_VOLUME"}}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    with patch("src.daily_stock_analyse.live_alerts._evaluate_trigger_lifecycle") as lifecycle_mock:
        from src.daily_stock_analyse.live_alerts import TriggerLifecycle

        lifecycle_mock.return_value = TriggerLifecycle("TRIGGER_EXPIRED", "max age")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "TRIGGER_EXPIRED"


def test_v47_invalidated_trigger_cannot_alert_after_volume_rise():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "volume_lifecycle": {"state": "WAIT_FOR_VOLUME"}}}}
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.80

    with patch("src.daily_stock_analyse.live_alerts._evaluate_trigger_lifecycle") as lifecycle_mock:
        from src.daily_stock_analyse.live_alerts import TriggerLifecycle

        lifecycle_mock.return_value = TriggerLifecycle("TRIGGER_INVALIDATED", "invalidated")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "TRIGGER_INVALIDATED"


def test_v47_expired_trigger_cannot_alert_after_volume_rise():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "volume_lifecycle": {"state": "WAIT_FOR_VOLUME"}}}}
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.80

    with patch("src.daily_stock_analyse.live_alerts._evaluate_trigger_lifecycle") as lifecycle_mock:
        from src.daily_stock_analyse.live_alerts import TriggerLifecycle

        lifecycle_mock.return_value = TriggerLifecycle("TRIGGER_EXPIRED", "max age")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "TRIGGER_EXPIRED"


def test_v47_waiting_candidate_revalidation_invalid_levels_blocks_invalid_risk_levels():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "volume_lifecycle": {"state": "WAIT_FOR_VOLUME"}}}}
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.80

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, None, 102.0, 103.0, None, "none", "missing stop")
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "INVALID_RISK_LEVELS"


def test_v47_waiting_candidate_revalidation_rr_too_low_blocks():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "volume_lifecycle": {"state": "WAIT_FOR_VOLUME"}}}}
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.80

    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure") as levels_mock:
        from src.daily_stock_analyse.live_alerts import TradeLevels

        levels_mock.return_value = TradeLevels("LONG", 100.0, 99.5, 100.4, 100.8, 0.8, "swing_structure", None)
        event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RR_TOO_LOW"


def test_v47_reliable_rvol_gate_remains_150():
    cfg = _cfg()
    assert abs(cfg.v4_normal_min_rvol - 1.50) < 1e-9


def test_normal_phase_rvol_exactly_below_threshold_blocks():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.49
    a.market_data.intraday_rvol_quality = "RELIABLE"

    event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RVOL_TOO_LOW"


def test_normal_phase_rvol_exactly_at_threshold_allows_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.50
    a.market_data.intraday_rvol_quality = "RELIABLE"

    event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is not None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"
    assert state["symbols"]["PLTR"]["volume_lifecycle"]["state"] == "VOLUME_CONFIRMED"


def test_normal_phase_rvol_above_threshold_allows_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.60
    a.market_data.intraday_rvol_quality = "RELIABLE"

    event = _determine_event(a, state, cfg, now, opening_range_window=False)

    assert event is not None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_smci_style_rvol_145_is_below_normal_volume_gate(capsys):
    cfg = _cfg()
    state = {"symbols": {"SMCI": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.symbol = "SMCI"
    a.name = "SMCI"
    a.market_data.symbol = "SMCI"
    a.market_data.intraday_rvol = 1.45
    a.market_data.intraday_rvol_quality = "RELIABLE"

    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    out = capsys.readouterr().out

    assert event is None
    assert "RVOL 1.45" in out
    assert "below volume gate" in out
    assert state["symbols"]["SMCI"]["last_alert_reason"] == "RVOL_TOO_LOW"


def test_confirmed_regular_session_trigger_reaches_telegram_dispatch():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)

    assert event is not None
    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        send_mock.return_value = TelegramSendResult(success=True, status_code=200)
        sent = _send_telegram_alerts([event], cfg)

    assert sent == 1
    assert send_mock.call_count == 1


def test_later_blocking_gate_logs_explicit_reason_and_prevents_dispatch(capsys):
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.80
    a.market_data.intraday_rvol_quality = "RELIABLE"
    entry = float(a.market_data.price or 0.0)
    a.battle_plan.target_1 = entry + 0.30
    a.battle_plan.target_2 = entry + 0.60

    blocked_event = _determine_event(a, state, cfg, now, opening_range_window=False)
    out = capsys.readouterr().out

    assert blocked_event is None
    assert "ALERT_BLOCKED | RR_TOO_LOW" in out
    assert "minimum 1.50" in out

    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        sent = _send_telegram_alerts([], cfg)
    assert sent == 0
    assert send_mock.call_count == 0


def test_duplicate_alert_suppression_prevents_second_dispatch_within_cooldown():
    cfg = _cfg(telegram_enabled=True)
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}

    first = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert first is not None

    state["symbols"]["PLTR"]["last_alert_type"] = "WAIT_TO_LONG"
    state["symbols"]["PLTR"]["last_alert_timestamp"] = (now - timedelta(minutes=5)).isoformat()

    second = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert second is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "COOLDOWN"

    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        send_mock.return_value = TelegramSendResult(success=True, status_code=200)
        sent = _send_telegram_alerts([a for a in [first, second] if a is not None], cfg)

    assert sent == 1
    assert send_mock.call_count == 1


def test_confirmed_trigger_below_setup_threshold_returns_explicit_non_eligible_reason():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()

    mocked_assessment = {
        "components": {},
        "final_setup_score": 62,
        "setup_state": "SETUP_DEVELOPING",
        "trigger": "VWAP reclaim and hold",
        "confirmation": "VWAP reclaim confirmed",
        "state_reason": "trigger confirmed but setup score 62 below minimum 70",
        "vwap_status": "AVAILABLE",
        "opening_range_status": "AVAILABLE",
        "trigger_evidence": TriggerEvidence(
            confirmed=True,
            direction="LONG",
            trigger_type="RECLAIM",
            trigger_price=float(a.market_data.price or 0.0),
            reference_level=float(a.market_data.vwap or 0.0),
            current_price=float(a.market_data.price or 0.0),
            timestamp=now.isoformat(),
            detail="VWAP reclaim confirmed",
        ),
    }

    with patch("src.daily_stock_analyse.live_alerts._build_live_setup_assessment", return_value=mocked_assessment):
        ok, reason, _ = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)

    assert ok is False
    assert reason == "trigger confirmed but setup score 62 below minimum 70"
