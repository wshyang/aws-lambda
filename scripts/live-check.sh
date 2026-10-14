#!/usr/bin/env bash
#
# Live check: does this build still understand the pages SAP is serving today?
#
#   scripts/live-check.sh                 live tests plus a dry run of this month
#   scripts/live-check.sh --target 2026-10
#
# Run this from a host with egress to support.sap.com — not from CI, and not
# from the Lambda's VPC unless it has a NAT route. It never sends email: the dry
# run uses the logging mailer and the read-only state store (spec FR-3.9).
#
# Bash 3.2 compatible.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "$REPO_ROOT"

TARGET=""
PASSTHROUGH=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target)
      [ $# -ge 2 ] || { printf 'live-check.sh: --target needs YYYY-MM\n' >&2; exit 2; }
      TARGET="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      PASSTHROUGH=("${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}" "$1")
      shift
      ;;
  esac
done

if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON=python3
fi

DRY_ARGS=(--live)
if [ -n "$TARGET" ]; then
  DRY_ARGS=("${DRY_ARGS[@]+"${DRY_ARGS[@]}"}" --target "$TARGET")
fi

printf '== live tests against support.sap.com\n'
env PYTHONPATH=src "$PYTHON" -m pytest tests/live --live -v "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"

printf '\n== live dry run: what would be emailed right now\n'
env PYTHONPATH=src "$PYTHON" -m sap_patch_day.local_cli \
  "${DRY_ARGS[@]+"${DRY_ARGS[@]}"}" --preview "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"

printf '\nNothing was sent and no state was written. Preview written under dist/.\n'
