from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from ..models import CatalystEvent, IntelligenceBlock
from ..providers.base import NewsProvider
from ..providers.yfinance_provider import _classify_catalyst


_LOW_QUALITY_SOURCE_PATTERNS = (
    "mshale",
    "mexc",
    "stockinvest",
    "marketbeat",
    "gurufocus",
    "chartmill",
    "tradingkey",
    "ts2.tech",
)

_ACTIONABLE_CATEGORIES = {
    "EARNINGS", "GUIDANCE", "ANALYST", "PRODUCT", "PARTNERSHIP",
    "REGULATORY", "LEGAL", "ACQUISITION", "FINANCING", "INSIDER",
}


class RSSNewsProvider(NewsProvider):
    def __init__(self, provider_name: str, url_template: str):
        self.provider_name = provider_name
        self.url_template = url_template
        self._last_diagnostic: dict[str, object] = {}

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        out = IntelligenceBlock()
        query = f'"{symbol}" stock'
        url = self.url_template.format(query=quote_plus(query))
        self._last_diagnostic = {
            "request_url": url,
            "http_status": "UNAVAILABLE",
            "raw_result_count": 0,
            "candidate_result_count": 0,
            "relevant_result_count": 0,
            "quality_filtered_count": 0,
            "parsed_result_count": 0,
        }
        try:
            response = requests.get(
                url,
                timeout=float(os.getenv("NEWS_RSS_TIMEOUT_SECONDS", "10")),
                headers={"User-Agent": "daily-stock-analyse/1.0"},
            )
            self._last_diagnostic["http_status"] = response.status_code
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except Exception as exc:
            self._last_diagnostic["error"] = f"{exc.__class__.__name__}: {exc}"
            out.news_available = False
            out.facts.append("NO_RECENT_NEWS")
            out.interpretation.append(f"{self.provider_name} unavailable")
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            out.catalyst_status = "NO_RECENT_NEWS"
            return out

        items = root.findall(".//item")
        self._last_diagnostic["raw_result_count"] = len(items)
        candidates: list[dict] = []
        for item in items:
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            source = _text(item.find("source")) or self.provider_name
            pub_date = _text(item.find("pubDate"))
            if not title:
                continue
            candidates.append({
                "title": title,
                "publisher": source,
                "providerPublishTime": _parse_rss_timestamp(pub_date),
                "link": link,
            })

        self._last_diagnostic["candidate_result_count"] = len(candidates)
        relevant = [item for item in candidates if _is_symbol_relevant(symbol, item["title"])]
        self._last_diagnostic["relevant_result_count"] = len(relevant)

        allow_low_quality = _env_flag("NEWS_RSS_ALLOW_LOW_QUALITY", False)
        quality_filtered = [
            item for item in relevant
            if _source_quality(item["publisher"]) >= _min_source_quality()
        ]
        self._last_diagnostic["quality_filtered_count"] = len(relevant) - len(quality_filtered)
        pool = relevant if allow_low_quality else quality_filtered

        # Rank actionable catalyst types first, then source quality, then recency.
        ranked: list[tuple[int, int, float, dict]] = []
        for item in pool:
            event = _classify_catalyst(symbol, item)
            category_score = 2 if event.category in _ACTIONABLE_CATEGORIES else 0
            quality = _source_quality(item["publisher"])
            timestamp = item.get("providerPublishTime") or 0
            ranked.append((category_score, quality, float(timestamp), item))
        ranked.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
        candidates = [item for _, _, _, item in ranked[: max(1, limit)]]
        self._last_diagnostic["parsed_result_count"] = len(candidates)

        for item in candidates:
            event = _classify_catalyst(symbol, item)
            event.source = item["publisher"]
            event.url = item.get("link") or None
            out.facts.append(f"{event.source}: {event.headline}")
            out.structured_catalysts.append(event)

        if out.structured_catalysts:
            out.news_available = True
            material = [x for x in out.structured_catalysts if _is_actionable_catalyst(x)]
            if material:
                out.catalyst_status = "CATALYST_IDENTIFIED"
                out.upcoming_catalysts = [
                    f"{x.category} | {x.catalyst_direction} | {x.source} | {x.headline}"
                    for x in material[:3]
                ]
            else:
                out.catalyst_status = "NO_ACTIONABLE_CATALYST"
                out.upcoming_catalysts = ["NO_ACTIONABLE_CATALYST"]
        else:
            out.news_available = False
            out.facts.append("NO_RECENT_NEWS")
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            out.catalyst_status = "NO_RECENT_NEWS"
        return out

    def diagnostic_snapshot(self) -> dict[str, object]:
        return dict(self._last_diagnostic)


class GoogleNewsRSSProvider(RSSNewsProvider):
    def __init__(self):
        super().__init__(
            "google_news_rss",
            os.getenv(
                "GOOGLE_NEWS_RSS_URL",
                "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
            ),
        )


class BingNewsRSSProvider(RSSNewsProvider):
    def __init__(self):
        super().__init__(
            "bing_news_rss",
            os.getenv(
                "BING_NEWS_RSS_URL",
                "https://www.bing.com/news/search?q={query}&format=rss",
            ),
        )


def _text(element) -> str:
    return (element.text or "").strip() if element is not None else ""


def _parse_rss_timestamp(value: str) -> float | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _min_source_quality() -> int:
    try:
        return max(0, min(100, int(os.getenv("NEWS_RSS_MIN_SOURCE_QUALITY", "40"))))
    except ValueError:
        return 40


def _source_quality(source: str) -> int:
    normalized = (source or "").strip().lower()
    if not normalized:
        return 50
    if any(pattern in normalized for pattern in _LOW_QUALITY_SOURCE_PATTERNS):
        return 25
    if any(name in normalized for name in (
        "reuters", "associated press", "bloomberg", "cnbc", "wall street journal", "financial times"
    )):
        return 95
    if any(name in normalized for name in (
        "yahoo finance", "marketwatch", "investing.com", "benzinga", "seeking alpha", "motley fool"
    )):
        return 80
    if normalized in {"google_news_rss", "bing_news_rss"}:
        return 50
    return 60


def _is_symbol_relevant(symbol: str, title: str) -> bool:
    text = " ".join((title or "").upper().split())
    token = symbol.upper()
    # Require a ticker-like token, not a loose substring. This blocks unrelated
    # results such as a Fastly article appearing in a DDOG search.
    return bool(re.search(rf"(?<![A-Z0-9])\$?{re.escape(token)}(?![A-Z0-9])", text))


def _is_actionable_catalyst(event: CatalystEvent) -> bool:
    return (
        event.category in _ACTIONABLE_CATEGORIES
        and event.importance in {"HIGH", "MEDIUM"}
        and event.confidence in {"HIGH", "MEDIUM"}
    )
