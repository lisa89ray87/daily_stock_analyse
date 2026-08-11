from __future__ import annotations

from ..providers.base import NewsProvider
from ..providers.yfinance_provider import YFinanceNewsProvider
from .diagnostics import wrap


def create_news_provider(provider_name: str, **_kwargs) -> NewsProvider:
    """Create the configured news provider.

    The production workflow intentionally uses YFinance only. Unsupported
    providers fail fast rather than silently changing the news source.
    """
    normalized = provider_name.strip().lower()
    if normalized in {"", "yfinance"}:
        return wrap(YFinanceNewsProvider(), "yfinance")
    raise ValueError(f"Unsupported news provider '{provider_name}'. Supported provider: yfinance")
