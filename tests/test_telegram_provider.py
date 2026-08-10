from datetime import UTC, datetime
from unittest.mock import Mock, patch

import requests

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import _determine_event, _render_telegram_message, _send_telegram_alerts
from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.telegram_provider import TelegramBotProvider


def _cfg() -> AppConfig:
    return AppConfig(
        openai_api_key=None,
        resend_api_key=None,
        email_from=None,
        email_to="x@example.com",
        send_email=False,
        data_provider="yfinance",
        news_provider="yfinance",
        fixed_watchlist=["AMD"],
        candidate_universe=["PLTR"],
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
        telegram_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
    )


def _sample_alert(event_type: str = "WAIT_TO_LONG", signal: str = "LONG") -> dict:
    return {
        "symbol": "PLTR",
        "name": "PLTR",
        "signal": signal,
        "event_type": event_type,
        "subject": "x",
        "title": "x",
        "reason": "Strong momentum",
        "price": 102.5,
        "setup_score": 82,
        "direction_bias": "LONG_BIAS",
        "market_regime": "RISK_ON",
        "entry_trigger": 103.0,
        "confirmation_level": 103.0,
        "invalidation": 99.0,
        "target_1": 106.0,
        "target_2": 109.0,
        "risk_reward": "2.10",
        "timestamp": datetime(2026, 8, 10, 14, 35, tzinfo=UTC).isoformat(),
        "timestamp_market": datetime(2026, 8, 10, 14, 35, tzinfo=UTC).isoformat(),
        "level_unavailable_reason": None,
        "rvol": 1.85,
        "rvol_quality": "RELIABLE",
        "risk_reward_ratio": 2.10,
        "vwap_status": "AVAILABLE",
        "opening_range_status": "AVAILABLE",
        "setup_state": "ENTRY_TRIGGERED",
    }


def _analysis(symbol: str, signal: str, bias: str) -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=symbol,
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias=bias,
        market_alignment="MARKET_ALIGNED",
        setup_score=85,
        day_trade_candidate=True,
        candidate_score=85,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="x",
        confidence="HIGH",
        one_liner="x",
        main_reason="Strong setup",
        risk_classification="MEDIUM",
        market_data=MarketData(
            symbol=symbol,
            price=102.0,
            relative_volume=2.0,
            trend="UPTREND",
            day_change_pct=1.5,
            vwap=100.0,
            opening_range_high=101.0,
            opening_range_low=99.0,
            breakout_state="BREAKOUT",
            intraday_timestamp="2026-08-10T14:35:00Z",
            data_timestamp="2026-08-10T14:35:00Z",
        ),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan(
            bullish_scenario="b",
            bearish_scenario="s",
            key_support="99",
            key_resistance="101",
            entry_area="Break above 101",
            target_area="x",
            invalidation="Below 99",
            risk_reward_assessment="2.00",
            entry_trigger_price=101.0,
            confirmation_level=101.0,
            invalidation_price=99.0,
            target_1=104.0,
            target_2=106.0,
        ),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_successful_telegram_send():
    provider = TelegramBotProvider(enabled=True, bot_token="bot-token", chat_id="chat-id")
    mock_resp = Mock(status_code=200)
    with patch("src.daily_stock_analyse.telegram_provider.requests.post", return_value=mock_resp):
        result = provider.send_message("hello")
    assert result.success is True
    assert result.status_code == 200


def test_telegram_api_failure_status_code():
    provider = TelegramBotProvider(enabled=True, bot_token="bot-token", chat_id="chat-id")
    mock_resp = Mock(status_code=500)
    with patch("src.daily_stock_analyse.telegram_provider.requests.post", return_value=mock_resp):
        result = provider.send_message("hello")
    assert result.success is False
    assert result.status_code == 500


def test_missing_token_disables_send():
    provider = TelegramBotProvider(enabled=True, bot_token=None, chat_id="chat-id")
    result = provider.send_message("hello")
    assert result.disabled is True
    assert "TELEGRAM_BOT_TOKEN missing" in (result.error or "")


