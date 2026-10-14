# 0010 — Test fixture provenance and validation status

**Date:** 2026-10-14 (updated whenever a validation layer is run)
**Spec reference:** `GREENFIELD.md` §8 (validate early and honestly); `CLAUDE.md` §1.4
**Status:** open — this file is the running record

## Fixture provenance

| Fixture | Provenance | Contents |
| ------- | ---------- | -------- |
| `tests/fixtures/security_notes_news_index.html` | **Reconstructed** from the live page fetched 2026-10-14 (the rendered text was readable through an HTTP-egress proxy; raw bytes were not retrievable in the authoring sandbox) | The `Dates for 2026` heading and its twelve entries — January–October 2026 linked, November and December as plain text — plus surrounding navigation noise, so the heading/region logic is exercised, not just the pattern match |
| `tests/fixtures/october_2026.html` | **Reconstructed** the same way | The real October 2026 bulletin: header row `Note# / Title / Priority / CVSS`, 21 rows (17 SAP notes, 4 GitHub advisories, 2 of them updates), the real intro paragraph, and the closing "To know more about the security researchers…" paragraph. Rows that fell at a fetch-chunk boundary are omitted rather than invented |
| `tests/fixtures/hostile_title_cell.html` | **Synthetic** (adversarial by construction) | A note row whose title cell carries `<script>`, `onclick=`, `javascript:`/`data:` URLs, comments and an `<img>`, for the sanitiser tests |
| `tests/fixtures/empty_table.html`, `tests/fixtures/no_table.html`, `tests/fixtures/stale_page.html` | **Synthetic** (malformed by construction) | The `unverified_table`, `ParseError` and stale-month branches |

**Why reconstructed:** the authoring sandbox has no network egress to
`support.sap.com`, so raw bytes could not be captured. The page *content* is
real (taken from the live page on 2026-10-14); the *markup* is a faithful
reconstruction of the rendered structure — AEM-style `<p><strong><a>` entries
in the index and a four-column `<table>` in the bulletin.

**How this is corrected, deliberately:** `scripts/capture-fixture.sh --month
YYYY-MM [--index]` saves the raw HTML of a live page to
`tests/fixtures/captured/<YYYY-MM-DD>/` (and prints the provenance row and the
sha256 prefix to record). A capture never overwrites a curated fixture; it
lands beside it. `tests/integration/test_captured_pages.py` picks up every file
in that directory automatically, so the first capture becomes a regression test
without another commit to the test code. If a reconstruction ever disagrees
with a capture, **the capture wins** and the reconstruction is corrected in the
same change.

**Never present a reconstructed fixture as a capture.** That rule exists in
`tests/fixtures/README.md` too, next to the files themselves.

## Validation layers and their status

Everything below was run **in the authoring sandbox on 2026-10-14** unless the
row says otherwise. The sandbox has no AWS credentials and no egress to
`support.sap.com`, so the last three rows cannot be run there at all.

| Layer | Command | Status |
| ----- | ------- | ------ |
| Static: ruff lint, ruff format check, mypy, dependency pins | `scripts/check.sh` | **RUN — clean.** ruff 0.17.0 (0 findings, 67 files formatted), mypy 2.4.0 (16 source files, 0 issues), all dev pins `==` |
| Shell: `bash -n`, shellcheck, Bash-3.2 static guard, `aws_cli` wrapper guard | `scripts/check.sh`, `tests/integration/test_shell_portability.py` | **RUN — clean** (shellcheck 0.11.0 and `bash -n` over all 17 scripts). **Not** run under a real Bash 3.2 binary: that runs in CI (`ci.yml`, macOS job) and was not available in the sandbox |
| Unit: parsers, sanitiser, renderers, config validation, clock, state, mailer, handler payloads, dry-run CLI | `pytest tests/unit` | **RUN — 256 passed** |
| Integration: whole handler over injected ports (every outcome branch), deployment parity, template invariants, shell portability, module budget, captured-page replay | `pytest tests/integration` | **RUN — 316 passed, 3 skipped** (the captured-page suite has nothing to replay yet) |
| Whole suite, as CI runs it | `pytest` / `scripts/check.sh` | **RUN — 572 passed, 8 skipped** (5 live tests plus 3 captured-page tests, skipped without `--live` or a capture) |
| Templates: `cfn-lint` 1.57.2 | `scripts/check.sh` | **RUN — clean** |
| Artefact: deterministic packaging | `scripts/build-package.sh` | **RUN — 16 entries, 35 219 bytes, sha256 `ca92174c…`; two consecutive builds produced the identical hash.** The zip imports cleanly with only the standard library available |
| Offline dry run of the whole pipeline | `scripts/dev.sh` / `python -m sap_patch_day.local_cli` | **RUN — `status=delivered`, 21 notes, 4 critical / 2 high / 14 medium / 1 low**, preview written to `dist/preview-2026-10.html` |
| **Live HTTP against `support.sap.com`** | `scripts/live-check.sh` | **NOT RUN — the sandbox cannot reach the host (TLS connection closed). The failure is handled correctly: the run reported `attempt-failed` with the transport error and sent nothing.** Run it from a host with egress before trusting the parser |
| **AWS CLI deployment scripts** | `scripts/awscli/deploy-all.sh` | **NOT RUN — no AWS CLI, credentials or account in the sandbox.** Static checks only (shellcheck, `bash -n`, portability gates, parity against the template) |
| **CloudFormation deployment** | `scripts/deploy-cloudformation.sh` | **NOT RUN — as above.** `cfn-lint` and the template invariants are the only evidence so far |
| **Real SES delivery** | `scripts/awscli/08-verify.sh` (dry-run), then a live patch day | **NOT RUN — needs a verified SES identity.** The smoke test deliberately runs with `DRY_RUN=true` |

## Rules for updating this file

1. Every commit that touches a validation layer updates its row — including
   "still not run", if it is still not run.
2. A layer that ran only in part says exactly which part (see the Bash 3.2 row:
   the static guard ran, the real 3.2 binary did not).
3. The live and AWS rows may only be marked run by someone who actually
   executed them; the evidence is the command output pasted in the commit
   message, not an assumption.
4. If a live run reveals a markup change, the fix lands with a captured fixture
   and a regression test — see the fixture-provenance rule above.
