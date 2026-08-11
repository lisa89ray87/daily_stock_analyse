from __future__ import annotations

from typing import Any

from .ai_providers import AIProviderError, AIProviderResponse, create_ai_provider
from .config import AppConfig
from .models import MarketRegime, StockAnalysis


def generate_ai_overlay(
    analyses: list[StockAnalysis],
    market_regime: MarketRegime,
    cfg: AppConfig,
) -> dict[str, Any]:
    try:
        payload = build_ai_overlay_payload(analyses, market_regime)
    except Exception:
        print("AI overlay payload build failed | category=internal_overlay_error")
        return {
            "enabled": False,
            "provider": None,
            "provider_display": None,
            "status": "Unavailable",
            "fallback_used": False,
            "summary": None,
            "action_points": [],
            "message": "AI unavailable: internal overlay error",
        }

    attempt_order = _provider_attempt_order(cfg.ai_primary_provider, cfg.ai_fallback_provider)
    failures: list[AIProviderError] = []

    for index, provider_name in enumerate(attempt_order):
        try:
            print(
                f"AI overlay attempt {index + 1}/{len(attempt_order)} | "
                f"provider={provider_name} | key_configured={_provider_has_key(provider_name, cfg)}"
            )
            provider = create_ai_provider(provider_name, cfg)
            result = provider.generate_overlay(payload)
            print(
                f"AI overlay success | provider={provider_name} | "
                f"fallback_used={'yes' if index > 0 else 'no'}"
            )
            return _success_overlay(result, payload, fallback_used=index > 0)
        except AIProviderError as exc:
            print(
                f"AI overlay failure | provider={exc.provider} | category={exc.category} | "
                f"status_code={exc.status_code if exc.status_code is not None else 'n/a'} | message={exc.public_message}"
            )
            failures.append(exc)
            continue
        except Exception:
            print(f"AI overlay failure | provider={provider_name} | category=internal_overlay_error")
            return _disabled_overlay("AI unavailable: internal overlay error")

    if not failures:
        return _disabled_overlay("AI unavailable: no AI providers configured")

    if len(failures) == 1:
        return _disabled_overlay(f"AI unavailable: {_provider_display(failures[0].provider)} provider unavailable")

    return _disabled_overlay("AI unavailable: OpenAI and Gemini providers were unavailable")


def build_ai_overlay_payload(
    analyses: list[StockAnalysis],
    market_regime: MarketRegime,
) -> dict[str, Any]:
    return {
        "market_regime": {
            "label": market_regime.label,
            "bias": market_regime.bias,
            "main_catalyst": market_regime.main_catalyst,
            "main_risk": market_regime.main_risk,
        },
        "session_context": {
            "session_state": analyses[0].market_data.session_state if analyses else "CLOSED",
            "selected_data_source": analyses[0].market_data.selected_data_source if analyses else "UNAVAILABLE",
            "live_regular_session": analyses[0].market_data.live_regular_session if analyses else False,
        },
        "stocks": [
            {
                "symbol": x.symbol,
                "signal": x.signal,
                "direction_bias": x.direction_bias,
                "market_alignment": x.market_alignment,
                "confidence": x.confidence,
                "setup_score": x.setup_score,
                "candidate_score": x.candidate_score,
                "candidate_status": x.candidate_status,
                "confirmation_needed": x.confirmation_needed,
                "trading_horizon": x.trading_horizon,
                "day_trade_candidate": x.day_trade_candidate,
                "price": x.market_data.price,
                "price_session": x.market_data.selected_price_session,
                "session_state": x.market_data.session_state,
                "selected_data_source": x.market_data.selected_data_source,
                "live_regular_session": x.market_data.live_regular_session,
                "extended_hours_used": x.market_data.extended_hours_used,
                "trend": x.market_data.trend,
                "sma20": x.market_data.sma20,
                "sma50": x.market_data.sma50,
                "sma200": x.market_data.sma200,
                "rsi": x.market_data.rsi14,
                "macd": x.market_data.macd,
                "vwap": x.market_data.vwap,
                "opening_range_high": x.market_data.opening_range_high,
                "opening_range_low": x.market_data.opening_range_low,
                "breakout_state": x.market_data.breakout_state,
                "atr": x.market_data.atr14,
                "volume": x.market_data.volume,
                "relative_volume": x.market_data.relative_volume,
                "volatility": x.market_data.volatility_20d,
                "support": x.market_data.support,
                "resistance": x.market_data.resistance,
                "news_facts": x.intelligence.facts,
                "catalysts": x.intelligence.upcoming_catalysts,
                "risks": x.intelligence.interpretation,
                "data_quality_warnings": x.data_quality.warnings,
            }
            for x in analyses
        ],
    }


