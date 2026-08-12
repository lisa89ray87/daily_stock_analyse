from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re

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


def _normalize_trade_direction(message: str) -> str:
    """Correct a contradictory LONG/SHORT Telegram header from validated levels.

    The live engine already validates trade geometry before an alert is eligible.
    This final transport guard prevents a stale/inconsistent signal label from
    producing a Telegram message that contradicts Entry/Stop/Target1.
    """
    header = re.search(r"(<b>[🟢🔴]\s+)([^\n<]+?)\s+(LONG|SHORT)(\s+-\s+ENTRY TRIGGERED</b>)", message)
    if not header:
        return message

    numbers = {}
    for label in ("Entry", "Stop", "Target 1"):
        match = re.search(rf"{re.escape(label)}:\s*\$([0-9]+(?:\.[0-9]+)?)", message)
        if match:
            numbers[label] = float(match.group(1))

    if len(numbers) != 3:
        return message

    entry = numbers["Entry"]
    stop = numbers["Stop"]
    target1 = numbers["Target 1"]
    if stop < entry < target1:
        validated_direction = "LONG"
    elif target1 < entry < stop:
        validated_direction = "SHORT"
    else:
        return message

    current_direction = header.group(3)
    if current_direction == validated_direction:
        return message

    replacement = f"{header.group(1)}{header.group(2)} {validated_direction}{header.group(4)}"
    return message[:header.start()] + replacement + message[header.end():]


def _label_live_stock_alert(message: str) -> str:
    """Clearly identify messages produced by the Live Stock Alert engine.

    Live Event messages already carry their own LIVE EVENT WARNING header.
    Only the known Live Stock Alert message families are labelled here, so
    Telegram connectivity tests and unrelated provider messages are untouched.
    """
    if "LIVE EVENT WARNING" in message or "LIVE STOCK ALERT" in message:
        return message

    stock_alert_markers = (
        "ENTRY TRIGGERED",
        "TARGET REACHED",
        "LONG SETUP INVALIDATED",
        "SHORT SETUP INVALIDATED",
    )
    if not any(marker in message for marker in stock_alert_markers):
        return message

    return "<b>🟢 LIVE STOCK ALERT</b>\n\n" + message


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
        if not message.strip():
            return TelegramSendResult(success=False, error="Telegram message is empty")

        message = _label_live_stock_alert(message)
        message = _normalize_trade_direction(message)

        if len(message) > 4096:
            return TelegramSendResult(success=False, error=f"Telegram message exceeds 4096 characters ({len(message)})")

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
            detail = ""
            try:
                payload_error = response.json().get("description")
                if payload_error:
                    detail = f": {payload_error}"
            except ValueError:
                pass
            return TelegramSendResult(
                success=False,
                status_code=response.status_code,
                error=f"Telegram API error: {response.status_code}{detail}",
            )

        return TelegramSendResult(success=True, status_code=response.status_code)
