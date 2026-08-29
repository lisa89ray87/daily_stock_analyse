from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..models import CatalystEvent, IntelligenceBlock
from ..providers.base import NewsProvider
from ..providers.yfinance_provider import YFinanceNewsProvider
from .diagnostics import wrap
from .rss_provider import BingNewsRSSProvider, GoogleNewsRSSProvider, _is_actionable_catalyst


class AggregatedNewsProvider(NewsProvider):
    """Fail-open news provider with ordered fallback sources.

    Providers are tried in order. The first provider that returns at least one
    fresh, usable headline wins. Later providers are only queried when the
    earlier source is unavailable, empty, or has no fresh usable headlines.
    This keeps live scans fast and avoids unnecessary calls to multiple public
    endpoints on every symbol.
    """

    def __init__(self, providers: list[tuple[str, NewsProvider]], *, max_age_hours: int = 24):
        self.providers = providers
        self.max_age_hours = max(1, int(max_age_hours))
        self._last_diagnostic: dict[str, object] = {}

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        provider_statuses: list[str] = []
        selected_label: str | None = None
        selected_events: list[CatalystEvent] = []
        total_attempts = 0

        for label, provider in self.providers:
            total_attempts += 1
            try:
                data = provider.get_news(symbol, limit=limit)
                fresh = self._fresh_events(data.structured_catalysts)
                if fresh:
                    selected_label = label
                    selected_events = self._dedupe(fresh)[: max(1, limit)]
                    provider_statuses.append(f"{label}:USED:{len(selected_events)}")
                    break
                provider_statuses.append(f"{label}:EMPTY_OR_STALE")
            except Exception as exc:
                provider_statuses.append(f"{label}:ERROR:{exc.__class__.__name__}")

        events = selected_events
        actionable = [event for event in events if _is_actionable_catalyst(event)]
        out = IntelligenceBlock(
            facts=[f"{event.source}: {event.headline}" for event in events],
            interpretation=[
                f"News supplied by fallback provider: {selected_label}"
                if selected_label
                else "No configured news provider returned fresh usable headlines"
            ],
            upcoming_catalysts=[
                f"{event.category} | {event.catalyst_direction} | {event.source} | {event.headline}"
                for event in actionable[:3]
            ],
            news_available=bool(events),
            structured_catalysts=events,
            catalyst_status=(
                "CATALYST_IDENTIFIED"
                if actionable
                else ("NO_ACTIONABLE_CATALYST" if events else "NO_RECENT_NEWS")
            ),
        )
        if not out.upcoming_catalysts:
            out.upcoming_catalysts = [out.catalyst_status]

        self._last_diagnostic = {
            "provider_attempts": total_attempts,
            "selected_provider": selected_label or "NONE",
            "selected_count": len(events),
            "actionable_count": len(actionable),
            "max_age_hours": self.max_age_hours,
            "provider_statuses": ",".join(provider_statuses),
        }
        return out

    def _fresh_events(self, events: list[CatalystEvent]) -> list[CatalystEvent]:
        cutoff = datetime.now(UTC) - timedelta(hours=self.max_age_hours)
        fresh: list[CatalystEvent] = []
        for event in events:
            published = _parse_timestamp(event.published_at)
            if published is not None and published >= cutoff:
                fresh.append(event)
        return sorted(fresh, key=lambda x: _published_sort_value(x.published_at), reverse=True)

    @staticmethod
    def _dedupe(events: list[CatalystEvent]) -> list[CatalystEvent]:
        deduped: list[CatalystEvent] = []
        seen: set[str] = set()
        for event in events:
            key = (event.url or "").strip().lower() or f"{event.source}|{event.headline}".strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped

    def diagnostic_snapshot(self) -> dict[str, object]:
        return dict(self._last_diagnostic)


def create_news_provider(provider_name: str, **kwargs) -> NewsProvider:
    """Create one or more fail-open news providers.

    Supported values are comma-separated and are evaluated in order:
    yfinance, google_rss, bing_rss. The first source with fresh usable
    headlines is used; later sources are queried only when necessary.
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

    max_age_hours = kwargs.get("news_max_age_hours", kwargs.get("max_age_hours", 24))
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


def _published_sort_value(value: str | None) -> float:
    parsed = _parse_timestamp(value)
    return parsed.timestamp() if parsed else 0.0