def _provider_attempt_order(primary: str, fallback: str) -> list[str]:
    order: list[str] = []
    for provider in [primary, fallback]:
        normalized = (provider or "").strip().lower()
        if normalized and normalized not in order:
            order.append(normalized)
    return order


def _success_overlay(result: AIProviderResponse, payload: dict[str, Any], *, fallback_used: bool) -> dict[str, Any]:
    market_bias = _normalize_market_bias(result.parsed.get("market_bias"), payload)
    market_regime = _normalize_market_regime(result.parsed.get("market_regime"), payload)
    best_long = _normalize_symbol_reason_entry(result.parsed.get("best_long_candidate"), payload, expected="LONG")
    best_short = _normalize_symbol_reason_entry(result.parsed.get("best_short_candidate"), payload, expected="SHORT")
    best_day_trade = _normalize_best_day_trade(result.parsed.get("best_day_trade"), payload)
    stocks_to_watch = _normalize_symbol_reason_list(result.parsed.get("stocks_to_watch"), payload)
    stocks_to_avoid = _normalize_symbol_reason_list(result.parsed.get("stocks_to_avoid"), payload)
    key_risks = _normalize_text_list(result.parsed.get("key_risks"), limit=5)
    summary = _normalize_text(result.parsed.get("summary")) or result.summary
    final_conclusion = _normalize_text(result.parsed.get("final_conclusion")) or summary
    action_points = result.action_points or _normalize_text_list(result.parsed.get("action_points"), limit=3)

    return {
        "enabled": True,
        "provider": result.provider,
        "provider_display": _provider_display(result.provider),
        "status": "Fallback" if fallback_used else "Enabled",
        "fallback_used": fallback_used,
        "summary": summary,
        "action_points": action_points,
        "market_bias": market_bias,
        "market_regime": market_regime,
        "best_long_candidate": best_long,
        "best_short_candidate": best_short,
        "best_day_trade": best_day_trade,
        "stocks_to_watch": stocks_to_watch,
        "stocks_to_avoid": stocks_to_avoid,
        "key_risks": key_risks,
        "final_conclusion": final_conclusion,
        "message": final_conclusion or summary,
    }


def _disabled_overlay(message: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": None,
        "provider_display": None,
        "status": "Unavailable",
        "fallback_used": False,
        "summary": None,
        "action_points": [],
        "market_bias": None,
        "market_regime": None,
        "best_long_candidate": _none_candidate(),
        "best_short_candidate": _none_candidate(),
        "best_day_trade": _none_day_trade(),
        "stocks_to_watch": [],
        "stocks_to_avoid": [],
        "key_risks": [],
        "final_conclusion": None,
        "message": message,
    }


def _provider_display(provider_name: str | None) -> str | None:
    if provider_name == "openai":
        return "OpenAI"
    if provider_name == "gemini":
        return "Gemini"
    return provider_name


def _provider_has_key(provider_name: str, cfg: AppConfig) -> bool:
    normalized = (provider_name or "").strip().lower()
    if normalized == "openai":
        return bool(cfg.openai_api_key)
    if normalized == "gemini":
        return bool(cfg.gemini_api_key)
    return False


def _normalize_market_bias(value, payload: dict[str, Any]) -> str:
    text = _normalize_text(value)
    if text in {"BULLISH", "BEARISH", "MIXED", "NEUTRAL"}:
        return text

    market = payload.get("market_regime", {}) if isinstance(payload, dict) else {}
    label = _normalize_text(market.get("label"))
    bias = _normalize_text(market.get("bias"))
    if label == "MIXED":
        return "MIXED"
    if bias in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return bias
    return "NEUTRAL"


