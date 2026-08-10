from pathlib import Path

from src.daily_stock_analyse.config import load_config


def test_config_loads_defaults(monkeypatch):
    monkeypatch.delenv("EMAIL_TO", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert "NOK" in cfg.fixed_watchlist
    assert "SKHY" in cfg.fixed_watchlist
    assert "000660.KS" not in cfg.fixed_watchlist
    assert cfg.email_to == "raymond87tan@gmail.com"
    assert cfg.morning_report_time == "08:00"
    assert cfg.morning_report_timezone == "Asia/Kuala_Lumpur"
    assert cfg.live_market_timezone == "America/New_York"
    assert cfg.live_market_open == "09:30"
    assert cfg.live_market_close == "16:00"
    assert cfg.telegram_enabled is False
    assert cfg.telegram_bot_token is None
    assert cfg.telegram_chat_id is None


def test_send_email_flag(monkeypatch):
    monkeypatch.setenv("SEND_EMAIL", "0")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.send_email is False


def test_threshold_overrides(monkeypatch):
    monkeypatch.setenv("MIN_SETUP_SCORE", "66")
    monkeypatch.setenv("MIN_RELATIVE_VOLUME", "1.3")
    monkeypatch.setenv("DAY_TRADE_THRESHOLD", "75")
    monkeypatch.setenv("SHORT_THRESHOLD", "0.31")
    monkeypatch.setenv("LONG_THRESHOLD", "0.29")
    monkeypatch.setenv("DYNAMIC_COUNT", "4")
    monkeypatch.setenv("DAY_TRADE_GAP_THRESHOLD", "3.2")
    monkeypatch.setenv("DAY_TRADE_RVOL_THRESHOLD", "1.7")
    monkeypatch.setenv("DAY_TRADE_MIN_SETUP_SCORE", "67")
    monkeypatch.setenv("LIVE_ALERT_ENABLED", "1")
    monkeypatch.setenv("LIVE_ALERT_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("ALERT_MIN_SETUP_SCORE", "72")
    monkeypatch.setenv("ALERT_MIN_RVOL", "1.8")
    monkeypatch.setenv("ALERT_COOLDOWN_MINUTES", "20")
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.min_setup_score == 66
    assert cfg.min_relative_volume == 1.3
    assert cfg.day_trade_threshold == 75
    assert cfg.short_threshold == 0.31
    assert cfg.long_threshold == 0.29
    assert cfg.dynamic_count == 4
    assert cfg.day_trade_gap_threshold == 3.2
    assert cfg.day_trade_rvol_threshold == 1.7
    assert cfg.day_trade_min_setup_score == 67
    assert cfg.live_alert_enabled is True
    assert cfg.live_alert_interval_minutes == 5
    assert cfg.alert_min_setup_score == 72
    assert cfg.alert_min_rvol == 1.8
    assert cfg.alert_cooldown_minutes == 20
    assert cfg.telegram_enabled is True
    assert cfg.telegram_bot_token == "token-x"
    assert cfg.telegram_chat_id == "chat-1"
