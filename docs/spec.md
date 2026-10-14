# Functional design specification — SAP Security Patch Day notifier

**Status:** living document. This is the product contract for the repository.
Decisions that are **not** dictated by this specification live in
[`addenda/`](./addenda/) and are appended, never renumbered.
**Revision:** 1 — 2026-10-14.

Related: [`../CLAUDE.md`](../CLAUDE.md) (coding discipline),
[`addenda/`](./addenda/) (decision register),
[`../CLAUDE-deployment.md`](../CLAUDE-deployment.md) (deployment runbook).

---

## 1. Purpose and scope

On SAP's monthly **Security Patch Day** (second Tuesday of the month), SAP
publishes the month's security notes. Security teams must notice publication
quickly, and the published page must reach stakeholders who do not have SAP
Support logins.

This system:

1. runs on a schedule aligned to SAP Patch Day, in Singapore time;
2. detects that the **current month's** bulletin is published;
3. extracts the monthly notes table (columns `Note#`, `Title`, `Priority`,
   `CVSS`, ending before the "security researchers … visit here" paragraph);
4. emails the extracted table through Amazon SES to a configured list.

**Out of scope:** downloading the notes themselves, evaluating SAP systems,
authenticating to `me.sap.com` (the notes *links* are included, not their
content), any interactive interface, any database.

## 2. Actors and external systems

| Actor | Role |
| ----- | ---- |
| SAP Support Portal (`support.sap.com`) | Source of the publication index and the monthly bulletin page |
| Amazon EventBridge | Trigger, in UTC, aligned to Singapore time |
| AWS Lambda (`python3.12`, arm64) | The whole application |
| Amazon SES | Delivery |
| SSM Parameter Store | Configuration (recipients, sender, policy) and delivery state |
| SNS + CloudWatch | Operational alarms to an optional human-email endpoint |
| Operator | Deploys via CloudFormation or AWS CLI; reads logs; runs the smoke test |
| Recipient | Human receiving the bulletin email |

## 3. Functional requirements

### FR-1 Schedule

| Id | Requirement |
| -- | ----------- |
| FR-1.1 | The delivery rule fires on the **second Tuesday of each month** at `16:00` Singapore time (`UTC+08:00`), expressed as an EventBridge cron in UTC: `cron(0 8 ? * TUE#2 *)`. |
| FR-1.2 | Retry attempts fire every 30 minutes through the retry window `16:00`–`20:30` Singapore time on that day: `cron(0,30 8-12 ? * TUE#2 *)` — ten attempts, the last one at `20:30`. |
| FR-1.3 | A watchdog rule fires on the **second Wednesday** at `09:00` Singapore time (`cron(0 1 ? * WED#2 *)`). |
| FR-1.4 | All schedule expressions are CloudFormation parameters with the defaults above, so a deployment can shift them without editing code. |
| FR-1.5 | The application never assumes it is scheduled: it derives the patch-day month from the injected clock in `Asia/Singapore`. |

### FR-2 Publication detection

| Id | Requirement |
| -- | ----------- |
| FR-2.1 | The canonical bulletin URL is constructed, not scraped: `https://support.sap.com/en/my-support/knowledge-base/security-notes-news/{month-name-lowercase}-{year}.html` (for example `october-2026.html`). |
| FR-2.2 | The canonical URL is fetched; **HTTP 200 with a parseable notes table is the authority** on publication. This holds even when the index page has not yet turned this month's date into a link. |
| FR-2.3 | The index page `https://support.sap.com/en/my-support/knowledge-base/security-notes-news.html` is parsed for the section whose heading text matches `Dates for <year>`; the entry whose text matches `<Month> <day>` is inspected for an `<a href>` ancestor or descendant. |
| FR-2.4 | The link-transition result is **corroborating evidence only** and is reported in the run result, in logs and in the delivered email's provenance line — never used to suppress delivery. |
| FR-2.5 | `HTTP 404`/`410` on the canonical URL is treated as *definitively not published yet*. Timeouts, DNS failures, connection resets, `429` and `5xx` are *unverified* and are retried (see FR-3). |
| FR-2.6 | A canonical URL that returns 200 but yields no notes table, or a table with zero rows, is *not published*, and is recorded as `unverified_table` for diagnosis (it usually means SAP changed the markup). |
| FR-2.7 | The bulletin's own date claim is not trusted: the month name in the page title and the target month must agree, otherwise the page is treated as stale and not delivered (guards against a cached previous-month page). |

