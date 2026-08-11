from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


SYSTEM_RULES = (
    "You are the final analysis overlay for a quantitative daily stock report."
    " Never fabricate prices, news, earnings dates, or analyst ratings."
    " Distinguish FACT from INTERPRETATION."
    " Admit uncertainty when evidence is insufficient."
    " LONG and SHORT are both allowed. NO TRADE is allowed."
    " Never recalculate or override the deterministic engine's stock signals, scores, technical levels, or market regime."
)

OVERLAY_INSTRUCTION = (
    "Return strict JSON only. You are a reporting and decision-summary layer. Use only supplied data. "
    "Do not invent prices, signals, technical levels, catalysts, or confirmations. Respect WAIT and NO_TRADE. "
    "If evidence is insufficient, say Candidate, not confirmed, Watch only, No trade, or Insufficient confirmation. "
    "Identify the overall market bias, strongest LONG candidate, strongest SHORT candidate, and best day-trade candidate only when sufficiently confirmed. "
    "Also identify stocks to watch, stocks to avoid, key risks, action points, and a concise final conclusion. "
    "Use this schema: {"
    '\"market_bias\": \"BULLISH|BEARISH|MIXED|NEUTRAL\", '
    '\"market_regime\": \"...\", '
    '\"best_long_candidate\": {\"symbol\": \"...\", \"reason\": \"...\"}, '
    '\"best_short_candidate\": {\"symbol\": \"...\", \"reason\": \"...\"}, '
    '\"best_day_trade\": {\"symbol\": \"...\", \"direction\": \"LONG|SHORT|NONE\", \"reason\": \"...\", \"status\": \"Confirmed|Candidate, not confirmed|No trade|Insufficient confirmation\"}, '
    '\"stocks_to_watch\": [{\"symbol\": \"...\", \"reason\": \"...\"}], '
    '\"stocks_to_avoid\": [{\"symbol\": \"...\", \"reason\": \"...\"}], '
    '\"key_risks\": [\"...\"], '
    '\"action_points\": [\"...\", \"...\", \"...\"], '
    '\"summary\": \"...\", '
    '\"final_conclusion\": \"...\"}. '
    "If there is no sufficiently confirmed day-trade candidate, use symbol NONE and explain that no sufficiently confirmed candidate exists in supplied data."
)


@dataclass
class AIProviderResponse:
    provider: str
    summary: str
    action_points: list[str]
    raw_text: str
    parsed: dict = field(default_factory=dict)


class AIProviderError(Exception):
    def __init__(self, provider: str, public_message: str, *, fallback_eligible: bool = True):
        super().__init__(public_message)
        self.provider = provider
        self.public_message = public_message
        self.fallback_eligible = fallback_eligible


class AIOverlayProvider(ABC):
    provider_name: str

    @abstractmethod
    def generate_overlay(self, payload: dict) -> AIProviderResponse:
        raise NotImplementedError


def normalize_overlay_text(provider: str, text: str) -> AIProviderResponse:
    raw_text = text.strip()
    parsed = _extract_json_object(raw_text)

    summary = raw_text
    action_points: list[str] = []
    if isinstance(parsed, dict):
        parsed_summary = parsed.get("summary")
        if isinstance(parsed_summary, str) and parsed_summary.strip():
            summary = parsed_summary.strip()

        parsed_points = parsed.get("action_points")
        if isinstance(parsed_points, list):
            action_points = [str(x).strip() for x in parsed_points if str(x).strip()]

    if not summary:
        summary = "AI provider returned no summary"

    return AIProviderResponse(
        provider=provider,
        summary=summary,
        action_points=action_points[:3],
        raw_text=raw_text,
        parsed=parsed or {},
    )


def _extract_json_object(raw_text: str) -> dict | None:
    if not raw_text:
        return None

    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(raw_text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
