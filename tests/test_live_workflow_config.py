from pathlib import Path


def test_live_workflow_explicitly_enables_alert_engine_and_telegram():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "live_stock_alerts.yml").read_text(encoding="utf-8")

    assert 'LIVE_ALERT_ENABLED: "1"' in wf
    assert 'LIVE_MARKET_TIMEZONE: "America/New_York"' in wf
    assert 'LIVE_MARKET_OPEN: "09:30"' in wf
    assert 'LIVE_MARKET_CLOSE: "16:00"' in wf
    assert 'LIVE_ALERT_INTERVAL_MINUTES: "5"' in wf
    assert 'TELEGRAM_ENABLED: "1"' in wf
    assert 'TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}' in wf
    assert 'TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}' in wf
    assert 'ANALYSIS_SYMBOLS: ${{ vars.ANALYSIS_SYMBOLS }}' in wf
    assert 'MAX_ANALYSIS_SYMBOLS: ${{ vars.MAX_ANALYSIS_SYMBOLS }}' in wf
    assert 'FIXED_SIX_SYMBOLS: ${{ vars.FIXED_SIX_SYMBOLS }}' in wf
    assert 'GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}' in wf


def test_live_workflow_uses_safe_alert_threshold_defaults():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "live_stock_alerts.yml").read_text(encoding="utf-8")

    assert 'ALERT_MIN_SETUP_SCORE: "70"' in wf
    assert 'ALERT_MIN_RVOL: "1.5"' in wf
    assert 'ALERT_COOLDOWN_MINUTES: "15"' in wf


def test_live_workflow_uses_session_start_schedule_not_every_5_minutes():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "live_stock_alerts.yml").read_text(encoding="utf-8")

    assert 'cron: "*/5 * * * 1-5"' not in wf
    assert 'cron: "25 13 * * 1-5"' in wf
    assert 'cron: "30 19 * * 1-5"' in wf


def test_live_workflow_has_non_cancelling_concurrency_guard():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "live_stock_alerts.yml").read_text(encoding="utf-8")

    assert 'group: live-stock-alerts-${{ github.ref_name }}' in wf
    assert 'cancel-in-progress: false' in wf


def test_daily_workflow_supports_dynamic_analysis_symbol_variables():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily_stock_analysis.yml").read_text(encoding="utf-8")

    assert 'ANALYSIS_SYMBOLS: ${{ vars.ANALYSIS_SYMBOLS }}' in wf
    assert 'MAX_ANALYSIS_SYMBOLS: ${{ vars.MAX_ANALYSIS_SYMBOLS }}' in wf
    assert 'FIXED_SIX_SYMBOLS: ${{ vars.FIXED_SIX_SYMBOLS }}' in wf


def test_daily_workflow_maps_neon_secret_to_database_url():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily_stock_analysis.yml").read_text(encoding="utf-8")

    assert 'DATABASE_ENABLED: ${{ vars.DATABASE_ENABLED }}' in wf
    assert 'DATABASE_URL: ${{ secrets.NEON_DATABASE_URL }}' in wf


def test_neon_validation_workflow_is_manual_and_uses_secret_url():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "neon_postgres_validation.yml").read_text(encoding="utf-8")

    assert 'workflow_dispatch:' in wf
    assert 'DATABASE_URL: ${{ secrets.NEON_DATABASE_URL }}' in wf
    assert 'DATABASE_ENABLED: "1"' in wf
    assert 'python -m src.daily_stock_analyse.database.neon_smoke' in wf