### FR-3 Retry, idempotency and state

| Id | Requirement |
| -- | ----------- |
| FR-3.1 | An attempt delivers the bulletin at most once per (month, kind), where kind ∈ {`bulletin`, `pending`, `watchdog`}. Re-running the Lambda repeatedly must not resend. |
| FR-3.2 | State lives in SSM Parameter Store under the state path (spec §9.2) and is written after a successful send, never before. |
| FR-3.3 | If the bulletin is not published on a non-final attempt, the Lambda records the attempt and returns without emailing. |
| FR-3.4 | On the **final attempt** of the window (`>=` retry-window end, Singapore time), if the bulletin is still not published the Lambda sends one *pending notice* (if `notify_when_not_published` is `true`) and records it. **At most one notice per month**: a further final attempt returns `notice-already-sent` instead of emailing again. `RETRY_WINDOW_END_SGT` should equal the last scheduled attempt so only that attempt is final. |
| FR-3.5 | If the final attempt is *unverified* (SAP unreachable), the Lambda sends a notice whose subject and body state that publication could not be verified — it must never claim the notes are absent when the truth is unknown. |
| FR-3.6 | A `pending` notice does not prevent a later successful `bulletin` delivery for the same month, and vice versa. |
| FR-3.7 | Every attempt writes `last_attempt_at` (ISO-8601 UTC) to the state path, whatever the outcome. |
| FR-3.8 | The watchdog (FR-1.3) checks that a `bulletin` delivery was recorded for the current patch-day month; if not, it emails an alert to the recipients (once per month) naming the month and the state observed. |
| FR-3.9 | `DRY_RUN=true` substitutes logging implementations of the mailer and the state store; the delivery logic itself is unchanged, no email is sent and no state is written. |

### FR-4 Extraction

| Id | Requirement |
| -- | ----------- |
| FR-4.1 | The notes table is located by its header row, whose cells contain `Note#`, `Title`, `Priority`, `CVSS` (case-insensitive, whitespace-insensitive, tolerant of the shared first cell spanning columns). |
| FR-4.2 | Rows are read as `(note_ref, title, priority, cvss)`; a row that does not yield a note reference is skipped and logged, it never aborts the run. |
| FR-4.3 | Extraction stops at the paragraph beginning `To know more about the security researchers`, whether that paragraph follows the table or appears inside it. |
| FR-4.4 | Per note, the extractor derives: `note_id`, `note_url`, `source` (`sap` when the link host is `me.sap.com`, `github` when it is a `github.com` advisory), `cve_ids`, `is_update` (title cell contains "Update to Security Note released on"), `product`, `versions`, `headline`, `priority`, `cvss_score`, `cvss_vector`, `cvss_url`, and the sanitised `title_html`. |
| FR-4.5 | `product` and `versions` are parsed from the title cell's `Product - …` and `Version(s) - …` fragments; when absent (GitHub advisories use `Library - …`), the fragment label that *is* present is used as `product` and the other field stays empty. |
| FR-4.6 | `cvss_vector` is parsed from the `first.org` calculator fragment (`CVSS:3.0/…`); the CVSS cell text is kept as the displayed score even when the vector cannot be parsed. |
| FR-4.7 | The bulletin summary (number of new notes and number of updates as SAP states it in the intro paragraph) is captured when present, and is always recomputed from the parsed rows for the email (the recomputed values win). |
| FR-4.8 | Parsing is lenient about whitespace, non-breaking spaces and `<br>` separators, and strict about structure: a missing table is `unverified_table`, never an empty email. |

### FR-5 Email

