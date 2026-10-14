#!/usr/bin/env bash
#
# 04 - Execution role and inline policy.
#
# Same object (and the same statements) as the FunctionRole resource in
# infrastructure/cloudformation/sap-security-patch-day-notifier.yaml:
#
#   logs    - write only to this function's log group
#   ssm     - read the configuration path, write only the state path
#   ses     - send only as the configured sender identity
#   xray    - publish traces
#
# No wildcard actions, and no AWS-managed "full access" policy.
#
# Usage: scripts/awscli/04-iam-role.sh --sender-address sap-patch-day@example.com
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws
require_var SENDER_ADDRESS "--sender-address sap-patch-day@example.com"

ACCOUNT=$(account_id)
TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if aws_cli iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  info "role already exists: ${ROLE_NAME}"
else
  info "creating role: ${ROLE_NAME}"
  aws_cli iam create-role \
    --role-name "$ROLE_NAME" \
    --description "Execution role for the SAP Security Patch Day notifier" \
    --assume-role-policy-document "$TRUST_POLICY" >/dev/null
fi

keep_in_sync_note="# statements below mirror the CloudFormation template; keep them identical"
INLINE_POLICY=$(cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteOwnLogs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT}:log-group:${LOG_GROUP_NAME}:*"
    },
    {
      "Sid": "ReadConfiguration",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
      "Resource": "arn:aws:ssm:${REGION}:${ACCOUNT}:parameter${CONFIG_PATH}*"
    },
    {
      "Sid": "WriteDeliveryState",
      "Effect": "Allow",
      "Action": ["ssm:PutParameter"],
      "Resource": "arn:aws:ssm:${REGION}:${ACCOUNT}:parameter${STATE_PATH}*"
    },
    {
      "Sid": "SendPatchDayMail",
      "Effect": "Allow",
      "Action": ["ses:SendEmail", "ses:SendRawEmail"],
      "Resource": "arn:aws:ses:${REGION}:${ACCOUNT}:identity/${SENDER_ADDRESS}"
    },
    {
      "Sid": "PublishTraces",
      "Effect": "Allow",
      "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
      "Resource": "*"
    }
  ]
}
POLICY
)
: "$keep_in_sync_note"

aws_cli iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document "$INLINE_POLICY" >/dev/null
info "inline policy ${POLICY_NAME} applied to ${ROLE_NAME}"

ROLE_ARN=$(aws_cli iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
info "role arn: ${ROLE_ARN}"
info "waiting for IAM propagation before the role is used by Lambda"
sleep 10
