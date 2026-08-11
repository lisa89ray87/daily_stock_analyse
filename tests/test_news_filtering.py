from types import SimpleNamespace

from src.daily_stock_analyse.news.factory import AggregatedNewsProvider
from src.daily_stock_analyse.news.rss_provider import (
    _is_actionable_catalyst,
    _is_symbol_relevant,
    _source_quality,
)
from src.daily_stock_analyse.models import CatalystEvent, IntelligenceBlock
from src.daily_stock_analyse.providers.base import NewsProvider


class _FakeNewsProvider(NewsProvider):
    def __init__(self, events):
        self.events = events

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        return IntelligenceBlock(
            news_available=True,
            structured_catalysts=self.events,
            catalyst_status="CATALYST_IDENTIFIED",
        )


def test_rss_relevance_rejects_unrelated_ticker():
    assert _is_symbol_relevant("DDOG", "Fastly Stock Is Rising Today") is False
    assert _is_symbol_relevant("DDOG", "Datadog $DDOG stock update") is True


def test_low_quality_source_is_below_default_threshold():
    assert _source_quality("Mshale") < 40
    assert _source_quality("Reuters") >= 90


def test_other_low_confidence_event_is_not_actionable():
    event = SimpleNamespace(
        category="OTHER",
        importance="LOW",
        confidence="LOW",
    )
    assert _is_actionable_catalyst(event) is False


def test_earnings_medium_confidence_event_is_actionable():
    event = SimpleNamespace(
        category="EARNINGS",
        importance="HIGH",
        confidence="MEDIUM",
    )
    assert _is_actionable_catalyst(event) is True


def test_aggregated_provider_does_not_report_other_as_catalyst():
    event = CatalystEvent(
        symbol="DDOG",
        headline="DDOG stock update",
        source="test",
        published_at="2026-08-12T00:00:00+00:00",
        category="OTHER",
        importance="LOW",
        catalyst_direction="NEUTRAL",
        confidence="LOW",
    )
    provider = AggregatedNewsProvider([("test", _FakeNewsProvider([event]))], max_age_hours=24)
    result = provider.get_news("DDOG")
    assert result.catalyst_status == "NO_ACTIONABLE_CATALYST"
    assert provider.diagnostic_snapshot()["actionable_count"] == 0
