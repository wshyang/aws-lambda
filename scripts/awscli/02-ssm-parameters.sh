#!/usr/bin/env bash
#
# 02 - Configuration parameters in SSM Parameter Store.
#
# Same objects as the SSM::Parameter resources in
# infrastructure/cloudformation/sap-security-patch-day-notifier.yaml.
#
# The *state* parameters are deliberately not created here: the function creates
# them on its first write, so that no deployment can ever revert the record of
# what has already been sent (docs/addenda/0004).
#
# Usage: scripts/awscli/02-ssm-parameters.sh --sender-address sap-patch-day@example.com --recipients "one@example.com,two@example.com"
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws
require_var SENDER_ADDRESS "--sender-address sap-patch-day@example.com"
require_var RECIPIENT_EMAILS '--recipients "one@example.com,two@example.com"'

put_string() {
  parameter_name="$1"
  parameter_value="$2"
  aws_cli ssm put-parameter \
    --name "${CONFIG_PATH}${parameter_name}" \
    --type String \
    --value "$parameter_value" \
    --overwrite >/dev/null
  info "parameter ${CONFIG_PATH}${parameter_name} set"
}

put_string_list() {
  parameter_name="$1"
  parameter_value="$2"
  aws_cli ssm put-parameter \
    --name "${CONFIG_PATH}${parameter_name}" \
    --type StringList \
    --value "$parameter_value" \
    --overwrite >/dev/null
  info "parameter ${CONFIG_PATH}${parameter_name} set"
}

existing_value() {
  parameter_name="$1"
  aws_cli ssm get-parameter \
    --name "${CONFIG_PATH}${parameter_name}" \
    --query 'Parameter.Value' \
    --output text 2>/dev/null || printf ''
}

previous_recipients=$(existing_value recipients)
if [ -n "$previous_recipients" ] && [ "$previous_recipients" != "$RECIPIENT_EMAILS" ]; then
  warn "recipients currently in SSM: ${previous_recipients}"
  warn "this run will replace them with: ${RECIPIENT_EMAILS}"
  warn "if this was not intended, re-run with the current list to avoid an accidental change"
fi

put_string sender "$SENDER_ADDRESS"
put_string_list recipients "$RECIPIENT_EMAILS"
put_string subject_prefix "$SUBJECT_PREFIX"
put_string attach_csv "$ATTACH_CSV"
put_string notify_when_not_published "$NOTIFY_WHEN_NOT_PUBLISHED"

if [ -n "$REPLY_TO_ADDRESS" ]; then
  put_string reply_to "$REPLY_TO_ADDRESS"
fi

info "configuration parameters ready under ${CONFIG_PATH}"
