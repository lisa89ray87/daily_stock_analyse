from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import _determine_event, _risk_reward_ratio_from_analysis, _send_telegram_alerts
from src.daily_stock_analyse.models import BattlePlan, DataQuality, IntelligenceBlock, MarketData, ScoreBreakdown, StockAnalysis


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
            vwap=100.0,
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
            entry_trigger_price=101.0,
            confirmation_level=101.0,
            invalidation_price=100.0,
            target_1=103.0,
            target_2=104.5,
        ),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def _triggered_long() -> StockAnalysis:
    bars = _bars("up")
    resistance = float(bars[-2]["close"]) + 0.05
    bars[-1]["close"] = resistance + 0.20
    bars[-1]["high"] = resistance + 0.30
    a = _analysis("LONG", "LONG_BIAS", bars=bars, trend="UPTREND", breakout_state="BREAKOUT", support=99.0, resistance=resistance)
    a.battle_plan.entry_trigger_price = resistance
    a.battle_plan.invalidation_price = resistance - 1.0
    a.battle_plan.target_1 = resistance + 2.0
    a.battle_plan.target_2 = resistance + 3.0
    return a


def _triggered_short() -> StockAnalysis:
    bars = _bars("down", start=106.0)
    support = float(bars[-2]["close"]) - 0.05
    bars[-1]["close"] = support - 0.20
    bars[-1]["low"] = support - 0.30
    a = _analysis(
        "SHORT",
        "SHORT_BIAS",
        bars=bars,
        trend="DOWNTREND",
        breakout_state="BREAKDOWN",
        support=support,
        resistance=106.0,
        day_change_pct=-1.0,
    )
    a.battle_plan.entry_trigger_price = support
    a.battle_plan.invalidation_price = support + 1.0
    a.battle_plan.target_1 = support - 2.0
    a.battle_plan.target_2 = support - 3.0
    return a


def test_entry_triggered_valid_rr_valid_rvol_alert_eligible():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_entry_triggered_low_rvol_blocks_with_rvol_too_low():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = 1.20
    a.market_data.intraday_rvol_quality = "RELIABLE"
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_setup_state"] == "ENTRY_TRIGGERED"
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RVOL_TOO_LOW"


def test_entry_triggered_low_rr_blocks_with_rr_too_low():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.battle_plan.target_1 = a.battle_plan.entry_trigger_price + 1.48
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_setup_state"] == "ENTRY_TRIGGERED"
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "RR_TOO_LOW"


def test_entry_triggered_missing_entry_blocks_invalid_risk_levels():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.battle_plan.entry_trigger_price = None
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "INVALID_RISK_LEVELS"


def test_entry_triggered_missing_stop_blocks_invalid_risk_levels():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.battle_plan.invalidation_price = None
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "INVALID_RISK_LEVELS"


def test_entry_triggered_missing_target_blocks_invalid_risk_levels():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.battle_plan.target_1 = None
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "INVALID_RISK_LEVELS"


def test_data_limited_rvol_strong_trigger_continues_to_risk_gate():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.market_data.intraday_rvol = None
    a.market_data.intraday_rvol_quality = "DATA_LIMITED"
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ALERT_ELIGIBLE"


def test_cooldown_blocks_with_explicit_reason():
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


def test_duplicate_in_position_blocks_with_explicit_reason():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "position_state": "IN_POSITION", "active_direction": "LONG"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "DUPLICATE_POSITION"


def test_non_triggered_setup_blocks_with_entry_not_confirmed():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    bars = _bars("up")
    bars[-1]["close"] = float(bars[-2]["close"]) - 0.10
    resistance = float(bars[-1]["close"]) + 2.0
    a = _analysis("LONG", "LONG_BIAS", bars=bars, trend="UPTREND", breakout_state="NEAR BREAKOUT", support=99.0, resistance=resistance)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
    assert state["symbols"]["PLTR"]["last_alert_reason"] == "ENTRY_NOT_CONFIRMED"


def test_short_rr_calculation_uses_actual_levels():
    a = _triggered_short()
    a.battle_plan.entry_trigger_price = 100.0
    a.battle_plan.invalidation_price = 102.0
    a.battle_plan.target_1 = 97.0
    assert _risk_reward_ratio_from_analysis(a) == 1.5


def test_long_rr_calculation_uses_actual_levels():
    a = _triggered_long()
    a.battle_plan.entry_trigger_price = 101.0
    a.battle_plan.invalidation_price = 99.0
    a.battle_plan.target_1 = 104.0
    assert _risk_reward_ratio_from_analysis(a) == 1.5


def test_no_telegram_dispatch_when_blocked():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _triggered_long()
    a.battle_plan.target_1 = a.battle_plan.entry_trigger_price + 1.0
    blocked_event = _determine_event(a, state, cfg, now, opening_range_window=False)
    alerts = [x for x in [blocked_event] if x is not None]

    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        sent = _send_telegram_alerts(alerts, cfg)

    assert sent == 0
    assert send_mock.call_count == 0


def test_telegram_dispatch_when_eligible():
    cfg = _cfg(telegram_enabled=True)
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    event = _determine_event(_triggered_long(), state, cfg, now, opening_range_window=False)
    assert event is not None

    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message") as send_mock:
        send_mock.return_value.success = True
        send_mock.return_value.disabled = False
        sent = _send_telegram_alerts([event], cfg)

    assert sent == 1
    assert send_mock.call_count == 1
