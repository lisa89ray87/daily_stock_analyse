from __future__ import annotations

from .base import MarketDataProvider, NewsProvider
from .yfinance_provider import YFinanceMarketDataProvider, YFinanceNewsProvider


def create_market_data_provider(provider_name: str) -> MarketDataProvider:
    normalized = provider_name.strip().lower()
    if normalized == "yfinance":
        return YFinanceMarketDataProvider()

    raise ValueError(
        f"Unsupported live market data provider '{provider_name}'. Supported providers: yfinance"
    )


def create_news_provider(provider_name: str) -> NewsProvider:
    normalized = provider_name.strip().lower()
    if normalized == "yfinance":
        return YFinanceNewsProvider()

    raise ValueError(
        f"Unsupported news provider '{provider_name}'. Supported providers: yfinance"
    )
