from types import SimpleNamespace

from src.daily_stock_analyse.news.rss_provider import (
    _is_actionable_catalyst,
    _is_symbol_relevant,
    _source_quality,
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
