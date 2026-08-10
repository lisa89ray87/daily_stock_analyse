from __future__ import annotations

from pathlib import Path

from .config import load_config
from .telegram_provider import TelegramBotProvider

TEST_MESSAGE = (
    "🧪 Daily Stock Analysis\n\n"
    "Telegram connection test successful.\n\n"
    "The alert system can now send live notifications."
)


def run_telegram_connectivity_test(base_path: Path | None = None) -> int:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    cfg = load_config(repo_root)

    if not cfg.telegram_bot_token:
        print("Telegram test failed: TELEGRAM_BOT_TOKEN missing")
        return 1

    if not cfg.telegram_chat_id:
        print("Telegram test failed: TELEGRAM_CHAT_ID missing")
        return 1

    provider = TelegramBotProvider(
        enabled=True,
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
    )

    result = provider.send_message(TEST_MESSAGE, parse_mode="HTML")
    if result.success:
        print("Telegram connection test successful.")
        return 0

    if result.error:
        print(f"Telegram test failed: {result.error}")
        return 1

    print("Telegram test failed: Telegram API request rejected")
    return 1


def main() -> int:
    return run_telegram_connectivity_test()


if __name__ == "__main__":
    raise SystemExit(main())
