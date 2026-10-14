# 0001 — Repository layout: one Lambda at the root

**Date:** 2026-10-14
**Spec reference:** §1 (purpose and scope)
**Status:** accepted

## What was decided

The repository holds exactly one Lambda project, laid out at the root:

```
CLAUDE.md, GREENFIELD.md          discipline
docs/spec.md, docs/addenda/       contract + decision register
src/sap_patch_day/                the function's source (imported as a package)
tests/{unit,integration,live,support,fixtures}/
infrastructure/cloudformation/    templates
scripts/                          build, check, deploy, capture
```

The repository is named `aws-lambda` (generic), so this addendum also fixes the
migration path for a second lambda: it moves to
`lambdas/<lambda-name>/{src,tests,infrastructure}`, the discipline documents and
`scripts/check.sh` stay at the root and iterate over `lambdas/*`, and the
CloudFormation stack names stay per-lambda. That move is a single commit, not a
rewrite, because every path in this repository is already relative to the
project root.

## Why

- One deliverable today: a decision on day one beats inferring one later
  (`GREENFIELD.md` §11).
- Keeping `src/sap_patch_day/` as a real package (not loose `.py` files at the
  root) is what makes the artefact zip deterministic and the handler import
  path stable: the zip contains `sap_patch_day/` at its root, so the handler is
  `sap_patch_day.handler.lambda_handler`.
- Documenting the future layout now prevents the classic halfway move where
  tests and infrastructure are split across two conventions.

## Practical impact

- Any new top-level directory is a decision that needs an addendum.
- A second lambda means a `lambdas/` migration in **one** commit, including
  `scripts/check.sh`, `scripts/build-package.sh` and the deploy scripts.
- Relative paths in scripts must not assume CloudShell's home directory; the
  scripts resolve their own directory (`SCRIPT_DIR`) instead.
