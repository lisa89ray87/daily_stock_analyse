from __future__ import annotations

import json

from .base import AIOverlayProvider, AIProviderError, AIProviderResponse, OVERLAY_INSTRUCTION, SYSTEM_RULES, normalize_overlay_text


class OpenAIOverlayProvider(AIOverlayProvider):
    provider_name = "openai"
    model_name = "gpt-5-mini"
    request_timeout_seconds = 30.0

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def generate_overlay(self, payload: dict) -> AIProviderResponse:
        if not self.api_key:
            raise AIProviderError(
                self.provider_name,
                "OpenAI unavailable: OPENAI_API_KEY not configured",
                category="configuration",
            )

        try:
            from openai import OpenAI
        except Exception:
            raise AIProviderError(
                self.provider_name,
                "OpenAI unavailable: client not installed",
                category="client_import",
            )

        try:
            # Bound both the HTTP request and SDK retries so a quota/rate-limit
            # failure can hand off to the configured fallback promptly instead
            # of consuming the workflow's long default retry/timeout window.
            client = OpenAI(
                api_key=self.api_key,
                timeout=self.request_timeout_seconds,
                max_retries=0,
            )
            response = client.responses.create(
                model=self.model_name,
                input=[
                    {"role": "system", "content": SYSTEM_RULES},
                    {"role": "user", "content": OVERLAY_INSTRUCTION},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            )
        except Exception as exc:
            raise _classify_openai_error(exc) from None

        text = getattr(response, "output_text", "") or ""
        if not text.strip():
            raise AIProviderError(self.provider_name, "OpenAI returned no content", category="empty_response")
        return normalize_overlay_text(self.provider_name, text)


def _classify_openai_error(exc: Exception) -> AIProviderError:
    text = str(exc).lower()
    class_name = exc.__class__.__name__.lower()
    status_code = getattr(exc, "status_code", None)

    if status_code == 429 or "429" in text or "insufficient_quota" in text or "credit_balance_exhausted" in text:
        return AIProviderError(
            "openai",
            "OpenAI quota or rate limit exceeded",
            category="quota_or_rate_limit",
            status_code=status_code,
        )
    if "ratelimit" in class_name or "rate limit" in text:
        return AIProviderError(
            "openai",
            "OpenAI quota or rate limit exceeded",
            category="quota_or_rate_limit",
            status_code=status_code,
        )
    if status_code in {401, 403} or "authentication" in text or "unauthorized" in text or "api key" in text:
        return AIProviderError(
            "openai",
            "OpenAI authentication or configuration failed",
            category="authentication",
            status_code=status_code,
        )
    if status_code is not None or class_name.endswith("error"):
        return AIProviderError(
            "openai",
            "OpenAI API request failed",
            category="api_error",
            status_code=status_code,
        )
    raise exc
