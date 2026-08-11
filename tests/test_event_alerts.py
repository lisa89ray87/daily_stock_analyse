from types import SimpleNamespace

from src.daily_stock_analyse.event_alerts import detect_event_alerts
from src.daily_stock_analyse.models import MarketData


def _analysis(bars, *, vwap=None):
    return SimpleNamespace(
        symbol="TEST",
        market_data=MarketData(symbol="TEST", intraday_bars=bars, vwap=vwap),
    )


def _bars(closes, volumes):
    return [
        {"ts": f"2026-08-11T{9 + i // 60:02d}:{30 + i % 60:02d}:00+00:00", "open": c, "high": c, "low": c, "close": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def test_price_change_event():
    analysis = _analysis(_bars([100.0, 102.5], [100, 110]))
    cfg = SimpleNamespace(event_alert_price_change_pct=2.0, event_alert_volume_spike_multiplier=10.0)
    events = detect_event_alerts(analysis, cfg)
    assert any(event.event_type == "PRICE_CHANGE" for event in events)


def test_volume_spike_event():
    analysis = _analysis(_bars([100.0, 100.1, 100.2, 100.3, 100.4], [100, 100, 100, 100, 250]))
    cfg = SimpleNamespace(event_alert_price_change_pct=10.0, event_alert_volume_spike_multiplier=2.0)
    events = detect_event_alerts(analysis, cfg)
    assert any(event.event_type == "VOLUME_SPIKE" for event in events)


def test_vwap_cross_event():
    analysis = _analysis(_bars([99.0, 101.0], [100, 100]), vwap=100.0)
    cfg = SimpleNamespace(event_alert_price_change_pct=10.0, event_alert_volume_spike_multiplier=10.0)
    events = detect_event_alerts(analysis, cfg)
    assert any(event.event_type == "PRICE_CROSS" and event.key == "VWAP_BULLISH_CROSS" for event in events)


def test_no_event_for_small_move():
    analysis = _analysis(_bars([100.0, 100.2], [100, 110]))
    cfg = SimpleNamespace(event_alert_price_change_pct=2.0, event_alert_volume_spike_multiplier=10.0)
    events = detect_event_alerts(analysis, cfg)
    assert not any(event.event_type == "PRICE_CHANGE" for event in events)
