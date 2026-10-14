#!/usr/bin/env bash
#
# Shared configuration and helpers for the AWS CLI deployment path.
#
# This file is the single source of truth for the CLI path, exactly as the
# Parameters block of `infrastructure/cloudformation/sap-security-patch-day-notifier.yaml`
# is for the CloudFormation path. `tests/integration/test_deployment_parity.py`
# fails the build when the two disagree (docs/addenda/0008).
#
# It is *sourced*, never executed directly. Bash 3.2 compatible: no associative
# arrays, no mapfile, no ${var,,}, empty arrays expanded as ${a[@]+"${a[@]}"}.
#
set -euo pipefail

# ---------------------------------------------------------------- defaults
FUNCTION_NAME="${FUNCTION_NAME:-sap-security-patch-day-notifier}"
HANDLER="${HANDLER:-sap_patch_day.handler.lambda_handler}"
RUNTIME="${RUNTIME:-python3.12}"
ARCHITECTURE="${ARCHITECTURE:-arm64}"
MEMORY_SIZE="${MEMORY_SIZE:-256}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-60}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-90}"

DELIVERY_SCHEDULE="${DELIVERY_SCHEDULE:-cron(0,30 8-12 ? * TUE#2 *)}"
WATCHDOG_SCHEDULE="${WATCHDOG_SCHEDULE:-cron(0 1 ? * WED#2 *)}"
ENABLE_WATCHDOG="${ENABLE_WATCHDOG:-true}"
RETRY_WINDOW_END_SGT="${RETRY_WINDOW_END_SGT:-20:00}"

CONFIG_PATH="${CONFIG_PATH:-/sap-security-patch-day/}"
STATE_PATH="${STATE_PATH:-/sap-security-patch-day/state/}"
SES_CONFIGURATION_SET="${SES_CONFIGURATION_SET:-sap-security-patch-day}"
SUBJECT_PREFIX="${SUBJECT_PREFIX:-[SAP Security Patch Day]}"
ATTACH_CSV="${ATTACH_CSV:-false}"
NOTIFY_WHEN_NOT_PUBLISHED="${NOTIFY_WHEN_NOT_PUBLISHED:-true}"

HTTP_TIMEOUT_SECONDS="${HTTP_TIMEOUT_SECONDS:-20}"
HTTP_MAX_ATTEMPTS="${HTTP_MAX_ATTEMPTS:-3}"
HTTP_BACKOFF_SECONDS="${HTTP_BACKOFF_SECONDS:-2}"
HTTP_DEADLINE_SECONDS="${HTTP_DEADLINE_SECONDS:-30}"
USER_AGENT="${USER_AGENT:-sap-patch-day-notifier/1 (aws-lambda; +https://support.sap.com)}"
DRY_RUN="${DRY_RUN:-false}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

ARTIFACT_KEY="${ARTIFACT_KEY:-sap-security-patch-day-notifier.zip}"
CREATE_SES_IDENTITY="${CREATE_SES_IDENTITY:-true}"

REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-southeast-1}}}"
SENDER_ADDRESS="${SENDER_ADDRESS:-}"
RECIPIENT_EMAILS="${RECIPIENT_EMAILS:-}"
REPLY_TO_ADDRESS="${REPLY_TO_ADDRESS:-}"
ALARM_EMAIL="${ALARM_EMAIL:-}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-}"
ASSUME_YES="${ASSUME_YES:-false}"

