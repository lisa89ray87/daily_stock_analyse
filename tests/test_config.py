from pathlib import Path

from src.daily_stock_analyse.config import load_config


def test_config_loads_defaults(monkeypatch):
    monkeypatch.delenv("EMAIL_TO", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert "NOK" in cfg.fixed_watchlist
    assert cfg.email_to == "raymond87tan@gmail.com"


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
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.min_setup_score == 66
    assert cfg.min_relative_volume == 1.3
    assert cfg.day_trade_threshold == 75
    assert cfg.short_threshold == 0.31
    assert cfg.long_threshold == 0.29
    assert cfg.dynamic_count == 4
