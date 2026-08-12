from __future__ import annotations

from datetime import time


def is_time_in_window(value: time, start: time, end: time, *, include_end: bool = False) -> bool:
    """Return whether a local clock time falls inside a window.

    Supports both same-day windows (e.g. 16:00-20:00) and windows that cross
    midnight (e.g. 20:00-04:00).
    """
    if start == end:
        return True
    if start < end:
        return start <= value <= end if include_end else start <= value < end
    # Cross-midnight: 20:00-04:00 means 20:00-23:59 and 00:00-04:00.
    return value >= start or (value <= end if include_end else value < end)
