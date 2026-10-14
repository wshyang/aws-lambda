#!/usr/bin/env bash
#
# Every mechanical gate, in one command (GREENFIELD.md §7).
#
#   scripts/check.sh          all gates
#   scripts/check.sh --fast   skip the slow ones (cfn-lint, full pytest)
#
# The gates are the same ones CI runs, so a green `check.sh` and a green build
# mean the same thing. The live tests are never run from here: they need egress
# to support.sap.com and are opt-in (`scripts/live-check.sh`).
#
# Bash 3.2 compatible.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "$REPO_ROOT"

FAST="false"
for argument in "$@"; do
  case "$argument" in
    --fast) FAST="true" ;;
    -h | --help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'unknown argument: %s (try --help)\n' "$argument" >&2
      exit 2
      ;;
  esac
done

if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON=python3
fi
SHELLCHECK="${REPO_ROOT}/.venv/bin/shellcheck"
CFN_LINT="${REPO_ROOT}/.venv/bin/cfn-lint"

FAILURES=0
STEPS=0

info() { printf '\n== %s\n' "$*"; }
fail() { printf '   FAILED: %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }

step() {
  STEP_NAME="$1"
  shift
  STEPS=$((STEPS + 1))
  if "$@" >/tmp/check-step.log 2>&1; then
    printf '   ok: %s\n' "$STEP_NAME"
  else
    fail "$STEP_NAME"
    sed 's/^/      /' /tmp/check-step.log | tail -n 20 >&2
  fi
}

check_pins() {
  # Every development dependency is pinned exactly, so two machines run the
  # same gates (GREENFIELD.md §7, requirements-dev.txt).
  awk '!/^[[:space:]]*(#|$)/ && $0 !~ /^[A-Za-z0-9_.-]+==[0-9]/ { print "unpinned: " $0; bad = 1 } END { exit bad }' \
    requirements-dev.txt
}

check_shell_syntax() {
  for script in scripts/*.sh scripts/awscli/*.sh; do
    bash -n "$script" || return 1
  done
}

check_shellcheck() {
  if [ ! -x "$SHELLCHECK" ]; then
    printf '   shellcheck not found at %s; install requirements-dev.txt\n' "$SHELLCHECK" >&2
    return 0
  fi
  for script in scripts/*.sh scripts/awscli/*.sh; do
    "$SHELLCHECK" -x -P scripts -P scripts/awscli "$script" || return 1
  done
}

check_cfn_lint() {
  if [ ! -x "$CFN_LINT" ]; then
    printf '   cfn-lint not found at %s; install requirements-dev.txt\n' "$CFN_LINT" >&2
    return 0
  fi
  "$CFN_LINT" infrastructure/cloudformation/*.yaml
}

info "static gates"
step "dependency pins are exact" check_pins
step "shell syntax (bash -n)" check_shell_syntax
step "shellcheck" check_shellcheck
step "ruff check" "$PYTHON" -m ruff check .
step "ruff format --check" "$PYTHON" -m ruff format --check .
step "mypy (src)" "$PYTHON" -m mypy

if [ "$FAST" = "true" ]; then
  info "fast mode: skipping cfn-lint and the test suite"
else
  info "cloudformation"
  step "cfn-lint" check_cfn_lint
  info "tests"
  # The unit, integration and structural gates. Live tests stay opt-in.
  step "pytest" "$PYTHON" -m pytest
fi

printf '\n---------------------------------------------\n'
if [ "$FAILURES" -eq 0 ]; then
  printf 'check.sh: %d gate(s) passed\n' "$STEPS"
else
  printf 'check.sh: %d of %d gate(s) FAILED\n' "$FAILURES" "$STEPS"
fi
rm -f /tmp/check-step.log
[ "$FAILURES" -eq 0 ]
