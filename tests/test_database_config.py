from pathlib import Path

from src.daily_stock_analyse.config import load_config


def test_database_config_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_ENABLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.database_enabled is True
    assert cfg.database_url is None


def test_database_config_parsing(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.database_enabled is False
    assert cfg.database_url == "postgresql://user:pass@host/db"
