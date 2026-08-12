from src.daily_stock_analyse.event_alert_runner import _event_explanation, _message, _message_batches
from src.daily_stock_analyse.event_alerts import EventAlert


def _event(event_type: str, detail: str, *, symbol: str = "TEST", direction: str = "NEUTRAL", price: float = 101.25) -> EventAlert:
    return EventAlert(
        symbol=symbol,
        event_type=event_type,
        direction=direction,
        price=price,
        detail=detail,
        severity="MEDIUM",
        key=f"{event_type}:{direction}",
    )


def test_event_explanation_branches():
    cases = [
        (_event("PRICE_CHANGE", "REGULAR price change +2.50% exceeded 2.00%"), "stronger-than-usual upside momentum"),
        (_event("PRICE_CHANGE", "REGULAR price change -2.50% exceeded 2.00%"), "stronger-than-usual downside momentum"),
        (_event("PRICE_CROSS", "REGULAR price crossed above OPENING_RANGE_HIGH 100.00"), "above the early-session high"),
        (_event("PRICE_CROSS", "REGULAR price crossed below OPENING_RANGE_LOW 95.00"), "below the early-session low"),
        (_event("PRICE_CROSS", "REGULAR price crossed above VWAP 99.50"), "above VWAP"),
        (_event("PRICE_CROSS", "REGULAR price crossed below VWAP 99.50"), "below VWAP"),
        (_event("PRICE_CROSS", "REGULAR price crossed above RESISTANCE 100.00"), "important intraday reference level"),
        (_event("MA_CROSS", "REGULAR price crossed above SMA20 (100.20)"), "momentum is strengthening"),
        (_event("MA_CROSS", "REGULAR price crossed below SMA20 (100.20)"), "momentum is weakening"),
        (_event("MA_CROSS", "REGULAR price crossed SMA20 (100.20)"), "crossed a moving average"),
        (_event("RSI_THRESHOLD", "REGULAR RSI crossed below 30 (28.9)"), "oversold territory"),
        (_event("RSI_THRESHOLD", "REGULAR RSI crossed above 70 (72.1)"), "overbought territory"),
        (_event("RSI_THRESHOLD", "REGULAR RSI crossed threshold (55.0)"), "crossed a threshold"),
        (_event("MACD_CROSS", "REGULAR MACD bullish cross (1.0000 > 0.9000)"), "above its signal line"),
        (_event("MACD_CROSS", "REGULAR MACD bearish cross (0.8000 < 0.9000)"), "below its signal line"),
        (_event("VOLUME_SPIKE", "REGULAR volume 2.10x recent session average"), "unusually high"),
        (_event("SOMETHING_NEW", "Unexpected event detail"), "notable market condition was detected"),
    ]

    for event, expected in cases:
        assert expected in _event_explanation(event)


def test_message_escapes_html_and_includes_one_explanation_per_event():
    message = _message(
        [
            _event(
                "PRICE_CROSS",
                "REGULAR price crossed above OPENING_RANGE_HIGH <100 & rising>",
                symbol="ABC<1>&",
                direction="BULLISH",
            )
        ],
        "2026-08-13T13:35:00-04:00",
        "US_REGULAR",
    )

    assert "ABC&lt;1&gt;&amp;" in message
    assert "OPENING_RANGE_HIGH &lt;100 &amp; rising&gt;" in message
    assert message.count("What this means:") == 1
    assert "Price moved above the early-session high" in message


def test_message_handles_multiple_events_in_one_telegram_payload():
    message = _message(
        [
            _event("PRICE_CROSS", "REGULAR price crossed above VWAP 100.00", direction="BULLISH"),
            _event("VOLUME_SPIKE", "REGULAR volume 2.50x recent session average", direction="NEUTRAL"),
        ],
        "2026-08-13T13:35:00-04:00",
        "US_REGULAR",
    )

    assert "LIVE EVENT WARNING" in message
    assert message.count("What this means:") == 2
    assert "above VWAP" in message
    assert "unusually high compared with normal activity" in message


def test_message_batches_stay_within_safe_limit_with_actual_batch_labels():
    events = [
        _event(
            "PRICE_CROSS",
            f"REGULAR price crossed above OPENING_RANGE_HIGH 100.00 note {'X' * 220} #{index}",
            symbol=f"SYM{index}",
            direction="BULLISH",
        )
        for index in range(1, 21)
    ]

    batches = _message_batches(events, "2026-08-13T13:35:00-04:00", "US_REGULAR")

    assert len(batches) > 1
    rendered = [
        _message(batch, "2026-08-13T13:35:00-04:00", "US_REGULAR", index, len(batches))
        for index, batch in enumerate(batches, start=1)
    ]

    assert sum(len(batch) for batch in batches) == len(events)
    assert all(len(message) <= 3800 for message in rendered)