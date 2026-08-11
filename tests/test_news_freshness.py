from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.daily_stock_analyse.models import CatalystEvent, IntelligenceBlock
from src.daily_stock_analyse.news.factory import AggregatedNewsProvider


class _FakeNewsProvider:
    def __init__(self, events):
        self.events = events

    def get_news(self, symbol: str, limit: int = 5):
        return IntelligenceBlock(
            news_available=bool(self.events),
            structured_catalysts=self.events,
            catalyst_status="CATALYST_IDENTIFIED" if self.events else "NO_RECENT_NEWS",
        )


def _event(published_at: str, headline: str = "Fresh test headline"):
    return CatalystEvent(
        symbol="TEST",
        headline=headline,
        source="test",
        published_at=published_at,
        category="OTHER",
        importance="LOW",
        catalyst_direction="NEUTRAL",
    )


def test_aggregated_provider_filters_stale_news():
    now = datetime.now(UTC)
    provider = AggregatedNewsProvider(
        [("test", _FakeNewsProvider([
            _event((now - timedelta(hours=2)).isoformat(), "fresh"),
            _event((now - timedelta(hours=30)).isoformat(), "stale"),
        ]))],
        max_age_hours=24,
    )

    result = provider.get_news("TEST", limit=5)

    assert [event.headline for event in result.structured_catalysts] == ["fresh"]
    diagnostic = provider.diagnostic_snapshot()
    assert diagnostic["collected_count"] == 2
    assert diagnostic["deduped_count"] == 2
    assert diagnostic["freshness_input_count"] == 2
    assert diagnostic["freshness_filtered_count"] == 1


def test_aggregated_provider_rejects_undated_news():
    provider = AggregatedNewsProvider(
        [("test", _FakeNewsProvider([_event("not-a-timestamp")]))],
        max_age_hours=24,
    )

    result = provider.get_news("TEST", limit=5)

    assert result.news_available is False
    assert result.catalyst_status == "NO_RECENT_NEWS"
