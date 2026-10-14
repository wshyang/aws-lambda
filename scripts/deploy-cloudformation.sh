#!/usr/bin/env bash
#
# Deploy the CloudFormation path: bootstrap the artefact bucket, upload the
# package, then deploy the application stack.
#
# The resulting objects are the same names and defaults as the AWS CLI path
# (scripts/awscli/), because both read their defaults from scripts/awscli/00-env.sh
# and the stack template (docs/addenda/0008).
#
# Usage:
#   scripts/deploy-cloudformation.sh \
#       --region ap-southeast-1 \
#       --sender-address sap-patch-day@example.com \
#       --recipients "you@example.com,team@example.com"
#
# Bash 3.2 compatible.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
# shellcheck source=awscli/00-env.sh
. "${SCRIPT_DIR}/awscli/00-env.sh"

TEMPLATE_DIR="${REPO_ROOT}/infrastructure/cloudformation"
BOOTSTRAP_TEMPLATE="${TEMPLATE_DIR}/artifacts-bucket.yaml"
APP_TEMPLATE="${TEMPLATE_DIR}/sap-security-patch-day-notifier.yaml"
ZIP_PATH="${REPO_ROOT}/dist/sap-security-patch-day-notifier.zip"

SKIP_VERIFY="false"
PASSTHROUGH=()
for argument in "$@"; do
  case "$argument" in
    --skip-verify) SKIP_VERIFY="true" ;;
    --delete-stack)
      DELETE_STACK="true"
      ;;
    *) PASSTHROUGH=("${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}" "$argument") ;;
  esac
done
if [ "${#PASSTHROUGH[@]}" -gt 0 ]; then
  parse_common_args "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"
else
  parse_common_args
fi

DELETE_STACK="${DELETE_STACK:-false}"
require_commands aws

if [ "$DELETE_STACK" = "true" ]; then
  info "deleting stack ${STACK_NAME} (artefact bucket and SES identity are kept)"
  aws_cli cloudformation delete-stack --stack-name "$STACK_NAME"
  aws_cli cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
  info "stack deleted"
  exit 0
fi

require_var SENDER_ADDRESS "--sender-address sap-patch-day@example.com"

# ------------------------------------------------------------- prerequisites
if command -v cfn-lint >/dev/null 2>&1; then
  info "cfn-lint: validating templates"
  cfn-lint "${TEMPLATE_DIR}"/*.yaml
else
  warn "cfn-lint is not installed; template linting skipped (scripts/check.sh installs it)"
fi

info "validating templates with CloudFormation"
aws_cli cloudformation validate-template \
  --template-body "file://${BOOTSTRAP_TEMPLATE}" >/dev/null
aws_cli cloudformation validate-template \
  --template-body "file://${APP_TEMPLATE}" >/dev/null

# ------------------------------------------------------------------- package
info "building the deployment package"
"${SCRIPT_DIR}/build-package.sh" >/dev/null
[ -f "$ZIP_PATH" ] || die "package not found: ${ZIP_PATH}"

# --------------------------------------------------------------- bootstrap
BUCKET=$(resolved_artifact_bucket)
if aws_cli s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  info "artefact bucket exists: ${BUCKET}"
else
  info "deploying bootstrap stack for the artefact bucket"
  aws_cli cloudformation deploy \
    --stack-name "${STACK_NAME}-artifacts" \
    --template-file "$BOOTSTRAP_TEMPLATE" \
    --parameter-overrides "ArtifactBucketName=${BUCKET}" \
    --no-fail-on-empty-changeset \
    --tags Application=sap-security-patch-day-notifier >/dev/null
fi

info "uploading package to s3://${BUCKET}/${ARTIFACT_KEY}"
aws_cli s3 cp "$ZIP_PATH" "s3://${BUCKET}/${ARTIFACT_KEY}" --only-show-errors >/dev/null

# ---------------------------------------------------------------- app stack
PARAMETER_OVERRIDES=(
  "FunctionName=${FUNCTION_NAME}"
  "ArtifactBucket=${BUCKET}"
  "ArtifactKey=${ARTIFACT_KEY}"
  "SenderEmailAddress=${SENDER_ADDRESS}"
  "CreateSesIdentity=${CREATE_SES_IDENTITY}"
  "ConfigurationSetName=${SES_CONFIGURATION_SET}"
  "ConfigParameterPath=${CONFIG_PATH}"
  "StateParameterPath=${STATE_PATH}"
  "DeliveryScheduleExpression=${DELIVERY_SCHEDULE}"
  "WatchdogScheduleExpression=${WATCHDOG_SCHEDULE}"
  "EnableWatchdog=${ENABLE_WATCHDOG}"
  "RetryWindowEndSgt=${RETRY_WINDOW_END_SGT}"
  "Runtime=${RUNTIME}"
  "Architecture=${ARCHITECTURE}"
  "MemorySize=${MEMORY_SIZE}"
  "TimeoutSeconds=${TIMEOUT_SECONDS}"
  "LogRetentionDays=${LOG_RETENTION_DAYS}"
)
if [ -n "$RECIPIENT_EMAILS" ]; then
  PARAMETER_OVERRIDES=("${PARAMETER_OVERRIDES[@]+"${PARAMETER_OVERRIDES[@]}"}" "RecipientEmails=${RECIPIENT_EMAILS}")
fi
if [ -n "$REPLY_TO_ADDRESS" ]; then
  PARAMETER_OVERRIDES=("${PARAMETER_OVERRIDES[@]+"${PARAMETER_OVERRIDES[@]}"}" "ReplyToAddress=${REPLY_TO_ADDRESS}")
fi
if [ -n "$ALARM_EMAIL" ]; then
  PARAMETER_OVERRIDES=("${PARAMETER_OVERRIDES[@]+"${PARAMETER_OVERRIDES[@]}"}" "AlarmNotificationEmail=${ALARM_EMAIL}")
fi

info "deploying stack ${STACK_NAME}"
aws_cli cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$APP_TEMPLATE" \
  --parameter-overrides "${PARAMETER_OVERRIDES[@]+"${PARAMETER_OVERRIDES[@]}"}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --tags Application=sap-security-patch-day-notifier

info "stack outputs:"
aws_cli cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs' --output table

if [ "$SKIP_VERIFY" = "true" ]; then
  info "smoke test skipped (--skip-verify)"
else
  info "running the deployed smoke test"
  "${SCRIPT_DIR}/awscli/08-verify.sh" "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"
fi

info "CloudFormation deployment complete"
