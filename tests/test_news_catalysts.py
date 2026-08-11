from src.daily_stock_analyse.providers.yfinance_provider import _classify_catalyst


def test_classify_catalyst_detects_earnings_bullish():
    event = _classify_catalyst(
        "AMD",
        {
            "title": "AMD beats earnings and raises guidance",
            "publisher": "Example",
            "providerPublishTime": 1700000000,
        },
    )
    assert event.category == "EARNINGS"
    assert event.catalyst_direction == "BULLISH"
    assert event.importance == "HIGH"


def test_classify_catalyst_detects_regulatory_bearish():
    event = _classify_catalyst(
        "AMD",
        {
            "title": "Company faces regulator investigation after weak quarter",
            "publisher": "Example",
            "providerPublishTime": 1700000000,
        },
    )
    assert event.category == "REGULATORY"
    assert event.catalyst_direction == "BEARISH"
