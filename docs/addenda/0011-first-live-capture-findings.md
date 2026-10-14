# 0011 — First live capture: what it disproved

**Date:** 2026-10-14
**Spec reference:** §3 FR-2.3 (index URL), §3 FR-4 (table extraction), intro text in §6/§7 `Bulletin.intro_text`
**Status:** accepted

## What was decided

The first captures from `support.sap.com` (`tests/fixtures/captured/2026-10-14/`,
taken with `scripts/capture-fixture.sh`) were checked against the code that had
been written from reconstructed fixtures. Three disagreements were found; per
`tests/fixtures/README.md` ("the capture wins") the code or test was corrected.

1. **`INDEX_URL` was wrong (code bug).** `probe.py` built the index URL as
   `BASE_URL + "security-notes-news.html"`, i.e. `…/security-notes-news/security-notes-news.html`,
   which answers **404**. The real index is one level up:
   `https://support.sap.com/en/my-support/knowledge-base/security-notes-news.html`
   (this is what spec FR-2.3 already said). Effect before the fix: the index probe
   always failed, `link_transitioned` was always `None`, and the corroborating
   signal never contributed. Delivery was unaffected because the canonical URL
   probe is authoritative (addendum 0005). `INDEX_URL` is now a literal.
2. **`extract_intro_text` returned boilerplate (code bug).** On the live page
   SAP's standing paragraph ("This post shares the information on security notes…")
   and the month's summary ("On 13th of October 2026, SAP security patch day saw
   the release of…") are **one `<p>` separated by `<br />`**; the reconstruction
   had them as two paragraphs. The extractor returned the whole paragraph, so the
   email's summary line would have opened with boilerplate. It now keeps the
   `<br>`-separated lines from the one containing the marker onward.
   Regression test: `test_intro_drops_boilerplate_sharing_a_paragraph_with_the_summary`.
3. **Two test assumptions were wrong (test bugs, parser fine).**
   `tests/live/test_live_pages.py` assumed a note's own link appears in the
   *title* cell; live, it is in column 1 (`note_url`) and the title cell carries
   the CVE links. `tests/integration/test_captured_pages.py` applied the
   bulletin's "summary sentence or table" rule to the index page; the index has
   neither, and is now exempt (it is covered by the `Dates for` assertion).

## Reconstruction vs capture (not yet reconciled)

`tests/fixtures/october_2026.html` (reconstructed, 21 rows, 2 High) differs from
the capture (22 rows, 3 High) and uses a different intro layout and cell
structure. The unit and integration tests pin the reconstruction's 21 rows, so it
was **left as is** and the capture sits beside it. Reconciling means replacing
the reconstruction with the capture and updating the pinned counts; do that as a
deliberate follow-up.

## Practical impact

- `link_transitioned` now carries real information in the run result, logs and
  email provenance line.
- Email summary text is SAP's monthly sentence only.
- Live and captured layers now exist and pass (see 0010).
