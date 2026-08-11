from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from src.daily_stock_analyse.ai_analysis import build_ai_overlay_payload, generate_ai_overlay
from src.daily_stock_analyse.ai_providers import AIProviderError
from src.daily_stock_analyse.ai_providers.base import AIProviderResponse
from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.models import (
    BattlePlan,
    DailyAnalysisReport,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.runner import run_analysis


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
        live_data_provider="yfinance",
        ai_primary_provider="openai",
        ai_fallback_provider="gemini",
    )


def _analysis() -> StockAnalysis:
    return StockAnalysis(
        symbol="AMD",
        name="AMD",
        signal="LONG",
        trading_horizon="DAY_TRADE",
        direction_bias="LONG_BIAS",
        market_alignment="MARKET_ALIGNED",
        setup_score=80,
        day_trade_candidate=True,
        candidate_score=82,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="Break above resistance",
        confidence="MEDIUM",
        one_liner="x",
        main_reason="Strong setup",
        risk_classification="MEDIUM",
        market_data=MarketData(
            symbol="AMD",
            price=100.0,
            trend="UPTREND",
            sma20=98.0,
            sma50=95.0,
            sma200=90.0,
            rsi14=55.0,
            macd=1.1,
            vwap=99.5,
            opening_range_high=101.0,
            opening_range_low=98.5,
            breakout_state="BREAKOUT",
            atr14=2.2,
            volume=1_000_000,
            relative_volume=1.8,
            volatility_20d=0.25,
            support=96.0,
            resistance=105.0,
        ),
        intelligence=IntelligenceBlock(
            facts=["Growth momentum improving"],
            interpretation=["Watch semiconductor leadership"],
            upcoming_catalysts=["Earnings next week"],
        ),
        battle_plan=BattlePlan("b", "s", "96", "105", "entry", "target", "stop", "rr"),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def _market_regime() -> MarketRegime:
    return MarketRegime(
        label="MIXED",
        bias="NEUTRAL",
        main_catalyst="Fed",
        main_risk="Volatility",
        summary="Mixed tape",
        indicators={},
    )


class _FakeProvider:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error

    def generate_overlay(self, payload: dict):
        if self.error is not None:
            raise self.error
        return self.response


def test_ai_payload_structure_is_preserved():
    payload = build_ai_overlay_payload([_analysis()], _market_regime())
    assert payload == {
        "market_regime": {
            "label": "MIXED",
            "bias": "NEUTRAL",
            "main_catalyst": "Fed",
            "main_risk": "Volatility",
        },
        "stocks": [
            {
                "symbol": "AMD",
                "signal": "LONG",
                "direction_bias": "LONG_BIAS",
                "market_alignment": "MARKET_ALIGNED",
                "confidence": "MEDIUM",
                "setup_score": 80,
                "candidate_score": 82,
                "candidate_status": "DAY_TRADE CANDIDATE",
                "confirmation_needed": "Break above resistance",
                "trading_horizon": "DAY_TRADE",
                "day_trade_candidate": True,
                "price": 100.0,
                "trend": "UPTREND",
                "sma20": 98.0,
                "sma50": 95.0,
                "sma200": 90.0,
                "rsi": 55.0,
                "macd": 1.1,
                "vwap": 99.5,
                "opening_range_high": 101.0,
                "opening_range_low": 98.5,
                "breakout_state": "BREAKOUT",
                "atr": 2.2,
                "volume": 1_000_000,
                "relative_volume": 1.8,
                "volatility": 0.25,
                "support": 96.0,
                "resistance": 105.0,
                "news_facts": ["Growth momentum improving"],
                "catalysts": ["Earnings next week"],
                "risks": ["Watch semiconductor leadership"],
                "data_quality_warnings": [],
            }
        ],
    }


def _overlay_response(provider: str, parsed: dict) -> AIProviderResponse:
    return AIProviderResponse(
        provider=provider,
        summary=parsed.get("summary", ""),
        action_points=parsed.get("action_points", []),
        raw_text="raw",
        parsed=parsed,
    )


def test_openai_success_skips_gemini():
    cfg = _cfg()
    openai_provider = _FakeProvider(
        response=_overlay_response(
            "openai",
            {
                "market_bias": "MIXED",
                "market_regime": "Mixed tape with selective strength.",
                "best_long_candidate": {"symbol": "AMD", "reason": "Strongest long candidate in supplied data."},
                "best_short_candidate": {"symbol": "NONE", "reason": "No valid short candidate in supplied data."},
                "best_day_trade": {
                    "symbol": "AMD",
                    "direction": "LONG",
                    "reason": "Best day-trade candidate from supplied setup.",
                    "status": "Confirmed",
                },
                "stocks_to_watch": [{"symbol": "AMD", "reason": "Trend and RVOL are constructive."}],
                "stocks_to_avoid": [],
                "key_risks": ["Mixed market regime"],
                "action_points": ["Watch AMD", "Wait for confirmation", "Stay selective"],
                "summary": "Selective long bias with mixed tape.",
                "final_conclusion": "The market is mixed, AMD is the strongest long idea, and traders should stay selective while waiting for confirmation.",
            },
        )
    )
    gemini_provider = _FakeProvider(response=_overlay_response("gemini", {"summary": "Fallback", "action_points": ["A", "B", "C"]}))

    with patch("src.daily_stock_analyse.ai_analysis.create_ai_provider", side_effect=[openai_provider, gemini_provider]):
        with patch.object(openai_provider, "generate_overlay", wraps=openai_provider.generate_overlay) as openai_call:
            with patch.object(gemini_provider, "generate_overlay", wraps=gemini_provider.generate_overlay) as gemini_call:
                overlay = generate_ai_overlay([_analysis()], _market_regime(), cfg)

    assert overlay["enabled"] is True
    assert overlay["provider"] == "openai"
    assert overlay["status"] == "Enabled"
    assert overlay["best_day_trade"]["symbol"] == "AMD"
    assert overlay["market_bias"] == "MIXED"
    assert overlay["message"].startswith("The market is mixed")
    assert openai_call.call_count == 1
    assert gemini_call.call_count == 0


def test_openai_quota_failure_uses_gemini_fallback():
    cfg = _cfg()
    openai_provider = _FakeProvider(error=AIProviderError("openai", "OpenAI quota or rate limit exceeded"))
    gemini_provider = _FakeProvider(
        response=_overlay_response(
            "gemini",
            {
                "market_bias": "NEUTRAL",
                "market_regime": "Neutral tape with limited conviction.",
                "best_long_candidate": {"symbol": "AMD", "reason": "Only constructive long bias in supplied data."},
                "best_short_candidate": {"symbol": "NONE", "reason": "No short setup supplied."},
                "best_day_trade": {
                    "symbol": "AMD",
                    "direction": "LONG",
                    "reason": "Strongest candidate, not yet fully confirmed.",
                    "status": "Candidate, not confirmed",
                },
                "stocks_to_watch": [{"symbol": "AMD", "reason": "Watch for confirmation."}],
                "stocks_to_avoid": [],
                "key_risks": ["Confirmation is pending"],
                "action_points": ["A1", "A2", "A3"],
                "summary": "Fallback summary",
                "final_conclusion": "No fully confirmed day-trade is present; AMD remains the main watch-only candidate.",
            },
        )
    )

    with patch("src.daily_stock_analyse.ai_analysis.create_ai_provider", side_effect=[openai_provider, gemini_provider]):
        with patch.object(openai_provider, "generate_overlay", wraps=openai_provider.generate_overlay):
            with patch.object(gemini_provider, "generate_overlay", wraps=gemini_provider.generate_overlay):
                overlay = generate_ai_overlay([_analysis()], _market_regime(), cfg)

    assert overlay["enabled"] is True
    assert overlay["provider"] == "gemini"
    assert overlay["status"] == "Fallback"
    assert overlay["fallback_used"] is True
    assert overlay["action_points"] == ["A1", "A2", "A3"]
    assert overlay["provider_display"] == "Gemini"


def test_both_providers_fail_returns_disabled_without_secret_leak():
    cfg = _cfg()
    secret = "super-secret-key"
    openai_provider = _FakeProvider(error=AIProviderError("openai", f"OpenAI failed {secret}"))
    gemini_provider = _FakeProvider(error=AIProviderError("gemini", f"Gemini failed {secret}"))

    with patch("src.daily_stock_analyse.ai_analysis.create_ai_provider", side_effect=[openai_provider, gemini_provider]):
        with patch.object(openai_provider, "generate_overlay", wraps=openai_provider.generate_overlay):
            with patch.object(gemini_provider, "generate_overlay", wraps=gemini_provider.generate_overlay):
                overlay = generate_ai_overlay([_analysis()], _market_regime(), cfg)

    assert overlay["enabled"] is False
    assert overlay["provider"] is None
    assert secret not in overlay["message"]


def test_wait_candidate_is_not_converted_into_confirmed_trade():
    cfg = _cfg()
    wait_analysis = _analysis()
    wait_analysis.signal = "WAIT"
    wait_analysis.candidate_status = "DAY_TRADE CANDIDATE - WAIT FOR LIVE CONFIRMATION"
    openai_provider = _FakeProvider(
        response=_overlay_response(
            "openai",
            {
                "market_bias": "MIXED",
                "market_regime": "Mixed tape.",
                "best_long_candidate": {"symbol": "AMD", "reason": "Long bias remains constructive."},
                "best_short_candidate": {"symbol": "NONE", "reason": "No short setup."},
                "best_day_trade": {
                    "symbol": "AMD",
                    "direction": "LONG",
                    "reason": "Model attempted to upgrade a wait candidate.",
                    "status": "Confirmed",
                },
                "stocks_to_watch": [{"symbol": "AMD", "reason": "Watch only pending confirmation."}],
                "stocks_to_avoid": [],
                "key_risks": ["Signal is WAIT"],
                "action_points": ["Wait", "Do not chase", "Require confirmation"],
                "summary": "Watch only.",
                "final_conclusion": "AMD is a watch-only candidate and is not confirmed as a trade.",
            },
        )
    )

    with patch("src.daily_stock_analyse.ai_analysis.create_ai_provider", return_value=openai_provider):
        overlay = generate_ai_overlay([wait_analysis], _market_regime(), cfg)

    assert overlay["best_day_trade"]["symbol"] == "NONE"
    assert overlay["best_day_trade"]["status"] == "No trade"


def test_missing_candidate_data_produces_none_entries():
    cfg = _cfg()
    openai_provider = _FakeProvider(
        response=_overlay_response(
            "openai",
            {
                "market_bias": "MIXED",
                "market_regime": "Mixed tape.",
                "summary": "Summary only.",
                "final_conclusion": "Summary only.",
                "action_points": ["Stay selective"],
            },
        )
    )

    with patch("src.daily_stock_analyse.ai_analysis.create_ai_provider", return_value=openai_provider):
        overlay = generate_ai_overlay([_analysis()], _market_regime(), cfg)

    assert overlay["best_long_candidate"]["symbol"] == "NONE"
    assert overlay["best_short_candidate"]["symbol"] == "NONE"
    assert overlay["best_day_trade"]["symbol"] == "NONE"


def test_run_analysis_remains_non_fatal_when_both_ai_providers_fail(tmp_path: Path):
    cfg = _cfg()
    repo_root = Path(__file__).resolve().parents[1]

    class _FakeMarketProvider:
        def get_market_data(self, symbol: str) -> MarketData:
            md = _analysis().market_data
            md.symbol = symbol
            return md

    class _FakeNewsProvider:
        def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
            return _analysis().intelligence

    regime = _market_regime()

    with patch("src.daily_stock_analyse.runner.load_config", return_value=cfg):
        with patch("src.daily_stock_analyse.runner.YFinanceMarketDataProvider", return_value=_FakeMarketProvider()):
            with patch("src.daily_stock_analyse.runner.YFinanceNewsProvider", return_value=_FakeNewsProvider()):
                with patch("src.daily_stock_analyse.runner.build_market_regime", return_value=regime):
                    with patch("src.daily_stock_analyse.runner.select_dynamic_opportunities", return_value=[]):
                        with patch(
                            "src.daily_stock_analyse.ai_analysis.create_ai_provider",
                            side_effect=[
                                    _FakeProvider(error=AIProviderError("openai", "quota")),
                                    _FakeProvider(error=AIProviderError("gemini", "unavailable")),
                            ],
                        ):
                                rc = run_analysis(repo_root)

    assert rc == 0
    markdown = (repo_root / "artifacts" / "daily_stock_analysis.md").read_text(encoding="utf-8")
    html = (repo_root / "artifacts" / "daily_stock_analysis.html").read_text(encoding="utf-8")
    assert "AI Trading Conclusion" in markdown
    assert "AI unavailable" in markdown
    assert "AI Trading Conclusion" in html
