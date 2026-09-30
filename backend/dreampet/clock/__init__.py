"""Time source for the whole app.

No other module reads wall time directly (enforced by a ruff banned-api rule and a test).
`RealClock` wraps wall time; `SimClock` advances in fixed steps or as fast as possible,
which is what makes simulated days reproducible.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...

    def sleep_until(self, t: datetime, stop: threading.Event | None = None) -> None:
        """Block (really or virtually) until `t`, or until `stop` is set."""
        ...

    def spend(self, duration: timedelta) -> None:
        """Account for an action taking `duration`. Sim clocks advance; real clocks do nothing
        because real work already took real time."""
        ...

    @property
    def simulated(self) -> bool: ...


class RealClock:
    simulated = False

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep_until(self, t: datetime, stop: threading.Event | None = None) -> None:
        while True:
            remaining = (t - self.now()).total_seconds()
            if remaining <= 0:
                return
            if stop is not None:
                if stop.wait(min(remaining, 1.0)):
                    return
            else:
                time.sleep(min(remaining, 1.0))

    def spend(self, duration: timedelta) -> None:
        return None


class SimClock:
    """A virtual clock.

    speed=None runs as fast as possible. speed=60.0 means one real second is one sim minute.
    """

    simulated = True

    def __init__(self, start: datetime, speed: float | None = None):
        if start.tzinfo is None:
            raise ValueError("SimClock start must be timezone-aware")
        self._t = start.astimezone(UTC)
        self.speed = speed
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._t

    def advance(self, duration: timedelta) -> None:
        with self._lock:
            self._t = self._t + duration

    def spend(self, duration: timedelta) -> None:
        self.advance(duration)

    STEP_REAL_SECONDS = 0.25

    def sleep_until(self, t: datetime, stop: threading.Event | None = None) -> None:
        # Advance in small real-time steps, re-reading `speed` each step, so a speed change
        # takes effect immediately and now() moves smoothly while waiting.
        while True:
            with self._lock:
                remaining = (t - self._t).total_seconds()
            if remaining <= 0:
                return
            speed = self.speed
            if not speed:
                break
            step = min(remaining, speed * self.STEP_REAL_SECONDS)
            if stop is not None:
                if stop.wait(step / speed):
                    return
            else:
                time.sleep(step / speed)
            self.advance(timedelta(seconds=step))
        with self._lock:
            if t > self._t:
                self._t = t


def wall_monotonic() -> float:
    """Monotonic seconds for measuring real durations (timeouts, backoff), never stored."""
    return time.monotonic()


def real_sleep(seconds: float) -> None:
    """Real blocking sleep, used only for network polling backoff."""
    time.sleep(seconds)


def parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def at_local(day_start: datetime, hhmm: str, tz) -> datetime:
    """The UTC instant for `hhmm` local time on the local calendar day of `day_start`."""
    local = day_start.astimezone(tz)
    h, m = parse_hhmm(hhmm)
    return local.replace(hour=h, minute=m, second=0, microsecond=0).astimezone(UTC)


def next_local(after: datetime, hhmm: str, tz) -> datetime:
    """The next UTC instant strictly after `after` at local time `hhmm`."""
    cand = at_local(after, hhmm, tz)
    if cand <= after:
        cand = at_local(after + timedelta(days=1), hhmm, tz)
    return cand