| Id | Requirement |
| -- | ----------- |
| FR-5.1 | One email per delivery, sent through Amazon SES (`ses:SendEmail`) to every address in the configured recipient list, from the configured sender, with the configured configuration set. |
| FR-5.2 | Subject for a bulletin: `[SAP Security Patch Day] <Month> <Year> — <n> notes (<critical> critical, <high> high, <medium> medium, <low> low)`. Counts are computed from the parsed rows; the prefix is configurable. |
| FR-5.3 | Body is `multipart/alternative`: a table-based HTML part and a plain-text part. Both are generated from the same parsed model, and a test asserts both contain every note id. |
| FR-5.4 | The HTML table carries the same four columns as the source (`Note#`, `Title`, `Priority`, `CVSS`); note references link to their source, CVSS scores link to the FIRST calculator, CVE identifiers link to cve.org. Priority is colour-coded in a way that survives images-off clients (colour, not iconography). |
| FR-5.5 | A CSV attachment (`note_id,source,priority,cvss_score,cvss_vector,cve_ids,product,versions,is_update,headline,note_url`) is attached only when `attach_csv` is `true`; when attached, the message is sent as a raw MIME body. |
| FR-5.6 | HTML from the source page is **sanitised** before it is embedded: only `a`, `strong`, `b`, `em`, `i`, `br` survive; `a` keeps only `href` (scheme `http`, `https`, `mailto`) plus `rel="noopener noreferrer"`; everything else is dropped while keeping its text. `script`, `style`, `iframe`, `img`, `svg`, event handlers, `data:`/`javascript:` URLs and comments are removed. |
| FR-5.7 | The `(opens in new tab)` decoration and other markup-only artefacts are removed from visible text. |
| FR-5.8 | Pending and watchdog notices use the same renderers with a notice body; they never include an empty table. |
| FR-5.9 | Emails contain no internal identifiers: no stack name, no SSM path, no `run_mode`, no design rationale. Provenance lines name only the public URL, the fetch time (Singapore time) and whether the index link had transitioned. |

### FR-6 Observability

| Id | Requirement |
| -- | ----------- |
| FR-6.1 | Every run returns a JSON result (spec §7) and logs one structured line per phase: target month, index result, canonical URL status, table row count, delivery id or skip reason. |
| FR-6.2 | A CloudWatch alarm fires on `Errors >= 1` and another on `Throttles >= 1` over a one-day window, publishing to an SNS topic (email subscription optional). |
| FR-6.3 | Log retention is a deployment parameter (default 90 days). |
| FR-6.4 | Logs never contain recipient addresses in full: addresses are masked to `f***@example.com` in log output (they are visible in the delivered email, which is the point). |

## 4. Data model

Frozen dataclasses in `src/sap_patch_day/models.py` — the single canonical
Python shape of each concept.

| Model | Fields |
| ----- | ------ |
| `PatchDayMonth` | `year:int`, `month:int`; derived `label` (`2026-10`), `month_name` (`October`), `bulletin_slug` (`october-2026`), `display` (`October 2026`) |
| `IndexEntry` | `month_name:str`, `day:int`, `url:str \| None`; derived `is_linked` |
| `SecurityNote` | `note_id`, `note_url`, `source`, `title_html`, `headline`, `cve_ids:tuple`, `product`, `versions`, `priority`, `cvss_score`, `cvss_vector`, `cvss_url`, `is_update`, `update_period` |
| `Bulletin` | `target:PatchDayMonth`, `source_url`, `page_title`, `intro_text`, `notes:tuple[SecurityNote,…]`, `fetched_at:datetime`, `link_transitioned:bool \| None` |
| `BulletinSummary` | `total`, `critical`, `high`, `medium`, `low`, `updates`, `new_notes`, `sap_notes`, `github_advisories` |
| `PublicationCheck` | `target`, `canonical_url`, `http_status:int \| None`, `outcome` (`published` \| `not_published` \| `unverified`), `link_transitioned`, `detail` |
| `RuntimeConfig` | see §9.3 |
| `StateSnapshot` | `last_delivered_month`, `last_delivered_kind`, `last_delivered_at`, `last_attempt_at`, `last_watchdog_alert_month`, `last_watchdog_alert_at` |
| `SendRequest` | `recipients:tuple`, `sender`, `reply_to`, `content:EmailContent`, `configuration_set` |
| `EmailContent` | `subject`, `html_body`, `text_body`, `attachment:Attachment \| None` |

## 5. States and transitions

Delivery state machine, per patch-day month:

```
                attempt (not published, non-final)
   ┌────────────────────────────────────────────────┐
   │                                                ▼
 [IDLE] ──attempt (published)──► [BULLETIN_SENT] ──retry──► (no-op: already delivered)
   │                                 ▲
   │ attempt (not published, final)  │ later attempt finds it published
   ▼                                 │
 [PENDING_SENT] ─────────────────────┘

 [IDLE] ──watchdog (no bulletin this month)──► [WATCHDOG_ALERTED] ──watchdog──► (no-op)
```

