# 0005 — Publication detection: URL probe authoritative, link transition corroborating

**Date:** 2026-10-14
**Spec reference:** §3 FR-2
**Status:** accepted

## What was decided

Two independent signals, deliberately ranked:

1. **Authoritative — canonical URL probe (FR-2.2).** The bulletin URL is
   *constructed* from the pattern
   `…/security-notes-news/{month-name-lowercase}-{year}.html` and fetched.
   `HTTP 200` plus a parseable notes table means *published*. This holds even
   if the index page has not yet converted the month's date into a hyperlink.
   The user's requirement was explicit: the index link is sometimes stale while
   the live page already exists, so the probe must not depend on it.
2. **Corroborating — index link transition (FR-2.3, FR-2.4).** The index page's
   `Dates for <year>` section is parsed and the month's entry is checked for an
   `<a href>` at, above or below the entry node. The result is reported as
   `link_transitioned: true|false|null` in the run result, the logs, and the
   email's provenance line — and it never suppresses a delivery.

Outcome classification (FR-2.5, FR-2.6):

| Observation | Outcome |
| ----------- | ------- |
| 200 + table with ≥ 1 row | `published` |
| 404 / 410 | `not_published` |
| 200 but no table, or 0 rows | `unverified_table` → treated as **not published, but flagged** |
| Timeout, DNS failure, connection reset, 429, 5xx after all retries | `unverified` |
| Page title month ≠ target month | not delivered (stale/cached page guard, FR-2.7) |

`unverified*` is never rendered to recipients as "nothing was published"
(FR-3.5, NFR-6): the pending notice wording changes to "could not verify".

## Why

- The failure mode we are protecting against is **a missed patch day**, which
  is far more expensive than a duplicate check. Making the index the gate would
  mean a page-cache or CMS lag could silently cost a month.
- Conversely, probing the URL *alone* would blindly email a 200 error page or a
  stale previous-month page, so the probe also requires a real table whose month
  agrees with the target.
- Distinguishing `not_published` from `unverified` matters because they lead to
  different recipient-facing claims and different operator actions (wait vs.
  investigate SAP).
- Constructing the URL from the month, rather than harvesting it from the page,
  also removes a dependency on anchor markup that SAP has changed before.

## Practical impact

- **SAP changes the slug pattern** (assumption A-1 breaks): the probe returns
  404 for every attempt and the operator gets a pending notice naming the URL
  that failed. The fix is one constant in `sap_patch_day/probe.py`, and an
  addendum.
- **SAP changes the table markup** (assumption A-3 breaks): outcome becomes
  `unverified_table`; the alarm wording and the run result's `detail` carry
  `unverified_table:<reason>` so the cause is one log line away. Table parsing
  is where to look first.
- The `link_transitioned` field is *evidence*, not control flow: no code path
  may be gated on it (asserted in `tests/unit/test_probe.py`).
- `tests/unit/test_probe.py` pins the four-row decision table above, and
  the acceptance test "index not transitioned + live URL" lives in
  `tests/integration/test_handler_delivery.py`.
