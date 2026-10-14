# 0008 — Deployment parity: CloudFormation and AWS CLI describe the same objects

**Date:** 2026-10-14
**Spec reference:** §10 (deployment contract)
**Status:** accepted

## What was decided

Two interchangeable deployment paths, created to describe the **same objects**
with the **same names and defaults**:

| Path | Form | Intended use |
| ---- | ---- | ------------ |
| `infrastructure/cloudformation/` | Declarative: `artifacts-bucket.yaml` (bootstrap) + `sap-security-patch-day-notifier.yaml` (stack), driven by `scripts/deploy-cloudformation.sh` | Repeatable environments, reviewable diffs, `aws cloudformation deploy` from CloudShell or CI |
| `scripts/awscli/NN-*.sh` | Imperative, ordered, idempotent `aws` CLI calls sharing `scripts/awscli/00-env.sh` | The "just do it in CloudShell" path; also the path that works when a stack is not wanted, or when an organisation's stack policy forbids unnamed resources |

Rules that keep them honest:

1. **One source of truth per path** — the template's `Parameters` block and
   `scripts/awscli/00-env.sh`. No other file hardcodes a resource name.
2. **A parity test** (`tests/integration/test_deployment_parity.py`) loads
   `00-env.sh` in a subprocess, parses the CloudFormation YAML, and fails if
   the function name, role name, rule names, log group, topic name, runtime,
   architecture, memory, timeout, log retention, schedule expressions,
   retry-window end, config path or state path disagree.
3. **One path per environment at a time.** CloudFormation will revert drift
   introduced by the CLI path, and the CLI path has no drift detection —
   mixing them for the same object is a bug, not a hybrid.
4. **Structural invariants are tested too**, not just parity: no wildcard IAM
   actions beyond the scoped prefixes, alarms wired to the SNS topic, the
   artefact bucket encrypted and versioned, log retention set, runtime pinned
   (`tests/integration/test_template_invariants.py`).

## Why

- The user asked for both, and both are legitimate: CloudFormation for anything
  that will be maintained, CLI for the CloudShell quick path. Two paths that
  silently diverge are worse than one path, so the divergence itself is what
  the parity test forbids.
- The CloudFormation path needs an artefact bucket, which is why the bootstrap
  template exists separately: the app stack should not own a bucket whose
  deletion would remove the code that a rollback needs.
- The CLI path must be genuinely usable by a human in CloudShell at 3pm on
  patch day: numbered, ordered, idempotent, and printing exactly what it
  created or updated.

## Practical impact

- **Changing a default** (memory, schedule, retention) means editing the
  template *and* `00-env.sh` in the same commit, or CI fails.
- **CFN-seeded SSM values vs. later edits:** both paths seed `sender` and
  `recipients` from their parameters. After onboarding a new recipient via SSM,
  either pass the current list on the next stack update or the update reverts
  it (the script path warns when the stored value differs). Recorded in
  [addendum 0004](./0004-configuration-via-ssm-parameters.md).
- **Teardown** is explicit on both sides: `delete-stack` for CloudFormation,
  `09-teardown.sh` (with a confirmation prompt and reverse ordering) for the
  CLI path. Neither deletes the artefact bucket implicitly.
- **The deployed smoke test is a script, not a unit test**
  (`scripts/awscli/08-verify.sh`, dry-run invoke): it is the only layer that
  proves IAM, SES identity state and EventBridge targets actually line up, and
  it must be run by a human with credentials — so it cannot be part of
  `scripts/check.sh`.
