from pathlib import Path
from unittest.mock import patch

from src.daily_stock_analyse.telegram_connectivity_test import TEST_MESSAGE, run_telegram_connectivity_test
from src.daily_stock_analyse.telegram_provider import TelegramSendResult


def test_missing_token_reports_expected_message(capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id")

    code = run_telegram_connectivity_test(Path(__file__).resolve().parents[1])
    out = capsys.readouterr().out

    assert code == 1
    assert "Telegram test failed: TELEGRAM_BOT_TOKEN missing" in out


def test_missing_chat_id_reports_expected_message(capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    code = run_telegram_connectivity_test(Path(__file__).resolve().parents[1])
    out = capsys.readouterr().out

    assert code == 1
    assert "Telegram test failed: TELEGRAM_CHAT_ID missing" in out


def test_success_sends_exactly_one_message(capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id")

    with patch("src.daily_stock_analyse.telegram_connectivity_test.TelegramBotProvider.send_message") as send_mock:
        send_mock.return_value = TelegramSendResult(success=True, status_code=200)
        code = run_telegram_connectivity_test(Path(__file__).resolve().parents[1])

    out = capsys.readouterr().out
    assert code == 0
    assert "Telegram connection test successful." in out
    assert send_mock.call_count == 1
    args, kwargs = send_mock.call_args
    assert args[0] == TEST_MESSAGE
    assert kwargs["parse_mode"] == "HTML"


def test_api_failure_reports_sanitized_error(capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id")

    with patch("src.daily_stock_analyse.telegram_connectivity_test.TelegramBotProvider.send_message") as send_mock:
        send_mock.return_value = TelegramSendResult(success=False, status_code=400, error="Telegram API error: 400")
        code = run_telegram_connectivity_test(Path(__file__).resolve().parents[1])

    out = capsys.readouterr().out
    assert code == 1
    assert "Telegram test failed: Telegram API error: 400" in out
    assert "bot-token" not in out
    assert "chat-id" not in out
