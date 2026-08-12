from datetime import time

from src.daily_stock_analyse.session_windows import is_time_in_window


def test_same_day_window():
    assert is_time_in_window(time(17, 0), time(16, 0), time(20, 0))
    assert not is_time_in_window(time(20, 0), time(16, 0), time(20, 0))


def test_overnight_window_crosses_midnight():
    start = time(20, 0)
    end = time(4, 0)
    assert is_time_in_window(time(23, 29), start, end)
    assert is_time_in_window(time(0, 30), start, end)
    assert is_time_in_window(time(3, 59), start, end)
    assert not is_time_in_window(time(4, 0), start, end)
    assert not is_time_in_window(time(19, 59), start, end)


def test_overnight_window_can_include_endpoint():
    assert is_time_in_window(time(4, 0), time(20, 0), time(4, 0), include_end=True)