| From | Trigger | To | Notes |
| ---- | ------- | -- | ----- |
| `IDLE`/`PENDING_SENT` | attempt, published | `BULLETIN_SENT` | state written only after SES accepts the message |
| `IDLE` | attempt, not published, non-final | `IDLE` | `last_attempt_at` still written |
| `IDLE` | attempt, not published, final | `PENDING_SENT` | one notice per month |
| `PENDING_SENT` | attempt, published | `BULLETIN_SENT` | allowed: the notice must not suppress the real thing |
| `BULLETIN_SENT` | any further attempt | unchanged | returns `already-delivered` |
| `IDLE`/`PENDING_SENT` | watchdog | `WATCHDOG_ALERTED` | once per month |
| `BULLETIN_SENT` | watchdog | unchanged | returns `watchdog-satisfied` |

**Forbidden transitions**

| Transition | Why forbidden |
| ---------- | ------------- |
| `PENDING_SENT` → suppress `BULLETIN_SENT` for the same month | A notice is a status report, not a delivery; suppressing the bulletin would silently lose the month's work product |
| Any → state written before the send succeeds | A crash between write and send would lose the delivery permanently |
| `BULLETIN_SENT` → resend on the same month+kind | Duplicate mail to a distribution list is a support burden; a manual resend is expressed as `ignore_idempotency` in the invoke payload |
| `unverified` → treated as `not_published` in the notice wording | Unknown must never be presented as fact |

## 6. Trigger contract (EventBridge input)

| Payload | Effect |
| ------- | ------ |
| `{}` (no payload) | `run_mode=delivery` |
| `{"run_mode": "delivery"}` | The FR-2 → FR-5 delivery path |
| `{"run_mode": "watchdog"}` | The FR-3.8 watchdog path |
| `{"run_mode": "delivery", "target_month": "2026-09"}` | Operator override: run against a specific month (`YYYY-MM`), for smoke tests. Invalid values are rejected with the message in §8 |
| `{"run_mode": "delivery", "ignore_idempotency": true}` | Manual resend; logged loudly, and the result reports `idempotency_bypassed`. It bypasses both the bulletin and the notice guard, so an operator can force a notice that was lost |

Unknown `run_mode` values are rejected (not silently defaulted). Payload
booleans are read tolerantly: `false`, `"false"`, `0`, `"0"`, `"no"`, `"off"` and
`""` all mean *false*, because `aws lambda invoke` delivers a payload's `false`
as the string `"false"` and `bool("false")` is true. Any other value is true.

## 7. Result contract (return value)

A JSON-serialisable object; `status` is the stable part.

```json
{
  "status": "delivered",
  "target_month": "2026-10",
  "canonical_url": "https://support.sap.com/en/my-support/knowledge-base/security-notes-news/october-2026.html",
  "http_status": 200,
  "link_transitioned": true,
  "note_count": 21,
  "message_id": "0100018f-...",
  "attempt": "final",
  "detail": ""
}
```

`attempt` is `"window"` while the retry window is still open and `"final"`
once the Singapore-time boundary has passed. `canonical_url`, `http_status` and
`link_transitioned` are present whenever a probe happened; `note_count` and
`message_id` only when something was rendered and sent.

| `status` | Meaning |
| -------- | ------- |
| `delivered` | Bulletin emailed; `message_id` present |
| `already-delivered` | FR-3.1 skip |
| `not-published-yet` | Non-final attempt, nothing emailed |
| `pending-notice-sent` | Final attempt, notice emailed |
| `unverified-notice-sent` | Final attempt, SAP unreachable, notice emailed |
| `notice-already-sent` | Final attempt, but this month's notice was already emailed |
| `attempt-failed` | Non-final attempt, SAP unreachable, nothing emailed |
| `watchdog-alert-sent` | FR-3.8 alert emailed |
| `watchdog-satisfied` | FR-3.8 found a delivered bulletin for the month |
| `watchdog-already-alerted` | FR-3.8 found that this month's alert was already sent |
| `invalid-request` | Payload rejected per §6; `detail` carries the reason |

