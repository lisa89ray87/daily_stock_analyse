from datetime import UTC, datetime

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import _determine_event, _is_live_confirmable, _v4_session_phase
from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    ScoreBreakdown,
    StockAnalysis,
)


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
        v4_opening_start="09:30",
        v4_opening_end="10:00",
        v4_opening_min_rvol=1.20,
        v4_opening_min_setup_score=75,
        v4_normal_min_rvol=1.50,
        v4_normal_min_setup_score=70,
    )


def _analysis(
    signal: str,
    bias: str,
    setup_score: int,
    rvol: float | None,
    *,
    provider: str = "testfeed",
    trend: str = "UPTREND",
    breakout_state: str = "BREAKOUT",
    price: float = 102.0,
    vwap: float | None = 100.0,
    opening_range_high: float | None = 101.0,
    opening_range_low: float | None = 99.0,
    resistance: float | None = 101.0,
    support: float | None = 99.0,
    alignment: str = "MARKET_ALIGNED",
    volume: float | None = 2_000_000,
    avg_volume_20d: float | None = 1_000_000,
    day_change_pct: float | None = 1.2,
    risk_reward_assessment: str = "2.0",
) -> StockAnalysis:
    return StockAnalysis(
        symbol="PLTR",
        name="PLTR",
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias=bias,
        market_alignment=alignment,
        setup_score=setup_score,
        day_trade_candidate=True,
        candidate_score=setup_score,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="x",
        confidence="HIGH",
        one_liner="x",
        main_reason="Strong setup",
        risk_classification="MEDIUM",
        market_data=MarketData(
            symbol="PLTR",
            price=price,
            relative_volume=rvol,
            trend=trend,
            breakout_state=breakout_state,
            vwap=vwap,
            opening_range_high=opening_range_high,
            opening_range_low=opening_range_low,
            resistance=resistance,
            support=support,
            day_change_pct=day_change_pct,
            volume=volume,
            avg_volume_20d=avg_volume_20d,
            intraday_timestamp="2026-08-10T14:35:00Z",
            data_timestamp="2026-08-10T14:35:00Z",
            provider=provider,
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
            risk_reward_assessment=risk_reward_assessment,
            entry_trigger_price=101.0,
            confirmation_level=101.0,
            invalidation_price=99.0,
            target_1=104.0,
            target_2=106.0,
        ),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, provider, []),
    )


def test_v4_phase_selection_0930_opening():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    assert _v4_session_phase(now, cfg) == "OPENING"


def test_v4_phase_selection_0959_opening():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 13, 59, tzinfo=UTC)
    assert _v4_session_phase(now, cfg) == "OPENING"


def test_v4_phase_selection_1000_normal():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    assert _v4_session_phase(now, cfg) == "NORMAL"


def test_setup_score_component_calculation_present():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=80, rvol=1.72, provider="testfeed")
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    comps = meta["setup_components"]
    assert "trend" in comps
    assert "momentum" in comps
    assert "price_action" in comps
    assert "vwap" in comps
    assert "opening_range" in comps
    assert "market_alignment" in comps
    assert "risk_reward" in comps
    assert isinstance(meta["setup_score"], int)


def test_missing_vwap_marked_unavailable_not_fabricated():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=80, rvol=1.72, provider="testfeed", vwap=None)
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["setup_components"]["vwap"]["value"] is None
    assert meta["setup_components"]["vwap"]["status"] == "UNAVAILABLE"


def test_missing_opening_range_marked_unavailable_not_fabricated():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis(
        "LONG",
        "LONG_BIAS",
        setup_score=80,
        rvol=1.72,
        provider="testfeed",
        opening_range_high=None,
        opening_range_low=None,
    )
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["setup_components"]["opening_range"]["value"] is None
    assert meta["setup_components"]["opening_range"]["status"] == "UNAVAILABLE"


def test_data_limited_rvol_classification_present():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=85, rvol=0.83, provider="yfinance")
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["rvol_quality"] == "DATA_LIMITED"


def test_strong_long_setup_triggers_entry_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=85, rvol=1.72, provider="testfeed")
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"
    assert event["setup_state"] == "ENTRY_TRIGGERED"


def test_strong_short_setup_triggers_entry_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis(
        "SHORT",
        "SHORT_BIAS",
        setup_score=85,
        rvol=1.72,
        provider="testfeed",
        price=98.5,
        vwap=100.0,
        opening_range_low=99.0,
        opening_range_high=101.0,
        support=99.0,
        resistance=101.0,
        breakout_state="BREAKDOWN",
        trend="DOWNTREND",
        day_change_pct=-1.4,
    )
    a.battle_plan.entry_trigger_price = 99.0
    a.battle_plan.invalidation_price = 101.0
    a.battle_plan.target_1 = 97.0
    a.battle_plan.target_2 = 95.0
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_SHORT"
    assert event["setup_state"] == "ENTRY_TRIGGERED"


def test_weak_setup_rejected_no_trade():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis(
        "LONG",
        "LONG_BIAS",
        setup_score=40,
        rvol=1.72,
        provider="testfeed",
        trend="RANGE",
        breakout_state="NO CLEAR BREAK",
        vwap=None,
        opening_range_high=None,
        opening_range_low=None,
        day_change_pct=0.0,
    )
    assert _determine_event(a, state, cfg, now, opening_range_window=False) is None


def test_setup_developing_does_not_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis(
        "LONG",
        "LONG_BIAS",
        setup_score=80,
        rvol=1.72,
        provider="testfeed",
        trend="RANGE",
        breakout_state="NO CLEAR BREAK",
        price=100.95,
        vwap=None,
        opening_range_high=101.0,
        resistance=101.0,
        day_change_pct=1.3,
    )
    a.battle_plan.target_1 = 106.0
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_entry_triggered_alert_contains_v42_fields():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=85, rvol=1.72, provider="testfeed")
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["setup_state"] == "ENTRY_TRIGGERED"
    assert "rvol_quality" in event
    assert "vwap_status" in event
    assert "opening_range_status" in event


def test_risk_reward_rejection_blocks_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=85, rvol=1.72, provider="testfeed", risk_reward_assessment="0.60")
    a.battle_plan.entry_trigger_price = 101.0
    a.battle_plan.invalidation_price = 100.5
    a.battle_plan.target_1 = 101.2
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_candidate_logging_includes_setup_components_and_final_score(capsys):
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", setup_score=85, rvol=1.72, provider="testfeed")
    _determine_event(a, state, cfg, now, opening_range_window=False)
    captured = capsys.readouterr().out
    assert "setup components" in captured
    assert "Final" in captured
