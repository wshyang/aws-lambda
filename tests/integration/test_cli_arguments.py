"""The AWS CLI path's argument surface: documented flags must actually exist.

`scripts/awscli/08-verify.sh` documented `--target-month` in its usage block
while the shared parser rejected it, and nothing caught it — the deployment
scripts cannot be executed in CI, so a text-level comparison is the only
available guard. These tests make the documented surface and the accepted
surface agree, and they check the two behaviours a caller depends on: a good
value is accepted, a malformed one fails before AWS is called.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

import pytest

from tests.support.templates import REPO_ROOT

SCRIPTS = REPO_ROOT / "scripts"
CLI_DIR = SCRIPTS / "awscli"
ENV_SCRIPT = CLI_DIR / "00-env.sh"

FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
CASE_LABEL = re.compile(r"^\s+(--[a-z0-9-]+)(?:\s*\|\s*(--[a-z0-9-]+))?\)", re.MULTILINE)
USAGE_BLOCK = re.compile(
    r"^# Usage:(.*?)(?=\n#\n|\n#  ?[A-Z]|\n[A-Za-z])", re.MULTILINE | re.DOTALL
)

# Flags handled by a script itself rather than by the shared parser, plus the
# universal ones every CLI understands.
LOCAL_FLAGS = {
    "--skip-verify",
    "--delete-stack",
    "--delete-artifacts",
    "--delete-ses-identity",
    "--help",
    "-h",
}


def shell(snippet: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet with `00-env.sh` sourced, in the repository root.

    The snippet carries its own literals: arguments passed after `bash -c` would
    land in `$0`, `$1`, ... and silently change what is being tested.
    """
    return subprocess.run(
        ["bash", "-c", f". {ENV_SCRIPT.relative_to(REPO_ROOT)}; {snippet}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={key: value for key, value in os.environ.items() if not key.startswith("AWS")},
    )


def accepted_flags() -> set[str]:
    """Every flag any script's `case` statement matches, shared parser included."""
    found: set[str] = set()
    for script in sorted(list(SCRIPTS.glob("*.sh")) + list(CLI_DIR.glob("*.sh"))):
        text = script.read_text(encoding="utf-8")
        for first, second in CASE_LABEL.findall(text):
            for flag in (first, second):
                if flag:
                    found.add(flag)
    return found


def documented_flags() -> dict[str, set[str]]:
    """Flags named in a script's usage block or in the shared help text."""
    documented: dict[str, set[str]] = {}
    for script in sorted(list(SCRIPTS.glob("*.sh")) + list(CLI_DIR.glob("*.sh"))):
        text = script.read_text(encoding="utf-8")
        block = USAGE_BLOCK.search(text)
        if block:
            documented[script.name] = set(FLAG.findall(block.group(1)))
    help_text = subprocess.run(
        ["bash", "-c", f". {ENV_SCRIPT.relative_to(REPO_ROOT)}; print_usage"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    documented["00-env.sh (help text)"] = set(FLAG.findall(help_text))
    return documented


def test_every_documented_flag_is_accepted_by_some_script() -> None:
    accepted = accepted_flags() | LOCAL_FLAGS
    offenders = {
        script: sorted(flags - accepted)
        for script, flags in documented_flags().items()
        if flags - accepted
    }
    assert offenders == {}, "documented but never parsed"


def test_the_shared_help_text_lists_only_real_flags() -> None:
    accepted = accepted_flags() | LOCAL_FLAGS
    help_text = subprocess.run(
        ["bash", "-c", f". {ENV_SCRIPT.relative_to(REPO_ROOT)}; print_usage"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "--help" not in (set(FLAG.findall(help_text)) - accepted)
    assert "--target-month" in help_text


def test_the_documented_flags_are_parsed_without_touching_aws() -> None:
    completed = shell(
        "parse_common_args --target-month 2026-10 --region eu-west-1 --yes"
        '; echo "TARGET_MONTH=${TARGET_MONTH} REGION=${REGION} STACK_NAME=${STACK_NAME}"'
    )
    assert completed.returncode == 0, completed.stderr
    assert "TARGET_MONTH=2026-10 REGION=eu-west-1" in completed.stdout


@pytest.mark.parametrize("value", ["2026-1", "October 2026", "2026/10", ""])
def test_a_malformed_target_month_fails_before_aws_is_called(value: str) -> None:
    completed = shell(f"parse_common_args --target-month {shlex.quote(value)}")
    assert completed.returncode == 1
    assert "--target-month expects YYYY-MM" in completed.stderr


def test_an_unknown_flag_is_rejected_loudly() -> None:
    completed = shell("parse_common_args --frobnicate")
    assert completed.returncode == 1
    assert "unknown argument: --frobnicate" in completed.stderr


def test_help_exits_zero_and_names_the_shared_flags() -> None:
    completed = shell("parse_common_args --help")
    assert completed.returncode == 0
    assert "--recipients" in completed.stdout


def test_function_name_and_stack_name_are_the_same_knob() -> None:
    by_function = shell(
        'parse_common_args --function-name my-notifier; echo "${STACK_NAME} ${LOG_GROUP_NAME}"'
    )
    assert by_function.returncode == 0, by_function.stderr
    assert by_function.stdout.strip() == "my-notifier /aws/lambda/my-notifier"

    # `--stack-name` is documented as an alias, so it must move the function name too.
    by_stack = shell('parse_common_args --stack-name other-stack; echo "${FUNCTION_NAME}"')
    assert by_stack.stdout.strip() == "other-stack"


def test_the_snapshot_is_the_documented_contract() -> None:
    """`--print-config` is what the parity test compares, so its keys are fixed."""
    completed = shell("parse_common_args; print_config_snapshot")
    assert completed.returncode == 0, completed.stderr
    keys = {line.split("=", 1)[0] for line in completed.stdout.splitlines() if line}
    for required in ("FUNCTION_NAME", "ARTIFACT_KEY", "LOG_GROUP_NAME", "DELIVERY_RULE_NAME"):
        assert required in keys
