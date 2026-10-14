# 0012 — Local SES testing through Floci

**Date:** 2026-10-14
**Spec reference:** §3 FR-3.9 (dry run), FR-5 (email contract); `CLAUDE-testing.md` §1
**Status:** accepted — run against a real Floci container on 2026-10-14

## What was decided

A developer can send the real rendered email through the real `SesMailer` to a
local SES emulator, **Floci** (`floci-io/floci`, MIT, no account, no token):

```bash
scripts/local-aws.sh up       # docker compose, port 4566 on loopback
scripts/local-aws.sh send     # captured October bulletin -> SES v2 SendEmail
scripts/local-aws.sh inbox    # GET /_aws/ses: what the emulator recorded
scripts/local-aws.sh down
```

- `local_cli` gains `--ses-endpoint URL`. Without it nothing changes (the
  `LoggingMailer` stays the default). With it, the CLI builds a `sesv2` boto3
  client at that endpoint with dummy credentials, creates the sender identity
  (an existing identity is fine), and hands the client to the **same
  `SesMailer`** the Lambda uses. The Lambda handler is untouched.
- The compose file pins `floci/floci:2.2.0` (no `latest`, `CLAUDE.md` §1.8) and
  binds to `127.0.0.1`. Docker can be Docker Desktop or Colima.

## Why Floci, not LocalStack

The first attempt used `localstack/localstack:2026.10.0`. That image refuses to
start without a licensed `LOCALSTACK_AUTH_TOKEN` (it exits with code 55, "License
activation failed"), and behind a TLS-inspecting corporate network the container
could not reach LocalStack's licence server at all. Floci needs neither, and
implements both services this repository uses (SES v1/v2, SSM Parameter Store),
exposes the same `/_aws/ses` mailbox endpoint, and enforces sender
verification as AWS does. The CLI flags and the mailbox URL did not change.

## Things a future reader must know

- **It is an emulator.** It records the message; it does not deliver it. It
  proves the request is accepted by an SES v2 implementation, not that SES in
  your account, IAM, a verified domain, sandbox limits or an inbox will accept
  it. `scripts/awscli/08-verify.sh` stays the only layer for those.
- Floci implements a *subset* of SES v2. Anything beyond `CreateEmailIdentity`
  and `SendEmail` (Simple/Raw) is unverified here. The configuration set is not
  created locally, so `send` does not pass one.
- The image is a young project (v2.2.0, released 2026-10-06). Treat a surprising
  local result as a possible emulator difference and check against SES.
- First boot takes about 15 seconds; `up` waits for the mailbox endpoint.

## Validation status

| What | Status |
| ---- | ------ |
| `--ses-endpoint` wiring, sender-identity handling, unreachable-endpoint message | **RUN** — `tests/unit/test_local_cli.py`, botocore `Stubber` |
| `scripts/local-aws.sh`: shellcheck, `bash -n`, Bash 3.2 static guards | **RUN — clean** |
| `local-aws.sh up` / `send` / `inbox` / `down` against Floci 2.2.0 on Colima | **RUN 2026-10-14** — `status=delivered`, 22 notes, one message captured with the expected subject and both recipients as `To`; a second `send` returned `already-delivered` |
| `local-aws.sh init` / `params` (need the `aws` CLI) | **NOT RUN** — no `aws` CLI in the authoring environment (see 0013) |
