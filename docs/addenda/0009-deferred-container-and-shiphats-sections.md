# 0009 — GREENFIELD §10.1 (containers) and §10.3 (SHIP-HATS) deferred, with revisit triggers

**Date:** 2026-10-14
**Spec reference:** §10 (deployment contract); `CLAUDE.md` §2 rows 12 and 13
**Status:** accepted (reversible)

## What was decided

Two sections of the adopted discipline are **deferred**, not rejected:

| Upstream section | Decision | Revisit trigger |
| ---------------- | -------- | --------------- |
| §10.1 Container files (`Dockerfile`, `Dockerfile.dev`, `Dockerfile.cloudflared`, `.dockerignore`, port-3000-on-`0.0.0.0` contract, proxy trust by address) | Not created | The moment any component here runs as a long-lived process (a webhook receiver, an admin UI, a scheduler microservice), or the deployment target moves from Lambda to a container platform |
| §10.3 SHIP-HATS inert pipeline (`*.shiphats`, `activate-shiphats.sh`, SonarQube/Nexus/Trivy wiring, `CF_LEVEL`) | Not created | The moment this repository is mirrored into SHIP-HATS (`sgts.gitlab-dedicated.com`) or the code is shipped into a government environment |

The *spirit* of both sections **is** honoured, through the artefacts this
project actually deploys:

| §10 principle | Carried here by |
| ------------- | --------------- |
| Decide how the project ships before the first feature; commit deployment files with the first code | Both deployment paths and `scripts/build-package.sh` land with the first commit |
| No secrets in config; placeholders, never credentials | Spec §9: SSM holds people, env holds policy, nothing secret is committed; fixtures use `example.test` |
| Pin everything; no `latest` | Pinned runtime, architecture, memory, timeout, retention, schedule expressions, SES configuration set — in **both** paths, enforced by the parity test |
| Keep CI files in step with local gates, in the same change | `.github/workflows/ci.yml` calls `scripts/check.sh`; the parity and invariant tests fail the build when the paths drift |
| Root scripts run on Bash 3.2 and are linted | `CLAUDE-deployment.md` §5, `tests/integration/test_shell_portability.py`, `shellcheck` in `check.sh` |
| Test the contract | `tests/integration/*` asserts the runtime/architecture/env contract and the resource names; the deployed smoke test asserts the AWS-side contract |

## Why

- **YAGNI, stated as a rule.** `GREENFIELD.md` §7's own smell table lists
  speculative generality as a defect to remove. A Lambda zip has no port to
  bind and no container contract to satisfy; a Dockerfile here would be an
  untested artefact that implies a deployment path that does not exist — the
  opposite of the section's intent, which is to make the deployment contract
  *real*.
- **SHIP-HATS is an organisational context, not a stack choice.** The inert
  files exist so a project moving onto SHIP-HATS does not retrofit a pipeline
  onto finished code. Nothing in this repository is being shipped to
  SHIP-HATS today, and generating a pipeline that nobody runs would create the
  drift §10.3 explicitly warns against ("keep the inert files in step, in the
  same change as the scripts and tools they call") with no mechanism to keep
  them in step.
- **The deferral is auditable.** Anyone can see exactly what is missing, why,
  and what would change the answer — which is the point of keeping these
  instructions in a repository rather than in a chat log.

## Practical impact

- **A reviewer must not treat these as oversights.** If a change introduces a
  long-lived process or a container target, this addendum is superseded by a
  new one that lands the Dockerfiles/Dockerfile.dev/cloudflared files,
  `.dockerignore`, the port contract and the hadolint gate in the same commit.
- **If the SHIP-HATS move happens**, the activation script and inert pipeline
  files are generated from that section at that time; the discipline's
  Bash-3.2 portability rules already apply here, so the script will run in the
  same environments.
- The `docs/`-first structure means the deferred work is a documented gap in
  the register, not a silent one.