# Derived names. The CloudFormation template derives the same values with
# !Sub expressions; keep the two in step (the parity test checks them).
derive_names() {
  STACK_NAME="${STACK_NAME:-${FUNCTION_NAME}}"
  ROLE_NAME="${ROLE_NAME:-${FUNCTION_NAME}-role}"
  LOG_GROUP_NAME="${LOG_GROUP_NAME:-/aws/lambda/${FUNCTION_NAME}}"
  DELIVERY_RULE_NAME="${DELIVERY_RULE_NAME:-${FUNCTION_NAME}-delivery}"
  WATCHDOG_RULE_NAME="${WATCHDOG_RULE_NAME:-${FUNCTION_NAME}-watchdog}"
  ALARM_TOPIC_NAME="${ALARM_TOPIC_NAME:-${FUNCTION_NAME}-alerts}"
  POLICY_NAME="${POLICY_NAME:-${FUNCTION_NAME}}"
  DELIVERY_STATEMENT_ID="${DELIVERY_STATEMENT_ID:-${DELIVERY_RULE_NAME}}"
  WATCHDOG_STATEMENT_ID="${WATCHDOG_STATEMENT_ID:-${WATCHDOG_RULE_NAME}}"
}

# ------------------------------------------------------------- AWS plumbing
build_common_args() {
  COMMON_ARGS=()
  if [ -n "${AWS_PROFILE:-}" ]; then
    COMMON_ARGS=("${COMMON_ARGS[@]+"${COMMON_ARGS[@]}"}" --profile "$AWS_PROFILE")
  fi
  COMMON_ARGS=("${COMMON_ARGS[@]+"${COMMON_ARGS[@]}"}" --region "$REGION")
  COMMON_ARGS=("${COMMON_ARGS[@]+"${COMMON_ARGS[@]}"}" --output json)
}

aws_cli() {
  aws "${COMMON_ARGS[@]+"${COMMON_ARGS[@]}"}" "$@"
}

account_id() {
  if [ -z "${AWS_ACCOUNT_ID:-}" ]; then
    AWS_ACCOUNT_ID=$(aws_cli sts get-caller-identity --query Account --output text)
  fi
  printf '%s' "$AWS_ACCOUNT_ID"
}

default_artifact_bucket() {
  printf 'sap-patch-day-artifacts-%s-%s' "$(account_id)" "$REGION"
}

resolved_artifact_bucket() {
  if [ -n "$ARTIFACT_BUCKET" ]; then
    printf '%s' "$ARTIFACT_BUCKET"
  else
    default_artifact_bucket
  fi
}

# ------------------------------------------------------------------ output
info() { printf '[%s] %s\n' "$(basename "$0")" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$(basename "$0")" "$*" >&2; }
die() { printf '[%s] ERROR: %s\n' "$(basename "$0")" "$*" >&2; exit 1; }

require_commands() {
  for required_command in "$@"; do
    command -v "$required_command" >/dev/null 2>&1 || die "required command not found: $required_command"
  done
}

require_var() {
  variable_name="$1"
  example="$2"
  eval "variable_value=\${${variable_name}:-}"
  if [ -z "$variable_value" ]; then
    die "${variable_name} is required (for example: ${example})"
  fi
}

confirm() {
  if [ "$ASSUME_YES" = "true" ]; then
    return 0
  fi
  printf '%s [y/N] ' "$1"
  read -r answer
  case "$answer" in
    y | Y | yes | YES) return 0 ;;
    *) return 1 ;;
  esac
}

# ------------------------------------------------------------ arg parsing
parse_common_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile)
        AWS_PROFILE="$2"
        shift 2
        ;;
      --region)
        REGION="$2"
        shift 2
        ;;
      --function-name)
        FUNCTION_NAME="$2"
        shift 2
        ;;
      --stack-name)
        STACK_NAME="$2"
        FUNCTION_NAME="$2"
        shift 2
        ;;
      --sender-address)
        SENDER_ADDRESS="$2"
        shift 2
        ;;
      --recipients)
        RECIPIENT_EMAILS="$2"
        shift 2
        ;;
      --reply-to)
        REPLY_TO_ADDRESS="$2"
        shift 2
        ;;
      --alarm-email)
        ALARM_EMAIL="$2"
        shift 2
        ;;
      --artifact-bucket)
        ARTIFACT_BUCKET="$2"
        shift 2
        ;;
      --artifact-key)
        ARTIFACT_KEY="$2"
        shift 2
        ;;
      --no-watchdog)
        ENABLE_WATCHDOG="false"
        shift
        ;;
      --no-ses-identity)
        CREATE_SES_IDENTITY="false"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      --yes)
        ASSUME_YES="true"
        shift
        ;;
      -h | --help)
        print_usage
        exit 0
        ;;
      *)
        die "unknown argument: $1 (try --help)"
        ;;
    esac
  done
  derive_names
  build_common_args
}

