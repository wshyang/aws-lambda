# 0003 — Runtime pinned to Python 3.12, zero bundled dependencies

**Date:** 2026-10-14
**Spec reference:** §10 (deployment contract), NFR-2, NFR-5
**Status:** accepted

## What was decided

- Runtime: **`python3.12`**, architecture **`arm64`** (Graviton), 256 MB memory,
  60 s timeout — all pinned as deployment parameters with those defaults.
- The deployed artefact contains **only this repository's source**. No
  vendored packages, no Lambda layer, no container image.
- The function uses the Python standard library (`urllib.request`,
  `html.parser`, `email`, `json`, `logging`, `datetime`, `dataclasses`,
  `csv`) plus **`boto3`/`botocore`, which the Lambda Python runtime provides**.
  `boto3` is *not* imported at module import time in a way that breaks the test
  environment: clients are constructed inside the adapter factory, so tests can
  import the package with a `boto3` of a different version or none at all.
- Version-sensitive third-party libraries (`requests`, `beautifulsoup4`,
  `lxml`) were deliberately **not** used. HTML is parsed with a small,
  purpose-built tree builder over `html.parser`.

## Why

- **The packaging problem disappears.** With no dependencies the artefact is a
  zip of source, produced by `zip` (or the stdlib) in CloudShell with no
  `pip install --platform manylinux2014_aarch64 --only-binary=:all:` dance, no
  wheel-architecture mismatch, no layer versioning, and a deterministic hash
  that makes drift visible.
- **Determinism and auditability.** Every byte that runs in AWS is reviewable
  in this repository. A pinned runtime with AWS-managed `boto3` removes a whole
  class of "which version is deployed" questions.
- **Resilience from the runtimes.** The pages used here are simple HTML tables
  and headings; a tolerant tree parser (with whitespace, `<br>`, and
  implicit-close handling) is enough, and it cannot be broken by a
  dependency's release cadence.
- `arm64` is cheaper per millisecond for this workload and is a first-class
  Lambda target for the pinned runtime.

## Practical impact

- **Rule:** adding a runtime dependency requires a new addendum that names it,
  justifies the packaging change, and amends `scripts/build-package.sh`. The
  default answer is no.
- **Rule:** code must stay compatible with the pinned runtime. Locally the
  tests run on the available CPython (3.11 in the authoring sandbox), so no
  3.12-only syntax is used; the pyproject `requires-python` is `>=3.11` and the
  runtime parameter is what actually ships.
- `boto3` is accessed only through the adapters in
  `src/sap_patch_day/{mailer,state,config}.py`; those modules are the boundary
  the tests substitute.
- A dependency-free artefact means `scripts/build-package.sh` can also run in a
  minimal CI container with nothing but `python3` and `zip`.
