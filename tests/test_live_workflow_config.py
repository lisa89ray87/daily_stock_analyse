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


def test_live_workflow_uses_safe_alert_threshold_defaults():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "live_stock_alerts.yml").read_text(encoding="utf-8")

    assert 'ALERT_MIN_SETUP_SCORE: "70"' in wf
    assert 'ALERT_MIN_RVOL: "1.5"' in wf
    assert 'ALERT_COOLDOWN_MINUTES: "15"' in wf
