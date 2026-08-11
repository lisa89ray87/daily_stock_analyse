from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..models import CatalystEvent, IntelligenceBlock
from ..providers.base import NewsProvider
from ..providers.yfinance_provider import YFinanceNewsProvider
from .diagnostics import wrap
from .rss_provider import BingNewsRSSProvider, GoogleNewsRSSProvider


class AggregatedNewsProvider(NewsProvider):
    def __init__(self, providers: list[tuple[str, NewsProvider]], *, max_age_hours: int = 24):
        self.providers = providers
        self.max_age_hours = max(1, int(max_age_hours))
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

        freshness_input = len(deduped)
        cutoff = datetime.now(UTC) - timedelta(hours=self.max_age_hours)
        fresh: list[CatalystEvent] = []
        for event in deduped:
            published = _parse_timestamp(event.published_at)
            if published is None:
                # Do not treat undated articles as fresh catalysts.
                continue
            if published >= cutoff:
                fresh.append(event)

        events = fresh[: max(1, limit)]
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
            "freshness_input_count": freshness_input,
            "freshness_filtered_count": len(fresh),
            "max_age_hours": self.max_age_hours,
            "provider_statuses": ",".join(provider_statuses),
        }
        return out

    def diagnostic_snapshot(self) -> dict[str, object]:
        return dict(self._last_diagnostic)


def create_news_provider(provider_name: str, **kwargs) -> NewsProvider:
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

    max_age_hours = kwargs.get("max_age_hours", 24)
    return wrap(AggregatedNewsProvider(providers, max_age_hours=max_age_hours), "aggregated")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None
