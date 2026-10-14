"""Offline dry run: ``python -m sap_patch_day.local_cli``.

Answers "what would this send?" without AWS, SAP, or a network: pages come from
files, the mailer logs, and the state store lives in memory. It runs the same
``execute_request`` path as the deployed function, so it is a check of the real
logic rather than a parallel implementation (spec FR-3.9, CLAUDE-testing.md §1).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from .clock import SystemClock
from .config import DEFAULT_CONFIG_PATH, MappingConfigSource, SsmConfigSource, load_config
from .email_render import render_bulletin_email
from .handler import RunContext, execute_request
from .httpclient import StaticHttpClient, UrllibHttpClient
from .mailer import LoggingMailer, Mailer, SesMailer
from .models import MONTH_NAMES, Bulletin, PatchDayMonth
from .probe import INDEX_URL, ProbeRequest, canonical_url, check_publication
from .state import InMemoryStateStore, SsmStateStore, StateStore

LOCAL_REGION = "ap-southeast-1"


def main(argv: Sequence[str] | None = None) -> int:
    options = _parse_args(argv)
    run = _run_context(options)
    result = execute_request(_payload(options), run)
    print(json.dumps(result, indent=2))
    if options.preview:
        _write_preview(options, run)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-file", help="saved bulletin page to use instead of the network")
    parser.add_argument("--index-file", help="saved index page to use for the link transition")
    parser.add_argument(
        "--target",
        help="patch-day month as YYYY-MM (default: from an --html-file name, else today)",
    )
    parser.add_argument("--recipients", default="patch-day-team@example.test")
    parser.add_argument("--sender", default="sap-patch-day@example.com")
    parser.add_argument("--out-dir", default="dist")
    parser.add_argument(
        "--live", action="store_true", help="fetch the real pages instead of using saved ones"
    )
    parser.add_argument(
        "--preview", action="store_true", help="write the rendered email to --out-dir"
    )
    parser.add_argument(
        "--ses-endpoint",
        default="",
        help="send through an SES-compatible local emulator such as Floci "
        "(http://localhost:4566) instead of only logging (addendum 0012)",
    )
    parser.add_argument(
        "--ssm-endpoint",
        default="",
        help="read configuration from, and keep state in, SSM at this endpoint "
        "(http://localhost:4566): the sender and recipients are seeded there first (addendum 0013)",
    )
    return parser.parse_args(argv)


def _run_context(options: argparse.Namespace) -> RunContext:
    ssm = _local_client("ssm", options.ssm_endpoint) if options.ssm_endpoint else None
    config = load_config(_config_values(options, ssm), os.environ)
    transport = UrllibHttpClient(config.http) if options.live else StaticHttpClient(_pages(options))
    return RunContext(
        config=config,
        http=transport,
        mailer=_mailer(options.ses_endpoint, config.sender),
        state=_state_store(ssm, config.state_path),
        clock=SystemClock(),
    )


def _config_values(options: argparse.Namespace, ssm: Any | None) -> Mapping[str, str]:
    """Command-line values, or the same values round-tripped through SSM."""
    values = {"sender": options.sender, "recipients": options.recipients}
    if ssm is None:
        return MappingConfigSource(values).read()
    path = os.environ.get("SSM_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    try:
        _seed_ssm(ssm, path, values)
        return SsmConfigSource(ssm, path).read()
    except _unreachable_errors() as error:
        raise SystemExit(_unreachable_message(options.ssm_endpoint)) from error


def _seed_ssm(ssm: Any, path: str, values: Mapping[str, str]) -> None:
    """Write the parameters the deployment scripts write (same names and types)."""
    prefix = path if path.endswith("/") else f"{path}/"
    for name, value in values.items():
        kind = "StringList" if name == "recipients" else "String"
        ssm.put_parameter(Name=f"{prefix}{name}", Value=value, Type=kind, Overwrite=True)


def _state_store(ssm: Any | None, state_path: str) -> StateStore:
    return InMemoryStateStore() if ssm is None else SsmStateStore(ssm, state_path)


def _mailer(endpoint: str, sender: str) -> Mailer:
    """Log by default; with an endpoint, send through the real SES adapter."""
    if not endpoint:
        return LoggingMailer()
    client = _local_client("sesv2", endpoint)
    _verify_sender(client, sender, endpoint)
    return SesMailer(client)


def _local_client(service: str, endpoint: str) -> Any:
    import boto3  # noqa: PLC0415 - only a developer using a local emulator needs it

    return boto3.client(
        service,
        endpoint_url=endpoint,
        region_name=os.environ.get("AWS_DEFAULT_REGION", LOCAL_REGION),
        aws_access_key_id="test",
        aws_secret_access_key="test",  # noqa: S106 - the local emulator accepts any credentials
    )


def _verify_sender(client: Any, sender: str, endpoint: str) -> None:
    """SES refuses unverified senders; creating the identity verifies it locally."""
    try:
        client.create_email_identity(EmailIdentity=sender)
    except client.exceptions.AlreadyExistsException:
        return
    except _unreachable_errors() as error:
        raise SystemExit(_unreachable_message(endpoint)) from error


def _unreachable_errors() -> tuple[type[Exception], ...]:
    from botocore.exceptions import EndpointConnectionError  # noqa: PLC0415

    return (EndpointConnectionError,)


def _unreachable_message(endpoint: str) -> str:
    return f"cannot reach {endpoint}: is the local emulator running? (scripts/local-aws.sh up)"


def _pages(options: argparse.Namespace) -> Mapping[str, str]:
    pages: dict[str, str] = {}
    target = _target_month(options)
    if options.html_file:
        pages[canonical_url(target)] = pathlib.Path(options.html_file).read_text(encoding="utf-8")
    if options.index_file:
        pages[INDEX_URL] = pathlib.Path(options.index_file).read_text(encoding="utf-8")
    return pages


def _target_month(options: argparse.Namespace) -> PatchDayMonth:
    if options.target:
        return PatchDayMonth.parse(options.target)
    if options.html_file:
        return _month_from_filename(options.html_file)
    return PatchDayMonth(year=date.today().year, month=date.today().month)


def _month_from_filename(path: str) -> PatchDayMonth:
    """``october_2026.html`` -> October 2026, so the dry run needs no arguments."""
    stem = pathlib.Path(path).stem.replace("_", "-")
    parts = stem.split("-")
    if len(parts) >= 2 and parts[0].isalpha() and parts[1].isdigit():
        month = _month_number(parts[0])
        if month is not None:
            return PatchDayMonth(year=int(parts[1]), month=month)
    raise SystemExit(f"cannot infer a patch-day month from '{path}'; pass --target YYYY-MM")


def _month_number(name: str) -> int | None:
    lowered = name.lower()
    for index, month_name in enumerate(MONTH_NAMES, start=1):
        if month_name.lower() == lowered:
            return index
    return None


def _payload(options: argparse.Namespace) -> Mapping[str, Any]:
    return {"run_mode": "delivery", "target_month": _target_month(options).label}


def _write_preview(options: argparse.Namespace, run: RunContext) -> None:
    target = _target_month(options)
    check = check_publication(ProbeRequest(target=target, observed_at=run.clock.now()), run.http)
    if check.bulletin is None:
        print(f"nothing to preview: {check.outcome} ({check.detail or 'no detail'})")
        return
    _write_email_files(check.bulletin, run, pathlib.Path(options.out_dir))


def _write_email_files(bulletin: Bulletin, run: RunContext, out_dir: pathlib.Path) -> None:
    content = render_bulletin_email(bulletin, run.config)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"preview-{bulletin.target.label}"
    (out_dir / f"{stem}.html").write_text(content.html_body, encoding="utf-8")
    (out_dir / f"{stem}.txt").write_text(content.text_body, encoding="utf-8")
    print(f"preview written: {out_dir / stem}.html")
    print(f"subject: {content.subject}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
