"""Clock abstraction (CON-010): business logic never reads the OS clock directly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimulatedClock:
    """Clock driven by replayed data; time may never move backwards."""

    def __init__(self, start: datetime):
        _require_utc(start)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, instant: datetime) -> None:
        _require_utc(instant)
        if instant < self._now:
            raise ValueError(f"simulated time cannot move backwards: {instant} < {self._now}")
        self._now = instant


def _require_utc(instant: datetime) -> None:
    if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(None):
        raise ValueError("timestamps must be timezone-aware UTC (CON-023)")
