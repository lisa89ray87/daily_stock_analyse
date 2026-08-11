from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.config import AppConfig
from src.daily_stock_analyse.live_alerts import _determine_event, _update_symbol_state, _v4_session_phase
from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    ScoreBreakdown,
    StockAnalysis,
)


def _good_trade_levels(direction: str):
    if direction == "LONG":
        return {
            "direction": "LONG",
            "entry": 101.0,
            "stop": 100.0,
            "target1": 103.0,
            "target2": 105.0,
            "risk_reward": 2.0,
            "source": "test",
            "detail": None,
        }
    return {
        "direction": "SHORT",
        "entry": 100.0,
        "stop": 101.0,
        "target1": 98.0,
        "target2": 96.0,
        "risk_reward": 2.0,
        "source": "test",
        "detail": None,
    }


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


def _analysis(symbol: str, signal: str, bias: str, breakout_state: str = "BREAKOUT", vwap: float | None = 100.0) -> StockAnalysis:
    direction = "up" if bias == "LONG_BIAS" else "down"
    start = 100.0 if direction == "up" else 102.2
    bars = _bars(direction, start=start)
    if signal == "LONG" and breakout_state == "BREAKOUT":
        resistance = float(bars[-2]["close"]) + 0.05
        bars[-1]["close"] = resistance + 0.30
        bars[-1]["high"] = resistance + 0.35
        support = float(bars[-1]["close"]) - 0.70
    elif signal == "SHORT" and breakout_state == "BREAKDOWN":
        support = float(bars[-2]["close"]) - 0.05
        bars[-1]["close"] = support - 0.30
        bars[-1]["low"] = support - 0.35
        resistance = float(bars[-1]["close"]) + 0.70
    else:
        last_close = float(bars[-1]["close"])
        resistance = last_close + 0.60
        support = last_close - 0.60

    price = float(bars[-1]["close"])
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
            price=price,
            relative_volume=2.0,
            intraday_rvol=2.0,
            intraday_rvol_quality="RELIABLE",
            intraday_rvol_note="Time-normalized intraday RVOL",
            trend="UPTREND" if bias == "LONG_BIAS" else "DOWNTREND",
            vwap=vwap,
            opening_range_high=resistance,
            opening_range_low=support,
            resistance=resistance,
            support=support,
            breakout_state=breakout_state,
            day_change_pct=1.0 if bias == "LONG_BIAS" else -1.0,
            intraday_timestamp="2026-08-10T14:35:00Z",
            data_timestamp="2026-08-10T14:35:00Z",
            intraday_bars=bars,
        ),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan(
            bullish_scenario="b",
            bearish_scenario="s",
            key_support=f"{support:.2f}",
            key_resistance=f"{resistance:.2f}",
            entry_area="Break above 101",
            target_area="x",
            invalidation="Below 99",
            risk_reward_assessment="2.00",
            entry_trigger_price=resistance if signal == "LONG" else support,
            confirmation_level=resistance if signal == "LONG" else support,
            invalidation_price=support if signal == "LONG" else resistance,
            target_1=(price + 1.6) if signal == "LONG" else (price - 1.6),
            target_2=(price + 3.2) if signal == "LONG" else (price - 3.2),
        ),
        score=ScoreBreakdown(total=0.1, long_score=0.8, short_score=0.2, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_wait_to_long_transition_alert_generated():
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 40, tzinfo=UTC)
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure", return_value=type("TL", (), _good_trade_levels("LONG"))()):
        event = _determine_event(_analysis("PLTR", "LONG", "LONG_BIAS"), state, _cfg(), now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"


def test_wait_to_short_transition_alert_generated():
    a = _analysis("SNDK", "SHORT", "SHORT_BIAS", breakout_state="BREAKDOWN")
    a.battle_plan.entry_trigger_price = 100.0
    a.battle_plan.confirmation_level = 100.0
    a.battle_plan.invalidation_price = 103.0
    a.battle_plan.target_1 = 98.0
    a.battle_plan.target_2 = 96.0
    a.market_data.price = 99.0

    state = {"symbols": {"SNDK": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 40, tzinfo=UTC)
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure", return_value=type("TL", (), _good_trade_levels("SHORT"))()):
        event = _determine_event(a, state, _cfg(), now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_SHORT"


def test_long_invalidation_alert_generated():
    a = _analysis("PLTR", "WAIT", "LONG_BIAS")
    a.market_data.price = 98.0
    state = {"symbols": {"PLTR": {"last_signal": "LONG"}}}
    now = datetime(2026, 8, 10, 14, 45, tzinfo=UTC)
    event = _determine_event(a, state, _cfg(), now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "LONG_INVALIDATED"


def test_short_invalidation_alert_generated():
    a = _analysis("SNDK", "WAIT", "SHORT_BIAS", breakout_state="BREAKDOWN")
    a.battle_plan.invalidation_price = 103.0
    a.battle_plan.entry_trigger_price = 100.0
    a.battle_plan.confirmation_level = 100.0
    a.market_data.price = 104.0
    state = {"symbols": {"SNDK": {"last_signal": "SHORT"}}}
    now = datetime(2026, 8, 10, 14, 45, tzinfo=UTC)
    event = _determine_event(a, state, _cfg(), now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "SHORT_INVALIDATED"


def test_target_alert_deduplication():
    a = _analysis("PLTR", "LONG", "LONG_BIAS")
    a.battle_plan.invalidation_price = 100.0
    a.market_data.price = (a.battle_plan.target_1 or 104.0) + 0.10
    now = datetime(2026, 8, 10, 14, 50, tzinfo=UTC)
    state = {"symbols": {"PLTR": {"last_signal": "LONG", "alerted_targets": ["LONG_TARGET_1"]}}}
    event = _determine_event(a, state, _cfg(), now, opening_range_window=False)
    assert event is None


def test_no_alert_when_live_data_unavailable():
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="NO CLEAR BREAK", vwap=None)
    a.market_data.opening_range_high = None
    a.market_data.opening_range_low = None
    a.market_data.trend = "RANGE"
    a.market_data.day_change_pct = 0.0
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 40, tzinfo=UTC)
    event = _determine_event(a, state, _cfg(), now, opening_range_window=False)
    assert event is None


def test_opening_range_developing_suppresses_breakout_alert():
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="NO CLEAR BREAK")
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 32, tzinfo=UTC)
    event = _determine_event(a, state, _cfg(), now, opening_range_window=True)
    assert event is None


def test_cooldown_prevents_duplicate_same_alert_type():
    state = {
        "symbols": {
            "PLTR": {
                "last_signal": "WAIT",
                "last_alert_type": "WAIT_TO_LONG",
                "last_alert_timestamp": datetime(2026, 8, 10, 14, 35, tzinfo=UTC).isoformat(),
            }
        }
    }
    now = datetime(2026, 8, 10, 14, 40, tzinfo=UTC)
    event = _determine_event(_analysis("PLTR", "LONG", "LONG_BIAS"), state, _cfg(), now, opening_range_window=False)
    assert event is None


def test_state_update_tracks_last_alert_fields():
    state = {"symbols": {}}
    now = datetime(2026, 8, 10, 14, 40, tzinfo=UTC)
    event = {
        "event_type": "LONG_TARGET_1",
        "subject": "x",
    }
    analysis = _analysis("PLTR", "LONG", "LONG_BIAS")
    _update_symbol_state(state, analysis, event, now)
    symbol_state = state["symbols"]["PLTR"]
    assert symbol_state["last_signal"] == "LONG"
    assert symbol_state["last_alert_type"] == "LONG_TARGET_1"
    assert "LONG_TARGET_1" in symbol_state["alerted_targets"]


def test_v4_opening_phase_at_0930():
    cfg = _cfg()
    phase = _v4_session_phase(datetime(2026, 8, 10, 13, 30, tzinfo=UTC), cfg)
    assert phase == "OPENING"


def test_v4_opening_phase_at_0959():
    cfg = _cfg()
    phase = _v4_session_phase(datetime(2026, 8, 10, 13, 59, tzinfo=UTC), cfg)
    assert phase == "OPENING"


def test_v4_normal_phase_at_1000():
    cfg = _cfg()
    phase = _v4_session_phase(datetime(2026, 8, 10, 14, 0, tzinfo=UTC), cfg)
    assert phase == "NORMAL"


def test_opening_long_rvol_120_setup_75_qualifies_with_confirmation():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="BREAKOUT")
    a.setup_score = 75
    a.market_data.relative_volume = 1.20
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 13, 35, tzinfo=UTC)
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure", return_value=type("TL", (), _good_trade_levels("LONG"))()):
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["event_type"] == "WAIT_TO_LONG"
    assert event["phase"] == "OPENING"


