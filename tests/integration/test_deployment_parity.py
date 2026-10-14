"""Deployment parity: the CloudFormation template and the CLI scripts describe
the same objects with the same names and defaults.

Two interchangeable paths exist (docs/addenda/0008):
`infrastructure/cloudformation/` and `scripts/awscli/NN-*.sh`. Each path has
exactly one source of truth — the template's `Parameters` block and
`scripts/awscli/00-env.sh` — and this test fails the build when those two
disagree, so a default can never be changed in only one place.

Only `00-env.sh` is executed (with `--print-config`, which needs no AWS
credentials); every other script is read as text.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping

import pytest

from tests.support.templates import (
    APP_TEMPLATE,
    BUCKET_TEMPLATE,
    REPO_ROOT,
    load_template,
    parameters as template_parameters,
    resources as template_resources,
    sub_text,
)

SCRIPTS_DIR = REPO_ROOT / "scripts" / "awscli"
ENV_SCRIPT = SCRIPTS_DIR / "00-env.sh"
BUILD_PACKAGE = REPO_ROOT / "scripts" / "build-package.sh"

# Environment variables that would leak into the snapshot and make the test
# depend on the developer's shell.
INHERITED_KEYS = (
    "FUNCTION_NAME",
    "RUNTIME",
    "ARCHITECTURE",
    "MEMORY_SIZE",
    "TIMEOUT_SECONDS",
    "LOG_RETENTION_DAYS",
    "DELIVERY_SCHEDULE",
    "WATCHDOG_SCHEDULE",
    "ENABLE_WATCHDOG",
    "RETRY_WINDOW_END_SGT",
    "CONFIG_PATH",
    "STATE_PATH",
    "SES_CONFIGURATION_SET",
    "SUBJECT_PREFIX",
    "ATTACH_CSV",
    "NOTIFY_WHEN_NOT_PUBLISHED",
    "HTTP_TIMEOUT_SECONDS",
    "HTTP_MAX_ATTEMPTS",
    "HTTP_BACKOFF_SECONDS",
    "HTTP_DEADLINE_SECONDS",
    "USER_AGENT",
    "DRY_RUN",
    "LOG_LEVEL",
    "ARTIFACT_KEY",
    "CREATE_SES_IDENTITY",
    "REGION",
)

# The template parameter each snapshot value must equal. The names differ
# between the two paths on purpose: shell style here, PascalCase there.
TEMPLATE_PARAMETERS = {
    "FUNCTION_NAME": "FunctionName",
    "RUNTIME": "Runtime",
    "MEMORY_SIZE": "MemorySize",
    "TIMEOUT_SECONDS": "TimeoutSeconds",
    "LOG_RETENTION_DAYS": "LogRetentionDays",
    "ARTIFACT_KEY": "ArtifactKey",
    "SES_CONFIGURATION_SET": "ConfigurationSetName",
    "SUBJECT_PREFIX": "SubjectPrefix",
    "ATTACH_CSV": "AttachCsv",
    "NOTIFY_WHEN_NOT_PUBLISHED": "NotifyWhenNotPublished",
    "DELIVERY_SCHEDULE": "DeliveryScheduleExpression",
    "WATCHDOG_SCHEDULE": "WatchdogScheduleExpression",
    "ENABLE_WATCHDOG": "EnableWatchdog",
    "RETRY_WINDOW_END_SGT": "RetryWindowEndSgt",
    "CONFIG_PATH": "ConfigParameterPath",
    "STATE_PATH": "StateParameterPath",
    "CREATE_SES_IDENTITY": "CreateSesIdentity",
}

# Template resources whose `!Sub` name is derived from the function name; the
# CLI derives the same strings in `derive_names()` in 00-env.sh.
DERIVED_NAMES = {
    "LOG_GROUP_NAME": ("FunctionLogGroup", "LogGroupName"),
    "ROLE_NAME": ("FunctionRole", "RoleName"),
    "DELIVERY_RULE_NAME": ("DeliveryRule", "Name"),
    "WATCHDOG_RULE_NAME": ("WatchdogRule", "Name"),
    "ALARM_TOPIC_NAME": ("SnsTopic", "TopicName"),
}

PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_:]*)\}")

NUMBERED_STEPS = [
    "01-artifact-bucket.sh",
    "02-ssm-parameters.sh",
    "03-ses-identity.sh",
    "04-iam-role.sh",
    "05-lambda-function.sh",
    "06-eventbridge.sh",
    "07-observability.sh",
    "08-verify.sh",
    "09-teardown.sh",
]


def snapshot_from_cli() -> dict[str, str]:
    """Run `00-env.sh --print-config` and parse the snapshot it prints."""
    environment = {key: value for key, value in os.environ.items() if key not in INHERITED_KEYS}
    completed = subprocess.run(
        ["bash", str(ENV_SCRIPT), "--print-config"],
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    parsed: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition("=")
        parsed[key] = value
    return parsed


@pytest.fixture(scope="module")
def snapshot() -> dict[str, str]:
    return snapshot_from_cli()


@pytest.fixture(scope="module")
def app_template() -> Mapping[str, object]:
    return load_template(APP_TEMPLATE)


@pytest.fixture(scope="module")
def parameters(app_template: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    return template_parameters(app_template)


@pytest.fixture(scope="module")
def resources(app_template: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    return template_resources(app_template)


def test_snapshot_covers_every_compared_value(snapshot: dict[str, str]) -> None:
    assert (set(TEMPLATE_PARAMETERS) | set(DERIVED_NAMES)) - set(snapshot) == set()


@pytest.mark.parametrize(("key", "parameter"), sorted(TEMPLATE_PARAMETERS.items()))
def test_defaults_match_the_template(
    snapshot: dict[str, str],
    parameters: Mapping[str, Mapping[str, object]],
    key: str,
    parameter: str,
) -> None:
    assert parameter in parameters, f"template has no parameter {parameter}"
    declared = parameters[parameter]
    assert declared.get("Default") is not None, f"{parameter} has no default to compare"
    assert str(declared["Default"]) == snapshot[key]


@pytest.mark.parametrize(
    ("key", "resource_name"), sorted((key, value[0]) for key, value in DERIVED_NAMES.items())
)
def test_derived_names_match_the_template(
    snapshot: dict[str, str],
    resources: Mapping[str, Mapping[str, object]],
    key: str,
    resource_name: str,
) -> None:
    _, property_name = DERIVED_NAMES[key]
    pattern = sub_text(resources[resource_name]["Properties"][property_name])
    # `${FunctionName}` is the template's spelling of the function name, and the
    # only placeholder a derived name may contain.
    assert PLACEHOLDER.findall(pattern) == ["FunctionName"]
    assert pattern.replace("${FunctionName}", snapshot["FUNCTION_NAME"]) == snapshot[key]


def test_log_group_name_matches_the_function_name(snapshot: dict[str, str]) -> None:
    assert snapshot["LOG_GROUP_NAME"] == f"/aws/lambda/{snapshot['FUNCTION_NAME']}"


def test_artefact_key_matches_the_package_the_build_produces(snapshot: dict[str, str]) -> None:
    assert snapshot["ARTIFACT_KEY"] in BUILD_PACKAGE.read_text(encoding="utf-8")


def test_architecture_is_a_string_in_both_paths(
    snapshot: dict[str, str], parameters: Mapping[str, Mapping[str, object]]
) -> None:
    assert snapshot["ARCHITECTURE"] == str(parameters["Architecture"]["Default"])


def test_the_schedule_is_the_second_tuesday_in_singapore_time(snapshot: dict[str, str]) -> None:
    assert snapshot["DELIVERY_SCHEDULE"] == "cron(0,30 8-12 ? * TUE#2 *)"
    assert snapshot["WATCHDOG_SCHEDULE"] == "cron(0 1 ? * WED#2 *)"
    assert snapshot["RETRY_WINDOW_END_SGT"] == "20:00"


def test_rule_inputs_match_the_run_modes_the_handler_accepts(
    resources: Mapping[str, Mapping[str, object]],
) -> None:
    delivery = resources["DeliveryRule"]["Properties"]["Targets"]
    assert delivery[0]["Input"] == '{"run_mode":"delivery"}'
    watchdog = resources["WatchdogRule"]["Properties"]["Targets"]
    assert watchdog[0]["Input"] == '{"run_mode":"watchdog"}'


def _resolve(value: object, parameters: Mapping[str, Mapping[str, object]]) -> str:
    """A literal, or the default of the template parameter it references."""
    text = str(value)
    if text.startswith("!Ref "):
        referenced = text[len("!Ref ") :]
        assert referenced in parameters, f"environment references unknown parameter {referenced}"
        text = str(parameters[referenced]["Default"])
    return text


def test_function_environment_matches_the_scripts(
    snapshot: dict[str, str],
    resources: Mapping[str, Mapping[str, object]],
    parameters: Mapping[str, Mapping[str, object]],
) -> None:
    raw = resources["NotifierFunction"]["Properties"]["Environment"]["Variables"]
    variables = {key: _resolve(value, parameters) for key, value in raw.items()}
    assert set(variables) == {
        "SSM_CONFIG_PATH",
        "SSM_STATE_PATH",
        "CONFIGURATION_SET",
        "RETRY_WINDOW_END_SGT",
        "HTTP_TIMEOUT_SECONDS",
        "HTTP_MAX_ATTEMPTS",
        "HTTP_BACKOFF_SECONDS",
        "HTTP_DEADLINE_SECONDS",
        "USER_AGENT",
        "DRY_RUN",
        "LOG_LEVEL",
    }
    assert variables["SSM_CONFIG_PATH"] == snapshot["CONFIG_PATH"]
    assert variables["SSM_STATE_PATH"] == snapshot["STATE_PATH"]
    assert variables["CONFIGURATION_SET"] == snapshot["SES_CONFIGURATION_SET"]
    assert variables["RETRY_WINDOW_END_SGT"] == snapshot["RETRY_WINDOW_END_SGT"]
    assert variables["USER_AGENT"] == snapshot["USER_AGENT"]
    assert variables["HTTP_TIMEOUT_SECONDS"] == snapshot["HTTP_TIMEOUT_SECONDS"]
    assert variables["HTTP_MAX_ATTEMPTS"] == snapshot["HTTP_MAX_ATTEMPTS"]
    assert variables["HTTP_BACKOFF_SECONDS"] == snapshot["HTTP_BACKOFF_SECONDS"]
    assert variables["HTTP_DEADLINE_SECONDS"] == snapshot["HTTP_DEADLINE_SECONDS"]
    assert variables["DRY_RUN"] == snapshot["DRY_RUN"]
    assert variables["LOG_LEVEL"] == snapshot["LOG_LEVEL"]


def test_configuration_parameters_are_seeded_under_the_config_path(
    snapshot: dict[str, str],
    parameters: Mapping[str, Mapping[str, object]],
    resources: Mapping[str, Mapping[str, object]],
) -> None:
    names = {
        sub_text(resource["Properties"]["Name"])
        for resource in resources.values()
        if resource.get("Type") == "AWS::SSM::Parameter"
    }
    expected = {
        "${ConfigParameterPath}" + suffix
        for suffix in (
            "sender",
            "recipients",
            "reply_to",
            "subject_prefix",
            "attach_csv",
            "notify_when_not_published",
        )
    }
    assert names == expected
    assert snapshot["CONFIG_PATH"] == parameters["ConfigParameterPath"]["Default"]
    assert snapshot["STATE_PATH"] == parameters["StateParameterPath"]["Default"]
    assert snapshot["CONFIG_PATH"] != snapshot["STATE_PATH"]


def test_no_state_parameter_is_managed_by_cloudformation(
    resources: Mapping[str, Mapping[str, object]],
) -> None:
    for name, resource in resources.items():
        if resource.get("Type") == "AWS::SSM::Parameter":
            assert "StateParameterPath" not in sub_text(resource["Properties"]["Name"]), (
                f"{name} would revert the record of what has been sent"
            )


def test_the_cli_path_does_not_hardcode_a_resource_name(snapshot: dict[str, str]) -> None:
    """Every default lives in 00-env.sh; the other scripts use the variables."""
    derived = [
        snapshot["ROLE_NAME"],
        snapshot["LOG_GROUP_NAME"],
        snapshot["DELIVERY_RULE_NAME"],
        snapshot["WATCHDOG_RULE_NAME"],
        snapshot["ALARM_TOPIC_NAME"],
    ]
    offenders = {}
    for script in sorted(SCRIPTS_DIR.glob("*.sh")):
        if script.name == ENV_SCRIPT.name:
            continue
        text = script.read_text(encoding="utf-8")
        found = [value for value in derived if value in text]
        if found:
            offenders[script.name] = found
    assert offenders == {}


def test_every_numbered_step_exists_and_is_run_by_deploy_all() -> None:
    assert sorted(path.name for path in SCRIPTS_DIR.glob("0[1-9]-*.sh")) == NUMBERED_STEPS
    deploy_all = (SCRIPTS_DIR / "deploy-all.sh").read_text(encoding="utf-8")
    for step in NUMBERED_STEPS:
        if step == "09-teardown.sh":
            continue
        assert step in deploy_all, f"deploy-all.sh never runs {step}"


def test_teardown_removes_what_the_numbered_steps_create() -> None:
    teardown = (SCRIPTS_DIR / "09-teardown.sh").read_text(encoding="utf-8")
    for key in (
        "ROLE_NAME",
        "LOG_GROUP_NAME",
        "DELIVERY_RULE_NAME",
        "WATCHDOG_RULE_NAME",
        "ALARM_TOPIC_NAME",
    ):
        assert key in teardown, f"teardown never removes ${key}"


def test_cloudformation_path_reuses_the_cli_defaults() -> None:
    """The CloudFormation driver sources 00-env.sh, so it cannot invent a name."""
    driver = (REPO_ROOT / "scripts" / "deploy-cloudformation.sh").read_text(encoding="utf-8")
    assert "awscli/00-env.sh" in driver
    assert "resolved_artifact_bucket" in driver
    for parameter in (
        "FunctionName",
        "ArtifactBucket",
        "ArtifactKey",
        "SenderEmailAddress",
        "ConfigParameterPath",
    ):
        assert parameter in driver, f"{parameter} is never passed to the stack"


def test_bootstrap_bucket_default_matches_the_cli_default() -> None:
    document = load_template(BUCKET_TEMPLATE)
    bucket = template_resources(document)["ArtifactBucket"]["Properties"]["BucketName"]
    assert "sap-patch-day-artifacts-${AWS::AccountId}-${AWS::Region}" in str(bucket)
    script = (SCRIPTS_DIR / "01-artifact-bucket.sh").read_text(encoding="utf-8")
    assert "resolved_artifact_bucket" in script
    assert "sap-patch-day-artifacts-" not in script, (
        "the default bucket name lives only in 00-env.sh"
    )


def test_the_snapshot_is_machine_readable_key_value_lines(snapshot: dict[str, str]) -> None:
    assert snapshot["FUNCTION_NAME"] == "sap-security-patch-day-notifier"
    assert all(value == value.strip() for value in snapshot.values())
