"""Time handling, always explicit about Singapore time.

The schedule is defined in UTC by EventBridge, while every decision that a
human cares about ("is this the final attempt?", "which month is it?") is made
in Singapore time (UTC+08:00, no daylight saving). Keeping that conversion in
one module is what stops off-by-eight-hours bugs from reappearing in every
caller.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Protocol

from .models import PatchDayMonth

SINGAPORE_OFFSET = timedelta(hours=8)
SINGAPORE = timezone(SINGAPORE_OFFSET, name="Asia/Singapore")
SINGAPORE_TZ_LABEL = "SGT"


class Clock(Protocol):
    """Injected time source: tests never depend on the wall clock."""

    def now(self) -> datetime:
        """Current moment, timezone-aware, in UTC."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class FixedClock:
    """A clock pinned to one moment, for tests and dry runs."""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware moment")
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


def to_singapore(moment: datetime) -> datetime:
    """Convert any aware datetime to Singapore time."""
    if moment.tzinfo is None:
        raise ValueError("to_singapore requires a timezone-aware datetime")
    return moment.astimezone(SINGAPORE)


def second_tuesday(year: int, month: int) -> date:
    """The second Tuesday of a month — SAP's Security Patch Day."""
    first_of_month = date(year, month, 1)
    days_until_tuesday = (1 - first_of_month.weekday()) % 7
    first_tuesday = first_of_month + timedelta(days=days_until_tuesday)
    return first_tuesday + timedelta(days=7)


def patch_day_month(moment: datetime) -> PatchDayMonth:
    """The patch-day month a moment belongs to, in Singapore time."""
    local = to_singapore(moment)
    return PatchDayMonth(year=local.year, month=local.month)


def is_final_attempt(moment: datetime, window_end: time) -> bool:
    """Whether ``moment`` is at or past the retry window's end, in Singapore time."""
    return to_singapore(moment).time() >= window_end


def attempt_label(moment: datetime, window_end: time) -> str:
    """``final`` once the window has closed, otherwise ``window``."""
    return "final" if is_final_attempt(moment, window_end) else "window"


def singapore_stamp(moment: datetime) -> str:
    """Human-readable timestamp for emails: ``13 Oct 2026, 16:05 SGT``."""
    local = to_singapore(moment)
    return local.strftime("%d %b %Y, %H:%M") + f" {SINGAPORE_TZ_LABEL}"
