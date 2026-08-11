from __future__ import annotations

from datetime import UTC, datetime

from ..models import CatalystEvent, IntelligenceBlock
from ..providers.base import NewsProvider
from ..providers.yfinance_provider import YFinanceNewsProvider
from .searxng_provider import SearXNGNewsProvider


class AggregatedNewsProvider(NewsProvider):
    """Aggregate multiple free news providers into one normalized IntelligenceBlock."""

    def __init__(self, providers: list[NewsProvider], *, max_age_hours: int = 24):
        self._providers = providers
        self._max_age_hours = max(1, int(max_age_hours))

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        collected: list[CatalystEvent] = []
        provider_results: list[IntelligenceBlock] = []

        for provider in self._providers:
            try:
                data = provider.get_news(symbol, limit=max(limit * 2, 8))
            except Exception:
                continue
            provider_results.append(data)
            collected.extend(data.structured_catalysts)

        if not collected:
            if any(x.catalyst_status == "NO_MATERIAL_CATALYST" for x in provider_results):
                return IntelligenceBlock(
                    facts=["NO_MATERIAL_CATALYST"],
                    interpretation=["News exists but no material catalyst was identified"],
                    upcoming_catalysts=["NO_MATERIAL_CATALYST"],
                    news_available=True,
                    structured_catalysts=[],
                    catalyst_status="NO_MATERIAL_CATALYST",
                )
            return IntelligenceBlock(
                facts=["NO_RECENT_NEWS"],
                interpretation=["No recent usable provider headlines were available"],
                upcoming_catalysts=["NO_RECENT_NEWS"],
                news_available=False,
                structured_catalysts=[],
                catalyst_status="NO_RECENT_NEWS",
            )

        deduped = self._deduplicate(collected)
        filtered = self._apply_freshness(deduped)

        if not filtered:
            return IntelligenceBlock(
                facts=["NO_RECENT_NEWS"],
                interpretation=["No recent usable provider headlines were available"],
                upcoming_catalysts=["NO_RECENT_NEWS"],
                news_available=False,
                structured_catalysts=[],
                catalyst_status="NO_RECENT_NEWS",
            )

        ranked = sorted(filtered, key=lambda x: self._rank(symbol, x), reverse=True)
        selected = ranked[: max(1, limit)]

        facts = [f"{x.source}: {x.headline}" for x in selected]
        material = [x for x in selected if x.category != "NONE"]

        if material:
            return IntelligenceBlock(
                facts=facts,
                interpretation=["Merged free-provider news and prioritized direct-company catalysts"],
                upcoming_catalysts=[
                    f"{x.category} | {x.catalyst_direction} | {x.source} | {x.headline}" for x in material[:3]
                ],
                news_available=True,
                structured_catalysts=selected,
                catalyst_status="CATALYST_IDENTIFIED",
            )

        return IntelligenceBlock(
            facts=facts,
            interpretation=["Recent headlines found but no material catalyst classified"],
            upcoming_catalysts=["NO_MATERIAL_CATALYST"],
            news_available=True,
            structured_catalysts=selected,
            catalyst_status="NO_MATERIAL_CATALYST",
        )

    def _deduplicate(self, events: list[CatalystEvent]) -> list[CatalystEvent]:
        by_key: dict[str, CatalystEvent] = {}
        for event in events:
            key = self._event_key(event)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = event
                continue
            by_key[key] = self._prefer(existing, event)
        return list(by_key.values())

    def _apply_freshness(self, events: list[CatalystEvent]) -> list[CatalystEvent]:
        cutoff = datetime.now(UTC).timestamp() - (self._max_age_hours * 3600)
        fresh: list[CatalystEvent] = []
        unknown_time: list[CatalystEvent] = []
        for event in events:
            if not event.published_at:
                unknown_time.append(event)
                continue
            try:
                ts = datetime.fromisoformat(event.published_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                unknown_time.append(event)
                continue
            if ts >= cutoff:
                fresh.append(event)

        if fresh:
            return fresh
        return unknown_time

    @staticmethod
    def _event_key(event: CatalystEvent) -> str:
        headline_key = " ".join(event.headline.lower().split())
        url_key = (event.url or "").strip().lower()
        source_key = (event.source or "").strip().lower()
        if url_key:
            return f"url::{url_key}"
        return f"hl::{headline_key}::{source_key}"

    @staticmethod
    def _prefer(a: CatalystEvent, b: CatalystEvent) -> CatalystEvent:
        def score(x: CatalystEvent) -> tuple[int, int, int]:
            has_url = 1 if x.url else 0
            has_time = 1 if x.published_at else 0
            importance = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}.get(x.importance, 0)
            return has_url, has_time, importance

        return b if score(b) > score(a) else a

    def _rank(self, symbol: str, event: CatalystEvent) -> tuple[int, int, float]:
        scope = self._scope(symbol, event)
        importance = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}.get(event.importance, 0)
        published = 0.0
        if event.published_at:
            try:
                published = datetime.fromisoformat(event.published_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                published = 0.0
        return scope, importance, published

    @staticmethod
    def _scope(symbol: str, event: CatalystEvent) -> int:
        text = event.headline.lower()
        sym = symbol.lower()
        if sym in text:
            return 3  # DIRECT_COMPANY
        if event.category in {"SEMICONDUCTOR", "SECTOR"}:
            return 2  # SECTOR
        if event.category == "MACRO":
            return 1  # MACRO_MARKET
        if any(k in text for k in ["semiconductor", "industry", "chip", "foundry", "cloud", "ai demand"]):
            return 2
        if any(k in text for k in ["fed", "cpi", "inflation", "rates", "treasury", "jobs report", "geopolitical"]):
            return 1
        return 0


def create_news_provider(
    provider_name: str,
    *,
    searxng_base_urls: list[str] | None = None,
    searxng_timeout_seconds: int = 8,
    searxng_public_instances_enabled: bool = True,
    news_max_age_hours: int = 24,
) -> NewsProvider:
    raw = provider_name.strip().lower()
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    if not parts:
        parts = ["yfinance"]

    providers: list[NewsProvider] = []
    for name in parts:
        if name == "yfinance":
            providers.append(YFinanceNewsProvider())
        elif name == "searxng":
            providers.append(
                SearXNGNewsProvider(
                    base_urls=searxng_base_urls,
                    timeout_seconds=searxng_timeout_seconds,
                    use_public_instances=searxng_public_instances_enabled,
                )
            )
        else:
            raise ValueError(f"Unsupported news provider '{name}'. Supported providers: yfinance, searxng")

    if len(providers) == 1:
        return providers[0]

    return AggregatedNewsProvider(providers, max_age_hours=news_max_age_hours)
