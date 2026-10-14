#!/usr/bin/env bash
#
# 07 - Alerting: SNS topic, optional email subscriptions, two CloudWatch alarms.
#
# Same objects as the SnsTopic, SnsTopicPolicy, AlarmEmailSubscriptions,
# ErrorsAlarm and ThrottlesAlarm resources in the application stack.
#
#   errors    >= 1 error in a day   -> the run raised; delivery is not assumed
#   throttles >= 1 throttle in a day -> a monthly run may have been skipped
#
# Usage: scripts/awscli/07-observability.sh [--alarm-email "ops@example.com"]
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws

TOPIC_ARN=$(aws_cli sns create-topic --name "$ALARM_TOPIC_NAME" --query 'TopicArn' --output text)
info "alert topic: ${TOPIC_ARN}"

TOPIC_POLICY=$(cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudWatchAlarmsToPublish",
      "Effect": "Allow",
      "Principal": {"Service": "cloudwatch.amazonaws.com"},
      "Action": "sns:Publish",
      "Resource": "${TOPIC_ARN}",
      "Condition": {"StringEquals": {"aws:SourceAccount": "$(account_id)"}}
    }
  ]
}
POLICY
)

aws_cli sns set-topic-attributes \
  --topic-arn "$TOPIC_ARN" \
  --attribute-name Policy \
  --attribute-value "$TOPIC_POLICY" >/dev/null
info "topic policy set: only CloudWatch alarms in this account may publish"

if [ -n "$ALARM_EMAIL" ]; then
  existing_subscription=$(aws_cli sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
    --query "Subscriptions[?Endpoint=='${ALARM_EMAIL}'].SubscriptionArn" --output text)
  if [ -n "$existing_subscription" ]; then
    info "subscription already present: ${ALARM_EMAIL}"
  else
    aws_cli sns subscribe \
      --topic-arn "$TOPIC_ARN" \
      --protocol email \
      --notification-endpoint "$ALARM_EMAIL" >/dev/null
    info "subscription requested: ${ALARM_EMAIL} (confirm the email AWS just sent)"
  fi
else
  info "no --alarm-email given; alarms publish to the topic without a subscriber"
fi

put_alarm() {
  alarm_suffix="$1"
  metric_name="$2"
  description="$3"
  aws_cli cloudwatch put-metric-alarm \
    --alarm-name "${FUNCTION_NAME}-${alarm_suffix}" \
    --alarm-description "$description" \
    --namespace AWS/Lambda \
    --metric-name "$metric_name" \
    --statistic Sum \
    --period 86400 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    --dimensions "Name=FunctionName,Value=${FUNCTION_NAME}" \
    --alarm-actions "$TOPIC_ARN" >/dev/null
  info "alarm ${FUNCTION_NAME}-${alarm_suffix} -> ${TOPIC_ARN}"
}

put_alarm errors Errors "The notifier raised an error; no assumption is made about delivery."
put_alarm throttles Throttles "The notifier was throttled; a monthly run may have been skipped."

info "alarms in place"
