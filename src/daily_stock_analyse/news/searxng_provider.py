from __future__ import annotations

import time
from datetime import UTC, datetime
from threading import Lock
from urllib.parse import urlparse

import requests

from ..models import CatalystEvent, IntelligenceBlock
from ..providers.base import NewsProvider
from ..providers.yfinance_provider import _classify_catalyst


class SearXNGNewsProvider(NewsProvider):
    """Free SearXNG-based news provider with graceful failover across instances."""

    PUBLIC_INSTANCES_URL = "https://searx.space/data/instances.json"
    PUBLIC_INSTANCES_TIMEOUT_SECONDS = 5
    PUBLIC_INSTANCES_POOL_LIMIT = 20
    PUBLIC_INSTANCES_MAX_ATTEMPTS = 3
    PUBLIC_INSTANCES_CACHE_TTL_SECONDS = 3600

    _public_instances_cache: tuple[float, list[str]] | None = None
    _public_instances_lock = Lock()

    def __init__(
        self,
        *,
        base_urls: list[str] | None = None,
        timeout_seconds: int = 8,
        use_public_instances: bool = True,
    ):
        normalized = [x.strip().rstrip("/") for x in (base_urls or []) if isinstance(x, str) and x.strip()]
        self._base_urls = normalized
        self._timeout_seconds = max(2, int(timeout_seconds))
        self._use_public_instances = bool(use_public_instances and not self._base_urls)
        self._cursor = 0
        self._cursor_lock = Lock()
        self._diagnostic_lock = Lock()
        self._diagnostic = {
            "selected_instance": "NONE",
            "http_status": "NONE",
            "raw_result_count": 0,
            "parsed_result_count": 0,
        }

    def _reset_diagnostic(self) -> None:
        with self._diagnostic_lock:
            self._diagnostic = {
                "selected_instance": "NONE",
                "http_status": "NONE",
                "raw_result_count": 0,
                "parsed_result_count": 0,
            }

    def diagnostic_snapshot(self) -> dict[str, object]:
        with self._diagnostic_lock:
            return dict(self._diagnostic)

    def _record_request_diagnostic(
        self,
        *,
        instance: str,
        http_status: int | str,
        raw_result_count: int = 0,
        parsed_result_count: int = 0,
    ) -> None:
        with self._diagnostic_lock:
            self._diagnostic["selected_instance"] = instance
            self._diagnostic["http_status"] = http_status
            self._diagnostic["raw_result_count"] = int(self._diagnostic["raw_result_count"]) + raw_result_count
            self._diagnostic["parsed_result_count"] = int(self._diagnostic["parsed_result_count"]) + parsed_result_count

    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        self._reset_diagnostic()
        out = IntelligenceBlock()
        out.catalyst_status = "UNAVAILABLE"

        try:
            raw_items = self._search(symbol, limit=max(limit * 2, 8))
        except Exception:
            raw_items = []

        if not raw_items:
            out.news_available = False
            out.facts = ["NO_RECENT_NEWS"]
            out.interpretation = ["SearXNG returned no recent usable headlines"]
            out.catalyst_status = "NO_RECENT_NEWS"
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            return out

        events = [self._to_event(symbol, item) for item in raw_items]
        events = [x for x in events if x is not None]

        if not events:
            out.news_available = False
            out.facts = ["NO_RECENT_NEWS"]
            out.interpretation = ["SearXNG returned no recent usable headlines"]
            out.catalyst_status = "NO_RECENT_NEWS"
            out.upcoming_catalysts = ["NO_RECENT_NEWS"]
            return out

        ranked = sorted(events, key=lambda x: self._event_rank(symbol, x), reverse=True)
        selected = ranked[: max(1, limit)]

        out.news_available = True
        out.structured_catalysts = selected
        out.facts = [f"{x.source}: {x.headline}" for x in selected]

        material = [x for x in selected if x.category != "NONE"]
        if material:
            out.catalyst_status = "CATALYST_IDENTIFIED"
            out.upcoming_catalysts = [
                f"{x.category} | {x.catalyst_direction} | {x.source} | {x.headline}"
                for x in material[:3]
            ]
        else:
            out.catalyst_status = "NO_MATERIAL_CATALYST"
            out.upcoming_catalysts = ["NO_MATERIAL_CATALYST"]

        out.interpretation = ["News reflects available public SearXNG search results"]
        return out

    def _search(self, symbol: str, limit: int) -> list[dict]:
        candidates = self._candidate_instances()
        if not candidates:
            return []

        queries = self._build_queries(symbol)
        seen_urls: set[str] = set()
        results: list[dict] = []

        for base in candidates:
            for query in queries:
                payload = self._search_once(base, query, limit)
                for item in payload:
                    url = str(item.get("url") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    results.append(item)
                    if len(results) >= limit:
                        return results

        return results

    def _candidate_instances(self) -> list[str]:
        if self._base_urls:
            return self._rotate(self._base_urls, max_attempts=len(self._base_urls))

        if not self._use_public_instances:
            return []

        public_urls = self._get_public_instances()
        return self._rotate(public_urls, max_attempts=min(len(public_urls), self.PUBLIC_INSTANCES_MAX_ATTEMPTS))

    def _search_once(self, base_url: str, query: str, limit: int) -> list[dict]:
        url = base_url if base_url.endswith("/search") else f"{base_url}/search"
        params = {
            "q": query,
            "format": "json",
            "language": "en",
            "categories": "news",
            "pageno": 1,
        }

        try:
            response = requests.get(url, params=params, timeout=self._timeout_seconds)
        except requests.RequestException as exc:
            self._record_request_diagnostic(instance=base_url, http_status=f"REQUEST_ERROR:{exc.__class__.__name__}")
            return []

        if response.status_code != 200:
            self._record_request_diagnostic(instance=base_url, http_status=response.status_code)
            return []

        try:
            data = response.json()
        except ValueError:
            self._record_request_diagnostic(instance=base_url, http_status=response.status_code)
            return []

        if not isinstance(data, dict):
            self._record_request_diagnostic(instance=base_url, http_status=response.status_code)
            return []

        raw = data.get("results", [])
        if not isinstance(raw, list):
            self._record_request_diagnostic(instance=base_url, http_status=response.status_code)
            return []

        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            link = str(item.get("url") or "").strip()
            if not title or not link:
                continue
            out.append(item)
            if len(out) >= limit:
                break

        self._record_request_diagnostic(
            instance=base_url,
            http_status=response.status_code,
            raw_result_count=len(raw),
            parsed_result_count=len(out),
        )
        return out

    def _to_event(self, symbol: str, item: dict) -> CatalystEvent | None:
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        if not title or not link:
            return None

        source = str(item.get("engine") or item.get("source") or self._domain(link) or "Unknown").strip() or "Unknown"
        published_iso = self._published_iso(item)
        epoch = self._published_epoch(published_iso)

        event = _classify_catalyst(
            symbol,
            {
                "title": title,
                "publisher": source,
                "providerPublishTime": epoch,
                "link": link,
            },
        )

        # Preserve richer timestamp parsing from SearXNG response when available.
        if published_iso is not None:
            event.published_at = published_iso
        if event.url is None:
            event.url = link
        return event

    def _build_queries(self, symbol: str) -> list[str]:
        s = symbol.strip().upper()
        return [
            f"{s} stock earnings guidance analyst news",
            f"{s} semiconductor industry news",
            f"{s} macro market fed cpi rates news",
        ]

    def _event_rank(self, symbol: str, event: CatalystEvent) -> tuple[int, int, float]:
        scope = self._relevance_scope(symbol, event.headline)
        importance_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}.get(event.importance, 0)
        ts_rank = self._published_epoch(event.published_at)
        return scope, importance_rank, ts_rank

    @staticmethod
    def _relevance_scope(symbol: str, headline: str) -> int:
        text = headline.lower()
        sym = symbol.lower()
        if sym in text:
            return 3  # DIRECT_COMPANY

        sector_keywords = [
            "semiconductor",
            "chip",
            "cloud",
            "ai demand",
            "industry",
            "foundry",
            "memory market",
        ]
        macro_keywords = [
            "federal reserve",
            "fed",
            "cpi",
            "inflation",
            "jobs report",
            "treasury",
            "rates",
            "geopolitical",
        ]

        if any(k in text for k in sector_keywords):
            return 2  # SECTOR
        if any(k in text for k in macro_keywords):
            return 1  # MACRO_MARKET
        return 0

    @staticmethod
    def _domain(url: str) -> str | None:
        try:
            return urlparse(url).netloc.replace("www.", "")
        except Exception:
            return None

    @staticmethod
    def _published_epoch(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _published_iso(item: dict) -> str | None:
        for key in ("publishedDate", "published_date", "published", "date"):
            raw = item.get(key)
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(float(raw), tz=UTC).isoformat()
            if isinstance(raw, str):
                value = raw.strip()
                if not value:
                    continue
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
                except ValueError:
                    continue
        return None

    def _rotate(self, pool: list[str], *, max_attempts: int) -> list[str]:
        if not pool or max_attempts <= 0:
            return []
        with self._cursor_lock:
            start = self._cursor % len(pool)
            self._cursor = (self._cursor + 1) % len(pool)
        ordered = pool[start:] + pool[:start]
        return ordered[:max_attempts]

    @classmethod
    def _extract_public_instances(cls, payload: dict) -> list[str]:
        if not isinstance(payload, dict):
            return []
        instances = payload.get("instances")
        if not isinstance(instances, dict):
            return []

        ranked: list[tuple[float, float, str]] = []
        for raw_url, item in instances.items():
            if not isinstance(raw_url, str) or not isinstance(item, dict):
                continue
            if item.get("network_type") != "normal":
                continue
            http_status = (item.get("http") or {}).get("status_code")
            if http_status != 200:
                continue
            timing = (item.get("timing") or {}).get("search") or {}
            uptime = timing.get("success_percentage")
            if not isinstance(uptime, (int, float)) or float(uptime) <= 0:
                continue
            latency = float("inf")
            all_timing = timing.get("all")
            if isinstance(all_timing, dict):
                for key in ("mean", "median"):
                    v = all_timing.get(key)
                    if isinstance(v, (int, float)):
                        latency = float(v)
                        break
            ranked.append((float(uptime), latency, raw_url.rstrip("/")))

        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [url for _, _, url in ranked[: cls.PUBLIC_INSTANCES_POOL_LIMIT]]

    @classmethod
    def _get_public_instances(cls) -> list[str]:
        now = time.time()
        with cls._public_instances_lock:
            if cls._public_instances_cache is not None:
                cached_at, cached_urls = cls._public_instances_cache
                if now - cached_at < cls.PUBLIC_INSTANCES_CACHE_TTL_SECONDS:
                    return list(cached_urls)

            try:
                response = requests.get(cls.PUBLIC_INSTANCES_URL, timeout=cls.PUBLIC_INSTANCES_TIMEOUT_SECONDS)
                if response.status_code != 200:
                    return list(cls._public_instances_cache[1]) if cls._public_instances_cache else []
                data = response.json()
                urls = cls._extract_public_instances(data)
                if urls:
                    cls._public_instances_cache = (now, urls)
                    return list(urls)
            except Exception:
                pass

            return list(cls._public_instances_cache[1]) if cls._public_instances_cache else []
