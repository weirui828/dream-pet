"""The pet's schedule: night is [bedtime, wake_time) in the pet's timezone (may wrap midnight)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dreampet.clock import at_local, next_local, parse_hhmm


def _minutes(hhmm: str) -> int:
    h, m = parse_hhmm(hhmm)
    return h * 60 + m


def in_window(t: datetime, start: str, end: str, tz: ZoneInfo) -> bool:
    local = t.astimezone(tz)
    x = local.hour * 60 + local.minute
    a, b = _minutes(start), _minutes(end)
    if a == b:
        return False
    return a <= x < b if a < b else (x >= a or x < b)


def is_night(t: datetime, bedtime: str, wake: str, tz: ZoneInfo) -> bool:
    return in_window(t, bedtime, wake, tz)


def night_start(t: datetime, bedtime: str, tz: ZoneInfo) -> datetime:
    """The most recent bedtime instant at or before t."""
    b = at_local(t, bedtime, tz)
    return b if b <= t else at_local(t - timedelta(days=1), bedtime, tz)


def night_id(t: datetime, bedtime: str, tz: ZoneInfo) -> str:
    """The local date of the evening the current night began."""
    return night_start(t, bedtime, tz).astimezone(tz).date().isoformat()


def next_bedtime(t: datetime, bedtime: str, tz: ZoneInfo) -> datetime:
    return next_local(t, bedtime, tz)
