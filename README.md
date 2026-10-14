# SAP Security Patch Day notifier

An AWS Lambda that notices when SAP publishes its monthly Security Patch Day
notes and emails the notes table to a list of recipients.

It runs on the **second Tuesday of the month** (the day SAP normally ships
patches), every 30 minutes from 16:00 until the last slot at **20:30 Singapore
time**, because SAP does not announce a release time. A **watchdog** the
following morning reports if the month produced no email at all, so a silent
failure cannot be mistaken for "nothing was published".

- Runtime: Python 3.12, arm64, no dependencies beyond the `boto3` the runtime
  already provides (`docs/addenda/0003`).
- Delivery: Amazon SES (`SendEmail`, or a raw MIME message when the CSV
  attachment is switched on).
- Recipients: SSM Parameter Store — **changeable without a redeploy**.
- Deployment: CloudFormation **or** an ordered set of AWS CLI scripts that
  create the same objects (`docs/addenda/0008`).

## How it decides there is something to send

1. Build this month's bulletin URL (`…/security-notes-news/october-2026.html`)
   and probe it — the URL shape is stable, so no scraping of the index is
   required to find it.
2. `404`/`410` means "not published yet": come back at the next attempt inside
   the retry window.
3. `200` and the page parses into the `Note# / Title / Priority / CVSS` table →
   email it. **The live URL wins even if the index page has not turned this
   month's date into a link yet** (the index link is corroborating evidence
   only, `docs/addenda/0005`).
4. Anything else (timeout, 429, 5xx, unparseable page) is `unverified`: the run
   says so, the alarm fires, and the last attempt of the window emails a notice
   instead of silently doing nothing — one notice per month, never one per
   attempt.
5. Each outcome is recorded in SSM, so a duplicate EventBridge delivery cannot
   send the same email twice — a manual resend is an explicit payload flag.

## Quick start (no AWS required)

```bash
scripts/dev.sh                 # dry run against the saved October 2026 pages
scripts/dev.sh --live          # same, but fetch the real pages from support.sap.com
scripts/check.sh               # every gate: lint, types, templates, tests
```

`scripts/dev.sh` creates `.venv`, installs the pinned tools, runs the same code
path the Lambda runs, and writes `dist/preview-<YYYY-MM>.html` so the email can
be opened in a browser. Nothing is sent and no state is written.

## Deploy

Pick **one** path per environment and stay on it: CloudFormation will revert
drift introduced by the CLI scripts, and the scripts have no drift detection.

### CloudFormation

```bash
scripts/deploy-cloudformation.sh \
    --region ap-southeast-1 \
    --sender-address sap-patch-day@example.com \
    --recipients "you@example.com,team@example.com" \
    --alarm-email "you@example.com"
```

The script lints and validates both templates, builds and uploads the package
(`scripts/build-package.sh`), creates the artefact-bucket bootstrap stack if the
bucket is missing, deploys the application stack, prints its outputs, and runs
the deployed smoke test.

### AWS CLI (CloudShell)

```bash
bash scripts/awscli/deploy-all.sh \
    --region ap-southeast-1 \
    --sender-address sap-patch-day@example.com \
    --recipients "you@example.com,team@example.com" \
    --alarm-email "you@example.com"
```

`scripts/awscli/01`–`09` are individually idempotent and can be re-run:

| Step | Creates |
| ---- | ------- |
| `01-artifact-bucket.sh` | versioned, encrypted, public-access-blocked artefact bucket |
| `02-ssm-parameters.sh` | sender, recipients, reply-to, subject prefix, CSV/bulletin flags |
| `03-ses-identity.sh` | SES sender identity and configuration set, plus the sandbox status |
| `04-iam-role.sh` | execution role with four scoped statements (no wildcard actions) |
| `05-lambda-function.sh` | package upload, log group with retention, the function and its environment |
| `06-eventbridge.sh` | delivery rule (`cron(0,30 8-12 ? * TUE#2 *)`) and watchdog rule, with invoke permissions |
| `07-observability.sh` | SNS alert topic, optional email subscription, Errors/Throttles alarms |
| `08-verify.sh` | deployed smoke test: invokes with `DRY_RUN=true`, prints the result, restores the environment |
| `09-teardown.sh` | removes everything the steps above created, after a confirmation prompt |

Before relying on real delivery: click the SES verification link sent to the
sender address, and verify every recipient too if the account is still in the
SES sandbox. `03-ses-identity.sh` prints the account's sandbox status.

## Configuration

