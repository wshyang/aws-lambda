#!/usr/bin/env bash
#
# Local SES and SSM through Floci (docs/addenda/0012, 0013).
#
#   scripts/local-aws.sh up                       start Floci and wait until it answers
#   scripts/local-aws.sh init [02 args]           create the SSM config parameters in it
#   scripts/local-aws.sh params                   list the SSM parameters it holds
#   scripts/local-aws.sh send [--target 2026-10]  run the saved October bulletin through SSM + SES
#   scripts/local-aws.sh inbox                    show the messages it captured
#   scripts/local-aws.sh down                     stop and remove it
#
# Needs Docker (Colima is fine). No account or token. Nothing leaves your machine:
# the emulator records the message, it does not deliver it. That proves the request
# is valid SES v2; it does not prove an inbox receives it.
#
# Bash 3.2 compatible.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.local-aws.yml"
ENDPOINT="http://localhost:4566"
FIXTURE="tests/fixtures/captured/2026-10-14/october_2026.html"
INDEX_FIXTURE="tests/fixtures/captured/2026-10-14/security_notes_news_index.html"
STARTUP_TRIES=60

if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON=python3
fi

fail() {
  printf 'local-aws.sh: %s\n' "$1" >&2
  exit 1
}

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

is_running() {
  curl -fsS "${ENDPOINT}/_aws/ses" >/dev/null 2>&1
}

require_running() {
  is_running || fail "the emulator is not running: scripts/local-aws.sh up"
}

cmd_up() {
  command -v docker >/dev/null 2>&1 || fail "docker is not installed"
  compose up -d
  tries=0
  while ! is_running; do
    tries=$((tries + 1))
    [ "$tries" -le "$STARTUP_TRIES" ] || fail "no answer from ${ENDPOINT} after ${STARTUP_TRIES}s: docker logs sap-patch-day-floci"
    sleep 1
  done
  printf 'Floci (SES + SSM) ready at %s\n' "$ENDPOINT"
}

cmd_send() {
  require_running
  env PYTHONPATH=src "$PYTHON" -m sap_patch_day.local_cli \
    --html-file "$FIXTURE" --index-file "$INDEX_FIXTURE" \
    --sender "sap-patch-day@example.com" --recipients "reader@example.test" \
    --ssm-endpoint "$ENDPOINT" --ses-endpoint "$ENDPOINT" "$@"
}

# Point the AWS CLI at the emulator for one command. AWS CLI v2 and boto3 both
# honour AWS_ENDPOINT_URL; a real profile is cleared so nothing can reach a real account.
local_env() {
  command -v aws >/dev/null 2>&1 || fail "the aws CLI is required for this command"
  env -u AWS_PROFILE AWS_ENDPOINT_URL="$ENDPOINT" AWS_ACCESS_KEY_ID=test \
    AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ap-southeast-1}" \
    AWS_REGION="${AWS_DEFAULT_REGION:-ap-southeast-1}" "$@"
}

# `aws` is added here so callers never spell a raw `aws <service> <verb>` (the
# portability gate wants the shared wrapper; this is its local-emulator counterpart).
local_aws_cli() {
  local_env aws "$@"
}

cmd_init() {
  require_running
  local_env "${SCRIPT_DIR}/awscli/02-ssm-parameters.sh" \
    --sender-address "sap-patch-day@example.com" --recipients "reader@example.test" "$@"
}

cmd_params() {
  require_running
  local_aws_cli ssm get-parameters-by-path --path "/sap-security-patch-day/" \
    --recursive --query 'Parameters[].[Name,Type,Value]' --output text
}

cmd_inbox() {
  require_running
  curl -fsS "${ENDPOINT}/_aws/ses"
  printf '\n'
}

cmd_down() {
  compose down
}

case "${1:-}" in
  up) cmd_up ;;
  send) shift; cmd_send "$@" ;;
  init) shift; cmd_init "$@" ;;
  params) cmd_params ;;
  inbox) cmd_inbox ;;
  down) cmd_down ;;
  -h | --help | "") sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "unknown command: $1 (up | init | params | send | inbox | down)" ;;
esac
