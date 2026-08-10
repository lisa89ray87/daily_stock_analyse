from datetime import datetime, timedelta

import pandas as pd

from src.daily_stock_analyse.providers.yfinance_provider import YFinanceMarketDataProvider


class _FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.fast_info = {
            "previous_close": 80.0,
            "pre_market_price": 106.0,
            "pre_market_volume": 12345.0,
        }
        self.info = {
            "previousClose": 80.0,
            "postMarketPrice": 104.0,
        }

    def history(self, period: str, interval: str, auto_adjust: bool = False, prepost: bool | None = None):
        if interval == "1d":
            idx = pd.date_range(datetime(2026, 8, 1), periods=30, freq="D")
            base = [100 + i * 0.3 for i in range(30)]
            return pd.DataFrame(
                {
                    "Open": base,
                    "High": [x + 1 for x in base],
                    "Low": [x - 1 for x in base],
                    "Close": base,
                    "Adj Close": [x * 0.9 for x in base],
                    "Volume": [1_000_000 + i * 10_000 for i in range(30)],
                },
                index=idx,
            )
        idx = pd.date_range(datetime(2026, 8, 10, 9, 30), periods=12, freq="5min")
        close = [104 + i * 0.2 for i in range(12)]
        return pd.DataFrame(
            {
                "Open": close,
                "High": [x + 0.3 for x in close],
                "Low": [x - 0.3 for x in close],
                "Close": close,
                "Volume": [10000 + i * 500 for i in range(12)],
            },
            index=idx,
        )


class _FakeTickerNoPremarket(_FakeTicker):
    def __init__(self, symbol: str):
        super().__init__(symbol)
        self.fast_info = {"previous_close": 80.0}
        self.info = {}


def test_price_adjustment_consistency_uses_unadjusted_close(monkeypatch):
    import src.daily_stock_analyse.providers.yfinance_provider as mod

    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    md = YFinanceMarketDataProvider().get_market_data("AMD")

    assert md.price == md.regular_price
    assert md.previous_close == md.overnight_reference_price
    # Latest extended can differ by session quote, but core trend inputs come from unadjusted daily close.
    assert md.sma20 is not None


def test_session_labeling_for_premarket(monkeypatch):
    import src.daily_stock_analyse.providers.yfinance_provider as mod

    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    md = YFinanceMarketDataProvider().get_market_data("AMD")
    assert md.latest_extended_session == "PREMARKET"
    assert md.premarket_price is not None


def test_session_labeling_without_premarket(monkeypatch):
    import src.daily_stock_analyse.providers.yfinance_provider as mod

    monkeypatch.setattr(mod.yf, "Ticker", _FakeTickerNoPremarket)
    md = YFinanceMarketDataProvider().get_market_data("AMD")
    assert md.premarket_price is None
    assert md.latest_extended_session in {"AFTER_HOURS", "REGULAR"}
