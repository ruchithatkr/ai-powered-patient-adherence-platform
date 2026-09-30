"""Injectable time source so scheduling, expiry and quiet-hour logic are deterministic in tests."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> None:
        self._at += timedelta(**kwargs)
