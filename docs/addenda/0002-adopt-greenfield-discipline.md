# 0002 — Adopt the greenfield discipline as this repository's coding discipline

**Date:** 2026-10-14
**Spec reference:** whole document
**Status:** accepted

## What was decided

`GREENFIELD.md` from
[`wshyang/greenfield`](https://github.com/wshyang/greenfield) — commit
`8ba74027be5fad41e48cadde82cc0120d628b109`, dated 2026-10-13 — is this
repository's standing coding discipline.

- It is kept **verbatim** in the repository as `GREENFIELD.md`, so drift from
  upstream is a diff, not a memory test.
- It is **contextualised** in `CLAUDE.md`, which answers its §11 checklist for
  this project and points to the section files:
  `CLAUDE-function-design.md`, `CLAUDE-testing.md`, `CLAUDE-deployment.md`.
- Sections that do not apply are not silently dropped: they are deferred with
  rationale and a revisit trigger in
  [addendum 0009](./0009-deferred-container-and-shiphats-sections.md).

## Why

- The template's own §0 says to copy it and replace every placeholder; leaving
  it as an un-contextualised template in the repository would satisfy nobody —
  a future agent would still have to guess whether "`<placeholder>` ORM"
  applies to a project with no database.
- Its §0 also says the *split into section files* is a day-one documentation
  architecture; retrofitting it is churn. This is day one.
- Keeping the upstream copy verbatim preserves auditability: a reader can see
  the source instruction next to our adaptation of it.

## Which parts were adopted as written

§1 (addenda discipline), §2 (tests first, no mocks where it counts), §3 (test
isolation and determinism), §6 (contracts, no internal copy leaking to users),
§7 (function design, module structure), §8 (honest validation), §9 (one-command
local environment, env separation), §10.2/§10.4 (SHIP-HATS *knowledge*, shell
portability), §11 (the thirteen day-one decisions).

## Which parts were adapted

| Upstream | Adaptation here |
| -------- | --------------- |
| §2 backend integration tests against a real database | There is no database. The "data-access layer" analogue is the HTML parsers; they are never mocked and are exercised against real captured page structure. The deployed stack is exercised by a scripted smoke test. See `CLAUDE-testing.md` |
| §4 ORM / casing / soft delete / UUIDv7 | Not applicable (no persistence beyond six SSM state parameters). The one-canonical-shape rule survives as: one frozen dataclass per concept, conversion only at adapter boundaries |
| §5 schema snapshot + migration twins | Not applicable. The analogous twin rule covers the SSM parameter contract: spec §9, the CloudFormation template, `scripts/awscli/00-env.sh` and the config fixtures change together |
| §3 Playwright video | No browser. The equivalent is recording the raw HTML of a failing page (`scripts/capture-fixture.sh`) so a field failure can be replayed offline |

## Which parts were deferred

§10.1 (container files, port 3000 contract) and §10.3 (inert SHIP-HATS
pipeline) — see [addendum 0009](./0009-deferred-container-and-shiphats-sections.md).

## Practical impact

- Every change is reviewed against `CLAUDE.md` §1 standing instructions; the
  most commonly missed one is writing the addendum in the *same* change.
- Updating `GREENFIELD.md` from upstream is its own commit, with the new commit
  hash recorded here and in `CLAUDE.md`.
