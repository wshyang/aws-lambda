"""Singapore-time arithmetic and patch-day derivation (spec FR-1, FR-3.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from sap_patch_day.clock import (
    SINGAPORE,
    FixedClock,
    attempt_label,
    is_final_attempt,
    patch_day_month,
    second_tuesday,
    singapore_stamp,
    to_singapore,
)
from sap_patch_day.models import PatchDayMonth

# The dates SAP published on the Security Notes & News index page for 2026.
PUBLISHED_PATCH_DAYS_2026 = {
    1: 13,
    2: 10,
    3: 10,
    4: 14,
    5: 12,
    6: 9,
    7: 14,
    8: 11,
    9: 8,
    10: 13,
    11: 10,
    12: 8,
}


@pytest.mark.parametrize("month,expected_day", sorted(PUBLISHED_PATCH_DAYS_2026.items()))
def test_second_tuesday_matches_saps_published_dates(month: int, expected_day: int) -> None:
    assert second_tuesday(2026, month) == date(2026, month, expected_day)


def test_second_tuesday_is_always_the_eighth_to_fourteenth() -> None:
    for year in (2025, 2026, 2027):
        for month in range(1, 13):
            day = second_tuesday(year, month).day
            assert 8 <= day <= 14


def test_singapore_conversion_from_utc() -> None:
    moment = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
    assert to_singapore(moment) == datetime(2026, 10, 13, 16, 5, tzinfo=SINGAPORE)


def test_patch_day_month_uses_singapore_not_utc() -> None:
    """23:30 UTC on 31 October is already November in Singapore."""
    moment = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
    assert patch_day_month(moment) == PatchDayMonth(year=2026, month=11)


def test_patch_day_month_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        patch_day_month(datetime(2026, 10, 13, 16, 0))


def test_is_final_attempt_boundaries() -> None:
    window_end = time(20, 0)
    assert not is_final_attempt(datetime(2026, 10, 13, 11, 59, tzinfo=UTC), window_end)
    assert is_final_attempt(datetime(2026, 10, 13, 12, 0, tzinfo=UTC), window_end)
    assert is_final_attempt(datetime(2026, 10, 13, 12, 30, tzinfo=UTC), window_end)


def test_attempt_label() -> None:
    window_end = time(20, 0)
    assert attempt_label(datetime(2026, 10, 13, 8, 5, tzinfo=UTC), window_end) == "window"
    assert attempt_label(datetime(2026, 10, 13, 12, 30, tzinfo=UTC), window_end) == "final"


def test_singapore_stamp_is_human_readable() -> None:
    moment = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
    assert singapore_stamp(moment) == "13 Oct 2026, 16:05 SGT"


def test_fixed_clock_requires_a_timezone() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 10, 13, 16, 0))


def test_fixed_clock_returns_the_same_moment_regardless_of_representation() -> None:
    moment = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
    alternate = moment.astimezone(timezone(timedelta(hours=-5)))
    assert FixedClock(moment).now() == FixedClock(alternate).now()
