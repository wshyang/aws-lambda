"""Fixture loading and page-map helpers for tests and the dry run."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping

from sap_patch_day.models import PatchDayMonth
from sap_patch_day.probe import INDEX_URL, canonical_url

FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

OCTOBER_2026 = PatchDayMonth(year=2026, month=10)


def fixture(name: str) -> str:
    """The contents of ``tests/fixtures/<name>``."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def page_map(
    bulletin: str = "october_2026.html",
    target: PatchDayMonth = OCTOBER_2026,
    index: str | None = "security_notes_news_index.html",
) -> Mapping[str, str]:
    """A URL-to-body map for :class:`~sap_patch_day.httpclient.StaticHttpClient`.

    URLs that are not in the map answer 404, which is exactly how a test
    expresses "SAP has not published this month yet".
    """
    pages = {canonical_url(target): fixture(bulletin)}
    if index is not None:
        pages[INDEX_URL] = fixture(index)
    return pages