## 8. Validation rules and exact error messages

Configuration errors abort **before** any HTTP request. Messages are the exact
strings raised as `ConfigError` and asserted in tests.

| Condition | Message |
| --------- | ------- |
| `sender` parameter absent or empty | `SES sender address is not configured: set the SSM parameter <path>sender` |
| `sender` not a valid address | `SES sender address is not a valid email address: '<value>'` |
| `recipients` absent, empty or whitespace only | `No recipients configured: set the SSM parameter <path>recipients` |
| recipient not a valid address | `Recipient is not a valid email address: '<value>'` |
| `reply_to` present but invalid | `Reply-to address is not a valid email address: '<value>'` |
| `attach_csv` not `true`/`false` | `attach_csv must be 'true' or 'false', got '<value>'` |
| `notify_when_not_published` not `true`/`false` | `notify_when_not_published must be 'true' or 'false', got '<value>'` |
| `retry_window_end` not `HH:MM` | `retry_window_end must be HH:MM in Singapore time, got '<value>'` |
| `HTTP_TIMEOUT_SECONDS` not a positive integer | `HTTP_TIMEOUT_SECONDS must be a positive integer, got '<value>'` |
| `HTTP_MAX_ATTEMPTS` not a positive integer | `HTTP_MAX_ATTEMPTS must be a positive integer, got '<value>'` |
| `HTTP_DEADLINE_SECONDS` not a positive integer | `HTTP_DEADLINE_SECONDS must be a positive integer, got '<value>'` |
| payload `run_mode` unknown | `Unsupported run_mode: '<value>'` |
| payload `target_month` unparseable | `target_month must be YYYY-MM, got '<value>'` |

Parse failures are `ParseError` with a message naming the missing element, e.g.
`No notes table found: expected a table header containing Note#, Title, Priority, CVSS`.
Missing table during a probe is **not** a raised error: it becomes
`unverified_table` per FR-2.6, so that one malformed page cannot wake anyone up.

## 9. Configuration contract

### 9.1 Environment variables (Lambda configuration, never secrets)

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `SSM_CONFIG_PATH` | `/sap-security-patch-day/` | Prefix of the configuration parameters |
| `SSM_STATE_PATH` | `/sap-security-patch-day/state/` | Prefix of the state parameters |
| `AWS_REGION` | injected by Lambda | Region for SES/SSM clients |
| `CONFIGURATION_SET` | empty | SES configuration set name, when deployed |
| `RETRY_WINDOW_END_SGT` | `20:30` | FR-3.4 final-attempt boundary, Singapore time `HH:MM`; keep it equal to the last slot of the delivery schedule |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `HTTP_MAX_ATTEMPTS` | `3` | Attempts per URL per invocation |
| `HTTP_BACKOFF_SECONDS` | `2` | Base of the linear backoff between attempts |
| `HTTP_DEADLINE_SECONDS` | `30` | HTTP budget for the whole invocation, across both URLs |
| `USER_AGENT` | `sap-patch-day-notifier/1 (aws-lambda; +https://support.sap.com)` | Identifies us to SAP politely |
| `DRY_RUN` | `false` | FR-3.9 |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `BULKHEAD_RECIPIENTS` | empty | Optional comma-separated override used by tests/smoke runs (never used in production deployments) |

### 9.2 SSM parameters

Configuration path (read-only for the function):

| Name | Type | Default seeded | Notes |
| ---- | ---- | -------------- | ----- |
| `sender` | String | deployment parameter | Must be a verified SES identity |
| `recipients` | StringList | deployment parameter | Comma-separated; editable later without redeploying |
| `reply_to` | String | empty (absent) | Optional |
| `subject_prefix` | String | `[SAP Security Patch Day]` | FR-5.2 |
| `attach_csv` | String | `false` | FR-5.5 |
| `notify_when_not_published` | String | `true` | FR-3.4 |

State path (read/write for the function):

| Name | Initial value | Written |
| ---- | ------------- | ------- |
| `last_delivered_month` | `-` | after a successful bulletin send |
| `last_delivered_kind` | `-` | with the month (`bulletin`/`pending`/`watchdog`) |
| `last_delivered_at` | `-` | with the month |
| `last_attempt_at` | `-` | every attempt (FR-3.7) |
| `last_watchdog_alert_month` | `-` | when a watchdog alert is sent |
| `last_watchdog_alert_at` | `-` | with the alert month |

