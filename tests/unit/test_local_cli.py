"""Unit tests for the offline dry run: `python -m sap_patch_day.local_cli`.

The command ships inside the artefact, so it is tested like the rest of the
package. It runs the same `execute_request` path as the deployed function
(spec FR-3.9), which is why these tests assert what it prints and writes rather
than re-checking the delivery rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from sap_patch_day import local_cli
from sap_patch_day.httpclient import StaticHttpClient, UrllibHttpClient
from sap_patch_day.mailer import LoggingMailer, SesMailer
from sap_patch_day.models import PatchDayMonth
from sap_patch_day.state import InMemoryStateStore, SsmStateStore
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


def test_without_an_endpoint_the_mailer_only_logs() -> None:
    assert isinstance(local_cli._mailer("", "sender@example.test"), LoggingMailer)


def test_the_local_client_targets_the_given_endpoint() -> None:
    client = local_cli._local_client("sesv2", "http://localhost:4566")
    assert client.meta.endpoint_url == "http://localhost:4566"


def test_an_endpoint_selects_the_real_ses_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the network call to an emulator that is not running here is replaced.
    monkeypatch.setattr(local_cli, "_verify_sender", lambda *_: None)
    mailer = local_cli._mailer("http://localhost:4566", "sender@example.test")
    assert isinstance(mailer, SesMailer)


def test_the_sender_identity_is_created_so_the_local_ses_accepts_it() -> None:
    client = stubbed_sesv2()
    with Stubber(client) as stubber:
        stubber.add_response("create_email_identity", {}, {"EmailIdentity": "a@example.test"})
        local_cli._verify_sender(client, "a@example.test", ENDPOINT)
        stubber.assert_no_pending_responses()


def test_an_already_verified_sender_is_not_an_error() -> None:
    client = stubbed_sesv2()
    with Stubber(client) as stubber:
        stubber.add_client_error("create_email_identity", "AlreadyExistsException")
        local_cli._verify_sender(client, "a@example.test", ENDPOINT)


def test_any_other_identity_failure_is_not_swallowed() -> None:
    client = stubbed_sesv2()
    with Stubber(client) as stubber:
        stubber.add_client_error("create_email_identity", "BadRequestException")
        with pytest.raises(ClientError):
            local_cli._verify_sender(client, "a@example.test", ENDPOINT)


ENDPOINT = "http://localhost:4566"


def stubbed_sesv2() -> BaseClient:
    return boto3.client(
        "sesv2",
        region_name="ap-southeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_an_unreachable_endpoint_says_how_to_start_it() -> None:
    client = local_cli._local_client("sesv2", "http://127.0.0.1:9")
    with pytest.raises(SystemExit, match="is the local emulator running"):
        local_cli._verify_sender(client, "a@example.test", "http://127.0.0.1:9")


def stubbed_ssm() -> BaseClient:
    return boto3.client(
        "ssm",
        region_name="ap-southeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_seeding_writes_the_names_and_types_the_deployment_scripts_write() -> None:
    client = stubbed_ssm()
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_parameter",
            {},
            {"Name": "/p/sender", "Value": "a@example.test", "Type": "String", "Overwrite": True},
        )
        stubber.add_response(
            "put_parameter",
            {},
            {
                "Name": "/p/recipients",
                "Value": "b@example.test,c@example.test",
                "Type": "StringList",
                "Overwrite": True,
            },
        )
        local_cli._seed_ssm(
            client,
            "/p",
            {"sender": "a@example.test", "recipients": "b@example.test,c@example.test"},
        )
        stubber.assert_no_pending_responses()


def test_the_configuration_is_read_back_from_ssm_not_taken_from_the_arguments() -> None:
    client = stubbed_ssm()
    parsed = options("--sender", "a@example.test", "--recipients", "b@example.test")
    with Stubber(client) as stubber:
        for name, kind, value in (
            ("sender", "String", "a@example.test"),
            ("recipients", "StringList", "b@example.test"),
        ):
            stubber.add_response(
                "put_parameter",
                {},
                {
                    "Name": f"/sap-security-patch-day/{name}",
                    "Value": value,
                    "Type": kind,
                    "Overwrite": True,
                },
            )
        stubber.add_response(
            "get_parameters_by_path",
            {
                "Parameters": [
                    {"Name": "/sap-security-patch-day/sender", "Value": "from-ssm@example.test"}
                ]
            },
            {"Path": "/sap-security-patch-day/", "Recursive": False, "WithDecryption": False},
        )
        values = local_cli._config_values(parsed, client)
    assert values == {"sender": "from-ssm@example.test"}


def test_without_an_ssm_endpoint_the_state_stays_in_memory() -> None:
    assert isinstance(local_cli._state_store(None, "/s/"), InMemoryStateStore)


def test_an_ssm_endpoint_keeps_the_state_in_ssm() -> None:
    assert isinstance(local_cli._state_store(stubbed_ssm(), "/s/"), SsmStateStore)


def test_an_unreachable_ssm_endpoint_says_how_to_start_it() -> None:
    parsed = options("--ssm-endpoint", "http://127.0.0.1:9")
    client = local_cli._local_client("ssm", "http://127.0.0.1:9")
    with pytest.raises(SystemExit, match="is the local emulator running"):
        local_cli._config_values(parsed, client)
