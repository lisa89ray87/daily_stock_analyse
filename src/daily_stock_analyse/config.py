from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FIXED_WATCHLIST = ["NOK", "AMD", "NVDA", "INTC", "SNDK", "SKHY"]
ANALYSIS_SYMBOLS_ENV_VAR = "ANALYSIS_SYMBOLS"
FIXED_SIX_ENV_VAR = "FIXED_SIX_SYMBOLS"
MAX_ANALYSIS_SYMBOLS_ENV_VAR = "MAX_ANALYSIS_SYMBOLS"
DEFAULT_MAX_ANALYSIS_SYMBOLS = 20
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")
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
    day_trade_gap_threshold: float
    day_trade_rvol_threshold: float
    day_trade_min_setup_score: int
    morning_report_time: str
    morning_report_timezone: str
    live_alert_enabled: bool
    live_alert_interval_minutes: int
    live_market_timezone: str
    live_market_open: str
    live_market_close: str
    alert_min_setup_score: int
    alert_min_rvol: float
    alert_cooldown_minutes: int
    telegram_enabled: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    database_enabled: bool = True
    database_url: str | None = None
    enable_news: bool = True
    news_lookback_hours: int = 24
    news_max_age_hours: int = 24
    searxng_base_urls: list[str] = None
    searxng_public_instances_enabled: bool = True
    searxng_timeout_seconds: int = 8
    enable_outcome_tracking: bool = True
    enable_backtest: bool = True
    signal_db_path: str = "artifacts/signal_history.db"
    signal_expiry_hours: int = 48
    live_data_provider: str = "yfinance"
    gemini_api_key: str | None = None
    ai_primary_provider: str = "openai"
    ai_fallback_provider: str = "gemini"
    v4_opening_start: str = "09:30"
    v4_opening_end: str = "10:00"
    v4_opening_min_rvol: float = 1.20
    v4_opening_min_setup_score: int = 75
    v4_normal_min_rvol: float = 1.50
    v4_normal_min_setup_score: int = 70
    v4_opening_range_minutes: int = 30
    v4_max_trigger_age_minutes: int = 30


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


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


def _env_nonempty_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


def _normalize_fraction_threshold(value: float) -> float:
    # Accept both decimal form (0.7) and percentage-like form (70).
    if value > 1.0:
        return value / 100.0
    return value


def _env_csv_urls(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default or [])
    out: list[str] = []
    for part in raw.split(","):
        value = part.strip().rstrip("/")
        if value:
            out.append(value)
    return out


def _parse_positive_int(raw: str | None, *, env_name: str, default: int) -> int:
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        raise ValueError(f"{env_name} must be a positive integer; got empty value")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a positive integer; got '{value}'") from exc
    if parsed <= 0:
        raise ValueError(f"{env_name} must be a positive integer; got {parsed}")
    return parsed


def _normalize_symbol_list(raw: str, *, env_name: str, max_symbols: int) -> list[str]:
    symbols = [part.strip().upper() for part in raw.split(",")]
    symbols = [symbol for symbol in symbols if symbol]
    if not symbols:
        raise ValueError(f"{env_name} must contain at least 1 symbol")

    deduped: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(symbol)

    invalid = [symbol for symbol in deduped if not _SYMBOL_RE.match(symbol)]
    if invalid:
        raise ValueError(
            f"{env_name} contains invalid symbol(s): {', '.join(invalid)}"
        )
    if len(deduped) > max_symbols:
        raise ValueError(
            f"{env_name} supplied {len(deduped)} unique symbols; MAX_ANALYSIS_SYMBOLS is {max_symbols}"
        )
    return deduped


