from __future__ import annotations

from datetime import UTC, datetime

import yfinance as yf

from .models import MarketRegime


def _fetch_return(symbol: str) -> float | None:
    hist = yf.Ticker(symbol).history(period="5d", interval="1d")
    if hist.empty or len(hist) < 2:
        return None
    close = hist["Close"].dropna()
    if len(close) < 2:
        return None
    prev = float(close.iloc[-2])
    curr = float(close.iloc[-1])
    if prev == 0:
        return None
    return (curr - prev) / prev * 100.0


def build_market_regime() -> MarketRegime:
    indicators = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "nasdaq_change_pct": _fetch_return("^IXIC"),
        "sp500_change_pct": _fetch_return("^GSPC"),
        "dow_change_pct": _fetch_return("^DJI"),
        "vix_change_pct": _fetch_return("^VIX"),
        "us10y_yield_change_pct": _fetch_return("^TNX"),
        "semiconductor_etf_change_pct": _fetch_return("SOXX"),
        "ai_proxy_etf_change_pct": _fetch_return("BOTZ"),
    }

    risk_score = 0
    growth_score = 0

    nasdaq = indicators["nasdaq_change_pct"]
    sp500 = indicators["sp500_change_pct"]
    vix = indicators["vix_change_pct"]
    soxx = indicators["semiconductor_etf_change_pct"]

    for x in (nasdaq, sp500, soxx):
        if x is not None and x > 0:
            growth_score += 1
        if x is not None and x < 0:
            risk_score += 1

    if vix is not None and vix > 2:
        risk_score += 2
    elif vix is not None and vix < -2:
        growth_score += 1

    if growth_score - risk_score >= 2:
        label = "RISK-ON"
        bias = "BULLISH"
        catalyst = "Broad equity strength with semiconductor participation"
        main_risk = "Crowded momentum unwind"
    elif risk_score - growth_score >= 2:
        label = "RISK-OFF"
        bias = "BEARISH"
        catalyst = "Defensive positioning and volatility pressure"
        main_risk = "Sharp bear market rallies"
    else:
        label = "MIXED"
        bias = "NEUTRAL"
        catalyst = "Cross-currents across macro and sector factors"
        main_risk = "Whipsaw conditions"

    summary = (
        "Assessment uses latest available daily index and ETF moves; "
        "this is not real-time tick data."
    )

    return MarketRegime(
        label=label,
        bias=bias,
        main_catalyst=catalyst,
        main_risk=main_risk,
        summary=summary,
        indicators=indicators,
    )
