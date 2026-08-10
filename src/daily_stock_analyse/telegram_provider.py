from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


@dataclass
class TelegramSendResult:
    success: bool
    status_code: int | None = None
    error: str | None = None
    disabled: bool = False


class TelegramProvider(ABC):
    @abstractmethod
    def send_message(self, message: str, parse_mode: str = "HTML") -> TelegramSendResult:
        raise NotImplementedError


class TelegramBotProvider(TelegramProvider):
    def __init__(self, enabled: bool, bot_token: str | None, chat_id: str | None):
        self.enabled = enabled
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id)

    def send_message(self, message: str, parse_mode: str = "HTML") -> TelegramSendResult:
        if not self.enabled:
            return TelegramSendResult(success=False, disabled=True, error="Telegram disabled")
        if not self.bot_token:
            return TelegramSendResult(success=False, disabled=True, error="TELEGRAM_BOT_TOKEN missing")
        if not self.chat_id:
            return TelegramSendResult(success=False, disabled=True, error="TELEGRAM_CHAT_ID missing")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=20)
        except requests.RequestException as exc:
            return TelegramSendResult(success=False, error=f"Telegram request failed: {exc.__class__.__name__}")

        if response.status_code >= 300:
            return TelegramSendResult(
                success=False,
                status_code=response.status_code,
                error=f"Telegram API error: {response.status_code}",
            )

        return TelegramSendResult(success=True, status_code=response.status_code)