print_usage() {
  cat <<'USAGE'
Deployment flags shared by every script in scripts/awscli:

  --profile NAME        AWS profile to use
  --region NAME         AWS region (default: ap-southeast-1)
  --function-name NAME  Lambda function name (default: sap-security-patch-day-notifier)
  --stack-name NAME     Alias for --function-name
  --sender-address ADDR Verified SES sender identity (required for 02/03/04/05)
  --recipients LIST     Comma-separated recipients seeded into SSM
  --reply-to ADDR       Optional reply-to address
  --alarm-email ADDR    Address subscribed to the alarm topic
  --artifact-bucket B   Artefact bucket (default: sap-patch-day-artifacts-<account>-<region>)
  --artifact-key K      Artefact key (default: sap-security-patch-day-notifier.zip)
  --no-watchdog         Do not create the second-Wednesday watchdog rule
  --no-ses-identity     Do not create the SES identity (it already exists)
  --dry-run             Deploy the function with DRY_RUN=true (no email, no state writes)
  --yes                 Do not prompt for confirmation
USAGE
}

# Machine-readable snapshot used by the deployment parity test. The value names
# are the contract between the two deployment paths.
print_config_snapshot() {
  cat <<SNAPSHOT
FUNCTION_NAME=${FUNCTION_NAME}
HANDLER=${HANDLER}
RUNTIME=${RUNTIME}
ARCHITECTURE=${ARCHITECTURE}
MEMORY_SIZE=${MEMORY_SIZE}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS}
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS}
LOG_GROUP_NAME=${LOG_GROUP_NAME}
ROLE_NAME=${ROLE_NAME}
DELIVERY_RULE_NAME=${DELIVERY_RULE_NAME}
WATCHDOG_RULE_NAME=${WATCHDOG_RULE_NAME}
ALARM_TOPIC_NAME=${ALARM_TOPIC_NAME}
DELIVERY_SCHEDULE=${DELIVERY_SCHEDULE}
WATCHDOG_SCHEDULE=${WATCHDOG_SCHEDULE}
ENABLE_WATCHDOG=${ENABLE_WATCHDOG}
RETRY_WINDOW_END_SGT=${RETRY_WINDOW_END_SGT}
CONFIG_PATH=${CONFIG_PATH}
STATE_PATH=${STATE_PATH}
SES_CONFIGURATION_SET=${SES_CONFIGURATION_SET}
SUBJECT_PREFIX=${SUBJECT_PREFIX}
ATTACH_CSV=${ATTACH_CSV}
NOTIFY_WHEN_NOT_PUBLISHED=${NOTIFY_WHEN_NOT_PUBLISHED}
HTTP_TIMEOUT_SECONDS=${HTTP_TIMEOUT_SECONDS}
HTTP_MAX_ATTEMPTS=${HTTP_MAX_ATTEMPTS}
HTTP_BACKOFF_SECONDS=${HTTP_BACKOFF_SECONDS}
HTTP_DEADLINE_SECONDS=${HTTP_DEADLINE_SECONDS}
USER_AGENT=${USER_AGENT}
DRY_RUN=${DRY_RUN}
LOG_LEVEL=${LOG_LEVEL}
ARTIFACT_KEY=${ARTIFACT_KEY}
CREATE_SES_IDENTITY=${CREATE_SES_IDENTITY}
SNAPSHOT
}

if [ "${1:-}" = "--print-config" ]; then
  derive_names
  print_config_snapshot
  exit 0
fi
