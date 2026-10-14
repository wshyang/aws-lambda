"""Shared fixtures.

Time is always injected: no test depends on today's date or on the wall clock
(CLAUDE-testing.md §4). The two moments below are the October 2026 patch day at
16:05 and 20:05 Singapore time.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from sap_patch_day.models import PatchDayMonth
from tests.support.harness import Harness, build_harness
from tests.support.pages import OCTOBER_2026, fixture, page_map

WINDOW_MOMENT = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
FINAL_MOMENT = datetime(2026, 10, 13, 12, 5, tzinfo=UTC)
WATCHDOG_MOMENT = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run the tests that reach the real support.sap.com pages",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the live suite unless it was asked for explicitly."""
    if config.getoption("--live") or os.environ.get("SAP_PATCH_DAY_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: run with --live (needs egress to support.sap.com)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def target() -> PatchDayMonth:
    return OCTOBER_2026


@pytest.fixture
def window_moment() -> datetime:
    """The first attempt of the retry window (16:05 SGT)."""
    return WINDOW_MOMENT


@pytest.fixture
def final_moment() -> datetime:
    """An attempt past the retry window end (20:05 SGT)."""
    return FINAL_MOMENT


@pytest.fixture
def watchdog_moment() -> datetime:
    """The second Wednesday at 09:00 SGT."""
    return WATCHDOG_MOMENT


@pytest.fixture
def bulletin_html() -> str:
    return fixture("october_2026.html")


@pytest.fixture
def index_html() -> str:
    return fixture("security_notes_news_index.html")


@pytest.fixture
def pages() -> Mapping[str, str]:
    return page_map()


@pytest.fixture
def harness(pages: Mapping[str, str], window_moment: datetime) -> Harness:
    return build_harness(pages, window_moment)
