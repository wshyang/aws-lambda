# CLAUDE.md — coding discipline for this repository

**Status: adopted.** This repository's coding discipline is the greenfield
standing-instructions template, contextualised to a serverless AWS project.
Standing instructions here are **not optional** and apply to every change,
human or agent.

- Upstream template: [`wshyang/greenfield`](https://github.com/wshyang/greenfield)
  `GREENFIELD.md`, commit `8ba74027be5fad41e48cadde82cc0120d628b109`
  (2026-10-13), kept verbatim in this repository as [`GREENFIELD.md`](./GREENFIELD.md)
  so future drift from upstream is diffable.
- Adoption decision and the sections deliberately deferred:
  [addendum 0002](./docs/addenda/0002-adopt-greenfield-discipline.md),
  [addendum 0009](./docs/addenda/0009-deferred-container-and-shiphats-sections.md).
- Read in this order: **this file** → `docs/spec.md` (product contract) →
  `docs/addenda/` (every decision not dictated by the base spec).

## 0. What this project is

A single-purpose Lambda: on the SAP Security Patch Day schedule (second Tuesday
of the month, retry window from 16:00 Singapore time) it probes SAP's public
Security Notes & News pages, extracts the monthly notes table, and emails it
through Amazon SES to a recipient list held in SSM Parameter Store.

- Runtime: **Python 3.12** (`python3.12` on Lambda), **stdlib only** plus the
  runtime-provided `boto3`. No dependency layer, no third-party packages in the
  deployed artefact. See [addendum 0003](./docs/addenda/0003-runtime-and-dependency-policy.md).
- Entry point: `sap_patch_day.handler.lambda_handler` (see `src/sap_patch_day/`).
- Deployment: two parallel, name-identical paths — CloudFormation
  (`infrastructure/cloudformation/`) and AWS CLI scripts
  (`scripts/awscli/`) meant for CloudShell. See
  [CLAUDE-deployment.md](./CLAUDE-deployment.md).

## 1. Standing instructions (do these without being asked)

1. **Record every decision, in the same change.** A gap filled, a field added,
   a deviation taken → a new sequentially-numbered file in `docs/addenda/`
   (`NNNN-kebab-title.md`) covering: what was decided, why, which
   `docs/spec.md` section it relates to, and the practical impact for a future
   reader. Addenda numbering is independent of the spec's own numbering.
2. **Tests land with the code, never as a follow-up.** A change without tests
   for its behaviour is incomplete. `tests/` layout by type is fixed:
   `unit/`, `integration/`, `live/`, `support/`, `fixtures/`.
3. **Never mock what you are trying to prove.** See
   [CLAUDE-testing.md](./CLAUDE-testing.md) for what that means here: the HTML
   parsers, renderers and decision logic run against **real captured page
   structure**; only the transport (HTTP) and the AWS SDK clients are injected.
4. **State the validation layer you actually ran.** If the real toolchain
   could not run something (live HTTP against sap.com, real SES delivery, real
   CloudFormation deploy), say so explicitly in the commit message, the summary
   and [addendum 0010](./docs/addenda/0010-test-provenance-and-validation-status.md).
   Never write "tests pass" for a layer that did not run.
5. **Function design rules are mechanical, not aspirational**: ≤ 20-line
   target / 50-line hard limit, guard clauses (nesting ≤ 3), ≤ 3 parameters,
   no boolean flag parameters, no in-place mutation, Separate Query from
   Modifier, one concern per file, 400 LOC typical / **800 LOC hard limit**.
   Full detail and the checkable gates: [CLAUDE-function-design.md](./CLAUDE-function-design.md).
6. **Keep user-facing copy user-facing.** Emails are read by recipients: no
   internal slugs, stack names, parameter paths, "run_mode" vocabulary or
   design rationale in the subject, body or attachment names. Rationale lives
   in comments, the spec and addenda.
7. **Secrets never enter the repository.** No credentials, no AWS account IDs,
   no real recipient addresses in committed files. Configuration is SSM
   Parameter Store + environment variables; committed examples use
   `example.com`.
8. **Deployment artefacts stay in step.** The CloudFormation template and the
   AWS CLI scripts must describe the *same* objects with the *same* names and
   defaults; `tests/integration/test_deployment_parity.py` fails the build when
   they drift. Pinned versions only — no `latest`, no unpinned runtimes.

## 2. The thirteen day-one decisions, answered for this repository

GREENFIELD.md §11 lists thirteen decisions to settle before writing the first
feature. Answered here, so no future reader has to infer them.

| # | Decision | This repository |
| - | -------- | --------------- |
| 1 | Source-of-truth spec + addenda numbering | `docs/spec.md` (living contract) + `docs/addenda/NNNN-*.md`; base spec numbering is stable, addenda append forever and never renumber |
| 2 | Test isolation, "tests first, no mocks" | `tests/unit` (pure logic, real fixture HTML), `tests/integration` (whole handler over ports and both deployment paths), `tests/live` (opt-in, real sap.com), `tests/fixtures` (captured/recorded pages). No test talks to the real AWS account — the deployed smoke test does that, and is a script, not a unit test |
| 3 | ORM + one-casing policy | **Not applicable** — no database. The equivalent rule: one canonical Python shape per concept (`snake_case` dataclasses in `sap_patch_day.models`), wire/AWS payload shapes converted at the adapter boundary only, never twice |
| 4 | Soft delete / UUIDv7 / real actor rows | **Not applicable** — no persistence beyond four SSM state parameters, which are overwritten (`Overwrite=True`), never deleted |
| 5 | Schema snapshot + migration twins | **Not applicable** (no schema). The analogous twin rule: any change to SSM parameter keys or the environment-variable contract updates `docs/spec.md` **and** the CloudFormation template **and** the CLI scripts **and** `tests/support/harness.py` in the same commit |
| 6 | Stable wire contracts | `HandlerResult` JSON (documented in spec §7) and the `SendRequest`/`RuntimeConfig` dataclasses; the EventBridge input payload contract is documented in spec §6 |
| 7 | Test layout + video-on | Layout fixed as in row 2; video capture does not apply (no browser). Playwright-equivalent for the live layer: `tests/live` records a raw HTML fixture so a failure in the field can be replayed offline |
| 8 | Deterministic realistic fixtures | Fixtures are real SAP page structure with real note IDs, CVEs, products and priorities; addresses are always `@example.test` |
| 9 | Static-check fallback + validation caveat | `scripts/check.sh` runs ruff, mypy, pytest, cfn-lint, shellcheck, `bash -n` and Bash-3.2 static guards; the live/deployed layers are named separately in every commit that touches them |
| 10 | One-command local env + env separation | `scripts/dev.sh` (wraps venv + checks + local dry run); committed `.env.example`, uncommitted `.env.local`; nothing reads a committed secret |
| 11 | Function design rules | Enforced — see [CLAUDE-function-design.md](./CLAUDE-function-design.md) and `scripts/check.sh` |
| 12 | Container contract (port 3000, `0.0.0.0`) | **Deferred, with rationale** — this artefact is a Lambda zip, not a listening server; there is no port to bind. [Addendum 0009](./docs/addenda/0009-deferred-container-and-shiphats-sections.md) records the reasoning and the trigger that reopens it |
| 13 | SHIP-HATS pipeline files kept inert | **Deferred, with rationale** — nothing in this repository is deployed by or distributed to SHIP-HATS today; the deployment discipline is instead carried by the two deploy paths and the CI workflow. Revisit trigger in [addendum 0009](./docs/addenda/0009-deferred-container-and-shiphats-sections.md) |

## 3. Section files

| File | Covers |
| ---- | ------ |
| [CLAUDE-function-design.md](./CLAUDE-function-design.md) | GREENFIELD §7 in full, plus the mechanically checkable gates used here |
| [CLAUDE-testing.md](./CLAUDE-testing.md) | GREENFIELD §2, §3, §8 as applied to a Lambda with no database |
| [CLAUDE-deployment.md](./CLAUDE-deployment.md) | GREENFIELD §9, §10 as applied to CloudFormation + AWS CLI parity, and the CloudShell runbook |
| [docs/spec.md](./docs/spec.md) | The living functional design specification (the product contract) |
| [docs/addenda/](./docs/addenda/) | The decision register |

## 4. Definition of done (every change)

1. Behaviour is covered by tests that land in the same commit.
2. `scripts/check.sh` passes locally (ruff, mypy, pytest, cfn-lint,
   shellcheck, bash syntax, Bash 3.2 static guards).
3. Any decision made along the way has an addendum.
4. CloudFormation and CLI paths still describe the same objects (parity test).
5. The commit message names the validation layers run and the ones that still
   must run on real infrastructure.
6. No secret, no real email address, no account ID in committed files.
