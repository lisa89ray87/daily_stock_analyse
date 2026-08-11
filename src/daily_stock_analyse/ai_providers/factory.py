from __future__ import annotations

from ..config import AppConfig
from .base import AIOverlayProvider, AIProviderError
from .gemini_provider import GeminiOverlayProvider
from .openai_provider import OpenAIOverlayProvider


def create_ai_provider(provider_name: str, cfg: AppConfig) -> AIOverlayProvider:
    normalized = provider_name.strip().lower()
    if normalized == "openai":
        return OpenAIOverlayProvider(cfg.openai_api_key)
    if normalized == "gemini":
        return GeminiOverlayProvider(cfg.gemini_api_key)

    raise AIProviderError(normalized or provider_name, f"Unsupported AI provider '{provider_name}'")
