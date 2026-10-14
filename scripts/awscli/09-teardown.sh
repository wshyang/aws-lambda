#!/usr/bin/env bash
#
# 09 - Remove everything the CLI path created, in reverse order.
#
# Asks before deleting anything. Two objects are kept by default because
# removing them is usually not what you want:
#
#   * the SES identity - shared with other senders in the account
#   * the artefact bucket - holds the package a rollback would redeploy
#
# Pass --delete-ses-identity and/or --delete-artifacts to remove them too.
#
# Usage: scripts/awscli/09-teardown.sh [--yes] [--delete-artifacts] [--delete-ses-identity]
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

DELETE_ARTIFACTS="false"
DELETE_SES_IDENTITY="false"
REMAINING_ARGS=()
for argument in "$@"; do
  case "$argument" in
    --delete-artifacts) DELETE_ARTIFACTS="true" ;;
    --delete-ses-identity) DELETE_SES_IDENTITY="true" ;;
    *) REMAINING_ARGS=("${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}" "$argument") ;;
  esac
done
if [ "${#REMAINING_ARGS[@]}" -gt 0 ]; then
  parse_common_args "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
fi

require_commands aws
BUCKET=$(resolved_artifact_bucket)

info "this will remove the notifier stack from ${REGION}:"
info "  function ${FUNCTION_NAME}, log group ${LOG_GROUP_NAME}, role ${ROLE_NAME}"
info "  rules ${DELIVERY_RULE_NAME} and ${WATCHDOG_RULE_NAME}, topic ${ALARM_TOPIC_NAME}"
info "  alarms and configuration parameters under ${CONFIG_PATH}"
if [ "$DELETE_SES_IDENTITY" = "true" ]; then info "  SES identity ${SENDER_ADDRESS}"; fi
if [ "$DELETE_ARTIFACTS" = "true" ]; then info "  artefact bucket s3://${BUCKET}"; fi

confirm "Delete these resources?" || die "cancelled"

aws_cli cloudwatch delete-alarms \
  --alarm-names "${FUNCTION_NAME}-errors" "${FUNCTION_NAME}-throttles" >/dev/null 2>&1 || true
info "alarms deleted"

aws_cli sns delete-topic --topic-arn "arn:aws:sns:${REGION}:$(account_id):${ALARM_TOPIC_NAME}" >/dev/null 2>&1 || true
info "alert topic deleted"

for rule_name in "$DELIVERY_RULE_NAME" "$WATCHDOG_RULE_NAME"; do
  aws_cli events remove-targets --rule "$rule_name" --ids delivery watchdog >/dev/null 2>&1 || true
  aws_cli events delete-rule --name "$rule_name" >/dev/null 2>&1 || true
  info "rule ${rule_name} deleted"
done

for statement_id in "$DELIVERY_STATEMENT_ID" "$WATCHDOG_STATEMENT_ID"; do
  aws_cli lambda remove-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "$statement_id" >/dev/null 2>&1 || true
done
info "invoke permissions removed"

aws_cli lambda delete-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1 || true
info "function deleted"

aws_cli logs delete-log-group --log-group-name "$LOG_GROUP_NAME" >/dev/null 2>&1 || true
info "log group deleted"

aws_cli iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" >/dev/null 2>&1 || true
aws_cli iam delete-role --role-name "$ROLE_NAME" >/dev/null 2>&1 || true
info "role deleted"

for parameter_name in sender recipients reply_to subject_prefix attach_csv notify_when_not_published; do
  aws_cli ssm delete-parameter --name "${CONFIG_PATH}${parameter_name}" >/dev/null 2>&1 || true
done
info "configuration parameters deleted"
info "delivery state under ${STATE_PATH} is kept: it records what has already been"
info "emailed, so a redeploy does not resend a month (delete the parameters by hand"
info "if you want a clean slate)"

if [ "$DELETE_SES_IDENTITY" = "true" ] && [ -n "$SENDER_ADDRESS" ]; then
  aws_cli sesv2 delete-email-identity --email-identity "$SENDER_ADDRESS" >/dev/null 2>&1 || true
  info "SES identity deleted: ${SENDER_ADDRESS}"
else
  info "SES identity kept (pass --delete-ses-identity to remove it)"
fi

aws_cli sesv2 delete-configuration-set --configuration-set-name "$SES_CONFIGURATION_SET" >/dev/null 2>&1 || true
info "configuration set deleted: ${SES_CONFIGURATION_SET}"

if [ "$DELETE_ARTIFACTS" = "true" ]; then
  aws_cli s3 rm "s3://${BUCKET}" --recursive --only-show-errors >/dev/null 2>&1 || true
  aws_cli s3api delete-bucket --bucket "$BUCKET" >/dev/null 2>&1 || true
  info "artefact bucket deleted: s3://${BUCKET}"
else
  info "artefact bucket kept: s3://${BUCKET}"
fi

info "teardown complete"
