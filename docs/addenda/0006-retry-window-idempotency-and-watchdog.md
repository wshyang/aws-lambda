# 0006 — Retry window, two-kind idempotency, and the second-Wednesday watchdog

**Date:** 2026-10-14
**Spec reference:** §3 FR-1, FR-3, §5 (states and transitions)
**Status:** accepted

## What was decided

**Cadence.** Two EventBridge rules feed one function:

- `sap-security-patch-day-delivery` — `cron(0,30 8-12 ? * TUE#2 *)` (UTC) =
  every 30 minutes from **16:00 to 20:30 Singapore time** on the second Tuesday.
- `sap-security-patch-day-watchdog` — `cron(0 1 ? * WED#2 *)` (UTC) =
  **09:00 Singapore time** on the second Wednesday, invoked with
  `{"run_mode":"watchdog"}` via the rule's constant input.

The retry window is ten attempts, not one. The window end (`RETRY_WINDOW_END_SGT`,
default `20:30` — the last slot of the schedule) is what makes "final attempt"
meaningful, and it is checked against the injected Singapore clock — not against
an attempt counter, so a missed or duplicated invocation cannot desynchronise
it. Keeping the two equal is the point: if the end were earlier, several slots
would be "final" and the operator would be told the same thing repeatedly.
`tests/integration/test_deployment_parity.py` asserts the equality by parsing
the cron expression, so a future schedule change cannot quietly break it.

**One notice per month.** Because more than one slot can be final (an operator
can move the end earlier, and a rule can be retried), a final attempt that would
repeat this month's notice returns `notice-already-sent` instead of emailing
again. `{"ignore_idempotency":true}` still forces a resend if a notice was
actually lost.

**Two-kind idempotency.** State is keyed by (`month`, `kind`) with kind ∈
{`bulletin`, `pending`, `watchdog`}; a `pending` notice never suppresses the
`bulletin` delivery for the same month and vice versa (FR-3.6). A manual resend
is explicit: `{"run_mode":"delivery","ignore_idempotency":true}`.

**Watchdog.** If the second Wednesday arrives with no `bulletin` recorded for
the month, the watchdog emails the recipients once (FR-3.8). It is the net that
catches "the whole pipeline failed": broken IAM, unverified SES identity,
deleted parameter, a code bug that raises on every attempt.

## Why

- SAP sometimes publishes several hours after the nominal start of patch day.
  A single 16:00 check optimises for the wrong risk: a late publication would
  mean a *month* without the notes, whereas an extra 30-minute poll costs
  almost nothing (NFR-1) and makes at most twenty requests to two public pages
  (NFR-5).
- "Final attempt" derived from the clock rather than a counter keeps the
  function stateless-correct: any attempt, in any order, in any number, reaches
  the same conclusion.
- Two-kind idempotency exists because the states are genuinely different:
  "we told you it is not out yet" is not "we sent you the notes". A single
  boolean would have created the classic bug where the notice from the final
  slot blocks the real bulletin a later run would have delivered.
- The watchdog is deliberately *not* an alarm: CloudWatch alarms page
  operators, but the people who need to know that a patch day was missed are
  the recipients. SNS alarms remain for code-level failures (FR-6.2).
- Writing state only *after* SES accepts the message (FR-3.2) means a crash
  loses an idempotency record but never a delivery.

## Practical impact

- **Cost of a wider window:** ten invocations per month instead of one. The
  nine non-final slots normally return `not-published-yet` after a single 404
  and cost nothing measurable; the last one decides between the notice and the
  bulletin. Timeout is 60 s, memory 256 MB.
- **Failure of a middle attempt is normal.** A `attempt-failed` result is
  logged, returns without raising, and lets the next slot try again, so a
  transient SAP blip does not trip the `Errors` alarm. Only *unexpected*
  exceptions raise.
- **SES sandbox:** if the account is still in the SES sandbox, only verified
  addresses receive mail. The watchdog alert is affected too, because it uses
  the same sender and list.
- **Removing the watchdog** means deleting the rule and `EnableWatchdog=false`
  in CloudFormation; the state parameters `last_watchdog_alert_*` then simply
  stop being written.
- If the operator re-runs a month deliberately (backfilling after an outage),
  the invoke payload must carry `ignore_idempotency: true`; the run result then
  reports `idempotency_bypassed` so the log tells the story.
- `last_attempt_at` is written on **every** attempt, so "did it even run?" is
  answerable from one parameter, without reading logs.