People belong in SSM; policy belongs in the environment. Nothing sensitive is
committed, and the Lambda's environment holds only paths and policy values.

| SSM parameter (`/sap-security-patch-day/…`) | Meaning |
| ------------------------------------------- | ------- |
| `sender` | Verified SES sender identity |
| `recipients` | Comma-separated (or JSON array) recipient list |
| `reply_to` | Optional reply-to address |
| `subject_prefix` | Subject prefix, default `[SAP Security Patch Day]` |
| `attach_csv` | Attach the notes as CSV (raw MIME message) |
| `notify_when_not_published` | Email a notice when the window closes with nothing sent |

Editing `recipients` (in the console, or `aws ssm put-parameter --overwrite`)
changes who receives the next email — no redeploy. Changing *policy* is an
environment update on the function: `SSM_CONFIG_PATH`, `SSM_STATE_PATH`,
`RETRY_WINDOW_END_SGT`, `HTTP_TIMEOUT_SECONDS`, `HTTP_MAX_ATTEMPTS`,
`HTTP_BACKOFF_SECONDS`, `HTTP_DEADLINE_SECONDS`, `USER_AGENT`, `DRY_RUN`,
`LOG_LEVEL`. The deployed
defaults are listed in `.env.example`, and
[`docs/spec.md`](docs/spec.md) §9 is the contract.

Delivery state lives under `/sap-security-patch-day/state/` and is written only
*after* SES accepts a message, so a crash can lose an idempotency record but
never a delivery. No deployment path manages those parameters, so a stack update
can never erase the record of what has been sent.

## Tests and gates

| Command | What it proves |
| ------- | -------------- |
| `scripts/check.sh` | Every mechanical gate: `ruff` lint + format, `mypy`, `shellcheck`, `bash -n`, Bash-3.2 guards, `cfn-lint`, the whole pytest suite |
| `pytest tests/unit` | Parsers, sanitiser, renderers, config validation, clock, state, mailer, handler payloads, dry-run CLI |
| `pytest tests/integration` | The whole handler over injected ports (every outcome branch), CloudFormation↔CLI parity, template invariants, module budget, shell portability, captured pages |
| `scripts/live-check.sh` | Opt-in: the live tests plus a live dry run against `support.sap.com` (needs egress) |
| `scripts/capture-fixture.sh --month YYYY-MM` | Saves a real page into `tests/fixtures/captured/` for regression testing |

Fixtures are either **captured** (raw bytes, never edited) or **reconstructed**
(real content, rebuilt markup) and are labelled as such — see
`tests/fixtures/README.md` and
[addendum 0010](docs/addenda/0010-test-provenance-and-validation-status.md),
which is also the honest record of which validation layers have actually run.

## Repository layout

```
src/sap_patch_day/       the function: 16 modules, ports and adapters, no dependencies
infrastructure/cloudformation/  bootstrap bucket + application stack
scripts/                 check.sh, dev.sh, live-check.sh, capture-fixture.sh,
                         build-package.sh, deploy-cloudformation.sh
scripts/awscli/          the ordered AWS CLI deployment path (00-env.sh holds the defaults)
tests/                   256 unit, 321 integration, opt-in live suite, fixtures
docs/spec.md             the contract: requirements, data model, payloads, config
docs/addenda/            numbered decisions and their rationale (0001–0010)
GREENFIELD.md, CLAUDE*.md  the coding discipline this repository follows
```

## What has not been validated yet

Stated plainly, because a green build must not be mistaken for a verified
deployment (`docs/addenda/0010`):

- **The live pages have never been fetched by this build.** The sandbox that
  wrote it has no egress to `support.sap.com`; the fixtures are reconstructed
  from the live pages' text. Run `scripts/live-check.sh` from a host with
  internet access before trusting the parser.
- **Neither deployment path has been run against a real AWS account.** Both are
  statically checked (`cfn-lint`, template invariants, parity tests, shellcheck)
  and neither has been executed.
- **No email has actually been sent.** SES delivery is exercised through the
  `boto3` service model and a recording client, never against the service.

## Deliberate deviations

Following `GREENFIELD.md` except for two sections, both recorded in
[addendum 0009](docs/addenda/0009-deferred-container-and-shiphats-sections.md):
there is no container contract (§10.1 — a Lambda with no dependencies does not
need Dockerfiles) and no SHIP-HATS GovTech pipeline files (§10.3 — this
repository deploys through CloudFormation/CloudShell and CI runs on GitHub
Actions). Both are reversible: nothing else depends on their absence.
