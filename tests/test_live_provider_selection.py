from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import _run_live_alert_evaluation_cycle
from src.daily_stock_analyse.providers import create_market_data_provider
from src.daily_stock_analyse.providers.yfinance_provider import YFinanceMarketDataProvider


def _cfg(*, live_data_provider: str = "yfinance") -> AppConfig:
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
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
        live_data_provider=live_data_provider,
    )


def test_factory_selects_yfinance_provider():
    provider = create_market_data_provider("yfinance")
    assert isinstance(provider, YFinanceMarketDataProvider)


def test_factory_rejects_unsupported_provider_name():
    with pytest.raises(ValueError, match="Unsupported live market data provider 'unsupported-provider'.*yfinance"):
        create_market_data_provider("unsupported-provider")


def test_live_evaluation_cycle_uses_selected_market_provider(tmp_path: Path):
    cfg = _cfg(live_data_provider="yfinance")
    now = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)
    session = SimpleNamespace(
        opening_range_window=False,
        reason="Market open",
        market_now=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
    )

    fake_market_provider = object()

    class _Regime:
        label = "MIXED"
        indicators = {"semiconductor_etf_change_pct": None}

    call_count = {"count": 0}

    def _fake_analyze_symbol(symbol, cfg_obj, regime_label, sector_strength, market_provider, news_provider):
        assert market_provider is fake_market_provider
        call_count["count"] += 1
        raise RuntimeError("synthetic stop")

    with patch("src.daily_stock_analyse.live_alerts.create_market_data_provider", return_value=fake_market_provider) as factory_mock:
        with patch("src.daily_stock_analyse.live_alerts.build_market_regime", return_value=_Regime()):
            with patch("src.daily_stock_analyse.live_alerts._analyze_symbol", side_effect=_fake_analyze_symbol):
                with patch("src.daily_stock_analyse.live_alerts._send_telegram_alerts", return_value=0):
                    generated = _run_live_alert_evaluation_cycle(tmp_path, cfg, now, session)

    assert generated == 0
    assert call_count["count"] == len(set(cfg.fixed_watchlist + cfg.candidate_universe))
    factory_mock.assert_called_once_with("yfinance")
