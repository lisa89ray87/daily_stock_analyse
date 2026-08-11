from src.daily_stock_analyse.providers.yfinance_provider import YFinanceNewsProvider, _classify_catalyst


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


def test_news_provider_filters_placeholder_titles_and_preserves_real_metadata(monkeypatch):
    class _FakeTicker:
        news = [
            {"title": "Untitled", "publisher": "BadFeed", "providerPublishTime": 1700000000},
            {"title": "  ", "publisher": "BadFeed", "providerPublishTime": 1700000001},
            {
                "title": "AMD beats earnings and raises guidance",
                "publisher": "Reuters",
                "providerPublishTime": 1700000002,
                "link": "https://example.com/amd-earnings",
            },
        ]

    monkeypatch.setattr("src.daily_stock_analyse.providers.yfinance_provider.yf.Ticker", lambda symbol: _FakeTicker())

    intelligence = YFinanceNewsProvider().get_news("AMD")

    assert intelligence.catalyst_status == "CATALYST_IDENTIFIED"
    assert intelligence.facts == ["Reuters: AMD beats earnings and raises guidance"]
    assert len(intelligence.structured_catalysts) == 1
    assert intelligence.structured_catalysts[0].headline == "AMD beats earnings and raises guidance"
    assert intelligence.structured_catalysts[0].source == "Reuters"
    assert intelligence.structured_catalysts[0].url == "https://example.com/amd-earnings"
    assert all("Untitled" not in item.headline for item in intelligence.structured_catalysts)


def test_news_provider_returns_news_unavailable_when_no_usable_headlines(monkeypatch):
    class _FakeTicker:
        news = [{"title": "Untitled", "publisher": "BadFeed", "providerPublishTime": 1700000000}]

    monkeypatch.setattr("src.daily_stock_analyse.providers.yfinance_provider.yf.Ticker", lambda symbol: _FakeTicker())

    intelligence = YFinanceNewsProvider().get_news("AMD")

    assert intelligence.news_available is False
    assert intelligence.catalyst_status == "NO_RECENT_NEWS"
    assert intelligence.upcoming_catalysts == ["NO_RECENT_NEWS"]
