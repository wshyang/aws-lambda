# CLAUDE-testing.md — testing discipline as applied here

`GREENFIELD.md` §2, §3 and §8, translated to a system with **no database** whose
external world is (a) two public HTML pages and (b) AWS SES + SSM.

## 1. What is real in every test

| Layer | Treatment |
| ----- | --------- |
| HTML parsing (`htmltree`, `bulletin_index`, `notes_table`, `note_fields`) | **Real** captured page structure, no stubs, no regex-only shortcuts |
| Decision logic (`publication`, `handler` flow) | **Real** — the whole `run_delivery` path is exercised end to end |
| Rendering (`email_render`) | **Real** — HTML, text and CSV renderers are asserted against the same model, including cross-renderer parity |
| HTTP transport | Injected port (`HttpClient`). Tests use recorded responses keyed by URL — a *fake*, not a mock, so the parsing above still runs on the real bytes |
| SES + SSM SDK clients | Injected at the port boundary. Requests are asserted against the **real botocore service model** with `botocore.stub.Stubber`, which fails on a wrong parameter name or type |
| SSM state store | `InMemoryStateStore` (a real implementation of the port, not a mock) |
| SAP's live pages | `tests/live/` — opt-in, marked `live`, never part of the default run |
| Deployed AWS stack | `scripts/awscli/08-verify.sh` dry-run invoke; the only layer that proves IAM, SES and EventBridge actually line up |

## 2. The "no mocks where it counts" rule here

`GREENFIELD.md` §2 forbids mocking the data-access layer. This project's
data-access equivalents are the **HTML parsers** and the **publication
decision**. Therefore:

- No test may mock `parse_notes_table`, `check_publication` or any renderer.
- Fixtures are inputs, never outputs: a test supplies the HTML bytes and
  asserts on the parsed model.
- The only injected doubles are the transport and the SDK client, and both are
  small typed ports with their own tests
  (`tests/unit/test_http_client.py`, `tests/unit/test_mailer.py`).

## 3. Negative and "wrong party" cases (the IDOR analogue)

Without a database there is no session scoping to attack, so the equivalent
negative matrix is: **wrong month, wrong year, unverified publication, hostile
input**.

- Index page that lists the month but with no link, and a canonical URL that
  also 404s → must not email a bulletin (the false-positive path).
- Index page with a link that has *not* transitioned but a live canonical URL
  that returns the table → **must** email (the primary acceptance path).
- Canonical URL returning 200 with a *previous* month's or an empty table →
  must not email a bulletin.
- Title cell containing `<script>`, `onclick=`, `javascript:` hrefs, or a
  `data:` URL → stripped by the sanitiser, asserted on the rendered email.
- Recipient list empty, sender unset, malformed addresses → `ConfigError` with
  the exact message from `docs/spec.md` §8, before any HTTP request is made.
- `To know more about the security researchers …` appearing *inside* the table
  → extraction truncates at that boundary.

## 4. Determinism and isolation

- Time is injected everywhere (`Clock` port). No test depends on today's date;
  the patch-day month, the retry-window end and the watchdog day are all
  asserted with fixed clocks.
- No sleeping: the HTTP port takes a `sleep` callable.
- Every fixture uses `@example.test` addresses and no account IDs.
- `tests/live` is the only suite allowed to touch the network, and it is
  skipped unless `--live` (or `SAP_PATCH_DAY_LIVE=1`) is given.

## 5. Recording reality for the field

When the live suite fails, the failure must be replayable offline:

```bash
scripts/capture-fixture.sh            # writes tests/fixtures/captured/<date>/*.html
```

`scripts/capture-fixture.sh` saves the raw bytes of the index page and the
current month's page. Those captures then become the fixtures a regression test
is written against — the same discipline as recording Playwright video, applied
to a scraping job.

## 6. Fixture provenance

Fixtures under `tests/fixtures/` are either:

- **reconstructed** — hand-authored from the real page's rendered structure and
  content (documented per file in `tests/fixtures/README.md`), or
- **captured** — raw bytes saved by `scripts/capture-fixture.sh`.

The provenance of each fixture is recorded in `tests/fixtures/README.md` and
in [addendum 0010](./../docs/addenda/0010-test-provenance-and-validation-status.md).
Never present a reconstructed fixture as a capture.

## 7. Honest validation

`scripts/check.sh` is the single entry point; it prints exactly which layers it
ran. The live layer and the deployed layer are run by a human or by CloudShell,
and their results are recorded in the commit message and addendum 0010 —
including when they have not been run yet.
