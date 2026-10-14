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
from .config import MappingConfigSource, load_config
from .email_render import render_bulletin_email
from .handler import RunContext, execute_request
from .httpclient import StaticHttpClient, UrllibHttpClient
from .mailer import LoggingMailer
from .models import MONTH_NAMES, Bulletin, PatchDayMonth
from .probe import INDEX_URL, ProbeRequest, canonical_url, check_publication
from .state import InMemoryStateStore


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
    return parser.parse_args(argv)


def _run_context(options: argparse.Namespace) -> RunContext:
    values = MappingConfigSource(
        {"sender": options.sender, "recipients": options.recipients}
    ).read()
    config = load_config(values, os.environ)
    transport = UrllibHttpClient(config.http) if options.live else StaticHttpClient(_pages(options))
    return RunContext(
        config=config,
        http=transport,
        mailer=LoggingMailer(),
        state=InMemoryStateStore(),
        clock=SystemClock(),
    )


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
