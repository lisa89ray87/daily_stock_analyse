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
    text = str(exc)
    lowered = text.lower()
    class_name = exc.__class__.__name__.lower()
    status_code = getattr(exc, "status_code", None)

    if status_code == 429 or "429" in lowered or "insufficient_quota" in lowered or "credit_balance_exhausted" in lowered:
        category = "quota_or_rate_limit"
        message = "OpenAI quota or rate limit exceeded"
    elif "ratelimit" in class_name or "rate limit" in lowered:
        category = "quota_or_rate_limit"
        message = "OpenAI quota or rate limit exceeded"
    elif status_code in {401, 403} or "authentication" in lowered or "unauthorized" in lowered or "api key" in lowered:
        category = "authentication"
        message = "OpenAI authentication or configuration failed"
    elif status_code is not None or class_name.endswith("error"):
        category = "api_error"
        message = "OpenAI API request failed"
    else:
        raise exc

    # Include a short, sanitized provider detail so GitHub Actions can identify
    # model/endpoint/API compatibility problems without exposing credentials.
    detail = " ".join(text.replace("\n", " ").split())[:240]
    if detail:
        message = f"{message} | detail={detail}"

    return AIProviderError(
        "openai",
        message,
        category=category,
        status_code=status_code,
    )
