#!/usr/bin/env bash
#
# Deploy everything with the AWS CLI, in order, in one command.
#
# Designed for AWS CloudShell: clone the repository, then
#
#   bash scripts/awscli/deploy-all.sh \
#       --region ap-southeast-1 \
#       --sender-address sap-patch-day@example.com \
#       --recipients "you@example.com,team@example.com" \
#       --alarm-email "you@example.com"
#
# Every step is idempotent, so re-running is safe. The steps create the same
# objects the CloudFormation stack creates; use one path or the other, never
# both for the same function (docs/addenda/0008).
#
# Usage: scripts/awscli/deploy-all.sh [flags] [--skip-verify]
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"

SKIP_VERIFY="false"
PASSTHROUGH=()
for argument in "$@"; do
  case "$argument" in
    --skip-verify) SKIP_VERIFY="true" ;;
    *) PASSTHROUGH=("${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}" "$argument") ;;
  esac
done
if [ "${#PASSTHROUGH[@]}" -gt 0 ]; then
  parse_common_args "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"
else
  parse_common_args
fi

require_commands aws python3
require_var SENDER_ADDRESS "--sender-address sap-patch-day@example.com"
require_var RECIPIENT_EMAILS '--recipients "one@example.com,two@example.com"'

if [ "$ENABLE_WATCHDOG" = "true" ] && [ "$NOTIFY_WHEN_NOT_PUBLISHED" != "true" ]; then
  warn "the watchdog is on but pending notices are off; the watchdog still emails when nothing was sent"
fi

run_step() {
  step_label="$1"
  step_script="$2"
  info "== ${step_label}"
  "${SCRIPT_DIR}/${step_script}" "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"
}

run_step "01 artefact bucket" "01-artifact-bucket.sh"
run_step "02 configuration parameters" "02-ssm-parameters.sh"
run_step "03 SES identity and configuration set" "03-ses-identity.sh"
run_step "04 execution role" "04-iam-role.sh"
run_step "05 Lambda function" "05-lambda-function.sh"
run_step "06 schedules" "06-eventbridge.sh"
run_step "07 alarms and alert topic" "07-observability.sh"

if [ "$SKIP_VERIFY" = "true" ]; then
  info "smoke test skipped (--skip-verify)"
else
  run_step "08 deployed smoke test" "08-verify.sh"
fi

cat <<SUMMARY

Deployment complete.

  function:   ${FUNCTION_NAME}
  log group:  ${LOG_GROUP_NAME}
  schedule:   ${DELIVERY_SCHEDULE} (UTC) = 16:00-20:30 Singapore time, second Tuesday
  sender:     ${SENDER_ADDRESS}
  recipients: ${RECIPIENT_EMAILS}

Before patch day:
  1. Click the SES verification link sent to ${SENDER_ADDRESS}.
  2. If the account is in the SES sandbox, verify every recipient as well.
  3. Re-run 08-verify.sh after any configuration change.

SUMMARY
