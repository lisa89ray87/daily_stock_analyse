from __future__ import annotations

import html
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, time as dt_time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import yfinance as yf

from .config import load_config
from .event_alerts import EventAlert, detect_event_alerts
from .market_hours import get_market_session_status, is_weekday_in_timezone, utc_now
from .providers import create_market_data_provider
from .session_windows import is_time_in_window
from .telegram_provider import TelegramBotProvider


def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

