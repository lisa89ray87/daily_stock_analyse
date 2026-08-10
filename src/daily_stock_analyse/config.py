from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FIXED_WATCHLIST = ["NOK", "AMD", "NVDA", "INTC", "SNDK", "000660.KS"]
DEFAULT_CANDIDATE_UNIVERSE = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "AVGO",
    "QCOM",
    "MU",
    "ASML",
    "ARM",
    "SMCI",
    "PLTR",
    "SNOW",
    "CRM",
    "ADBE",
    "CSCO",
    "NFLX",
    "INTU",
    "ORCL",
    "ANET",
    "TSM",
    "AMAT",
    "LRCX",
    "KLAC",
    "DELL",
    "UBER",
    "PANW",
    "CRWD",
    "DDOG",
    "MDB",
]


@dataclass
class AppConfig:
    openai_api_key: str | None
    resend_api_key: str | None
    email_from: str | None
    email_to: str
    send_email: bool
    data_provider: str
    news_provider: str
    fixed_watchlist: list[str]
    candidate_universe: list[str]
    score_weights: dict[str, float]
    schedule_utc_cron: str
    min_setup_score: int
    min_relative_volume: float
    day_trade_threshold: int
    short_threshold: float
    long_threshold: float
    dynamic_count: int


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _load_watchlist_config(base_path: Path) -> tuple[list[str], list[str], dict[str, float]]:
    cfg_path = base_path / "config" / "watchlist.json"
    if not cfg_path.exists():
        return DEFAULT_FIXED_WATCHLIST, DEFAULT_CANDIDATE_UNIVERSE, {
            "trend": 0.20,
            "momentum": 0.15,
            "volume": 0.10,
            "relative_strength": 0.10,
            "fundamentals_news": 0.20,
            "catalyst_event": 0.10,
            "risk_reward": 0.15,
        }

    with cfg_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    return (
        payload.get("fixed_watchlist", DEFAULT_FIXED_WATCHLIST),
        payload.get("candidate_universe", DEFAULT_CANDIDATE_UNIVERSE),
        payload.get(
            "score_weights",
            {
                "trend": 0.20,
                "momentum": 0.15,
                "volume": 0.10,
                "relative_strength": 0.10,
                "fundamentals_news": 0.20,
                "catalyst_event": 0.10,
                "risk_reward": 0.15,
            },
        ),
    )


def load_config(base_path: Path | None = None) -> AppConfig:
    repo_root = base_path or Path(__file__).resolve().parents[2]
    fixed, universe, weights = _load_watchlist_config(repo_root)
    email_to = os.getenv("EMAIL_TO", "raymond87tan@gmail.com")

    return AppConfig(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        resend_api_key=os.getenv("RESEND_API_KEY"),
        email_from=os.getenv("EMAIL_FROM"),
        email_to=email_to,
        send_email=_env_flag("SEND_EMAIL", True),
        data_provider=os.getenv("DATA_PROVIDER", "yfinance"),
        news_provider=os.getenv("NEWS_PROVIDER", "yfinance"),
        fixed_watchlist=fixed,
        candidate_universe=universe,
        score_weights=weights,
        schedule_utc_cron=os.getenv("DAILY_REPORT_CRON_UTC", "0 23 * * 1-5"),
        min_setup_score=_env_int("MIN_SETUP_SCORE", 60),
        min_relative_volume=_env_float("MIN_RELATIVE_VOLUME", 1.15),
        day_trade_threshold=_env_int("DAY_TRADE_THRESHOLD", 72),
        short_threshold=_env_float("SHORT_THRESHOLD", 0.28),
        long_threshold=_env_float("LONG_THRESHOLD", 0.28),
        dynamic_count=_env_int("DYNAMIC_COUNT", 3),
    )
