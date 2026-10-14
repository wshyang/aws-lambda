#!/usr/bin/env bash
#
# Local development in one command (GREENFIELD.md §9).
#
#   scripts/dev.sh                                   set up .venv, dry-run the saved pages
#   scripts/dev.sh --target 2026-11                  another month
#   scripts/dev.sh --live                            fetch the real SAP pages
#   scripts/dev.sh --recipients "me@example.com"     a different recipient list
#
# The dry run is the same code path the Lambda runs (spec FR-3.9): it renders the
# real email, logs instead of sending, and writes the preview to dist/ so it can
# be opened in a browser. Nothing is sent and no state is written.
#
# Bash 3.2 compatible.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "$REPO_ROOT"

VENV="${REPO_ROOT}/.venv"
PYTHON="${VENV}/bin/python"
FIXTURE_DIR="tests/fixtures"

month_name_for() {
  case "$1" in
    01) printf 'january' ;;
    02) printf 'february' ;;
    03) printf 'march' ;;
    04) printf 'april' ;;
    05) printf 'may' ;;
    06) printf 'june' ;;
    07) printf 'july' ;;
    08) printf 'august' ;;
    09) printf 'september' ;;
    10) printf 'october' ;;
    11) printf 'november' ;;
    12) printf 'december' ;;
  esac
}

month_number_for() {
  case "$1" in
    january) printf '01' ;;
    february) printf '02' ;;
    march) printf '03' ;;
    april) printf '04' ;;
    may) printf '05' ;;
    june) printf '06' ;;
    july) printf '07' ;;
    august) printf '08' ;;
    september) printf '09' ;;
    october) printf '10' ;;
    november) printf '11' ;;
    december) printf '12' ;;
  esac
}

newest_saved_month() {
  newest_key=""
  newest_month=""
  for candidate in "$FIXTURE_DIR"/*_20[0-9][0-9].html; do
    [ -f "$candidate" ] || continue
    stem=$(basename "$candidate" .html)
    name=${stem%%_*}
    year=${stem##*_}
    number=$(month_number_for "$name")
    [ -n "$number" ] || continue
    key="${year}${number}"
    if [ -z "$newest_key" ] || [ "$key" \> "$newest_key" ]; then
      newest_key="$key"
      newest_month="${year}-${number}"
    fi
  done
  if [ -n "$newest_month" ]; then
    printf '%s' "$newest_month"
  fi
}

LIVE="false"
TARGET=""
CLI_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --live)
      LIVE="true"
      shift
      ;;
    --target)
      [ $# -ge 2 ] || { printf 'dev.sh: --target needs YYYY-MM\n' >&2; exit 2; }
      TARGET="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      CLI_ARGS=("${CLI_ARGS[@]+"${CLI_ARGS[@]}"}" "$1")
      shift
      ;;
  esac
done

if [ ! -x "$PYTHON" ]; then
  printf 'creating %s\n' "$VENV"
  python3 -m venv "$VENV"
fi

if ! "$PYTHON" -c "import pytest, ruff" >/dev/null 2>&1; then
  printf 'installing pinned development tools\n'
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -r requirements-dev.txt
fi

mkdir -p dist

if [ "$LIVE" = "true" ]; then
  printf 'fetching the real pages from support.sap.com\n'
  exec env PYTHONPATH=src "$PYTHON" -m sap_patch_day.local_cli \
    --live --preview "${CLI_ARGS[@]+"${CLI_ARGS[@]}"}"
fi

if [ -z "$TARGET" ]; then
  TARGET=$(newest_saved_month)
  [ -n "$TARGET" ] || {
    printf 'dev.sh: no saved bulletin page in %s; pass --target YYYY-MM or --live\n' "$FIXTURE_DIR" >&2
    exit 2
  }
fi

PAGE="${FIXTURE_DIR}/$(month_name_for "${TARGET##*-}")_${TARGET%%-*}.html"
if [ ! -f "$PAGE" ]; then
  printf 'dev.sh: no saved page for %s (%s)\n' "$TARGET" "$PAGE" >&2
  printf 'dev.sh: run with --live to fetch it, or capture it with scripts/capture-fixture.sh\n' >&2
  exit 2
fi

printf 'dry run for %s against %s (nothing is sent, no state is written)\n' "$TARGET" "$PAGE"
env PYTHONPATH=src "$PYTHON" -m sap_patch_day.local_cli \
  --html-file "$PAGE" \
  --index-file "${FIXTURE_DIR}/security_notes_news_index.html" \
  --target "$TARGET" \
  --preview \
  "${CLI_ARGS[@]+"${CLI_ARGS[@]}"}"

printf '\npreview: dist/preview-%s.html\n' "$TARGET"
printf 'gates:   scripts/check.sh\n'