def test_missing_chat_id_disables_send():
    provider = TelegramBotProvider(enabled=True, bot_token="bot-token", chat_id=None)
    result = provider.send_message("hello")
    assert result.disabled is True
    assert "TELEGRAM_CHAT_ID missing" in (result.error or "")


def test_telegram_disabled():
    provider = TelegramBotProvider(enabled=False, bot_token="bot-token", chat_id="chat-id")
    result = provider.send_message("hello")
    assert result.disabled is True


def test_token_not_leaked_in_errors():
    secret = "bot-super-secret"
    provider = TelegramBotProvider(enabled=True, bot_token=secret, chat_id="chat-id")
    with patch(
        "src.daily_stock_analyse.telegram_provider.requests.post",
        side_effect=requests.RequestException(f"network error {secret}"),
    ):
        result = provider.send_message("hello")
    assert result.success is False
    assert secret not in (result.error or "")


def test_correct_message_payload_and_parse_mode():
    provider = TelegramBotProvider(enabled=True, bot_token="bot-token", chat_id="chat-id")
    mock_resp = Mock(status_code=200)
    with patch("src.daily_stock_analyse.telegram_provider.requests.post", return_value=mock_resp) as post_mock:
        provider.send_message("<b>alert</b>", parse_mode="Markdown")

    assert post_mock.call_count == 1
    _, kwargs = post_mock.call_args
    assert kwargs["json"]["chat_id"] == "chat-id"
    assert kwargs["json"]["text"] == "<b>alert</b>"
    assert kwargs["json"]["parse_mode"] == "Markdown"


def test_long_alert_formatting():
    msg = _render_telegram_message(_sample_alert(event_type="WAIT_TO_LONG", signal="LONG"), "America/New_York")
    assert "LONG ALERT" in msg
    assert "Signal: <b>LONG</b>" in msg
    assert "Break above" in msg
    assert "RVOL: 1.85 (RELIABLE)" in msg
    assert "VWAP: AVAILABLE" in msg
    assert "Opening Range: AVAILABLE" in msg
    assert "Risk/Reward: 2.10" in msg


def test_long_alert_formatting_with_data_limited_rvol_quality():
    alert = _sample_alert(event_type="WAIT_TO_LONG", signal="LONG")
    alert["rvol"] = None
    alert["rvol_quality"] = "DATA_LIMITED"
    msg = _render_telegram_message(alert, "America/New_York")
    assert "RVOL: DATA_LIMITED" in msg


def test_short_alert_formatting():
    msg = _render_telegram_message(_sample_alert(event_type="WAIT_TO_SHORT", signal="SHORT"), "America/New_York")
    assert "SHORT ALERT" in msg
    assert "Break below" in msg


def test_target_alert_formatting():
    msg = _render_telegram_message(_sample_alert(event_type="LONG_TARGET_1", signal="LONG"), "America/New_York")
    assert "TARGET REACHED" in msg
    assert "Target 1 reached" in msg


def test_invalidation_alert_formatting():
    msg = _render_telegram_message(_sample_alert(event_type="LONG_INVALIDATED", signal="WAIT"), "America/New_York")
    assert "LONG SETUP INVALIDATED" in msg


def test_duplicate_state_does_not_send_transition_event():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "LONG"}}}
    analysis = _analysis("PLTR", "LONG", "LONG_BIAS")
    event = _determine_event(analysis, state, cfg, datetime(2026, 8, 10, 14, 45, tzinfo=UTC), opening_range_window=False)
    assert event is None


def test_telegram_failure_does_not_fail_dispatch():
    cfg = _cfg()
    alerts = [_sample_alert(event_type="WAIT_TO_LONG", signal="LONG")]

    with patch("src.daily_stock_analyse.live_alerts.TelegramBotProvider.send_message", side_effect=RuntimeError("boom")):
        sent = _send_telegram_alerts(alerts, cfg)

    assert sent == 0


def test_no_trade_to_long_transition_generates_event():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "NO_TRADE"}}}
    analysis = _analysis("PLTR", "LONG", "LONG_BIAS")
    event = _determine_event(analysis, state, cfg, datetime(2026, 8, 10, 14, 45, tzinfo=UTC), opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"
