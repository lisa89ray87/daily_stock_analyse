from __future__ import annotations

from ..models import CatalystEvent, IntelligenceBlock
from ..providers.base import NewsProvider
from ..providers.yfinance_provider import YFinanceNewsProvider
from .diagnostics import wrap
from .rss_provider import BingNewsRSSProvider, GoogleNewsRSSProvider


class AggregatedNewsProvider(NewsProvider):
    def __init__(self, providers: list[tuple[str, NewsProvider]]):
        self.providers = providers
        self._last_diagnostic: dict[str, object] = {}

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        collected: list[CatalystEvent] = []
        provider_statuses: list[str] = []
        for label, provider in self.providers:
            try:
                data = provider.get_news(symbol, limit=limit)
                collected.extend(data.structured_catalysts)
                provider_statuses.append(f"{label}:{data.catalyst_status}")
            except Exception as exc:
                provider_statuses.append(f"{label}:ERROR:{exc.__class__.__name__}")

        deduped: list[CatalystEvent] = []
        seen: set[str] = set()
        for event in sorted(collected, key=lambda x: x.published_at or "", reverse=True):
            key = (event.url or "").strip().lower() or f"{event.source}|{event.headline}".strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)

        events = deduped[: max(1, limit)]
        material = [event for event in events if event.category != "NONE"]
        out = IntelligenceBlock(
            facts=[f"{event.source}: {event.headline}" for event in events],
            interpretation=["News aggregated from configured providers"],
            upcoming_catalysts=[
                f"{event.category} | {event.catalyst_direction} | {event.source} | {event.headline}"
                for event in material[:3]
            ],
            news_available=bool(events),
            structured_catalysts=events,
            catalyst_status="CATALYST_IDENTIFIED" if material else ("NO_MATERIAL_CATALYST" if events else "NO_RECENT_NEWS"),
        )
        if not out.upcoming_catalysts:
            out.upcoming_catalysts = [out.catalyst_status]

        self._last_diagnostic = {
            "collected_count": len(collected),
            "deduped_count": len(deduped),
            "provider_statuses": ",".join(provider_statuses),
        }
        return out

    def diagnostic_snapshot(self) -> dict[str, object]:
        return dict(self._last_diagnostic)


def create_news_provider(provider_name: str, **_kwargs) -> NewsProvider:
    """Create one or more fail-open news providers.

    Supported values are comma-separated: yfinance, google_rss, bing_rss.
    Each source is independent; one unavailable source does not block the others.
    """
    names = [part.strip().lower() for part in provider_name.split(",") if part.strip()]
    if not names:
        names = ["yfinance"]

    factories: dict[str, tuple[str, type[NewsProvider]]] = {
        "yfinance": ("yfinance", YFinanceNewsProvider),
        "google_rss": ("google_news_rss", GoogleNewsRSSProvider),
        "google_news_rss": ("google_news_rss", GoogleNewsRSSProvider),
        "bing_rss": ("bing_news_rss", BingNewsRSSProvider),
        "bing_news_rss": ("bing_news_rss", BingNewsRSSProvider),
    }

    providers: list[tuple[str, NewsProvider]] = []
    for name in names:
        if name not in factories:
            raise ValueError(
                f"Unsupported news provider '{name}'. Supported providers: yfinance, google_rss, bing_rss"
            )
        label, provider_cls = factories[name]
        providers.append((label, wrap(provider_cls(), label)))

    if len(providers) == 1:
        return providers[0][1]
    return wrap(AggregatedNewsProvider(providers), "aggregated")
