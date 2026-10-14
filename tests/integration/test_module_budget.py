"""Module budget, imports and layering.

GREENFIELD's machine-checkable rules, applied to the package that ships: files
stay within the line budget, the artefact imports nothing but the standard
library (plus the `boto3` the Lambda runtime provides), package modules refer to
each other relatively and never in a cycle, and the domain modules stay leaves
of the graph.

These are structural rules, so they are asserted structurally — a reviewer
should never have to notice them by eye.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "sap_patch_day"
SOURCE_FILES = sorted(SOURCE_ROOT.glob("*.py"))
TEST_FILES = sorted(REPO_ROOT.glob("tests/**/*.py"))
PACKAGE = "sap_patch_day"

TYPICAL_LIMIT = 400
HARD_LIMIT = 800

# Provided by the Lambda runtime (docs/addenda/0003).
RUNTIME_PROVIDED = {"boto3", "botocore"}

# Modules with no reason to import anything else in the package.
LEAF_MODULES = {"__init__.py", "errors.py", "models.py"}

# The one module allowed to print: it is a developer command, not a handler.
PRINT_ALLOWED = {"local_cli.py"}


def module_name(path: Path) -> str:
    return path.name if path.name == "__init__.py" else path.stem


def parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def external_imports(path: Path) -> Iterator[tuple[str, int]]:
    """Top-level modules imported from outside the package: (`name`, line)."""
    for node in ast.walk(parse_module(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            yield node.module.split(".")[0], node.lineno


def intra_package_imports(path: Path) -> set[str]:
    """Sibling modules this file imports, however the import is spelled."""
    siblings: set[str] = set()
    for node in ast.walk(parse_module(path)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None and node.level:
            siblings.update(alias.name.split(".")[0] for alias in node.names)
        elif node.module and node.level:
            siblings.add(node.module.split(".")[0])
        elif node.module and node.module.startswith(f"{PACKAGE}."):
            siblings.add(node.module.split(".")[1])
    return siblings


def test_the_package_is_not_empty() -> None:
    assert len(SOURCE_FILES) >= 10
    assert {path.name for path in SOURCE_FILES} >= {
        "config.py",
        "handler.py",
        "mailer.py",
        "models.py",
        "state.py",
    }


@pytest.mark.parametrize("path", SOURCE_FILES, ids=module_name)
def test_source_files_stay_within_the_typical_budget(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= TYPICAL_LIMIT, (
        f"{path.name} is {len(lines)} lines; split it with the barrel pattern "
        f"(call sites stay unchanged) or record why in docs/addenda/"
    )
    assert len(lines) <= HARD_LIMIT, f"{path.name} is over the {HARD_LIMIT}-line hard limit"


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda path: path.name)
def test_test_files_stay_within_the_hard_budget(path: Path) -> None:
    assert len(path.read_text(encoding="utf-8").splitlines()) <= HARD_LIMIT


@pytest.mark.parametrize("path", SOURCE_FILES, ids=module_name)
def test_source_imports_only_the_standard_library_and_boto3(path: Path) -> None:
    allowed = set(sys.stdlib_module_names) | RUNTIME_PROVIDED | {PACKAGE}
    unexpected = sorted({name for name, _ in external_imports(path)} - allowed)
    assert unexpected == [], (
        f"{path.name} imports {unexpected}; the runtime provides only boto3 (addendum 0003)"
    )


@pytest.mark.parametrize("path", SOURCE_FILES, ids=module_name)
def test_source_never_imports_the_tests(path: Path) -> None:
    assert "tests" not in {name for name, _ in external_imports(path)}


@pytest.mark.parametrize("path", SOURCE_FILES, ids=module_name)
def test_package_modules_are_imported_relatively(path: Path) -> None:
    absolute = sorted({name for name, _ in external_imports(path) if name == PACKAGE})
    assert absolute == [], f"{path.name} imports the package by name; use a relative import"


def test_no_module_imports_itself() -> None:
    for path in SOURCE_FILES:
        assert module_name(path) not in intra_package_imports(path), f"{path.name} imports itself"


def test_leaf_modules_import_nothing_else_in_the_package() -> None:
    offenders = {
        path.name
        for path in SOURCE_FILES
        if path.name in LEAF_MODULES and intra_package_imports(path)
    }
    assert offenders == set()


def test_the_handler_reaches_the_whole_package() -> None:
    """No orphan modules: everything except the entry points is reachable."""
    entry_points = {"__init__.py", "handler", "local_cli"}
    graph = {module_name(path): intra_package_imports(path) for path in SOURCE_FILES}
    reachable = {"handler"}
    frontier = ["handler"]
    while frontier:
        for target in graph.get(frontier.pop(), set()):
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    assert set(graph) - reachable <= entry_points, (
        f"modules nobody imports: {sorted(set(graph) - reachable - entry_points)}"
    )


def test_the_package_has_no_import_cycles() -> None:
    graph: dict[str, set[str]] = {
        module_name(path): intra_package_imports(path) for path in SOURCE_FILES
    }
    cycles: list[str] = []

    def walk(start: str, node: str, seen: tuple[str, ...]) -> None:
        for target in sorted(graph.get(node, set())):
            if target == start:
                cycles.append(" -> ".join((*seen, target)))
            elif target not in seen:
                walk(start, target, (*seen, target))

    for module in sorted(graph):
        walk(module, module, (module,))
    assert cycles == []


def test_only_the_local_command_prints_to_stdout() -> None:
    offenders = []
    for path in SOURCE_FILES:
        uses_print = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
            for node in ast.walk(parse_module(path))
        )
        if uses_print and path.name not in PRINT_ALLOWED:
            offenders.append(path.name)
    assert offenders == [], "the handler logs; it does not print (CLAUDE-testing.md §7)"


def test_every_module_is_documented() -> None:
    for path in SOURCE_FILES:
        assert ast.get_docstring(parse_module(path)) is not None, (
            f"{path.name} has no module docstring"
        )


def test_the_artefact_builder_stages_the_whole_package() -> None:
    text = (REPO_ROOT / "scripts" / "build-package.sh").read_text(encoding="utf-8")
    assert "sap_patch_day" in text
    assert "*.py" in text, "new modules must be picked up without editing the builder"