def _parse_analysis_symbols(analysis_raw: str | None, fixed_six_raw: str | None, *, max_symbols: int) -> list[str]:
    if analysis_raw is not None:
        return _normalize_symbol_list(
            analysis_raw,
            env_name=ANALYSIS_SYMBOLS_ENV_VAR,
            max_symbols=max_symbols,
        )
    if fixed_six_raw is not None:
        return _normalize_symbol_list(
            fixed_six_raw,
            env_name=FIXED_SIX_ENV_VAR,
            max_symbols=max_symbols,
        )
    return list(DEFAULT_FIXED_WATCHLIST)


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
    _, universe, weights = _load_watchlist_config(repo_root)
    email_to = os.getenv("EMAIL_TO", "raymond87tan@gmail.com")
    max_analysis_symbols = _parse_positive_int(
        os.getenv(MAX_ANALYSIS_SYMBOLS_ENV_VAR),
        env_name=MAX_ANALYSIS_SYMBOLS_ENV_VAR,
        default=DEFAULT_MAX_ANALYSIS_SYMBOLS,
    )
    fixed = _parse_analysis_symbols(
        os.getenv(ANALYSIS_SYMBOLS_ENV_VAR),
        os.getenv(FIXED_SIX_ENV_VAR),
        max_symbols=max_analysis_symbols,
    )

    news_max_age_hours = _env_int("NEWS_MAX_AGE_HOURS", _env_int("NEWS_LOOKBACK_HOURS", 24))

    return AppConfig(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        resend_api_key=os.getenv("RESEND_API_KEY"),
        email_from=os.getenv("EMAIL_FROM"),
        email_to=email_to,
        send_email=_env_flag("SEND_EMAIL", True),
        data_provider=os.getenv("DATA_PROVIDER", "yfinance"),
        news_provider=os.getenv("NEWS_PROVIDER", "yfinance"),
        fixed_watchlist=fixed,
        candidate_universe=universe,
        score_weights=weights,
        schedule_utc_cron=os.getenv("DAILY_REPORT_CRON_UTC", "0 0 * * 1-5"),
        min_setup_score=_env_int("MIN_SETUP_SCORE", 60),
        min_relative_volume=_env_float("MIN_RELATIVE_VOLUME", 1.15),
        day_trade_threshold=_env_int("DAY_TRADE_THRESHOLD", 72),
        short_threshold=_normalize_fraction_threshold(_env_float("SHORT_THRESHOLD", 0.28)),
        long_threshold=_normalize_fraction_threshold(_env_float("LONG_THRESHOLD", 0.28)),
        dynamic_count=_env_int("DYNAMIC_COUNT", 3),
        day_trade_gap_threshold=_env_float("DAY_TRADE_GAP_THRESHOLD", 3.0),
        day_trade_rvol_threshold=_env_float("DAY_TRADE_RVOL_THRESHOLD", 1.5),
        day_trade_min_setup_score=_env_int("DAY_TRADE_MIN_SETUP_SCORE", 65),
        morning_report_time=_env_nonempty_str("MORNING_REPORT_TIME", "08:00"),
        morning_report_timezone=_env_nonempty_str("MORNING_REPORT_TIMEZONE", "Asia/Kuala_Lumpur"),
        live_alert_enabled=_env_flag("LIVE_ALERT_ENABLED", True),
        live_alert_interval_minutes=_env_int("LIVE_ALERT_INTERVAL_MINUTES", 5),
        live_market_timezone=_env_nonempty_str("LIVE_MARKET_TIMEZONE", "America/New_York"),
        live_market_open=_env_nonempty_str("LIVE_MARKET_OPEN", "09:30"),
        live_market_close=_env_nonempty_str("LIVE_MARKET_CLOSE", "16:00"),
        alert_min_setup_score=_env_int("ALERT_MIN_SETUP_SCORE", 70),
        alert_min_rvol=_env_float("ALERT_MIN_RVOL", 1.5),
        alert_cooldown_minutes=_env_int("ALERT_COOLDOWN_MINUTES", 15),
        telegram_enabled=_env_flag("TELEGRAM_ENABLED", False),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        database_enabled=_env_flag("DATABASE_ENABLED", True),
        database_url=os.getenv("DATABASE_URL"),
        enable_news=_env_flag("ENABLE_NEWS", True),
        news_lookback_hours=news_max_age_hours,
        news_max_age_hours=news_max_age_hours,
        searxng_base_urls=_env_csv_urls("SEARXNG_BASE_URLS", []),
        searxng_public_instances_enabled=_env_flag("SEARXNG_PUBLIC_INSTANCES_ENABLED", True),
        searxng_timeout_seconds=_env_int("SEARXNG_TIMEOUT_SECONDS", 8),
        enable_outcome_tracking=_env_flag("ENABLE_OUTCOME_TRACKING", True),
        enable_backtest=_env_flag("ENABLE_BACKTEST", True),
        signal_db_path=_env_nonempty_str("SIGNAL_DB_PATH", "artifacts/signal_history.db"),
        signal_expiry_hours=_env_int("SIGNAL_EXPIRY_HOURS", 48),
        live_data_provider=_env_nonempty_str("LIVE_DATA_PROVIDER", "yfinance"),
        ai_primary_provider=_env_nonempty_str("AI_PRIMARY_PROVIDER", "openai"),
        ai_fallback_provider=_env_nonempty_str("AI_FALLBACK_PROVIDER", "gemini"),
        v4_opening_start=_env_nonempty_str("V4_OPENING_START", "09:30"),
        v4_opening_end=_env_nonempty_str("V4_OPENING_END", "10:00"),
        v4_opening_min_rvol=_env_float("V4_OPENING_MIN_RVOL", 1.20),
        v4_opening_min_setup_score=_env_int("V4_OPENING_MIN_SETUP_SCORE", 75),
        v4_normal_min_rvol=_env_float("V4_NORMAL_MIN_RVOL", 1.50),
        v4_normal_min_setup_score=_env_int("V4_NORMAL_MIN_SETUP_SCORE", 70),
        v4_opening_range_minutes=_env_int("V4_OPENING_RANGE_MINUTES", 30),
        v4_max_trigger_age_minutes=_env_int("V4_MAX_TRIGGER_AGE_MINUTES", 30),
    )