### 9.3 Runtime configuration object

`RuntimeConfig` (frozen) merges the environment with the SSM values, having
validated every field in §8. Design note: **environment variables hold paths
and policies; SSM holds people** (addresses). That split means a recipient
change is a parameter change, never a deploy.

## 10. Deployment contract

Both deployment paths (spec §10.1) create these objects with identical names by
default.

| Object | Default name / value |
| ------ | -------------------- |
| Lambda function | `sap-security-patch-day-notifier` |
| IAM role | `sap-security-patch-day-notifier-role` |
| Log group | `/aws/lambda/sap-security-patch-day-notifier` (retention 90 days) |
| EventBridge rules | `sap-security-patch-day-delivery`, `sap-security-patch-day-watchdog` |
| SNS topic | `sap-security-patch-day-alerts` |
| SES identity | the sender address (creation is a parameter) |
| SES configuration set | `sap-security-patch-day` |
| Artefact bucket (CFN path) | `sap-patch-day-artifacts-<account-id>-<region>` |
| Runtime | `python3.12`, `arm64`, 256 MB, 60 s timeout |
| CloudWatch alarms | `<function>-errors`, `<function>-throttles` |

CloudFormation parameters: `FunctionName`, `ArtifactBucket`, `ArtifactKey`,
`SenderEmailAddress`, `RecipientEmails`, `ReplyToAddress`, `CreateSesIdentity`,
`ConfigurationSetName`, `ConfigParameterPath`, `StateParameterPath`,
`DeliveryScheduleExpression`, `WatchdogScheduleExpression`, `RetryWindowEndSgt`,
`EnableWatchdog`, `AlarmNotificationEmails`, `MemorySize`, `TimeoutSeconds`,
`LogRetentionDays`, `Architecture`, `Runtime`, `Tags`.

## 11. Non-functional requirements

| Id | Requirement |
| -- | ----------- |
| NFR-1 | A monthly run costs effectively nothing: one Lambda invocation plus retries, no provisioned capacity, no always-on services. |
| NFR-2 | Least privilege: the function's policy grants only `logs:CreateLogStream`/`PutLogEvents` on its own group, `ssm:GetParameter*` on the config path, `ssm:PutParameter` on the state path, `ses:SendEmail`/`SendRawEmail` on the sender identity, and `xray:PutTraceSegments` when tracing is on. |
| NFR-3 | No credentials, account IDs or real addresses in the repository. |
| NFR-4 | The function timeout is 60 s; the HTTP client enforces a 30 s deadline across both URLs (`HTTP_DEADLINE_SECONDS`), so the worst case is 30 s + one 20 s request = 50 s and the run ends with a result rather than a Lambda timeout. |
| NFR-5 | The scrapers are read-only, identify themselves in `User-Agent`, and make at most 2 requests per attempt with linear backoff — no crawling, no concurrency. |
| NFR-6 | Failure to reach SAP never produces a false "nothing published" claim (FR-3.5). |
| NFR-7 | The system is idempotent per month; a repeated or missed scheduled invocation cannot duplicate or lose a month. |

## 12. Assumptions and open questions

| Id | Statement | Status |
| -- | --------- | ------ |
| A-1 | SAP keeps the URL slug format `{month}-{year}.html` under the same directory. | Assumed; FR-2.2 reports the status code when it breaks, and the pending notice tells the operator to look. Revisit if SAP renames the path. |
| A-2 | The index page keeps a `Dates for <year>` heading with one entry per patch day. | Assumed; `link_transitioned` becomes `None` (unknown) rather than failing the run if the heading disappears. |
| A-3 | The notes table keeps the four-column header and the "To know more about the security researchers" closing paragraph. | Assumed; FR-2.6 treats markup change as *unverified*, sounds the alarm, and never emails a misleading table. |
| A-4 | SAP's robots/ToS permit this read-only, twice-a-month access. | Operator's responsibility; requests are minimal and identified. |
| Q-1 | Should the attachment default ever become `true`? | Open — default stays `false` (FR-5.5); change via SSM, and record an addendum if the default changes. |
| Q-2 | Should recipients be split into HTML and plain-text audiences? | Open — no demand yet; `recipients` stays a single list. |