def test_opening_setup_rvol_119_does_not_qualify():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="BREAKOUT")
    a.setup_score = 80
    a.market_data.relative_volume = 1.19
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 13, 35, tzinfo=UTC)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_opening_setup_score_74_does_not_qualify():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="BREAKOUT")
    a.setup_score = 74
    a.market_data.relative_volume = 1.30
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 13, 35, tzinfo=UTC)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_normal_setup_rvol_149_does_not_qualify():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="BREAKOUT")
    a.setup_score = 80
    a.market_data.relative_volume = 1.49
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_normal_setup_rvol_150_setup_70_qualifies_with_confirmation():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="BREAKOUT")
    a.setup_score = 70
    a.market_data.relative_volume = 1.50
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
    with patch("src.daily_stock_analyse.live_alerts._trade_levels_from_intraday_structure", return_value=type("TL", (), _good_trade_levels("LONG"))()):
        event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is not None
    assert event["phase"] == "NORMAL"


def test_rvol_alone_never_generates_alert():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="NO CLEAR BREAK")
    a.setup_score = 82
    a.market_data.relative_volume = 1.60
    a.market_data.trend = "RANGE"
    a.market_data.vwap = None
    a.market_data.opening_range_high = None
    a.market_data.day_change_pct = 0.0
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None


def test_missing_vwap_and_opening_range_does_not_fabricate_values():
    cfg = _cfg()
    a = _analysis("PLTR", "LONG", "LONG_BIAS", breakout_state="NO CLEAR BREAK", vwap=None)
    a.market_data.opening_range_high = None
    a.market_data.opening_range_low = None
    a.market_data.trend = "RANGE"
    a.market_data.day_change_pct = 0.0
    state = {"symbols": {"PLTR": {"last_signal": "WAIT"}}}
    now = datetime(2026, 8, 10, 13, 40, tzinfo=UTC)
    event = _determine_event(a, state, cfg, now, opening_range_window=False)
    assert event is None
