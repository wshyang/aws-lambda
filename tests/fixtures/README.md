# Fixtures

Two kinds of file live here, and the difference matters (see
[addendum 0010](../../docs/addenda/0010-test-provenance-and-validation-status.md)):

| File | Kind | Provenance |
| ---- | ---- | ---------- |
| `october_2026.html` | **reconstructed** | Content is the real October 2026 bulletin as published on 2026-10-13, taken from a live fetch on 2026-10-14. Markup is a faithful reconstruction of the rendered AEM structure (four-column `<table>`, `<br>`-separated title cells, `(opens in new tab)` decorations). Rows 6–9 of the live table were truncated at a fetch-chunk boundary and are omitted rather than invented: 21 rows are present, with 4 Critical / 2 High / 14 Medium / 1 Low and 2 updates. |
| `security_notes_news_index.html` | **reconstructed** | The real `Dates for 2026` section: twelve entries, January–October linked, November–December plain text, followed by the "For previous years bulletins refer to" paragraph. |
| `no_table.html` | synthetic | A 200 response with the right heading and no notes table (`unverified_table`). |
| `empty_table.html` | synthetic | A 200 response whose table has a header and no body rows. |
| `stale_page.html` | synthetic | A 200 response whose heading claims **September** 2026 while being served for October (`stale_page`). |
| `hostile_title_cell.html` | synthetic, adversarial | A row whose title cell carries `<script>`, `onclick=`, `javascript:` and `data:` URLs, HTML comments and an `<img>`; the sanitiser must strip all of it (spec FR-5.6). |

## The rule

**A reconstructed fixture is never presented as a capture.** When
`scripts/capture-fixture.sh` has been run from a host with egress to
`support.sap.com`, the raw bytes land in `captured/<date>/`; if a capture
disagrees with a reconstruction, **the capture wins** and the reconstruction is
corrected in the same change.

## Adding a fixture

1. Prefer a capture (`scripts/capture-fixture.sh`).
2. If you must reconstruct, say so here, name what was omitted, and keep the
   content real — never invent note identifiers, CVEs or products.
3. Add the regression test in the same commit.
