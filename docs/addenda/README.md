# Addenda register

The decision register for this repository, as required by `GREENFIELD.md` §1
and `CLAUDE.md` §1.1.

## Rules

1. **One decision per file**, named `NNNN-kebab-title.md`, numbered
   sequentially and **never renumbered or deleted**. Superseding a decision
   means appending a new addendum that names the one it supersedes.
2. Numbering is **independent of `docs/spec.md` section numbers**, so the base
   specification stays stable while addenda append forever.
3. Each addendum states: **what was decided**, **why**, **which spec section it
   relates to**, and **the practical impact** — what a future reader must know.
4. Addenda are written **in the same change** that makes the decision, never as
   a follow-up.

## Register

| # | Decision | Spec reference |
| - | -------- | -------------- |
| [0001](./0001-repository-layout.md) | One Lambda project at the repository root; migration path for a second lambda | §1 |
| [0002](./0002-adopt-greenfield-discipline.md) | Adopt `wshyang/greenfield` as this repository's coding discipline, upstream commit recorded and kept verbatim | whole document |
| [0003](./0003-runtime-and-dependency-policy.md) | Runtime pinned to Python 3.12 with **zero** bundled dependencies (stdlib + runtime boto3) | §10, NFR-2 |
| [0004](./0004-configuration-via-ssm-parameters.md) | People live in SSM Parameter Store; paths and policies live in environment variables | §9 |
| [0005](./0005-publication-detection-url-probe-and-link-transition.md) | The canonical URL probe is authoritative for publication; the index link transition is corroborating evidence | §3 FR-2 |
| [0006](./0006-retry-window-idempotency-and-watchdog.md) | Six-attempt retry window, two-kind idempotency, second-Wednesday watchdog | §3 FR-3, §5 |
| [0007](./0007-email-contract-and-sanitization.md) | HTML + text from one model, allowlist sanitisation, CSV attachment opt-in via raw MIME | §3 FR-5 |
| [0008](./0008-deployment-parity-cloudformation-and-awscli.md) | CloudFormation and AWS CLI paths describe the same objects, enforced by a parity test | §10 |
| [0009](./0009-deferred-container-and-shiphats-sections.md) | `GREENFIELD.md` §10.1 (containers) and §10.3 (SHIP-HATS) deferred, with revisit triggers | §10, CLAUDE.md §2 |
| [0010](./0010-test-provenance-and-validation-status.md) | Test fixture provenance and the exact validation layers run so far | §3, §11 NFR-1 |
| [0011](./0011-first-live-capture-findings.md) | First live capture: wrong index URL, intro-text boilerplate, two wrong test assumptions | §3 FR-2.3, FR-4 |
