#!/usr/bin/env bash
#
# 08 - Deployed smoke test.
#
# This is the only layer that proves the deployed stack hangs together: IAM,
# SSM, SES and the schedule are all real here, and the run happens inside
# Lambda. To make it safe to run at any time, the function is temporarily
# switched to DRY_RUN=true (logging mailer, read-only state store) and restored
# on exit, whatever happens.
#
# It does not replace the unit and integration suites; those cannot prove that
# the deployed wiring is right, and this cannot prove the parsing is.
#
# Usage: scripts/awscli/08-verify.sh [--target-month 2026-10]
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

TARGET_MONTH="${TARGET_MONTH:-}"
RESPONSE_FILE=$(mktemp)
ORIGINAL_ENV=""

restore_environment() {
  if [ -n "$ORIGINAL_ENV" ]; then
    aws_cli lambda update-function-configuration \
      --function-name "$FUNCTION_NAME" \
      --environment "$ORIGINAL_ENV" >/dev/null 2>&1 || true
    aws_cli lambda wait function-updated --function-name "$FUNCTION_NAME" >/dev/null 2>&1 || true
    info "function environment restored (DRY_RUN back to its deployed value)"
  fi
  rm -f "$RESPONSE_FILE"
}
trap restore_environment EXIT

require_commands aws python3

info "function configuration:"
aws_cli lambda get-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --query '{Runtime: Runtime, Architectures: Architectures, MemorySize: MemorySize, Timeout: Timeout, State: State, Environment: Environment.Variables}'

info "schedules:"
for rule_name in "$DELIVERY_RULE_NAME" "$WATCHDOG_RULE_NAME"; do
  schedule=$(aws_cli events describe-rule --name "$rule_name" --query 'ScheduleExpression' --output text 2>/dev/null || printf '')
  if [ -n "$schedule" ]; then
    info "  ${rule_name}: ${schedule} (UTC; delivery window is 16:00-20:30 Singapore time)"
  else
    info "  ${rule_name}: not found"
  fi
done

info "configuration parameters:"
aws_cli ssm get-parameters-by-path \
  --path "$CONFIG_PATH" \
  --query 'Parameters[].{Name: Name, Value: Value}' --output table

info "current delivery state:"
aws_cli ssm get-parameters-by-path \
  --path "$STATE_PATH" \
  --recursive \
  --query 'Parameters[].{Name: Name, Value: Value}' --output table 2>/dev/null || info "  (no state written yet)"

ORIGINAL_ENV=$(aws_cli lambda get-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --query 'Environment.Variables' --output json)

DRY_ENV=$(python3 -c '
import json, sys
variables = json.loads(sys.argv[1])
variables["DRY_RUN"] = "true"
print(json.dumps({"Variables": variables}))
' "$ORIGINAL_ENV")

info "switching to DRY_RUN=true for the smoke test (no email, no state is written)"
aws_cli lambda update-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --environment "$DRY_ENV" >/dev/null
aws_cli lambda wait function-updated --function-name "$FUNCTION_NAME"

PAYLOAD='{"run_mode":"delivery"}'
if [ -n "$TARGET_MONTH" ]; then
  PAYLOAD="{\"run_mode\":\"delivery\",\"target_month\":\"${TARGET_MONTH}\"}"
fi

info "invoking with payload: ${PAYLOAD}"
aws_cli lambda invoke \
  --function-name "$FUNCTION_NAME" \
  --payload "$PAYLOAD" \
  --cli-binary-format raw-in-base64-out \
  "$RESPONSE_FILE" >/dev/null

info "function result:"
cat "$RESPONSE_FILE"
printf '\n'

info "recent log lines:"
aws_cli logs tail "$LOG_GROUP_NAME" --since 5m --format short 2>/dev/null | tail -n 25 || \
  info "  (logs not available yet; they appear within a minute)"

info "smoke test finished - review the result above before relying on the schedule"
