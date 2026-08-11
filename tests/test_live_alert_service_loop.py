from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.market_hours import MarketSessionStatus


def _cfg(interval_minutes: int = 5) -> AppConfig:
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
        live_alert_interval_minutes=interval_minutes,
        live_market_timezone="America/New_York",
        live_market_open="09:30",
        live_market_close="16:00",
        alert_min_setup_score=70,
        alert_min_rvol=1.5,
        alert_cooldown_minutes=15,
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )


def _session(
    *,
    market_open: bool,
    reason: str,
    now_market: datetime,
    open_time: datetime | None,
    close_time: datetime | None,
) -> MarketSessionStatus:
    return MarketSessionStatus(
        market_open=market_open,
        reason=reason,
        opening_range_window=False,
        market_now=now_market,
        market_open_time=open_time,
        market_close_time=close_time,
    )


def test_service_waits_for_open_then_runs_and_stops_after_close(tmp_path: Path):
    from src.daily_stock_analyse.live_alerts import run_live_alerts

    ny_tz = ZoneInfo("America/New_York")
    after_hours_market = datetime(2026, 8, 10, 18, 30, tzinfo=ny_tz)
    open_time = datetime(2026, 8, 10, 9, 30, tzinfo=ny_tz)
    close_time = datetime(2026, 8, 10, 16, 0, tzinfo=ny_tz)

    statuses = [
        _session(
            market_open=False,
            reason="Outside regular market hours (calendar fallback mode)",
            now_market=after_hours_market,
            open_time=open_time,
            close_time=close_time,
        ),
    ]

    with patch("src.daily_stock_analyse.live_alerts.load_config", return_value=_cfg(5)):
        with patch(
            "src.daily_stock_analyse.live_alerts.utc_now",
            side_effect=[
                datetime(2026, 8, 10, 13, 25, tzinfo=UTC),
                datetime(2026, 8, 14, 16, 30, tzinfo=UTC),
            ],
        ):
            with patch("src.daily_stock_analyse.live_alerts.get_market_session_status", side_effect=statuses):
                with patch("src.daily_stock_analyse.live_alerts._run_live_alert_evaluation_cycle", return_value=0) as eval_mock:
                    with patch("src.daily_stock_analyse.live_alerts.time_module.sleep") as sleep_mock:
                        rc = run_live_alerts(tmp_path)

    assert rc == 0
    assert eval_mock.call_count == 1
    sleep_mock.assert_called_once_with(300)


def test_service_exits_on_weekend_without_wait_or_evaluation(tmp_path: Path):
    from src.daily_stock_analyse.live_alerts import run_live_alerts

    ny_tz = ZoneInfo("America/New_York")
    weekend_market = datetime(2026, 8, 9, 10, 0, tzinfo=ny_tz)
    status = _session(
        market_open=False,
        reason="Weekend - U.S. regular market closed",
        now_market=weekend_market,
        open_time=None,
        close_time=None,
    )

    with patch("src.daily_stock_analyse.live_alerts.load_config", return_value=_cfg(5)):
        with patch("src.daily_stock_analyse.live_alerts.utc_now", return_value=datetime(2026, 8, 9, 14, 0, tzinfo=UTC)):
            with patch("src.daily_stock_analyse.live_alerts.get_market_session_status", return_value=status):
                with patch("src.daily_stock_analyse.live_alerts._run_live_alert_evaluation_cycle", return_value=0) as eval_mock:
                    with patch("src.daily_stock_analyse.live_alerts.time_module.sleep") as sleep_mock:
                        rc = run_live_alerts(tmp_path)

    assert rc == 0
    assert eval_mock.call_count == 0
    assert sleep_mock.call_count == 0


def test_service_uses_configured_interval_between_open_evaluations(tmp_path: Path):
    from src.daily_stock_analyse.live_alerts import run_live_alerts

    ny_tz = ZoneInfo("America/New_York")
    open_time = datetime(2026, 8, 10, 9, 30, tzinfo=ny_tz)
    close_time = datetime(2026, 8, 10, 16, 0, tzinfo=ny_tz)
    open_market_1 = datetime(2026, 8, 10, 10, 0, tzinfo=ny_tz)
    open_market_2 = datetime(2026, 8, 10, 10, 1, tzinfo=ny_tz)
    closed_market = datetime(2026, 8, 10, 16, 1, tzinfo=ny_tz)

    statuses = [
        _session(
            market_open=True,
            reason="Market open (calendar fallback mode)",
            now_market=open_market_1,
            open_time=open_time,
            close_time=close_time,
        ),
    ]

    with patch("src.daily_stock_analyse.live_alerts.load_config", return_value=_cfg(7)):
        with patch(
            "src.daily_stock_analyse.live_alerts.utc_now",
            side_effect=[
                datetime(2026, 8, 10, 14, 0, tzinfo=UTC),
                datetime(2026, 8, 14, 16, 30, tzinfo=UTC),
            ],
        ):
            with patch("src.daily_stock_analyse.live_alerts.get_market_session_status", side_effect=statuses) as session_mock:
                with patch("src.daily_stock_analyse.live_alerts._run_live_alert_evaluation_cycle", return_value=0) as eval_mock:
                    with patch("src.daily_stock_analyse.live_alerts.time_module.sleep") as sleep_mock:
                        rc = run_live_alerts(tmp_path)

    assert rc == 0
    assert eval_mock.call_count == 1
    sleep_mock.assert_called_once_with(420)
    for call in session_mock.call_args_list:
        _, kwargs = call
        assert kwargs["market_timezone"] == "America/New_York"
        assert kwargs["market_open_hhmm"] == "09:30"
        assert kwargs["market_close_hhmm"] == "16:00"
