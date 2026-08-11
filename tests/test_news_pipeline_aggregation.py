from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import requests

from src.daily_stock_analyse.models import CatalystEvent, IntelligenceBlock, MarketData
from src.daily_stock_analyse.news.factory import AggregatedNewsProvider, create_news_provider
from src.daily_stock_analyse.news.searxng_provider import SearXNGNewsProvider
from src.daily_stock_analyse.scoring import score_stock


def _mock_response(*, status_code: int = 200, payload=None, json_error: Exception | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_error is not None:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = payload if payload is not None else {}
    return resp


def _patch_yfinance_news(monkeypatch, news_items: list[dict]):
    class _FakeTicker:
        news = news_items

    monkeypatch.setattr("src.daily_stock_analyse.providers.yfinance_provider.yf.Ticker", lambda symbol: _FakeTicker())


def test_yfinance_only_behavior_still_works(monkeypatch):
    now_epoch = int(datetime.now(UTC).timestamp())
    _patch_yfinance_news(
        monkeypatch,
        [
            {
                "title": "AMD beats earnings and raises guidance",
                "publisher": "Reuters",
                "providerPublishTime": now_epoch,
                "link": "https://example.com/amd-yf",
            }
        ],
    )

    provider = create_news_provider("yfinance")
    intel = provider.get_news("AMD")

    assert intel.catalyst_status == "CATALYST_IDENTIFIED"
    assert len(intel.structured_catalysts) == 1


def test_searxng_success_returns_news(monkeypatch):
    payload = {
        "results": [
            {
                "title": "AMD signs major data center contract",
                "url": "https://example.com/amd-searx",
                "publishedDate": datetime.now(UTC).isoformat(),
                "engine": "SearXNews",
            }
        ]
    }
    monkeypatch.setattr("src.daily_stock_analyse.news.searxng_provider.requests.get", lambda *a, **k: _mock_response(payload=payload))

    provider = SearXNGNewsProvider(base_urls=["https://searx.example.org"], use_public_instances=False)
    intel = provider.get_news("AMD")

    assert intel.news_available is True
    assert len(intel.structured_catalysts) == 1


def test_searxng_timeout_falls_back_to_yfinance(monkeypatch):
    now_epoch = int(datetime.now(UTC).timestamp())
    _patch_yfinance_news(
        monkeypatch,
        [
            {
                "title": "AMD beats earnings and raises guidance",
                "publisher": "Reuters",
                "providerPublishTime": now_epoch,
                "link": "https://example.com/shared",
            }
        ],
    )

    def _raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr("src.daily_stock_analyse.news.searxng_provider.requests.get", _raise_timeout)

    provider = create_news_provider(
        "yfinance,searxng",
        searxng_base_urls=["https://searx.example.org"],
        searxng_public_instances_enabled=False,
    )
    intel = provider.get_news("AMD")

    assert intel.news_available is True
    assert any("AMD beats earnings" in x.headline for x in intel.structured_catalysts)


def test_searxng_http_failure_falls_back_to_yfinance(monkeypatch):
    now_epoch = int(datetime.now(UTC).timestamp())
    _patch_yfinance_news(
        monkeypatch,
        [
            {
                "title": "AMD announces product roadmap",
                "publisher": "Reuters",
                "providerPublishTime": now_epoch,
                "link": "https://example.com/yf-product",
            }
        ],
    )
    monkeypatch.setattr(
        "src.daily_stock_analyse.news.searxng_provider.requests.get",
        lambda *a, **k: _mock_response(status_code=500, payload={}),
    )

    provider = create_news_provider("yfinance,searxng", searxng_base_urls=["https://searx.example.org"], searxng_public_instances_enabled=False)
    intel = provider.get_news("AMD")

    assert intel.news_available is True
    assert len(intel.structured_catalysts) >= 1


def test_searxng_malformed_response_does_not_crash(monkeypatch):
    now_epoch = int(datetime.now(UTC).timestamp())
    _patch_yfinance_news(
        monkeypatch,
        [
            {
                "title": "AMD partnership announced",
                "publisher": "Reuters",
                "providerPublishTime": now_epoch,
                "link": "https://example.com/yf-partnership",
            }
        ],
    )
    monkeypatch.setattr(
        "src.daily_stock_analyse.news.searxng_provider.requests.get",
        lambda *a, **k: _mock_response(json_error=ValueError("bad json")),
    )

    provider = create_news_provider("yfinance,searxng", searxng_base_urls=["https://searx.example.org"], searxng_public_instances_enabled=False)
    intel = provider.get_news("AMD")

    assert intel.news_available is True
    assert intel.catalyst_status in {"CATALYST_IDENTIFIED", "NO_MATERIAL_CATALYST"}


def test_empty_searxng_results_fall_back(monkeypatch):
    now_epoch = int(datetime.now(UTC).timestamp())
    _patch_yfinance_news(
        monkeypatch,
        [
            {
                "title": "AMD earnings call highlights",
                "publisher": "Reuters",
                "providerPublishTime": now_epoch,
                "link": "https://example.com/yf-earnings",
            }
        ],
    )
    monkeypatch.setattr(
        "src.daily_stock_analyse.news.searxng_provider.requests.get",
        lambda *a, **k: _mock_response(payload={"results": []}),
    )

    provider = create_news_provider("yfinance,searxng", searxng_base_urls=["https://searx.example.org"], searxng_public_instances_enabled=False)
    intel = provider.get_news("AMD")

    assert any("earnings" in x.headline.lower() for x in intel.structured_catalysts)


def test_duplicate_articles_are_removed(monkeypatch):
    now_epoch = int(datetime.now(UTC).timestamp())
    _patch_yfinance_news(
        monkeypatch,
        [
            {
                "title": "AMD beats earnings and raises guidance",
                "publisher": "Reuters",
                "providerPublishTime": now_epoch,
                "link": "https://example.com/shared",
            }
        ],
    )
    monkeypatch.setattr(
        "src.daily_stock_analyse.news.searxng_provider.requests.get",
        lambda *a, **k: _mock_response(
            payload={
                "results": [
                    {
                        "title": "AMD beats earnings and raises guidance",
                        "url": "https://example.com/shared",
                        "publishedDate": datetime.now(UTC).isoformat(),
                        "engine": "SearXNews",
                    }
                ]
            }
        ),
    )

    provider = create_news_provider("yfinance,searxng", searxng_base_urls=["https://searx.example.org"], searxng_public_instances_enabled=False)
    intel = provider.get_news("AMD")

    assert len(intel.structured_catalysts) == 1


def test_freshness_filters_stale_news(monkeypatch):
    stale = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    fresh = datetime.now(UTC).isoformat()

    monkeypatch.setattr(
        "src.daily_stock_analyse.news.searxng_provider.requests.get",
        lambda *a, **k: _mock_response(
            payload={
                "results": [
                    {"title": "AMD old headline", "url": "https://example.com/old", "publishedDate": stale, "engine": "SearXNews"},
                    {"title": "AMD fresh headline", "url": "https://example.com/fresh", "publishedDate": fresh, "engine": "SearXNews"},
                ]
            }
        ),
    )

    provider = AggregatedNewsProvider(
        [SearXNGNewsProvider(base_urls=["https://searx.example.org"], use_public_instances=False)],
        max_age_hours=24,
    )
    intel = provider.get_news("AMD")

    assert any("fresh" in x.headline for x in intel.structured_catalysts)
    assert all("old" not in x.headline for x in intel.structured_catalysts)


def test_relevance_prioritization_direct_sector_macro(monkeypatch):
    now_iso = datetime.now(UTC).isoformat()
    monkeypatch.setattr(
        "src.daily_stock_analyse.news.searxng_provider.requests.get",
        lambda *a, **k: _mock_response(
            payload={
                "results": [
                    {"title": "Fed signals rates path for next quarter", "url": "https://example.com/macro", "publishedDate": now_iso, "engine": "MacroWire"},
                    {"title": "Semiconductor industry demand accelerates", "url": "https://example.com/sector", "publishedDate": now_iso, "engine": "SectorWire"},
                    {"title": "AMD beats earnings and raises guidance", "url": "https://example.com/direct", "publishedDate": now_iso, "engine": "Reuters"},
                ]
            }
        ),
    )

    provider = create_news_provider("searxng", searxng_base_urls=["https://searx.example.org"], searxng_public_instances_enabled=False)
    intel = provider.get_news("AMD", limit=3)

    headlines = [x.headline for x in intel.structured_catalysts]
    assert headlines[0].startswith("AMD beats earnings")
    assert any("Semiconductor" in x for x in headlines[1:])


def test_news_with_no_material_catalyst_is_not_no_recent_news():
    event = CatalystEvent(
        symbol="AMD",
        headline="General market chatter with no concrete trigger",
        source="Example",
        published_at=datetime.now(UTC).isoformat(),
        category="NONE",
        importance="LOW",
        catalyst_direction="UNKNOWN",
        summary="x",
        confidence="LOW",
        url="https://example.com/chatter",
    )

    class _OnlyNoneProvider:
        def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
            return IntelligenceBlock(
                facts=["Example: General market chatter"],
                interpretation=["x"],
                upcoming_catalysts=["NO_MATERIAL_CATALYST"],
                news_available=True,
                structured_catalysts=[event],
                catalyst_status="NO_MATERIAL_CATALYST",
            )

    agg = AggregatedNewsProvider([_OnlyNoneProvider()], max_age_hours=24)
    intel = agg.get_news("AMD")

    assert intel.catalyst_status == "NO_MATERIAL_CATALYST"
    assert intel.upcoming_catalysts == ["NO_MATERIAL_CATALYST"]


def test_no_news_is_reported_as_no_recent_news():
    class _NoNewsProvider:
        def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
            return IntelligenceBlock(
                facts=["NO_RECENT_NEWS"],
                interpretation=["x"],
                upcoming_catalysts=["NO_RECENT_NEWS"],
                news_available=False,
                structured_catalysts=[],
                catalyst_status="NO_RECENT_NEWS",
            )

    agg = AggregatedNewsProvider([_NoNewsProvider()], max_age_hours=24)
    intel = agg.get_news("AMD")

    assert intel.catalyst_status == "NO_RECENT_NEWS"
    assert intel.upcoming_catalysts == ["NO_RECENT_NEWS"]


def test_dedup_prevents_news_score_inflation():
    now_iso = datetime.now(UTC).isoformat()
    event = CatalystEvent(
        symbol="AMD",
        headline="AMD beats earnings and raises guidance",
        source="Reuters",
        published_at=now_iso,
        category="EARNINGS",
        importance="HIGH",
        catalyst_direction="BULLISH",
        summary="x",
        confidence="MEDIUM",
        url="https://example.com/shared",
    )

    class _ProviderA:
        def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
            return IntelligenceBlock(
                facts=["Reuters: AMD beats earnings and raises guidance"],
                interpretation=["x"],
                upcoming_catalysts=["EARNINGS"],
                news_available=True,
                structured_catalysts=[event],
                catalyst_status="CATALYST_IDENTIFIED",
            )

    class _ProviderB:
        def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
            return IntelligenceBlock(
                facts=["Reuters: AMD beats earnings and raises guidance"],
                interpretation=["x"],
                upcoming_catalysts=["EARNINGS"],
                news_available=True,
                structured_catalysts=[event],
                catalyst_status="CATALYST_IDENTIFIED",
            )

    agg = AggregatedNewsProvider([_ProviderA(), _ProviderB()], max_age_hours=24)
    merged = agg.get_news("AMD")

    weights = {
        "trend": 0.2,
        "momentum": 0.15,
        "volume": 0.1,
        "relative_strength": 0.1,
        "fundamentals_news": 0.2,
        "catalyst_event": 0.1,
        "risk_reward": 0.15,
    }
    md = MarketData(symbol="AMD", price=100.0, relative_volume=1.2)
    score = score_stock(md, merged, weights)

    assert len(merged.facts) == 1
    assert score.components["fundamentals_news"] <= 1.0
