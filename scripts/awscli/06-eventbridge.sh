#!/usr/bin/env bash
#
# 06 - EventBridge schedules and their Lambda permissions.
#
# Same objects as the DeliveryRule, WatchdogRule, DeliveryRulePermission and
# WatchdogRulePermission resources in the application stack.
#
#   delivery: cron(0,30 8-12 ? * TUE#2 *) = 16:00-20:30 Singapore time, 2nd Tuesday
#   watchdog: cron(0 1 ? * WED#2 *)       = 09:00 Singapore time, 2nd Wednesday
#
# Permissions are refreshed (remove then add) because a stale statement with the
# same id blocks re-running this script after a function is recreated.
#
# Usage: scripts/awscli/06-eventbridge.sh
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws

FUNCTION_ARN=$(aws_cli lambda get-function --function-name "$FUNCTION_NAME" \
  --query 'Configuration.FunctionArn' --output text 2>/dev/null || printf '')
[ -n "$FUNCTION_ARN" ] || die "function not found: ${FUNCTION_NAME} (run 05-lambda-function.sh first)"

grant_invoke() {
  statement_id="$1"
  rule_arn="$2"
  aws_cli lambda remove-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "$statement_id" >/dev/null 2>&1 || true
  aws_cli lambda add-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "$statement_id" \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "$rule_arn" >/dev/null
  info "invoke permission granted for ${statement_id}"
}

put_rule() {
  rule_name="$1"
  rule_description="$2"
  schedule="$3"
  aws_cli events put-rule \
    --name "$rule_name" \
    --description "$rule_description" \
    --schedule-expression "$schedule" \
    --state ENABLED >/dev/null
  info "rule ${rule_name}: ${schedule}"
}

put_target() {
  rule_name="$1"
  target_id="$2"
  run_mode="$3"
  aws_cli events put-targets \
    --rule "$rule_name" \
    --targets "[{\"Id\":\"${target_id}\",\"Arn\":\"${FUNCTION_ARN}\",\"Input\":\"{\\\"run_mode\\\":\\\"${run_mode}\\\"}\"}]" >/dev/null
  info "target ${target_id} -> ${run_mode} on ${rule_name}"
}

put_rule "$DELIVERY_RULE_NAME" \
  "Second Tuesday retry window (16:00-20:30 Singapore time)" \
  "$DELIVERY_SCHEDULE"
DELIVERY_RULE_ARN=$(aws_cli events describe-rule --name "$DELIVERY_RULE_NAME" --query 'Arn' --output text)
grant_invoke "$DELIVERY_STATEMENT_ID" "$DELIVERY_RULE_ARN"
put_target "$DELIVERY_RULE_NAME" delivery delivery

if [ "$ENABLE_WATCHDOG" = "true" ]; then
  put_rule "$WATCHDOG_RULE_NAME" \
    "Second Wednesday check that the month's notes were delivered" \
    "$WATCHDOG_SCHEDULE"
  WATCHDOG_RULE_ARN=$(aws_cli events describe-rule --name "$WATCHDOG_RULE_NAME" --query 'Arn' --output text)
  grant_invoke "$WATCHDOG_STATEMENT_ID" "$WATCHDOG_RULE_ARN"
  put_target "$WATCHDOG_RULE_NAME" watchdog watchdog
else
  info "watchdog disabled; removing any existing rule and permission"
  aws_cli events remove-targets --rule "$WATCHDOG_RULE_NAME" --ids watchdog >/dev/null 2>&1 || true
  aws_cli events delete-rule --name "$WATCHDOG_RULE_NAME" >/dev/null 2>&1 || true
  aws_cli lambda remove-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "$WATCHDOG_STATEMENT_ID" >/dev/null 2>&1 || true
fi

info "schedules in place; the next delivery window is the second Tuesday of next month"
