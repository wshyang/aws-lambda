"""Shell portability: every script must run on Bash 3.2 (macOS) as well as on
CloudShell's Bash 5.

GREENFIELD.md §10.4 and CLAUDE-deployment.md §5: `set -euo pipefail`, no
Bash-4-only constructs, and empty arrays expanded as `${a[@]+"${a[@]}"}` so
`set -u` cannot abort a script. CloudShell is not the only place these scripts
are run — a laptop with `/bin/bash` 3.2 is the stricter host, and this test is
what catches a macOS-only construct before it ships.

The scripts are checked three ways: with `bash -n` (syntax), with `shellcheck`
(when installed; it comes with `requirements-dev.txt`), and with a static grep
for the constructs 3.2 does not have.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(
    [
        *(REPO_ROOT / "scripts").glob("*.sh"),
        *(REPO_ROOT / "scripts" / "awscli").glob("*.sh"),
    ]
)

# Constructs Bash 3.2 does not have. Each entry is (pattern, why).
FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"\bdeclare\s+-A\b", "associative arrays need Bash 4"),
    (r"\blocal\s+-A\b", "associative arrays need Bash 4"),
    (r"\btypeset\s+-A\b", "associative arrays need Bash 4"),
    (r"\b(mapfile|readarray)\b", "mapfile/readarray need Bash 4"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*,,", "case conversion needs Bash 4"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^", "case conversion needs Bash 4"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*@[QLu]\}", "parameter transformations need Bash 4.4"),
    (r"&>>", "&>> redirection needs Bash 4"),
    (r";;&", "case fallthrough needs Bash 4"),
    (r"\bwait\s+-n\b", "wait -n needs Bash 4.3"),
    (r"\bexec\s+\{", "fd allocation needs Bash 4.1"),
    (r"\bshopt\s+-s\s+globstar\b", "globstar needs Bash 4"),
    (r"\bglobs?\s*\*\*/", "globstar needs Bash 4"),
    (r"\bprintf\s+-v\s+\w+\s+%\(\)", "printf %()T needs Bash 4.2"),
)

# `${name[@]}` is only safe under `set -u` in Bash 3.2 in the guarded form
# `${name[@]+"${name[@]}"}` or inside `${#name[@]}`.
ARRAY_EXPANSION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}")

SHEBANG = "#!/usr/bin/env bash"


def shellcheck_binary() -> str | None:
    found = shutil.which("shellcheck")
    if found:
        return found
    candidate = REPO_ROOT / ".venv" / "bin" / "shellcheck"
    return str(candidate) if candidate.exists() else None


def script_id(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def expansion_offsets(text: str) -> Iterator[tuple[str, int]]:
    for match in ARRAY_EXPANSION.finditer(text):
        yield match.group(1), match.start()


def test_there_are_scripts_to_check() -> None:
    assert len(SCRIPTS) >= 12, [path.name for path in SCRIPTS]


@pytest.mark.parametrize("path", SCRIPTS, ids=script_id)
def test_scripts_are_bash_with_strict_mode(path: Path) -> None:
    text = read(path)
    assert text.startswith(f"{SHEBANG}\n"), "scripts must run with the bash on PATH"
    without_comments = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert "set -euo pipefail" in without_comments, "set -euo pipefail is required"


@pytest.mark.parametrize("path", SCRIPTS, ids=script_id)
def test_scripts_are_syntactically_valid(path: Path) -> None:
    completed = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("path", SCRIPTS, ids=script_id)
def test_scripts_avoid_bash_four_constructs(path: Path) -> None:
    lines = read(path).splitlines()
    offenders: list[str] = []
    for pattern, reason in FORBIDDEN:
        expression = re.compile(pattern)
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("#"):
                continue
            if expression.search(line):
                offenders.append(f"{script_id(path)}:{number}: {reason}: {line.strip()}")
    assert offenders == []


@pytest.mark.parametrize("path", SCRIPTS, ids=script_id)
def test_array_expansions_are_guarded_for_set_u(path: Path) -> None:
    text = read(path)
    offenders = []
    for name, offset in expansion_offsets(text):
        before = text[max(0, offset - 32) : offset]
        guarded = before.endswith('[@]+"') or before.endswith("${#")
        if not guarded:
            line = text.count("\n", 0, offset) + 1
            offenders.append(f"{script_id(path)}:{line}: ${{{name}[@]}} is not guarded")
    assert offenders == []


@pytest.mark.parametrize("path", SCRIPTS, ids=script_id)
def test_scripts_pass_shellcheck(path: Path) -> None:
    binary = shellcheck_binary()
    if binary is None:
        pytest.skip(
            "shellcheck is not installed; `pip install -r requirements-dev.txt` provides it"
        )
    completed = subprocess.run(
        [binary, "-x", "-P", "scripts", "-P", "scripts/awscli", str(path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("path", SCRIPTS, ids=script_id)
def test_scripts_do_not_reference_a_nonexistent_helper(path: Path) -> None:
    """Every `foo.sh` a script sources exists, and every sourced file is readable."""
    text = read(path)
    for match in re.finditer(r"^\.\s+\"\$\{SCRIPT_DIR\}/([^\"]+)\"", text, re.MULTILINE):
        target = path.parent / match.group(1)
        assert target.exists(), f"{script_id(path)} sources a missing file: {target}"


def test_the_shared_environment_is_sourced_by_every_numbered_step() -> None:
    steps = [path for path in SCRIPTS if path.name[0].isdigit() and not path.name.startswith("00")]
    assert len(steps) == 9
    for path in steps:
        assert '00-env.sh"' in read(path), f"{path.name} reimplements the shared defaults"


def test_deploy_all_runs_the_steps_in_order() -> None:
    text = read(REPO_ROOT / "scripts" / "awscli" / "deploy-all.sh")
    order = [match.group(1) for match in re.finditer(r'"(\d\d-[a-z-]+\.sh)"', text)]
    assert order == sorted(order), "steps must run in numeric order"
    assert order[0].startswith("01") and len(order) >= 7


def test_no_script_calls_aws_without_the_shared_wrapper() -> None:
    """Raw `aws` calls bypass --region/--profile/--output and hide the defaults."""
    for path in SCRIPTS:
        if path.name == "00-env.sh":
            continue
        offenders = [
            f"{script_id(path)}:{number}"
            for number, line in enumerate(read(path).splitlines(), start=1)
            if re.search(r"(?<![\w.-])aws\s+[a-z0-9-]+\s+[a-z0-9-]+", line)
            and "aws_cli" not in line
        ]
        assert offenders == [], "use aws_cli so region and profile always apply"
