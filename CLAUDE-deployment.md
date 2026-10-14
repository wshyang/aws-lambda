# CLAUDE-deployment.md — deployment discipline, CloudFormation and AWS CLI parity

`GREENFIELD.md` §9 and §10, applied to a serverless artefact. Read the deferred
sections first: [addendum 0009](../docs/addenda/0009-deferred-container-and-shiphats-sections.md).

## 1. Two paths, one set of objects

| Object | CloudFormation | AWS CLI (`scripts/awscli/`) |
| ------ | -------------- | --------------------------- |
| Artefact bucket | `infrastructure/cloudformation/artifacts-bucket.yaml` | `01-artifact-bucket.sh` |
| Config + state parameters | `sap-security-patch-day-notifier.yaml` | `02-ssm-parameters.sh` |
| SES sender identity | `sap-security-patch-day-notifier.yaml` | `03-ses-identity.sh` |
| IAM role + inline policy | `sap-security-patch-day-notifier.yaml` | `04-iam-role.sh` |
| Lambda function, log group, env | `sap-security-patch-day-notifier.yaml` | `05-lambda-function.sh` |
| EventBridge rules + permissions | `sap-security-patch-day-notifier.yaml` | `06-eventbridge.sh` |
| SNS alerts + CloudWatch alarms | `sap-security-patch-day-notifier.yaml` | `07-observability.sh` |

The two paths may be used interchangeably but **not mixed for the same object
at the same time**: CloudFormation would fight the CLI over drift. Pick one per
environment; that decision is recorded per environment in the runbook below.

- Resource **names, defaults, runtimes, memory, timeout, schedules, parameter
  paths and SSM values are identical** across both paths, and
  `tests/integration/test_deployment_parity.py` fails the build when they
  drift. Names come from one place in each path: the "Parameters" block of the
  template, and `scripts/awscli/00-env.sh`; the parity test compares them.
- Every CLI script is individually idempotent (create-or-update) and safe to
  re-run; `deploy-all.sh` runs the full ordered sequence and is safe to re-run.

## 2. Packaging

- `scripts/build-package.sh` produces `dist/sap-security-patch-day-notifier.zip`
  containing the `sap_patch_day/` package **at the archive root** (so the
  handler is `sap_patch_day.handler.lambda_handler`), and prints the SHA-256 of
  the artefact.
- The zip is deterministic (fixed timestamps, sorted entries) so identical
  source produces an identical hash — a drift check a reviewer can eyeball.
- **No dependencies are vendored.** The artefact contains only this
  repository's source; see [addendum 0003](../docs/addenda/0003-runtime-and-dependency-policy.md).
- Architecture is `arm64`, runtime `python3.12`, both pinned in the template and
  in `00-env.sh`.

## 3. CloudFormation path

```bash
scripts/build-package.sh
scripts/deploy-cloudformation.sh --profile <profile> --region ap-southeast-1 \
    --sender-address sap-patch-day@example.com \
    --recipients "you@example.com,team@example.com"
```

- `scripts/deploy-cloudformation.sh` uploads the artefact to the bucket, then
  `aws cloudformation deploy` (change-set visible, `--no-fail-on-empty-changeset`
  absent so real failures surface), then prints the stack outputs.
- Stack parameters are documented in `docs/spec.md` §10; every parameter has a
  default except the sender address, which must be SES-verifiable.
- Deletion: `aws cloudformation delete-stack --stack-name <name>` (log group
  and SES identity are deleted with the stack; the artefact bucket is retained
  by design and must be removed deliberately).

## 4. AWS CLI path (CloudShell)

```bash
# In CloudShell, repository already cloned
bash scripts/awscli/deploy-all.sh --profile default --region ap-southeast-1 \
    --sender-address sap-patch-day@example.com \
    --recipients "you@example.com,team@example.com"
```

- Ordered, numbered scripts; each one prints what it created or updated and is
  safe to re-run.
- `09-teardown.sh` reverses the order and asks for confirmation before deleting
  anything; there is no silent destructive default.
- `08-verify.sh` performs the deployed smoke test (dry-run invoke with a fixed
  target month), then prints the resolved configuration, the EventBridge
  schedule in Singapore time and the last CloudWatch log lines.

## 5. Shell discipline

`GREENFIELD.md` §10.4 applies to every script here:

- **Bash 3.2 compatible** even though CloudShell ships Bash 5: no associative
  arrays, no `mapfile`, no `${var,,}`/`${var^^}`, empty arrays expanded as
  `${a[@]+"${a[@]}"}` under `set -u`.
- `set -euo pipefail` on all executable scripts.
- `shellcheck` (via `scripts/check.sh`) plus `bash -n` syntax checks plus a
  static Bash-3.2 guard test (`tests/integration/test_shell_portability.py`)
  that greps for the forbidden constructs, so Linux CI catches a macOS-only
  construct before it ships.

## 6. Secrets and configuration

- Nothing sensitive is committed: no account IDs, no ARNs with account numbers,
  no real addresses (fixtures use `example.com` / `example.test`).
- Runtime configuration is SSM Parameter Store; IAM credentials come from the
  caller's profile (CloudShell or a named profile), never from this repository.
- The Lambda's environment variables hold **paths and policies**, never
  addresses; see `docs/spec.md` §9.

## 7. Pinning

- Runtime `python3.12`, architecture `arm64`, memory, timeout, log retention,
  EventBridge schedules and the SES configuration set are all explicit values
  in both paths — a reviewer can diff two files and see the whole contract.
- No `latest` tags and no unbounded wildcards in IAM actions: the policy grants
  `logs:*` only on this function's log group, `ssm:GetParameter*` only on the
  config path, `ssm:PutParameter` only on the state path, and
  `ses:SendEmail`/`SendRawEmail` only on the sender identity.

## 8. Runbook (per environment)

1. `scripts/check.sh` — static and unit layers, green.
2. Deploy with **one** path (CloudFormation *or* CLI) and record which.
3. `scripts/awscli/08-verify.sh` — dry-run smoke test against a known-good
   month; confirm the rendered email in the log, not in the recipients' inbox.
4. `scripts/live-check.sh` — optional: run the live HTTP layer from a host with
   egress to `support.sap.com`.
5. Confirm the SES sender identity (click the verification link sent by SES) and
   check the SES sandbox status before depending on real delivery.
6. Expect real delivery on the next second Tuesday; the watchdog rule on the
   second Wednesday emails the recipients if no bulletin went out.
