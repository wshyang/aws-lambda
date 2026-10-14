# 0007 — Email contract: one model, two renderers, allowlist sanitisation, opt-in CSV

**Date:** 2026-10-14
**Spec reference:** §3 FR-5, §4 (data model)
**Status:** accepted

## What was decided

- **One model, two renderers.** `Bulletin` (frozen dataclass) is rendered by
  `render_html`, `render_text` and `render_csv`; no renderer reads the raw page
  HTML except through the already-sanitised `title_html` field. A test asserts
  the HTML and text parts contain every note id (cross-renderer parity), so the
  text part can never silently rot.
- **Body.** `multipart/alternative` HTML + plain text, table-based layout,
  inline styles only, no images, no external CSS, no tracking pixels. The HTML
  table keeps the source's four columns (`Note#`, `Title`, `Priority`, `CVSS`)
  so the email is recognisably the same extract the operator asked for.
- **Sanitisation is an allowlist, applied to the source markup.**
  Tags `a`, `strong`, `b`, `em`, `i`, `br` survive; `<a>` keeps only `href`
  (schemes `http`, `https`, `mailto`) and gains
  `rel="noopener noreferrer"`; every other tag is dropped while keeping its
  text; comments, `script`, `style`, `iframe`, `img`, `svg` and all event
  handler attributes are removed. `javascript:`, `data:` and `vbscript:` URLs
  are dropped, not escaped-and-kept.
- **CSV attachment is opt-in** (`attach_csv`, default `false`). When enabled,
  the message is sent as raw MIME built with the stdlib `email` package
  (SES `Simple` bodies cannot carry attachments); the CSV columns are fixed in
  spec §3 FR-5.5 and the filename is stable
  (`sap-security-notes-<year>-<month>.csv`).
- **Provenance line, not internals (FR-5.9).** The footer names the source URL,
  the fetch time in Singapore time, and whether the index link had transitioned
  — and nothing else. No stack name, no parameter path, no `run_mode`, no
  rationale.
- **Log masking (FR-6.4).** Recipients appear in logs as
  `f***@example.com`; the full list only ever reaches SES.

## Why

- The source HTML is authored by SAP, not by us, and it arrives inside an email
  we sign. An allowlist (rather than a blocklist, or escaping everything and
  losing the formatting) is the only safe default: unknown markup is dropped by
  construction.
- Emails are rendered by clients we do not control — Outlook, Gmail and mobile
  clients — so layout must be table-based with inline styles. Colour-coded
  priority survives images-off rendering, which iconography does not.
- Cross-renderer parity is the cheapest guard against the classic bug where the
  text alternative still shows last month's shape.
- Attachments are off by default because security gateways routinely strip
  them and a stripped attachment looks like a broken email; the CSV exists for
  people who feed the notes into a tracker.
- Masking addresses in logs keeps the log group from becoming a mailing-list
  export in CloudWatch.

## Practical impact

- **Adding a column or a field to the email** means updating the model, both
  renderers, the CSV columns, the spec, and a test — in one commit.
- **`attach_csv=true`** changes the SES call shape from `Simple` to `Raw`; both
  paths are covered by `tests/unit/test_mailer.py` using the real botocore
  service model, so a wrong parameter name fails in tests rather than at 16:00
  on patch day.
- **Sanitiser changes are security changes.** The hostile-input tests in
  `tests/unit/test_htmltree.py` and `tests/unit/test_email_render.py` are the gate; they assert removal of script
  tags, event handlers and dangerous URL schemes.
- The email never claims more than we know: `unverified` runs produce a notice
  with different wording (FR-3.5), and a `not_published` bulletin is never
  rendered as an empty table (FR-5.8).
