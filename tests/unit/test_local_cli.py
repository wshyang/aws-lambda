"""Unit tests for the offline dry run: `python -m sap_patch_day.local_cli`.

The command ships inside the artefact, so it is tested like the rest of the
package. It runs the same `execute_request` path as the deployed function
(spec FR-3.9), which is why these tests assert what it prints and writes rather
than re-checking the delivery rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sap_patch_day import local_cli
from sap_patch_day.httpclient import StaticHttpClient, UrllibHttpClient
from sap_patch_day.mailer import LoggingMailer
from sap_patch_day.models import PatchDayMonth
from sap_patch_day.state import InMemoryStateStore
from tests.support.pages import FIXTURE_DIR

OCTOBER_FIXTURE = FIXTURE_DIR / "october_2026.html"
INDEX_FIXTURE = FIXTURE_DIR / "security_notes_news_index.html"


def options(*arguments: str) -> local_cli.argparse.Namespace:
    return local_cli._parse_args(list(arguments))


def test_the_month_is_inferred_from_the_file_name() -> None:
    assert local_cli._target_month(options("--html-file", str(OCTOBER_FIXTURE))) == PatchDayMonth(
        year=2026, month=10
    )


def test_the_month_can_be_given_explicitly() -> None:
    assert local_cli._target_month(options("--target", "2027-03")) == PatchDayMonth(
        year=2027, month=3
    )


def test_an_uninferable_file_name_asks_for_the_month_instead_of_guessing() -> None:
    with pytest.raises(SystemExit, match="pass --target YYYY-MM"):
        local_cli._target_month(options("--html-file", "snapshot.html"))


def test_the_page_map_uses_the_canonical_and_index_urls() -> None:
    pages = local_cli._pages(
        options("--html-file", str(OCTOBER_FIXTURE), "--index-file", str(INDEX_FIXTURE))
    )
    assert len(pages) == 2
    assert any(url.endswith("october-2026.html") for url in pages)


def test_every_saved_page_needs_a_target_before_it_is_read(tmp_path: Path) -> None:
    saved = tmp_path / "snapshot.html"
    saved.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(SystemExit):
        local_cli._pages(options("--html-file", str(saved)))


def test_saved_pages_are_replayed_and_nothing_is_sent(tmp_path: Path) -> None:
    run = local_cli._run_context(
        options("--html-file", str(OCTOBER_FIXTURE), "--index-file", str(INDEX_FIXTURE))
    )
    assert isinstance(run.http, StaticHttpClient)
    assert isinstance(run.mailer, LoggingMailer)
    assert isinstance(run.state, InMemoryStateStore)


def test_live_mode_uses_the_real_transport() -> None:
    run = local_cli._run_context(options("--live", "--target", "2026-10"))
    assert isinstance(run.http, UrllibHttpClient)


def test_the_configuration_comes_from_the_command_line_not_from_aws() -> None:
    run = local_cli._run_context(
        options(
            "--target",
            "2026-10",
            "--sender",
            "patch-day@example.test",
            "--recipients",
            "reader@example.test",
        )
    )
    assert run.config.sender == "patch-day@example.test"
    assert run.config.recipients == ("reader@example.test",)


def test_a_dry_run_prints_the_result_and_writes_the_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = local_cli.main(
        [
            "--html-file",
            str(OCTOBER_FIXTURE),
            "--index-file",
            str(INDEX_FIXTURE),
            "--target",
            "2026-10",
            "--out-dir",
            str(tmp_path),
            "--preview",
        ]
    )
    assert exit_code == 0

    printed = capsys.readouterr().out
    result = json.loads(printed[: printed.index("\npreview written")])
    assert result["status"] == "delivered"
    assert result["note_count"] == 21
    assert result["message_id"] == "dry-run:no-message-id"

    html = (tmp_path / "preview-2026-10.html").read_text(encoding="utf-8")
    assert "OAuth2 SSRF vulnerability in SAP Cloud SDK for Python" in html
    assert "3767634" in html
    text = (tmp_path / "preview-2026-10.txt").read_text(encoding="utf-8")
    assert "SAP Security Patch Day — October 2026" in text


def test_a_preview_reports_when_there_is_nothing_to_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "pages"
    empty.mkdir()
    exit_code = local_cli.main(["--target", "2026-11", "--out-dir", str(empty), "--preview"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"status": "not-published-yet"' in output
    assert "nothing to preview" in output
    assert not list(empty.glob("preview-*"))


def test_the_dry_run_does_not_run_the_watchdog_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    local_cli.main(["--target", "2026-10"])
    assert '"run_mode": "delivery"' not in capsys.readouterr().out
