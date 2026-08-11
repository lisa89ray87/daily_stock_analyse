from __future__ import annotations

from ..news.factory import create_news_provider as create_aggregated_news_provider
from .base import MarketDataProvider, NewsProvider
from .yfinance_provider import YFinanceMarketDataProvider, YFinanceNewsProvider


def create_market_data_provider(provider_name: str) -> MarketDataProvider:
    normalized = provider_name.strip().lower()
    if normalized == "yfinance":
        return YFinanceMarketDataProvider()

    raise ValueError(
        f"Unsupported live market data provider '{provider_name}'. Supported providers: yfinance"
    )


def create_news_provider(
    provider_name: str,
    *,
    searxng_base_urls: list[str] | None = None,
    searxng_timeout_seconds: int = 8,
    searxng_public_instances_enabled: bool = True,
    news_max_age_hours: int = 24,
) -> NewsProvider:
    return create_aggregated_news_provider(
        provider_name,
        searxng_base_urls=searxng_base_urls,
        searxng_timeout_seconds=searxng_timeout_seconds,
        searxng_public_instances_enabled=searxng_public_instances_enabled,
        news_max_age_hours=news_max_age_hours,
    )
