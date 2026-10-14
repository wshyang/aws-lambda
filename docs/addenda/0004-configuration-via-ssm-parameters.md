# 0004 — Configuration: people in SSM Parameter Store, policies in environment variables

**Date:** 2026-10-14
**Spec reference:** §9 (configuration contract), §8 (exact error messages)
**Status:** accepted

## What was decided

Configuration splits along a line that stays meaningful as the system ages:

| Kind | Where | Why that place |
| ---- | ----- | -------------- |
| People (recipient list, sender address, reply-to) | **SSM Parameter Store**, under `SSM_CONFIG_PATH` | Changes are operational, not architectural. Onboarding a new recipient must not require a deployment, a review, or a stack update |
| Policy (retry-window end, HTTP timeouts/attempts/backoff, user agent, dry-run, log level) | **Environment variables** on the function | Reviewed with the code they affect, versioned with the template, visible in the console next to the function |
| Secrets | **Nowhere in this repository** — none are needed. SES/SSM access uses the execution role | Least privilege (NFR-2) |

Specifically:

- Config parameters (read by path, non-recursive): `sender`, `recipients`
  (`StringList`), `reply_to`, `subject_prefix`, `attach_csv`,
  `notify_when_not_published`.
- State parameters (a sub-path, recursive read, `Overwrite=True` writes):
  `last_delivered_month`, `last_delivered_kind`, `last_delivered_at`,
  `last_attempt_at`, `last_watchdog_alert_month`, `last_watchdog_alert_at`.
- The non-recursive read for config and the sub-path for state means **one IAM
  statement each**, scoped to a prefix, with no wildcard beyond the prefix.
- Unknown keys under the config path are ignored, so an operator can add
  documentation-only parameters without breaking a run.
- Every value is validated at load time and fails with the exact message in
  spec §8 before any HTTP request is made.

## Why

- The requirement was "a list of email addresses, configured via AWS
  parameters". SSM Parameter Store gives that without inventing infrastructure:
  no database, no DynamoDB table for six strings, no secrets to rotate.
- Storing recipients in a **CloudFormation parameter alone** would have made
  every address change a stack update, and (worse) would have silently reverted
  any console edit on the next deploy. The templates therefore *seed* SSM with
  the deployment-time list and are documented as doing so — see
  [addendum 0008](./0008-deployment-parity-cloudformation-and-awscli.md).
- The state parameters double as the idempotency mechanism
  ([addendum 0006](./0006-retry-window-idempotency-and-watchdog.md)) — one
  storage mechanism instead of two.
- Validation messages are part of the contract because the operator reads them
  in a CloudWatch log at 4pm on patch day, not in a debugger.

## Practical impact

- **Rotation:** after the first deploy, edit `recipients` in SSM directly. If
  you then run a *stack update*, pass the current list as `RecipientEmails` or
  the update will revert the parameter. Documented in `CLAUDE-deployment.md`
  and exercised in `scripts/awscli/02-ssm-parameters.sh` (which prints a
  warning when the existing value differs from the one being applied).
- **Adding a config key** means: spec §9.2 table, `RuntimeConfig`,
  `sap_patch_day/config.py` validation with a message in spec §8, a seeding
  entry in both deployment paths, and the parity test's fixture — in one
  commit (`CLAUDE.md` §2 row 5).
- **State parameters are never deleted**, only overwritten, so a run in a
  month with no publication leaves an audit trail rather than a blank slate.
- A `StringList` was chosen over a JSON blob so an operator can fix a typo in
  the console without learning a schema; the loader also accepts a
  comma-separated `String` and a JSON array defensively, because operators do
  change parameter types by accident.
