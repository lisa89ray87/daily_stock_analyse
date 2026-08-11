import json
from pathlib import Path
from unittest.mock import patch

from src.daily_stock_analyse.ai_providers import AIProviderError
from src.daily_stock_analyse.ai_providers.base import AIProviderResponse
from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.runner import run_analysis


class _FakeProvider:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error

    def generate_overlay(self, payload: dict):
        if self.error is not None:
            raise self.error
        return self.response


class _FakeMarketProvider:
    def get_market_data(self, symbol: str) -> MarketData:
        return MarketData(symbol=symbol, price=100.0, support=95.0, resistance=105.0, atr14=2.0)


class _FakeNewsProvider:
    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        return IntelligenceBlock(facts=["NEWS_UNAVAILABLE"], interpretation=["x"], upcoming_catalysts=["NO_MATERIAL_CATALYST"], news_available=False)


def _cfg() -> AppConfig:
    return AppConfig(
        openai_api_key="openai-key",
        gemini_api_key="gemini-key",
        resend_api_key=None,
        email_from=None,
        email_to="x@example.com",
        send_email=False,
        data_provider="yfinance",
        news_provider="yfinance",
        fixed_watchlist=["AMD"],
        candidate_universe=[],
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
        database_enabled=True,
        database_url="postgresql://user:very-secret@host/db",
        enable_outcome_tracking=True,
        enable_backtest=True,
    )


def test_database_unavailable_is_fail_open_and_no_secret_leak(tmp_path: Path):
    cfg = _cfg()
    repo_root = Path(__file__).resolve().parents[1]

    regime = MarketRegime("MIXED", "NEUTRAL", "x", "y", "z", {})

    with patch("src.daily_stock_analyse.runner.load_config", return_value=cfg):
        with patch("src.daily_stock_analyse.runner.create_market_data_provider", return_value=_FakeMarketProvider()):
            with patch("src.daily_stock_analyse.runner.create_news_provider", return_value=_FakeNewsProvider()):
                with patch("src.daily_stock_analyse.runner.build_market_regime", return_value=regime):
                    with patch("src.daily_stock_analyse.runner.select_dynamic_opportunities", return_value=[]):
                        with patch("src.daily_stock_analyse.runner.SignalHistoryStore", side_effect=RuntimeError("db down")):
                            with patch(
                                "src.daily_stock_analyse.ai_analysis.create_ai_provider",
                                side_effect=[
                                    _FakeProvider(error=AIProviderError("openai", "quota")),
                                    _FakeProvider(response=AIProviderResponse(provider="gemini", summary="ok", action_points=["a"], raw_text="x", parsed={})),
                                ],
                            ):
                                rc = run_analysis(repo_root)

    assert rc == 0
    md = (repo_root / "artifacts" / "daily_stock_analysis.md").read_text(encoding="utf-8")
    payload = json.loads((repo_root / "artifacts" / "daily_stock_analysis.json").read_text(encoding="utf-8"))
    notes = payload.get("notes", [])
    assert any("Signal lifecycle disabled" in str(note) for note in notes)
    assert "very-secret" not in md
    assert "postgresql://" not in md
