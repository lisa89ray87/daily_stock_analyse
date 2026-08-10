from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import IntelligenceBlock, MarketData


class MarketDataProvider(ABC):
    @abstractmethod
    def get_market_data(self, symbol: str) -> MarketData:
        raise NotImplementedError


class NewsProvider(ABC):
    @abstractmethod
    def get_news(self, symbol: str, limit: int = 5) -> IntelligenceBlock:
        raise NotImplementedError
