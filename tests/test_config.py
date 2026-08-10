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
