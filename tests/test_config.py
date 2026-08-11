from pathlib import Path
from zoneinfo import ZoneInfo

from src.daily_stock_analyse.config import load_config
from src.daily_stock_analyse.market_hours import _parse_hhmm


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


def test_morning_timezone_missing_uses_kuala_lumpur(monkeypatch):
    monkeypatch.delenv("MORNING_REPORT_TIMEZONE", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.morning_report_timezone == "Asia/Kuala_Lumpur"
    ZoneInfo(cfg.morning_report_timezone)


def test_morning_timezone_explicit_kuala_lumpur(monkeypatch):
    monkeypatch.setenv("MORNING_REPORT_TIMEZONE", "Asia/Kuala_Lumpur")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.morning_report_timezone == "Asia/Kuala_Lumpur"
    ZoneInfo(cfg.morning_report_timezone)


def test_morning_timezone_empty_falls_back_to_kuala_lumpur(monkeypatch):
    monkeypatch.setenv("MORNING_REPORT_TIMEZONE", "")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.morning_report_timezone == "Asia/Kuala_Lumpur"
    ZoneInfo(cfg.morning_report_timezone)


def test_live_market_timezone_missing_uses_new_york(monkeypatch):
    monkeypatch.delenv("LIVE_MARKET_TIMEZONE", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_timezone == "America/New_York"
    ZoneInfo(cfg.live_market_timezone)


def test_live_market_timezone_empty_uses_new_york(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_TIMEZONE", "")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_timezone == "America/New_York"
    ZoneInfo(cfg.live_market_timezone)


def test_live_market_timezone_explicit_new_york(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_TIMEZONE", "America/New_York")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_timezone == "America/New_York"
    ZoneInfo(cfg.live_market_timezone)


def test_live_market_open_missing_uses_0930(monkeypatch):
    monkeypatch.delenv("LIVE_MARKET_OPEN", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_open == "09:30"
    assert _parse_hhmm(cfg.live_market_open).hour == 9
    assert _parse_hhmm(cfg.live_market_open).minute == 30


def test_live_market_open_empty_uses_0930(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_OPEN", "")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_open == "09:30"
    assert _parse_hhmm(cfg.live_market_open).hour == 9
    assert _parse_hhmm(cfg.live_market_open).minute == 30


def test_live_market_open_whitespace_uses_0930(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_OPEN", "   ")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_open == "09:30"
    assert _parse_hhmm(cfg.live_market_open).hour == 9
    assert _parse_hhmm(cfg.live_market_open).minute == 30


def test_live_market_open_explicit_0930_preserved(monkeypatch):
    monkeypatch.setenv("LIVE_MARKET_OPEN", "09:30")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_open == "09:30"
    assert _parse_hhmm(cfg.live_market_open).hour == 9
    assert _parse_hhmm(cfg.live_market_open).minute == 30


def test_live_alert_enabled_missing_uses_default_true(monkeypatch):
    monkeypatch.delenv("LIVE_ALERT_ENABLED", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_alert_enabled is True


def test_live_alert_enabled_empty_uses_default_true(monkeypatch):
    monkeypatch.setenv("LIVE_ALERT_ENABLED", "")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_alert_enabled is True


def test_live_alert_interval_default_is_5(monkeypatch):
    monkeypatch.delenv("LIVE_ALERT_INTERVAL_MINUTES", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_alert_interval_minutes == 5


def test_live_market_close_default_is_1600(monkeypatch):
    monkeypatch.delenv("LIVE_MARKET_CLOSE", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_market_close == "16:00"


def test_live_data_provider_default_is_yfinance(monkeypatch):
    monkeypatch.delenv("LIVE_DATA_PROVIDER", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_data_provider == "yfinance"


def test_live_data_provider_explicit_yfinance(monkeypatch):
    monkeypatch.setenv("LIVE_DATA_PROVIDER", "yfinance")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.live_data_provider == "yfinance"


def test_ai_provider_defaults(monkeypatch):
    monkeypatch.delenv("AI_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("AI_FALLBACK_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.ai_primary_provider == "openai"
    assert cfg.ai_fallback_provider == "gemini"
    assert cfg.gemini_api_key is None


def test_fixed_six_symbols_missing_uses_current_defaults(monkeypatch):
    monkeypatch.delenv("ANALYSIS_SYMBOLS", raising=False)
    monkeypatch.delenv("FIXED_SIX_SYMBOLS", raising=False)
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA", "INTC", "SNDK", "SKHY"]


def test_analysis_symbols_parses_commas_and_whitespace(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", " nok, AMD , nvda, INTC, sndk , skhy ")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA", "INTC", "SNDK", "SKHY"]


def test_analysis_symbols_ignores_empty_entries(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "NOK,,AMD, ,NVDA")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA"]


def test_analysis_symbols_more_than_six_is_allowed(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "NOK,AMD,NVDA,INTC,SNDK,SKHY,AMAT,PANW,DDOG")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA", "INTC", "SNDK", "SKHY", "AMAT", "PANW", "DDOG"]


def test_analysis_symbols_fewer_than_six_is_allowed(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "NOK,AMD,NVDA")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA"]


def test_analysis_symbols_deduplicates_preserving_first_occurrence(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "NOK,AMD,NOK,amd,NVDA")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA"]


def test_analysis_symbols_empty_value_fails_clearly(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "   ")
    try:
        load_config(Path(__file__).resolve().parents[1])
    except ValueError as exc:
        assert "ANALYSIS_SYMBOLS must contain at least 1 symbol" in str(exc)
    else:
        raise AssertionError("Expected ANALYSIS_SYMBOLS empty-list validation error")


def test_max_analysis_symbols_accepts_exact_max(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "NOK,AMD,NVDA,INTC,SNDK,SKHY,AMAT,PANW,DDOG,CRM")
    monkeypatch.setenv("MAX_ANALYSIS_SYMBOLS", "10")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert len(cfg.fixed_watchlist) == 10


def test_max_analysis_symbols_rejects_more_than_max(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "NOK,AMD,NVDA,INTC,SNDK,SKHY,AMAT,PANW,DDOG,CRM,MU")
    monkeypatch.setenv("MAX_ANALYSIS_SYMBOLS", "10")
    try:
        load_config(Path(__file__).resolve().parents[1])
    except ValueError as exc:
        assert "ANALYSIS_SYMBOLS supplied 11 unique symbols; MAX_ANALYSIS_SYMBOLS is 10" in str(exc)
    else:
        raise AssertionError("Expected MAX_ANALYSIS_SYMBOLS limit validation error")


def test_invalid_max_analysis_symbols_values_fail_clearly(monkeypatch):
    for raw in ["abc", "0", "-1"]:
        monkeypatch.setenv("MAX_ANALYSIS_SYMBOLS", raw)
        monkeypatch.delenv("ANALYSIS_SYMBOLS", raising=False)
        monkeypatch.delenv("FIXED_SIX_SYMBOLS", raising=False)
        try:
            load_config(Path(__file__).resolve().parents[1])
        except ValueError as exc:
            assert "MAX_ANALYSIS_SYMBOLS must be a positive integer" in str(exc)
        else:
            raise AssertionError("Expected MAX_ANALYSIS_SYMBOLS validation error")


def test_fixed_six_symbols_used_when_analysis_symbols_missing(monkeypatch):
    monkeypatch.delenv("ANALYSIS_SYMBOLS", raising=False)
    monkeypatch.setenv("FIXED_SIX_SYMBOLS", "NOK,AMD,NVDA")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["NOK", "AMD", "NVDA"]


def test_analysis_symbols_takes_precedence_over_fixed_six_symbols(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SYMBOLS", "AMAT,PANW,DDOG")
    monkeypatch.setenv("FIXED_SIX_SYMBOLS", "NOK,AMD,NVDA,INTC,SNDK,SKHY")
    cfg = load_config(Path(__file__).resolve().parents[1])
    assert cfg.fixed_watchlist == ["AMAT", "PANW", "DDOG"]