def _normalize_market_regime(value, payload: dict[str, Any]) -> str:
    text = _normalize_text(value)
    if text:
        return text

    market = payload.get("market_regime", {}) if isinstance(payload, dict) else {}
    parts = [
        _normalize_text(market.get("label")),
        _normalize_text(market.get("main_catalyst")),
        _normalize_text(market.get("main_risk")),
    ]
    joined = " | ".join([x for x in parts if x])
    return joined or "Market regime unavailable"


def _normalize_best_day_trade(value, payload: dict[str, Any]) -> dict[str, str]:
    default = _none_day_trade()
    if not isinstance(value, dict):
        return default

    symbol = _normalize_symbol(value.get("symbol"))
    reason = _normalize_text(value.get("reason")) or default["reason"]
    direction = _normalize_text(value.get("direction")) or "NONE"
    status = _normalize_text(value.get("status")) or "No trade"

    if symbol == "NONE":
        return default

    stock = _stock_lookup(payload).get(symbol)
    if stock is None or not _is_confirmed_day_trade(stock):
        return default

    signal = _normalize_text(stock.get("signal")) or "NONE"
    safe_status = status if status in {"Confirmed", "Candidate, not confirmed", "No trade", "Insufficient confirmation", "Watch only"} else "Confirmed"
    return {
        "symbol": symbol,
        "direction": signal if signal in {"LONG", "SHORT"} else direction,
        "reason": reason,
        "status": safe_status if safe_status != "Candidate, not confirmed" else "Confirmed",
    }


def _normalize_symbol_reason_entry(value, payload: dict[str, Any], *, expected: str) -> dict[str, str]:
    default = _none_candidate()
    if not isinstance(value, dict):
        return default

    symbol = _normalize_symbol(value.get("symbol"))
    reason = _normalize_text(value.get("reason")) or default["reason"]
    if symbol == "NONE":
        return default

    stock = _stock_lookup(payload).get(symbol)
    if stock is None or not _matches_direction(stock, expected):
        return default
    return {"symbol": symbol, "reason": reason}


def _normalize_symbol_reason_list(value, payload: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    stock_lookup = _stock_lookup(payload)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        symbol = _normalize_symbol(item.get("symbol"))
        reason = _normalize_text(item.get("reason"))
        if not symbol or symbol == "NONE" or symbol in seen or symbol not in stock_lookup or not reason:
            continue
        seen.add(symbol)
        out.append({"symbol": symbol, "reason": reason})
    return out


def _normalize_text_list(value, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _normalize_text(item)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _normalize_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalize_symbol(value) -> str:
    text = _normalize_text(value)
    return text.upper() if text else ""


def _stock_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stocks = payload.get("stocks") if isinstance(payload, dict) else None
    if not isinstance(stocks, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        if isinstance(stock, dict):
            symbol = _normalize_symbol(stock.get("symbol"))
            if symbol:
                out[symbol] = stock
    return out


def _matches_direction(stock: dict[str, Any], expected: str) -> bool:
    signal = _normalize_text(stock.get("signal"))
    bias = _normalize_text(stock.get("direction_bias"))
    if expected == "LONG":
        return signal == "LONG" or bias == "LONG_BIAS"
    if expected == "SHORT":
        return signal == "SHORT" or bias == "SHORT_BIAS"
    return False


def _is_confirmed_day_trade(stock: dict[str, Any]) -> bool:
    signal = _normalize_text(stock.get("signal"))
    candidate_status = (_normalize_text(stock.get("candidate_status")) or "").upper()
    if signal not in {"LONG", "SHORT"}:
        return False
    if "WAIT" in candidate_status or "NO_TRADE" in candidate_status:
        return False
    return True


def _none_candidate() -> dict[str, str]:
    return {"symbol": "NONE", "reason": "No sufficiently strong candidate in supplied data."}


def _none_day_trade() -> dict[str, str]:
    return {
        "symbol": "NONE",
        "direction": "NONE",
        "reason": "No sufficiently confirmed candidate in supplied data.",
        "status": "No trade",
    }
