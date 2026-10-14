#!/usr/bin/env bash
#
# Capture a real SAP page into tests/fixtures/captured/<date>/ (addendum 0010).
#
# A real capture beats any reconstruction: it is the only way a parser change
# can be checked against the markup SAP actually serves. The script fetches the
# page, refuses to store anything that does not parse as the month you asked
# for, and prints the provenance line to add to tests/fixtures/README.md. It
# never overwrites a curated fixture: captures live in their own directory,
# where tests/integration/test_captured_pages.py picks them up automatically.
#
#   scripts/capture-fixture.sh --month 2026-11
#   scripts/capture-fixture.sh --month 2026-11 --index
#
# Bash 3.2 compatible.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "$REPO_ROOT"

OUT_DIR=""
MONTH=""
INDEX="false"
OVERWRITE="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --month)
      [ $# -ge 2 ] || { printf 'capture-fixture.sh: --month needs YYYY-MM\n' >&2; exit 2; }
      MONTH="$2"
      shift 2
      ;;
    --index)
      INDEX="true"
      shift
      ;;
    --out-dir)
      [ $# -ge 2 ] || { printf 'capture-fixture.sh: --out-dir needs a path\n' >&2; exit 2; }
      OUT_DIR="$2"
      shift 2
      ;;
    --overwrite)
      OVERWRITE="true"
      shift
      ;;
    -h | --help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'capture-fixture.sh: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

[ -n "$MONTH" ] || { printf 'capture-fixture.sh: --month YYYY-MM is required\n' >&2; exit 2; }

# Captures are dated and never edited, so the default directory is today's:
# a later capture of the same month sits beside the earlier one instead of
# overwriting it (tests/fixtures/README.md, "the rule").
if [ -z "$OUT_DIR" ]; then
  OUT_DIR="tests/fixtures/captured/$(date +%Y-%m-%d)"
fi

if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON=python3
fi

env PYTHONPATH=src CAPTURE_MONTH="$MONTH" CAPTURE_OUT="$OUT_DIR" \
  CAPTURE_INDEX="$INDEX" CAPTURE_OVERWRITE="$OVERWRITE" "$PYTHON" - <<'PYTHON'
"""Fetch one page, verify it, and record what was captured."""

import hashlib
import os
import pathlib
import sys

from sap_patch_day.bulletin_index import parse_dates_sections
from sap_patch_day.htmltree import parse
from sap_patch_day.httpclient import HttpPolicy, UrllibHttpClient
from sap_patch_day.models import PatchDayMonth
from sap_patch_day.notes_table import extract_page_title, extract_notes_table, page_claims_month
from sap_patch_day.probe import INDEX_URL, canonical_url

target = PatchDayMonth.parse(os.environ["CAPTURE_MONTH"])
want_index = os.environ["CAPTURE_INDEX"] == "true"
overwrite = os.environ["CAPTURE_OVERWRITE"] == "true"
out_dir = pathlib.Path(os.environ["CAPTURE_OUT"])

url = INDEX_URL if want_index else canonical_url(target)
name = "security_notes_news_index.html" if want_index else f"{target.bulletin_slug.replace('-', '_')}.html"
destination = out_dir / name

if destination.exists() and not overwrite:
    sys.exit(f"{destination} exists; pass --overwrite to replace it")

result = UrllibHttpClient(HttpPolicy(timeout_seconds=30, max_attempts=3, backoff_seconds=5.0)).get(url)
if not result.is_success:
    sys.exit(f"GET {url} -> {result.status} {result.error}".strip())

if want_index:
    sections = parse_dates_sections(result.body)
    years = ", ".join(str(section.year) for section in sections)
    summary = f"{len(sections)} 'Dates for' section(s): {years}"
else:
    root = parse(result.body)
    claimed = page_claims_month(extract_page_title(root), target)
    if claimed is False:
        sys.exit(f"{url} is serving another month ('{extract_page_title(root)}'); refusing to store it")
    notes = extract_notes_table(root)
    summary = f"{len(notes)} notes parsed from the bulletin table"

# The directory is created only once there is something to put in it, so a
# failed fetch leaves nothing behind.
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(result.body, encoding="utf-8")
digest = hashlib.sha256(result.body.encode("utf-8")).hexdigest()[:16]

print(f"captured  : {destination}")
print(f"url       : {url}")
print(f"bytes     : {len(result.body)}")
print(f"sha256[:16]: {digest}")
print(f"verified  : {summary}")
print()
print("Add a row to tests/fixtures/README.md:")
print(f"| {name} | captured {target.label} | {url} | {digest} |")
PYTHON

printf '\nNext: run scripts/check.sh, then commit the fixture together with the README row.\n'
