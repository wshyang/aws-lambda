#!/usr/bin/env bash
#
# 05 - Build, upload and deploy the Lambda function.
#
# Same object as the NotifierFunction and FunctionLogGroup resources in
# infrastructure/cloudformation/sap-security-patch-day-notifier.yaml, including
# the environment-variable contract from docs/spec.md §9.1.
#
# Idempotent: creates the function when absent, otherwise updates code and
# configuration. Safe to re-run to ship a new version.
#
# Usage: scripts/awscli/05-lambda-function.sh --sender-address sap-patch-day@example.com --recipients "one@example.com,two@example.com"
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws
require_var SENDER_ADDRESS "--sender-address sap-patch-day@example.com"

BUCKET=$(resolved_artifact_bucket)
ZIP_PATH="${REPO_ROOT}/dist/sap-security-patch-day-notifier.zip"

if [ ! -f "$ZIP_PATH" ]; then
  info "package not found, building it"
  "${REPO_ROOT}/scripts/build-package.sh" >/dev/null
fi
[ -f "$ZIP_PATH" ] || die "package not found after build: ${ZIP_PATH}"

info "uploading package to s3://${BUCKET}/${ARTIFACT_KEY}"
aws_cli s3 cp "$ZIP_PATH" "s3://${BUCKET}/${ARTIFACT_KEY}" --only-show-errors >/dev/null

if aws_cli logs describe-log-groups --log-group-name-prefix "$LOG_GROUP_NAME" \
  --query "logGroups[?logGroupName=='${LOG_GROUP_NAME}'].logGroupName" --output text | grep -q .; then
  info "log group exists: ${LOG_GROUP_NAME}"
else
  info "creating log group: ${LOG_GROUP_NAME}"
  aws_cli logs create-log-group --log-group-name "$LOG_GROUP_NAME" >/dev/null
fi
aws_cli logs put-retention-policy \
  --log-group-name "$LOG_GROUP_NAME" \
  --retention-in-days "$LOG_RETENTION_DAYS" >/dev/null
info "log retention set to ${LOG_RETENTION_DAYS} days"

ENVIRONMENT=$(cat <<ENVJSON
{"Variables":{"SSM_CONFIG_PATH":"${CONFIG_PATH}","SSM_STATE_PATH":"${STATE_PATH}","CONFIGURATION_SET":"${SES_CONFIGURATION_SET}","RETRY_WINDOW_END_SGT":"${RETRY_WINDOW_END_SGT}","HTTP_TIMEOUT_SECONDS":"${HTTP_TIMEOUT_SECONDS}","HTTP_MAX_ATTEMPTS":"${HTTP_MAX_ATTEMPTS}","HTTP_BACKOFF_SECONDS":"${HTTP_BACKOFF_SECONDS}","USER_AGENT":"${USER_AGENT}","DRY_RUN":"${DRY_RUN}","LOG_LEVEL":"${LOG_LEVEL}"}}
ENVJSON
)

if aws_cli lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
  info "updating code for ${FUNCTION_NAME}"
  aws_cli lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --s3-bucket "$BUCKET" \
    --s3-key "$ARTIFACT_KEY" >/dev/null
  aws_cli lambda wait function-updated --function-name "$FUNCTION_NAME"
else
  info "creating function ${FUNCTION_NAME}"
  aws_cli lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --description "SAP Security Patch Day detection and notification" \
    --runtime "$RUNTIME" \
    --architectures "$ARCHITECTURE" \
    --handler "$HANDLER" \
    --role "arn:aws:iam::$(account_id):role/${ROLE_NAME}" \
    --memory-size "$MEMORY_SIZE" \
    --timeout "$TIMEOUT_SECONDS" \
    --code "S3Bucket=${BUCKET},S3Key=${ARTIFACT_KEY}" \
    --tracing-config Mode=Active \
    --environment "$ENVIRONMENT" \
    --tags Application=sap-security-patch-day-notifier >/dev/null
  aws_cli lambda wait function-active-v2 --function-name "$FUNCTION_NAME"
fi

info "applying function configuration"
aws_cli lambda update-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --runtime "$RUNTIME" \
  --architectures "$ARCHITECTURE" \
  --handler "$HANDLER" \
  --role "arn:aws:iam::$(account_id):role/${ROLE_NAME}" \
  --memory-size "$MEMORY_SIZE" \
  --timeout "$TIMEOUT_SECONDS" \
  --tracing-config Mode=Active \
  --environment "$ENVIRONMENT" >/dev/null
aws_cli lambda wait function-updated --function-name "$FUNCTION_NAME"

aws_cli lambda get-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --query '{FunctionName: FunctionName, Runtime: Runtime, Architectures: Architectures, MemorySize: MemorySize, Timeout: Timeout, Handler: Handler, State: State, LastModified: LastModified}'
info "function deployed; logs at ${LOG_GROUP_NAME}"
