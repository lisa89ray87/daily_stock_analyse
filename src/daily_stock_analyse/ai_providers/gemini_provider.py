from __future__ import annotations

import json

import requests

from .base import AIOverlayProvider, AIProviderError, AIProviderResponse, OVERLAY_INSTRUCTION, SYSTEM_RULES, normalize_overlay_text


class GeminiOverlayProvider(AIOverlayProvider):
    provider_name = "gemini"
    model_name = "gemini-3.6-flash"
    api_base = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def generate_overlay(self, payload: dict) -> AIProviderResponse:
        if not self.api_key:
            raise AIProviderError(
                self.provider_name,
                "Gemini unavailable: GEMINI_API_KEY not configured",
                category="configuration",
            )

        try:
            response = requests.post(
                f"{self.api_base}/{self.model_name}:generateContent",
                params={"key": self.api_key},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": (
                                        f"{SYSTEM_RULES}\n\n{OVERLAY_INSTRUCTION}\n\n"
                                        f"Payload:\n{json.dumps(payload)}"
                                    )
                                }
                            ],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=30,
            )
        except requests.RequestException:
            raise AIProviderError(self.provider_name, "Gemini API request failed", category="transport_error")

        if response.status_code >= 300:
            raise _classify_gemini_error(response)

        try:
            data = response.json()
        except ValueError:
            raise AIProviderError(self.provider_name, "Gemini returned invalid JSON", category="invalid_json")

        text = _extract_gemini_text(data)
        if not text:
            raise AIProviderError(self.provider_name, "Gemini returned no content", category="empty_response")
        return normalize_overlay_text(self.provider_name, text)


def _classify_gemini_error(response: requests.Response) -> AIProviderError:
    body = response.text.lower()
    status_code = response.status_code

    if status_code == 429 or "insufficient_quota" in body or "credit_balance_exhausted" in body or "rate limit" in body:
        return AIProviderError(
            "gemini",
            "Gemini quota or rate limit exceeded",
            category="quota_or_rate_limit",
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return AIProviderError(
            "gemini",
            "Gemini authentication or configuration failed",
            category="authentication",
            status_code=status_code,
        )
    if status_code >= 500:
        return AIProviderError(
            "gemini",
            "Gemini API temporarily unavailable",
            category="server_error",
            status_code=status_code,
        )
    return AIProviderError(
        "gemini",
        "Gemini API request failed",
        category="api_error",
        status_code=status_code,
    )


def _extract_gemini_text(payload: dict) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""
