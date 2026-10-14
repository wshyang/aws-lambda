"""Structural invariants of the two CloudFormation templates.

Parity (test_deployment_parity.py) proves the template and the CLI scripts agree
with each other. This file proves the template says the *right* thing on its
own: least-privilege IAM, alarms that can actually reach a human, a bucket that
cannot leak or be versioned away, encoded log retention, a pinned runtime, and
no committed account IDs or addresses (docs/addenda/0008, CLAUDE-deployment.md
§6 and §7).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import pytest

from tests.support.templates import (
    APP_TEMPLATE,
    BUCKET_TEMPLATE,
    REPO_ROOT,
    load_template,
    references,
    resources as template_resources,
    sub_text,
)

WILDCARD = "*"
IAM_ACTION = re.compile(r"^[a-z0-9]+:[A-Za-z0-9]+$")
TWELVE_DIGITS = re.compile(r"\b\d{12}\b")
LITERAL_PARTITION = re.compile(r"arn:aws:")
REAL_MAILBOX = re.compile(r"@(?!example\.(?:com|test)\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

DEPLOYABLE_FILES = [
    APP_TEMPLATE,
    BUCKET_TEMPLATE,
    REPO_ROOT / "scripts" / "build-package.sh",
    REPO_ROOT / "scripts" / "deploy-cloudformation.sh",
    *(sorted((REPO_ROOT / "scripts" / "awscli").glob("*.sh"))),
]


@pytest.fixture(scope="module")
def app() -> Mapping[str, object]:
    return load_template(APP_TEMPLATE)


@pytest.fixture(scope="module")
def bucket() -> Mapping[str, object]:
    return load_template(BUCKET_TEMPLATE)


def _statements(app: Mapping[str, object]) -> list:
    role = template_resources(app)["FunctionRole"]
    policies = role["Properties"]["Policies"]
    statements = [
        statement for policy in policies for statement in policy["PolicyDocument"]["Statement"]
    ]
    return statements


def test_the_role_has_exactly_one_inline_policy_trusting_lambda(app: Mapping[str, object]) -> None:
    role = template_resources(app)["FunctionRole"]
    assert len(role["Properties"]["Policies"]) == 1
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert trust[0]["Principal"] == {"Service": "lambda.amazonaws.com"}
    assert trust[0]["Action"] == "sts:AssumeRole"
    assert trust[0]["Effect"] == "Allow"


def test_no_statement_grants_a_wildcard_action(app: Mapping[str, object]) -> None:
    for statement in _statements(app):
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        for action in actions:
            assert action != WILDCARD, f"wildcard action in {statement['Sid']}"
            assert IAM_ACTION.match(action), f"{action} is not a concrete action"
    serialised = str(_statements(app))
    assert "iam:" not in serialised, "the notifier never needs IAM permissions"
    assert "s3:" not in serialised, "the artefact is fetched by CloudFormation, not by the function"


def test_only_tracing_may_reach_every_resource(app: Mapping[str, object]) -> None:
    broad = [
        statement["Sid"] for statement in _statements(app) if statement["Resource"] == WILDCARD
    ]
    assert broad == ["PublishTraces"]


def test_each_scoped_statement_names_its_own_prefix(app: Mapping[str, object]) -> None:
    by_sid = {statement["Sid"]: statement for statement in _statements(app)}
    assert "parameter${ConfigParameterPath}*" in sub_text(by_sid["ReadConfiguration"]["Resource"])
    assert "parameter${StateParameterPath}*" in sub_text(by_sid["WriteDeliveryState"]["Resource"])
    assert by_sid["WriteDeliveryState"]["Action"] == ["ssm:PutParameter"]
    assert all(action.startswith("ssm:Get") for action in by_sid["ReadConfiguration"]["Action"])
    assert "identity/${SenderEmailAddress}" in sub_text(by_sid["SendPatchDayMail"]["Resource"])
    assert by_sid["SendPatchDayMail"]["Action"] == ["ses:SendEmail", "ses:SendRawEmail"]
    assert sub_text(by_sid["WriteOwnLogs"]["Resource"]) == "${FunctionLogGroup.Arn}:*"
    assert by_sid["WriteOwnLogs"]["Action"] == ["logs:CreateLogStream", "logs:PutLogEvents"]


def test_no_managed_policy_is_attached(app: Mapping[str, object]) -> None:
    role = template_resources(app)["FunctionRole"]
    assert "ManagedPolicyArns" not in role["Properties"]
    assert "AWS::IAM::ManagedPolicy" not in str(app)


def test_the_function_is_pinned_and_traced(app: Mapping[str, object]) -> None:
    properties = template_resources(app)["NotifierFunction"]["Properties"]
    assert properties["Runtime"] == "!Ref Runtime"
    assert properties["Architectures"] == ["!Ref Architecture"]
    assert properties["Handler"] == "sap_patch_day.handler.lambda_handler"
    assert properties["TracingConfig"] == {"Mode": "Active"}
    assert properties["LoggingConfig"] == {"LogFormat": "JSON"}
    assert properties["MemorySize"] == "!Ref MemorySize"
    assert properties["Timeout"] == "!Ref TimeoutSeconds"
    assert properties["Code"] == {"S3Bucket": "!Ref ArtifactBucket", "S3Key": "!Ref ArtifactKey"}
    assert properties["Role"] == "!GetAtt FunctionRole.Arn"


def test_the_runtime_and_architecture_defaults_are_the_expected_ones(
    app: Mapping[str, object],
) -> None:
    parameters = app["Parameters"]
    assert parameters["Runtime"]["Default"] == "python3.12"
    assert parameters["Architecture"]["Default"] == "arm64"
    assert parameters["Runtime"]["AllowedValues"] == ["python3.12", "python3.13"]
    assert "python3.8" not in str(app) and "python3.9" not in str(app)


def test_log_retention_is_encoded_not_hardcoded(app: Mapping[str, object]) -> None:
    log_group = template_resources(app)["FunctionLogGroup"]["Properties"]
    assert log_group["RetentionInDays"] == "!Ref LogRetentionDays"
    allowed = app["Parameters"]["LogRetentionDays"]["AllowedValues"]
    assert allowed == sorted(allowed), "CloudFormation requires an ascending list"
    assert 90 in allowed


def test_the_log_group_is_created_before_the_function_exists(app: Mapping[str, object]) -> None:
    assert template_resources(app)["NotifierFunction"].get("DependsOn") == "FunctionLogGroup"


@pytest.mark.parametrize("alarm_name", ["ErrorsAlarm", "ThrottlesAlarm"])
def test_every_alarm_can_reach_a_human(app: Mapping[str, object], alarm_name: str) -> None:
    properties = template_resources(app)[alarm_name]["Properties"]
    assert properties["AlarmActions"] == ["!Ref SnsTopic"]
    assert properties["TreatMissingData"] == "notBreaching"
    assert properties["Namespace"] == "AWS/Lambda"
    assert properties["Period"] == 86400
    assert properties["EvaluationPeriods"] == 1
    assert properties["Threshold"] == 1
    assert properties["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    assert properties["Statistic"] == "Sum"
    assert "!Sub ${FunctionName}-" in properties["AlarmName"]
    assert properties["Dimensions"] == [{"Name": "FunctionName", "Value": "!Ref NotifierFunction"}]


def test_only_cloudwatch_may_publish_to_the_alert_topic(app: Mapping[str, object]) -> None:
    topic = template_resources(app)["SnsTopicPolicy"]["Properties"]["PolicyDocument"]
    statement = topic["Statement"][0]
    assert statement["Principal"] == {"Service": "cloudwatch.amazonaws.com"}
    assert statement["Action"] == "sns:Publish"
    assert statement["Condition"] == {"StringEquals": {"aws:SourceAccount": "!Ref AWS::AccountId"}}


def test_the_alert_subscription_follows_the_parameter(app: Mapping[str, object]) -> None:
    subscription = template_resources(app)["AlarmEmailSubscription"]
    assert subscription["Condition"] == "HasAlarmEmail"
    assert subscription["Properties"]["Endpoint"] == "!Ref AlarmNotificationEmail"
    assert app["Conditions"]["HasAlarmEmail"] == {
        "!Not": [{"!Equals": ["!Ref AlarmNotificationEmail", ""]}]
    }


@pytest.mark.parametrize("rule_name", ["DeliveryRule", "WatchdogRule"])
def test_rules_invoke_the_function_with_an_explicit_run_mode(
    app: Mapping[str, object], rule_name: str
) -> None:
    properties = template_resources(app)[rule_name]["Properties"]
    assert properties["State"] == "ENABLED"
    assert properties["ScheduleExpression"].startswith("!Ref ")
    target = properties["Targets"][0]
    assert target["Arn"] == "!GetAtt NotifierFunction.Arn"
    assert set(target) == {"Id", "Arn", "Input"}
    permission = template_resources(app)[f"{rule_name}Permission"]["Properties"]
    assert permission["Principal"] == "events.amazonaws.com"
    assert permission["SourceArn"] == f"!GetAtt {rule_name}.Arn"
    assert permission["FunctionName"] == "!Ref NotifierFunction"


def test_the_watchdog_can_be_turned_off_without_leaving_a_dangling_rule(
    app: Mapping[str, object],
) -> None:
    resources = template_resources(app)
    assert resources["WatchdogRule"]["Condition"] == "WatchdogEnabled"
    assert resources["WatchdogRulePermission"]["Condition"] == "WatchdogEnabled"
    assert app["Conditions"]["WatchdogEnabled"] == {"!Equals": ["!Ref EnableWatchdog", "true"]}
    assert resources["DeliveryRule"].get("Condition") is None, "delivery is never conditional"


def test_recipients_are_seeded_but_editable_without_a_redeploy(app: Mapping[str, object]) -> None:
    recipients = template_resources(app)["RecipientsParameter"]
    assert recipients["Type"] == "AWS::SSM::Parameter"
    assert recipients["Properties"]["Type"] == "StringList"
    assert recipients["Condition"] == "HasRecipients"
    assert "RecipientEmails" in app["Parameters"]
    assert app["Parameters"]["RecipientEmails"]["Default"] == ""


def test_the_ses_configuration_set_tracks_reputation(app: Mapping[str, object]) -> None:
    properties = template_resources(app)["SesConfigurationSet"]["Properties"]
    assert properties["ReputationOptions"] == {"ReputationMetricsEnabled": True}
    assert properties["Name"] == "!Ref ConfigurationSetName"
    assert template_resources(app)["SesEmailIdentity"]["Condition"] == "CreateIdentity"


def test_the_stack_declares_its_outputs(app: Mapping[str, object]) -> None:
    outputs = app["Outputs"]
    assert {
        "FunctionName",
        "FunctionArn",
        "LogGroupName",
        "DeliverySchedule",
        "ConfigParameterPath",
    } <= set(outputs)
    for output in outputs.values():
        assert output["Description"]


def test_the_artefact_bucket_cannot_leak_or_be_deleted_implicitly(
    bucket: Mapping[str, object],
) -> None:
    properties = template_resources(bucket)["ArtifactBucket"]["Properties"]
    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert properties["OwnershipControls"] == {
        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
    }


def test_the_artefact_bucket_is_encrypted_with_a_bucket_key(bucket: Mapping[str, object]) -> None:
    encryption = template_resources(bucket)["ArtifactBucket"]["Properties"]["BucketEncryption"]
    rule = encryption["ServerSideEncryptionConfiguration"][0]
    assert rule["ServerSideEncryptionByDefault"] == {"SSEAlgorithm": "AES256"}
    assert rule["BucketKeyEnabled"] is True


def test_the_artefact_bucket_expires_what_it_no_longer_needs(bucket: Mapping[str, object]) -> None:
    rules = template_resources(bucket)["ArtifactBucket"]["Properties"]["LifecycleConfiguration"][
        "Rules"
    ]
    by_id = {rule["Id"]: rule for rule in rules}
    assert by_id["abort-incomplete-uploads"]["AbortIncompleteMultipartUpload"] == {
        "DaysAfterInitiation": 7
    }
    assert by_id["expire-old-versions"]["NoncurrentVersionExpiration"] == {
        "NoncurrentDays": "!Ref RetentionDays"
    }
    assert all(rule["Status"] == "Enabled" for rule in rules)


def test_both_templates_describe_themselves_and_use_no_hardcoded_partition() -> None:
    for path in (APP_TEMPLATE, BUCKET_TEMPLATE):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("AWSTemplateFormatVersion")
        assert path.name not in text, "the template should not reference itself"
        assert "Description: >-" in text
        assert LITERAL_PARTITION.search(text) is None, (
            "use ${AWS::Partition} instead of a literal partition"
        )
        assert "latest" not in text


@pytest.mark.parametrize("path", DEPLOYABLE_FILES, ids=lambda path: path.name)
def test_nothing_deployable_commits_a_secret_or_a_real_address(path) -> None:
    text = path.read_text(encoding="utf-8")
    assert TWELVE_DIGITS.search(text) is None, "a 12-digit account ID is committed"
    assert REAL_MAILBOX.search(text) is None, "a real mailbox is committed; use example.com/test"
    assert "AKIA" not in text and "BEGIN PRIVATE KEY" not in text


def test_the_function_references_every_parameter_it_is_given(app: Mapping[str, object]) -> None:
    resources = template_resources(app)
    referenced = set(references(resources["NotifierFunction"]))
    assert {"Runtime", "Architecture", "MemorySize", "TimeoutSeconds"} <= referenced
    assert referenced <= set(app["Parameters"]), "the function references an undeclared parameter"
