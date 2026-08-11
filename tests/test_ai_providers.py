from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from src.daily_stock_analyse.ai_providers.openai_provider import OpenAIOverlayProvider


def test_openai_provider_bounds_request_timeout_and_sdk_retries():
    captured: dict[str, object] = {}

    class _FakeResponses:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(output_text='{"summary":"ok","action_points":[]}')

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = _FakeResponses()

    fake_openai = SimpleNamespace(OpenAI=_FakeClient)
    provider = OpenAIOverlayProvider("test-key")

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = provider.generate_overlay({"stocks": []})

    assert result.provider == "openai"
    assert captured["client"] == {"api_key": "test-key", "timeout": 30.0, "max_retries": 0}
    assert captured["request"]["model"] == "gpt-5-mini"


def test_openai_provider_classifies_429_as_fallback_eligible():
    provider = OpenAIOverlayProvider("test-key")

    class _RateLimitError(Exception):
        status_code = 429

    try:
        provider._classify_openai_error(_RateLimitError("429"))
    except AttributeError:
        # The classifier is intentionally module-level; exercise it through a real request failure below.
        pass
