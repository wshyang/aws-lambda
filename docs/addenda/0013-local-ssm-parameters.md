# 0013 — Local SSM parameters through Floci

**Date:** 2026-10-14
**Spec reference:** §9 (configuration), `docs/addenda/0004` (SSM as the source of people and state)
**Status:** accepted — the CLI path was run against Floci; the `aws`-CLI path was not

## What was decided

The dry-run CLI can read its configuration from, and keep its state in, SSM at
a local emulator endpoint (Floci, see 0012), so the real `SsmConfigSource` and
`SsmStateStore` adapters are exercised locally rather than only the in-memory
stand-ins.

- `local_cli --ssm-endpoint URL` seeds `sender` and `recipients` under
  `SSM_CONFIG_PATH` (default `/sap-security-patch-day/`) with the same names and
  types the deployment writes (`recipients` is a `StringList`, the rest `String`),
  then loads the configuration **back through `SsmConfigSource`**, so what runs is
  what SSM returned, not what was typed. State goes through `SsmStateStore`.
- `scripts/local-aws.sh init` runs the existing
  `scripts/awscli/02-ssm-parameters.sh` against the emulator (`AWS_ENDPOINT_URL`,
  dummy credentials, `AWS_PROFILE` cleared). It reuses that script rather than
  copying it, so there is one definition of the parameter names, types and
  defaults, and the deployment-parity test keeps covering it. `params` lists what
  the emulator holds.
- The state parameters are still never created by a script; the first write
  creates them (addendum 0004). The handler is unchanged.

## What the CloudFormation and CLI paths already initialise

Both create `sender`, `recipients`, `subject_prefix`, `attach_csv`,
`notify_when_not_published`, and `reply_to` when given, and neither creates state.
Nothing in this change alters either deployment path, so no parity edit was needed.
Gap noticed, not acted on: neither path seeds a `retry_window_end` parameter
(it is an environment variable, `RETRY_WINDOW_END_SGT`).

## Practical impact

- Parameter-path, type or `Recursive` mistakes in the SSM adapters now show up
  locally instead of at 16:00 on patch day.
- `--ssm-endpoint` writes to whatever endpoint it is given, with
  `Overwrite=True`. Point it only at the local emulator.

## Validation status

| What | Status |
| ---- | ------ |
| Seeding names/types, read-back through `SsmConfigSource`, state-store selection, unreachable endpoint | **RUN** — `tests/unit/test_local_cli.py`, botocore `Stubber` |
| `local_cli --ssm-endpoint` against Floci 2.2.0 | **RUN 2026-10-14** — `sender` (String), `recipients` (StringList) and four state parameters (`last_delivered_month`, `last_attempt_at`, `last_delivered_at`, `last_delivered_kind`) were written and read back; a second run reported `already-delivered` from the state held in SSM |
| `scripts/local-aws.sh init/params`: shellcheck, `bash -n`, portability gates | **RUN — clean** |
| `init` / `params` against a running emulator | **NOT RUN** — no `aws` CLI in the authoring environment |
