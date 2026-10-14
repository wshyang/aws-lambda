#!/usr/bin/env bash
#
# 03 - SES sender identity and configuration set.
#
# Same objects as the AWS::SES::EmailIdentity and AWS::SES::ConfigurationSet
# resources in the application stack.
#
# The identity must be verified before mail is delivered: SES sends a
# verification email to the address, and until it is clicked, sends fail with
# "Email address is not verified". Account-level sandbox status is printed so
# nobody has to guess why a recipient did not receive anything.
#
# Usage: scripts/awscli/03-ses-identity.sh --sender-address sap-patch-day@example.com
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws
require_var SENDER_ADDRESS "--sender-address sap-patch-day@example.com"

if [ "$CREATE_SES_IDENTITY" = "true" ]; then
  if aws_cli sesv2 get-email-identity --email-identity "$SENDER_ADDRESS" >/dev/null 2>&1; then
    info "SES identity already exists: ${SENDER_ADDRESS}"
  else
    info "creating SES identity: ${SENDER_ADDRESS}"
    aws_cli sesv2 create-email-identity \
      --email-identity "$SENDER_ADDRESS" \
      --configuration-set-name "$SES_CONFIGURATION_SET" >/dev/null
    info "SES has emailed a verification link to ${SENDER_ADDRESS}: click it before patch day"
  fi
  aws_cli sesv2 get-email-identity \
    --email-identity "$SENDER_ADDRESS" \
    --query '{VerifiedForSendingStatus: VerifiedForSendingStatus, DkimAttributes: DkimAttributes.Status}'
else
  info "skipping identity creation (--no-ses-identity): assuming ${SENDER_ADDRESS} is already verified"
fi

if aws_cli sesv2 get-configuration-set --configuration-set-name "$SES_CONFIGURATION_SET" >/dev/null 2>&1; then
  info "configuration set already exists: ${SES_CONFIGURATION_SET}"
else
  info "creating configuration set: ${SES_CONFIGURATION_SET}"
  aws_cli sesv2 create-configuration-set \
    --configuration-set-name "$SES_CONFIGURATION_SET" >/dev/null
fi

aws_cli sesv2 put-configuration-set-reputation-options \
  --configuration-set-name "$SES_CONFIGURATION_SET" \
  --reputation-metrics-enabled >/dev/null
info "reputation metrics enabled on ${SES_CONFIGURATION_SET}"

info "SES account state (sandbox accounts can only send to verified addresses):"
aws_cli sesv2 get-account \
  --query '{ProductionAccessEnabled: ProductionAccessEnabled, SendingEnabled: SendingEnabled, Max24HourSend: SendQuota.Max24HourSend, SentLast24Hours: SendQuota.SentLast24Hours}'
