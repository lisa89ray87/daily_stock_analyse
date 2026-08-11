from __future__ import annotations

import os
from datetime import UTC, datetime

from ..models import IntelligenceBlock
from ..providers.base import NewsProvider


def _enabled() -> bool:
    raw = os.getenv("NEWS_DIAGNOSTICS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _valid_timestamp(value: str | None) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _summary(data: IntelligenceBlock) -> str:
    events = data.structured_catalysts
    timestamped = sum(1 for event in events if event.published_at)
    valid_timestamps = sum(1 for event in events if _valid_timestamp(event.published_at))
    categories: dict[str, int] = {}
    for event in events:
        categories[event.category] = categories.get(event.category, 0) + 1
    category_text = ",".join(f"{key}:{value}" for key, value in sorted(categories.items())) or "NONE"
    return (
        f"news_available={data.news_available} events={len(events)} "
        f"timestamped={timestamped} valid_timestamps={valid_timestamps} "
        f"categories={category_text} catalyst_status={data.catalyst_status}"
    )


def _provider_diagnostics(provider: NewsProvider) -> str:
    snapshot_fn = getattr(provider, "diagnostic_snapshot", None)
    if not callable(snapshot_fn):
        return ""
    try:
        snapshot = snapshot_fn()
    except Exception:
        return ""
    if not isinstance(snapshot, dict):
        return ""
    fields = (
        "selected_instance",
        "http_status",
        "raw_result_count",
        "parsed_result_count",
        "collected_count",
        "deduped_count",
        "freshness_input_count",
        "freshness_filtered_count",
    )
    parts = [f"{key}={snapshot[key]}" for key in fields if key in snapshot]
    return " | " + " ".join(parts) if parts else ""


class DiagnosticNewsProvider(NewsProvider):
    """Transparent wrapper that traces provider output without changing it."""

    def __init__(self, provider: NewsProvider, label: str):
        self._provider = provider
        self._label = label

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        try:
            data = self._provider.get_news(symbol, limit=limit)
        except Exception as exc:
            if _enabled():
                print(
                    f"NEWS_DIAGNOSTIC | stage=provider | provider={self._label} | "
                    f"symbol={symbol} | exception={exc.__class__.__name__}: {exc}"
                )
            raise

        if _enabled():
            print(
                f"NEWS_DIAGNOSTIC | stage=provider | provider={self._label} | "
                f"symbol={symbol} | {_summary(data)}{_provider_diagnostics(self._provider)}"
            )
            for event in data.structured_catalysts[:5]:
                print(
                    f"NEWS_DIAGNOSTIC | stage=provider_event | provider={self._label} | "
                    f"symbol={symbol} | category={event.category} | importance={event.importance} | "
                    f"direction={event.catalyst_direction} | published_at={event.published_at or 'NONE'} | "
                    f"source={event.source or 'UNKNOWN'} | headline={event.headline}"
                )
        return data


def wrap(provider: NewsProvider, label: str) -> DiagnosticNewsProvider:
    return DiagnosticNewsProvider(provider, label)
