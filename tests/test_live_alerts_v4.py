from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import _determine_event, _is_live_confirmable, _v4_session_phase
from src.daily_stock_analyse.models import BattlePlan, DataQuality, IntelligenceBlock, MarketData, ScoreBreakdown, StockAnalysis


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
        v4_opening_range_minutes=30,
    )


def _bars(direction: str = "up", n: int = 18, start: float = 100.0) -> list[dict[str, float | str]]:
    base = datetime(2026, 8, 10, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    out: list[dict[str, float | str]] = []
    price = start
    for i in range(n):
        step = 0.35 if direction == "up" else -0.35
        open_p = price
        close_p = price + step
        high_p = max(open_p, close_p) + 0.15
        low_p = min(open_p, close_p) - 0.10
        out.append(
            {
                "ts": (base + timedelta(minutes=5 * i)).isoformat(),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": 100_000 + (i * 4000),
            }
        )
        price = close_p
    return out


def _analysis(
    signal: str,
    bias: str,
    *,
    bars: list[dict[str, float | str]] | None = None,
    intraday_rvol: float | None = 1.6,
    intraday_rvol_quality: str = "RELIABLE",
    trend: str = "UPTREND",
    breakout_state: str = "BREAKOUT",
    vwap: float | None = 100.0,
    opening_range_high: float | None = 101.0,
    opening_range_low: float | None = 99.0,
    support: float | None = 99.0,
    resistance: float | None = 101.0,
    day_change_pct: float | None = 1.5,
    alignment: str = "MARKET_ALIGNED",
    rr_text: str = "2.0",
) -> StockAnalysis:
    return StockAnalysis(
        symbol="PLTR",
        name="PLTR",
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias=bias,
        market_alignment=alignment,
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
            price=102.0,
            relative_volume=1.2,
            intraday_rvol=intraday_rvol,
            intraday_rvol_quality=intraday_rvol_quality,
            intraday_rvol_note="intraday",
            trend=trend,
            breakout_state=breakout_state,
            vwap=vwap,
            opening_range_high=opening_range_high,
            opening_range_low=opening_range_low,
            resistance=resistance,
            support=support,
            day_change_pct=day_change_pct,
            volume=2_000_000,
            avg_volume_20d=1_000_000,
            intraday_timestamp="2026-08-10T14:35:00Z",
            data_timestamp="2026-08-10T14:35:00Z",
            provider="yfinance",
            intraday_bars=bars or _bars("up"),
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
            risk_reward_assessment=rr_text,
            entry_trigger_price=101.0,
            confirmation_level=101.0,
            invalidation_price=99.0,
            target_1=104.5,
            target_2=106.0,
        ),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_time_normalized_rvol_reliable():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", intraday_rvol=1.42, intraday_rvol_quality="RELIABLE")
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["rvol_quality"] == "RELIABLE"
    assert meta["rvol"] == 1.42


def test_rvol_data_limited():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", intraday_rvol=None, intraday_rvol_quality="DATA_LIMITED")
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["rvol_quality"] == "DATA_LIMITED"


def test_rvol_unavailable():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", intraday_rvol=None, intraday_rvol_quality="UNAVAILABLE")
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["rvol_quality"] == "UNAVAILABLE"


def test_intraday_trend_component_nonzero_with_evidence():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", bars=_bars("up"), intraday_rvol=1.8)
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["setup_components"]["trend"]["value"] > 0


def test_intraday_momentum_component_nonzero_with_evidence():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", bars=_bars("up"), intraday_rvol=1.8)
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["setup_components"]["momentum"]["value"] > 0


def test_vwap_component_available_when_intraday_present():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", bars=_bars("up"))
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["setup_components"]["vwap"]["status"] == "AVAILABLE"


def test_opening_range_component_available_when_intraday_present():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", bars=_bars("up"))
    _, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert meta["setup_components"]["opening_range"]["status"] == "AVAILABLE"


def test_long_breakout_trigger_entry():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    bars = _bars("up")
    resistance = float(bars[-2]["close"]) + 0.05
    bars[-1]["close"] = resistance + 0.20
    bars[-1]["high"] = resistance + 0.30
    a = _analysis("LONG", "LONG_BIAS", bars=bars, resistance=resistance, intraday_rvol=1.8)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"


def test_short_breakdown_trigger_entry():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    bars = _bars("down", start=105.0)
    support = float(bars[-2]["close"]) - 0.05
    bars[-1]["close"] = support - 0.20
    bars[-1]["low"] = support - 0.30
    a = _analysis(
        "SHORT",
        "SHORT_BIAS",
        bars=bars,
        resistance=106.0,
        support=support,
        trend="DOWNTREND",
        breakout_state="BREAKDOWN",
        intraday_rvol=1.8,
        day_change_pct=-1.6,
    )
    a.battle_plan.entry_trigger_price = support
    a.battle_plan.invalidation_price = support + 1.0
    a.battle_plan.target_1 = support - 4.0
    a.battle_plan.target_2 = 99.5
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_SHORT"


def test_setup_developing_state_no_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    bars = _bars("up")
    bars[-1]["close"] = 100.95
    bars[-1]["high"] = 100.98
    a = _analysis("LONG", "LONG_BIAS", bars=bars, resistance=101.0, intraday_rvol=1.8, breakout_state="NEAR BREAKOUT")
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_entry_triggered_state_alerts():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    bars = _bars("up")
    resistance = float(bars[-2]["close"]) + 0.05
    bars[-1]["close"] = resistance + 0.20
    bars[-1]["high"] = resistance + 0.30
    a = _analysis("LONG", "LONG_BIAS", intraday_rvol=1.8, bars=bars, resistance=resistance)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["setup_state"] == "ENTRY_TRIGGERED"


def test_no_trade_state_for_weak_setup():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    weak_bars = _bars("up", n=4)
    a = _analysis(
        "WAIT",
        "NEUTRAL",
        bars=weak_bars,
        intraday_rvol=None,
        intraday_rvol_quality="UNAVAILABLE",
        trend="RANGE",
        breakout_state="NO CLEAR BREAK",
        vwap=None,
        opening_range_high=None,
        opening_range_low=None,
        support=None,
        resistance=None,
        alignment="MARKET_COUNTERTREND",
        day_change_pct=0.0,
    )
    ok, _, meta = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert ok is False
    assert meta.get("setup_state") == "NO_TRADE"


def test_risk_reward_rejection():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    bars = _bars("up")
    resistance = float(bars[-2]["close"]) + 0.05
    bars[-1]["close"] = resistance + 0.20
    bars[-1]["high"] = resistance + 0.30
    a = _analysis("LONG", "LONG_BIAS", rr_text="1.20", intraday_rvol=1.8, bars=bars, resistance=resistance)
    a.battle_plan.entry_trigger_price = resistance
    a.battle_plan.invalidation_price = resistance - 1.0
    a.battle_plan.target_1 = resistance + 1.0
    ok, reason, _ = _is_live_confirmable(a, cfg, opening_range_window=False, now_utc=now)
    assert ok is False
    assert "Risk/reward below minimum" in reason


def test_duplicate_entry_prevention_when_in_position():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT", "position_state": "IN_POSITION", "active_direction": "LONG"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", intraday_rvol=1.8)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_existing_cooldown_behavior_remains_intact():
    cfg = _cfg()
    state = {
        "symbols": {
            "PLTR": {
                "last_signal": "WAIT",
                "last_alert_type": "WAIT_TO_LONG",
                "last_alert_timestamp": datetime(2026, 8, 10, 14, 0, tzinfo=UTC).isoformat(),
            }
        }
    }
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis("LONG", "LONG_BIAS", intraday_rvol=1.8)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_missing_intraday_data_does_not_crash_and_no_alert():
    cfg = _cfg()
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    a = _analysis(
        "LONG",
        "LONG_BIAS",
        bars=[],
        intraday_rvol=None,
        intraday_rvol_quality="UNAVAILABLE",
        vwap=None,
        opening_range_high=None,
        opening_range_low=None,
        support=None,
        resistance=None,
        trend="RANGE",
        breakout_state="NO CLEAR BREAK",
    )
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_v4_phase_selection_0930_opening():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    assert _v4_session_phase(now, cfg) == "OPENING"


def test_v4_phase_selection_1000_normal():
    cfg = _cfg()
    now = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    assert _v4_session_phase(now, cfg) == "NORMAL"
