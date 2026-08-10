from __future__ import annotations

import json
from typing import Any

from .models import MarketRegime, StockAnalysis


SYSTEM_RULES = (
    "You are a stock research assistant."
    " Never fabricate prices, news, earnings dates, or analyst ratings."
    " Distinguish FACT from INTERPRETATION."
    " Admit uncertainty when evidence is insufficient."
    " LONG and SHORT are both allowed. NO TRADE is allowed."
)


def generate_ai_overlay(
    analyses: list[StockAnalysis],
    market_regime: MarketRegime,
    openai_api_key: str | None,
) -> dict[str, Any]:
    if not openai_api_key:
        return {
            "enabled": False,
            "message": "AI disabled: OPENAI_API_KEY not configured",
        }

    try:
        from openai import OpenAI

        client = OpenAI(api_key=openai_api_key)
        payload = {
            "market_regime": {
                "label": market_regime.label,
                "bias": market_regime.bias,
                "main_catalyst": market_regime.main_catalyst,
                "main_risk": market_regime.main_risk,
            },
            "stocks": [
                {
                    "symbol": x.symbol,
                    "signal": x.signal,
                    "confidence": x.confidence,
                    "price": x.market_data.price,
                    "trend": x.market_data.trend,
                    "sma20": x.market_data.sma20,
                    "sma50": x.market_data.sma50,
                    "sma200": x.market_data.sma200,
                    "rsi": x.market_data.rsi14,
                    "macd": x.market_data.macd,
                    "volume": x.market_data.volume,
                    "relative_volume": x.market_data.relative_volume,
                    "volatility": x.market_data.volatility_20d,
                    "support": x.market_data.support,
                    "resistance": x.market_data.resistance,
                    "news_facts": x.intelligence.facts,
                    "catalysts": x.intelligence.upcoming_catalysts,
                    "risks": x.intelligence.interpretation,
                }
                for x in analyses
            ],
        }

        response = client.responses.create(
            model="gpt-5-mini",
            input=[
                {"role": "system", "content": SYSTEM_RULES},
                {
                    "role": "user",
                    "content": "Summarize market-wide risks and 3 high-priority action points in JSON.",
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        text = response.output_text
        return {
            "enabled": True,
            "message": text,
        }
    except Exception as exc:
        return {
            "enabled": False,
            "message": f"AI unavailable: {exc}",
        }
