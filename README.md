# Municipal Finance AI Workflow Tool

A local-first AI workflow tool for small municipal finance departments. It turns
recurring, error-prone finance tasks into auditable, source-linked review
workflows. It is a controlled workflow runner, not a chatbot: every calculation
is done by deterministic code, the model is used only for language tasks, and
every run is logged and exportable for human review.

This is an MVP built on synthetic data only. See
[`docs/Project_Outline_Master.md`](docs/Project_Outline_Master.md) for the full
spec and [`docs/decisions.md`](docs/decisions.md) for architecture decisions.

## Problem

Small-city finance teams repeat the same manual, error-prone tasks every month
and quarter: reconciling a bank statement against a ledger, explaining budget
variances, and checking draft financial reports for inconsistencies before they
go into an agenda packet or audit. These tasks are tedious and easy to get
wrong, but they also demand exactness and an audit trail. A general chatbot is
the wrong tool: finance staff cannot trust a black box that might invent an
account number or miscalculate a variance, and auditors need to see where every
number came from.

## Target users

Non-technical municipal finance staff:

- Finance director
- Accountants
- Accounts payable staff
- Finance analysts
- Administrative staff supporting financial reporting

Users are assumed to be non-technical, to need no prompt-engineering knowledge,
and to distrust black-box financial outputs. The tool gives them plain-language
explanations, source evidence for every claim, and explicit review controls.

## Why deterministic logic is used

All financial logic is deterministic Python code, never the model. Parsing,
cleaning, transaction matching, every calculation (dollar and percentage
variance, totals, subtotal checks), validation, source-row tracking, export
formatting, and audit logging are all deterministic and reproducible. Running
the same inputs produces the same findings every time, which is what makes the
output auditable and trustworthy. The deterministic core lives in
`src/normalize/`, `src/ingest/`, and the `src/workflows/*` modules; the run
ledger and audit log are in `src/core/`.

## Why the model is limited to language tasks

The model may only explain, summarize, draft, classify, and flag. It must never
calculate, decide a transaction match, or invent an account number, fund,
vendor, amount, date, or policy, and it must cite source-row references for
every claim. This is enforced two ways: each workflow builds the LLM prompt only
from the deterministic findings, and a deterministic validation layer
(`ValidationResult` in `src/core/schemas.py`) checks every LLM response for
invented references and numeric claims not present in the deterministic results,
flagging or rejecting anything that fails. The model is reached through a
uniform, duck-typed provider interface (`generate_structured_response` /
`mock_response`); each workflow ships a built-in **mock provider** that is the
default path, runs with no API key and no internet, and derives its output only
from the deterministic findings. Prompt templates are versioned in
`src/llm/prompts.py`.

## Supported workflows

| Workflow | What it does (deterministic) | What the AI does |
| --- | --- | --- |
| **Bank reconciliation** | Match bank statement to ledger by amount and date with configurable tolerances; flag unmatched items, timing differences, and potential duplicates; compute summary totals. | Summarize and explain unmatched items, citing source rows. |
| **Budget-to-actual variance review** | Join budget to actuals by fund/account/department/object; compute dollar and percent variance; flag lines over threshold and budget-only / actual-only / missing accounts. | Draft plain-language variance commentary referencing flagged rows. |
| **Financial report consistency review** | Check a draft report for subtotal mismatches, invalid account codes, duplicate lines, missing sections, large changes from a prior version, and inconsistent naming. | Explain each flagged issue and draft a review checklist. |
| **Guided freeform** | A structured (not chat) fallback that routes ad-hoc tasks through the same logging and validation; fails closed unless sensitivity is confirmed. | Produce a DRAFT output, source-linked where possible, for human review. |
| **Transaction search** | Parse a plain-English query into validated `SearchCriteria`; apply as deterministic pandas filters against Tyler-normalized GL, AP, check, and PO exports; return matched rows with source-row traceability. | Translate the plain-English query into a structured `SearchCriteria` JSON (schema-validated before execution); summarize matches and suggest review steps. |
| **AP duplicate / suspicious payment review** | Run eight deterministic checks (D1-D8) on Tyler AP invoice exports: duplicate invoice numbers, same-vendor/amount near-date pairs, similar vendor names, missing PO over threshold, payment before invoice date, inactive vendor, unknown vendor, split-payment pattern. | Summarize flagged issues and draft review notes citing source rows; does not conclude fraud. |
| **Journal entry upload prep** | Validate a draft JE against the chart of accounts and fiscal-period config: debit/credit balance, date in period, required fields, active account codes, no negative/both-populated, no duplicate journal-line; produce an upload-ready Munis-template workbook or a blocking error report. Strictly fail-closed: no upload file is written unless all blocking checks pass. | Draft a plain-language validation summary (labelled DRAFT); never edits or generates upload workbook content. |
| **PO / invoice mismatch review** | Run nine deterministic checks (P1-P8 + P3b) joining Tyler purchase-order and AP invoice exports: invoice exceeds PO, wrong vendor, missing PO reference, closed PO usage, unit-price mismatch, quantity mismatch, received-not-invoiced, invoiced-not-received. | Summarize flagged mismatches and draft review notes citing source rows; does not declare an invoice improper. |

All eight are exposed through the CLI, the registry
(`src/workflows/registry.py`), and the Streamlit UI — every workflow, including
bank reconciliation, is runnable end-to-end from the app on the bundled
synthetic sample data.

### Tyler/Munis-style exports

Four of the eight workflows consume Tyler/Munis-style ERP exports: transaction
search, AP duplicate review, JE upload prep, and PO/invoice review. They all
route file loading through `src/ingest/tyler.py`, the Tyler/Munis-style export
normalizer.

**What the normalizer supports.** Eight dataset types, each with a canonical
column set and Munis-style header aliases:

| Dataset type | `dataset_type` key | Contents |
| --- | --- | --- |
| GL detail | `gl_detail` | Journal transactions (debit/credit -> signed amount derived) |
| AP invoice detail | `ap_invoice_detail` | Invoice-level payables |
| Vendor list | `vendor_list` | Vendor master with Status |
| Check register | `check_register` | Issued checks |
| Purchase orders | `purchase_orders` | PO lines with qty/price/received |
| Budget-to-actual | `budget_to_actual` | Fund/org/object budget vs YTD actual |
| Chart of accounts | `chart_of_accounts` | Account codes with Status |
| JE upload template | `je_upload` | Munis JE upload headers (Journal/Line/Eff Date/...) |

**Dataset type detection** is deterministic header-overlap scoring
(0.8 * required-hit-ratio + 0.2 * optional-hit-ratio, snake_cased after
applying Munis-style aliases). Auto-detection requires a margin of at least
0.05 between the top two candidates; otherwise the caller must pass
`dataset_type` explicitly.

**Traceability guarantees.** The raw file is SHA-256 hashed into `InputFile`
before any normalization. Each row carries a `source_row_index` (0-based
position in the parsed data region); for Excel files with title blocks
`header_row_used` gives the 0-based sheet row of the header, so the absolute
file row is `header_row_used + 1 + source_row_index`. Amount columns are
parsed to `Decimal` (dollar signs, commas, and parentheses-negative all
handled). Debit/credit columns produce a derived signed `amount` column
(`debit - credit`); original columns are kept.

**Synthetic data note.** The files under `data/synthetic/tyler/` imitate common
Munis-style export shapes (local CSV/XLSX only; no ERP integration, no
credentials, no real vendor schemas). The "City of Riverbend" and all vendors,
invoices, checks, POs, and amounts are fabricated. See
`data/synthetic/tyler/README.md` for the full dataset description and
`data/synthetic/tyler/known_answers.json` for the machine-readable manifest of
every planted anomaly.

**CLI examples for the four Tyler workflows:**

```bat
REM Natural-language transaction search (Q1 from the known-answer dataset)
.venv\Scripts\python.exe cli\run_workflow.py transaction-search --sample

REM Run with a custom query against the synthetic dataset
.venv\Scripts\python.exe cli\run_workflow.py transaction-search ^
    --input query="checks to Acme in February 2026" ^
    --input gl_detail=data/synthetic/tyler/gl_detail.csv ^
    --input ap_invoices=data/synthetic/tyler/ap_invoice_detail.csv ^
    --input checks=data/synthetic/tyler/check_register.csv

REM AP duplicate / suspicious payment review
.venv\Scripts\python.exe cli\run_workflow.py ap-duplicate-review --sample

REM Journal entry upload prep (valid draft -> upload-ready workbook)
.venv\Scripts\python.exe cli\run_workflow.py je-upload-prep --sample

REM PO / invoice mismatch review
.venv\Scripts\python.exe cli\run_workflow.py po-invoice-review --sample

REM Export artifacts to disk for any of the above
.venv\Scripts\python.exe cli\run_workflow.py ap-duplicate-review --sample --export out\ap_review
```

All four run fully offline (mock LLM is the default). Add `--real` to use a
real LLM provider when an API key is configured.

### Preflight & capability checks

Before any workflow runs, a deterministic **preflight / capability layer**
(`src/core/preflight.py`) profiles the uploaded files against the workflow's
declared capability and returns one of three statuses:

- **PASS** — inputs satisfy the workflow; it runs normally.
- **PARTIAL** — the workflow runs its supported deterministic checks, but
  conditions it does not fully handle (e.g. a likely sign-convention mismatch, a
  batch deposit, an embedded subtotal) are flagged for human review; the AI may
  only explain the deterministic findings, never resolve the flagged condition.
- **FAIL** — a required file or column is missing, ambiguous, or unparseable; the
  workflow does **not** run, **the LLM is never called**, and a structured report
  with concrete next steps is returned.

This is a fail-closed safety property: the model never "takes over" failed
workflow logic. Preflight also does conservative messy-data handling (column
normalization, semantic-column detection, date/amount parse-confidence,
currency/comma/parenthesis/negative cleanup, repeated-header / footer-total /
duplicate detection, description normalization) while preserving every source-row
reference. Guided Freeform is **not** an automatic fallback for a FAIL — it stays
a separately-labeled, draft-only mode reachable only by a deliberate user action.
The preflight report is recorded in the run ledger, shown in the CLI and
Streamlit UI, and written into the review packet (`preflight_report.json` +
`preflight_summary.md`). See [`docs/workflow_capabilities.md`](docs/workflow_capabilities.md)
for the exact rules and the per-workflow capability table.

Every completed run also produces a **consolidated review packet**
(`review_packet.md` + `run_manifest.json`) on top of the workflow-specific
artifacts. The packet cleanly separates run metadata, source-file SHA-256
hashes, deterministic findings, the AI-assisted draft language (clearly
labelled), validation results, reviewer notes and actions, approval/rejection
status, and the audit history — built deterministically from the ledger, with no
LLM call. See `src/core/review_packet.py`.

## Architecture

### Three-layer architecture

```text
  +---------------------------------------------------------------------------+
  |  DETERMINISTIC CORE  (src/core/, src/workflows/, src/ingest/, src/llm/)   |
  |                                                                           |
  |  input files (CSV / Excel)                                                |
  |         |                                                                 |
  |         v                                                                 |
  |  parse -> clean -> normalize -> match / compute variances /               |
  |  consistency checks -> deterministic findings + source-row refs           |
  |         |                                                                 |
  |         v                                                                 |
  |  AI-assisted explanation / drafting (LLM, mock by default).              |
  |  Explains / summarizes / drafts ONLY. Cites source rows. Never calculates.|
  |         |                                                                 |
  |         v                                                                 |
  |  Validation (deterministic): reject/flag invented references,             |
  |  numeric claims not in findings, missing source refs.                     |
  |         |                                                                 |
  |         v                                                                 |
  |  Run ledger (SQLite) + append-only audit log                              |
  |  Export packet: findings.csv, review_packet.md, run_manifest.json, ...    |
  +---------------------------------------------------------------------------+
            |                              |
            v                              v
  +--------------------+        +---------------------+
  |  FastAPI seam       |        |  Streamlit app       |
  |  (api/)             |        |  (app/)              |
  |                    |        |                     |
  |  Thin HTTP adapter  |        |  Legacy / dev UI    |
  |  over the core.     |        |  (stays working;    |
  |  Typed JSON         |        |   shares the same   |
  |  contracts.         |        |   ledger + audit)   |
  |  No business logic. |        |                     |
  +--------------------+        +---------------------+
            |
            v
  +-----------------------------+
  |  React / Vite / TS console   |
  |  (frontend/)                 |
  |                             |
  |  Guided wizard run flow.     |
  |  Progressive disclosure.     |
  |  Human review + export.      |
  |  NEVER calculates or         |
  |  validates -- renders only.  |
  +-----------------------------+

  CLI (cli/run_workflow.py) drives the core directly, same as the API and
  Streamlit -- same pipeline, same ledger, same audit log.
```

### Why FastAPI + React after the Streamlit MVP

The Streamlit app validated that the workflow logic, audit model, and
trust-boundary concepts work end-to-end. It serves developers well. For
non-technical municipal finance staff, however, Streamlit's dense, form-heavy
layout makes it hard to understand what step they are on, what the AI wrote
versus what the deterministic code found, and what action is being asked of
them. The guided React console replaces that experience with:

- A four-step wizard that sequences upload -> file check -> run -> review,
  so a first-time user is never looking at a blank form wondering what to do.
- Progressive disclosure: the summary strip (finding counts by severity, AI
  safety-check badge, review status) appears first; source-row evidence and
  audit events are one click deeper.
- Explicit visual and structural separation of AI-drafted content from
  deterministic findings (the TrustBoundary component, always labeled "Draft -
  written by AI, verify before use", always in a distinct treatment).
- Clear review controls and export buttons that are never buried.

Streamlit is retained as the legacy/dev surface. It shares the same run
history (same ledger.db, same audit directory, same export directory), so a
developer can run the React UI for a workflow and inspect the result in
Streamlit's AI Audit Log or Export Center without any data migration.

### Responsibility split

| Layer | Owns | Never does |
| --- | --- | --- |
| Deterministic core | Parsing, cleaning, normalization, all financial calculations, transaction matching, validation, source-row tracking, preflight, exports, audit logging | Nothing delegated to the LLM or the UI |
| FastAPI seam (api/) | HTTP transport, multipart file handling, request/response typing, orchestration (calls core, reads ledger), CORS | No workflow business logic; no LLM calls; no recalculation |
| React console (frontend/) | Rendering API data, routing, human review actions (POST to API), export downloads | NEVER computes financial values, matches, or verdicts -- even display-only sums or percentages are forbidden (only currency/date formatting of backend-provided strings is allowed) |

This split is what makes the output trustworthy and auditable: every number
on screen came from deterministic code, not from the UI layer.

### Pipeline data flow (internal)

```text
  input files (CSV / Excel)
          |
          v
  +-------------------------+
  | DETERMINISTIC           |   parse -> clean -> normalize -> match /
  | PROCESSING              |   compute variances / consistency checks
  | (pandas, code only)     |   -> deterministic findings + source-row refs
  +-------------------------+
          |
          v
  +-------------------------+
  | AI-ASSISTED             |   LLM provider wrapper (mock by default).
  | EXPLANATION / DRAFTING  |   Explains / summarizes / drafts ONLY.
  | (language tasks only)   |   Cites source rows. Never calculates.
  +-------------------------+
          |
          v
  +-------------------------+
  | VALIDATION              |   reject/flag invented references, numeric
  | (deterministic)         |   claims not in findings, missing source refs
  +-------------------------+
          |
          v
  +-------------------------+
  | HUMAN REVIEW            |   per-finding: mark reviewed / resolved /
  | (React console or        |   needs follow-up / note / reject AI / approve
  |  Streamlit controls)    |
  +-------------------------+
          |
          v
  +-------------------------+
  | EXPORTABLE PACKET       |   *_summary.md, *.csv detail, draft memos,
  | (deterministic format)  |   validation_report.json + consolidated
  |                         |   review_packet.md + run_manifest.json
  +-------------------------+
          |
          v
  +-------------------------+
  | AUDIT LOG               |   append-only events: run created, parsed,
  | (run ledger, SQLite)    |   analysis, LLM req/resp, validation, review,
  +-------------------------+   export, completed/failed
```

Core logic, UI, CLI, persistence, validation, the LLM wrapper, and the workflow
modules are kept separate. The workflow modules contain no Streamlit, no FastAPI,
and no provider-specific code.

## Setup

The project uses a local virtual environment at `.venv\Scripts\python.exe`
(Windows). To create it and install the dependencies:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install pandas pydantic openpyxl streamlit pytest fastapi uvicorn httpx python-multipart
```

Use `.venv\Scripts\python.exe` for all Python and pytest invocations below.
Built and tested on Python 3.12+. Node v24+ and npm are required for the
React frontend. The tool runs fully offline by default (the mock LLM
provider requires no API key and no internet).

### Troubleshooting: `Unable to import required dependency numpy`

If launching the CLI or Streamlit fails with
`ImportError: Unable to import required dependency numpy` or
`No module named 'numpy._core._multiarray_umath'`, the virtual environment's
Python was upgraded after the packages were installed (for example from 3.12 to
3.14). Compiled wheels are tied to one Python version (`cp312` vs `cp314`), so
the old binaries can no longer be loaded. Check the mismatch and repair it by
reinstalling the binary packages for the current interpreter:

```bat
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -m pip install --force-reinstall --only-binary=:all: ^
  numpy pandas pyarrow pydantic_core pillow charset-normalizer ^
  httptools MarkupSafe rpds-py websockets
```

This keeps the same package versions and only swaps the binaries to match the
interpreter. If problems persist, recreate the environment from scratch
(`rmdir /s /q .venv` then repeat the setup commands above) so every package is
reinstalled for the current Python.

## How to run everything

### API (FastAPI)

```bat
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

The API listens on `http://127.0.0.1:8000`. A quick smoke check:

```bat
curl http://127.0.0.1:8000/api/health
REM -> {"status":"ok","app":"municipal-finance-ai","version":"0.1.0","llm_mode":"mock"}
curl http://127.0.0.1:8000/api/workflows
REM -> {"workflows": [...8 items...]}
```

See `docs/frontend/api_contract.md` for the full endpoint reference.

The API shares the same run ledger, audit log, and export directory as the
Streamlit app (`runs/ledger.db`, `runs/audit/`, `runs/exports/`). Runs from
either surface appear in the other.

### React console (frontend) -- development mode

```bat
cd frontend
npm install
npm run dev
```

Opens `http://localhost:5173`. All `/api` requests are proxied to
`http://127.0.0.1:8000`, so the API must be running first. The dev server
supports hot-reload; the backend does not need to be restarted for frontend
changes.

### React console -- built bundle (single-server mode)

```bat
cd frontend
npm run build
```

This produces `frontend/dist/`. When the API starts and `frontend/dist/`
exists, it is mounted at `/`. A single uvicorn process serves both the API
and the frontend:

```bat
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
REM Open http://127.0.0.1:8000
```

### Build-artifact policy for frontend/dist

`frontend/dist/` is a **build artifact** produced by `npm run build`. It is
gitignored and must never be committed. A fresh clone will not contain it.
To reproduce the served UI from a clean clone:

```bat
cd frontend
npm install
npm run build
cd ..
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
REM Open http://127.0.0.1:8000 -- the API serves both the React console and the
REM API endpoints from the single uvicorn process.
```

`npm run build` compiles TypeScript (`tsc -b`) then runs the Vite bundler,
writing the output to `frontend/dist/`. When `api/main.py` starts and
`frontend/dist/` is a directory, it mounts that directory at `/` using
FastAPI's `StaticFiles` (html=True, so the SPA index fallback works). The
`/api/*` routes are registered first, so they always take priority over the
static mount.

**The API runs fine with no dist present.** The static mount is guarded by
a directory check (`if settings.frontend_dist.is_dir()`), so starting the
API without running `npm run build` is not an error -- you simply get the
API only, reachable at `http://127.0.0.1:8000/api/...`. This is the normal
state in development (use the Vite dev server instead, see the section above)
and in CI (tests use the API directly with no frontend build step).

**Summary of modes:**

| Mode | How to start | URL |
| --- | --- | --- |
| API only (no build) | `uvicorn api.main:app --port 8000` | `http://127.0.0.1:8000/api/...` |
| Dev (hot-reload) | `npm run dev` in `frontend/` + API on 8000 | `http://localhost:5173` (proxies /api to 8000) |
| Single-server (prod-like) | `npm install && npm run build` then API | `http://127.0.0.1:8000` |

### CLI

List the available workflows, then run any of them on the bundled synthetic
sample data:

```bat
.venv\Scripts\python.exe cli\run_workflow.py list
.venv\Scripts\python.exe cli\run_workflow.py bank-reconciliation --sample
.venv\Scripts\python.exe cli\run_workflow.py budget-variance --sample
.venv\Scripts\python.exe cli\run_workflow.py report-review --sample
```

Each run prints the run ID, the summary results, the validation status, and the
export paths. To also write the export packet to disk, add `--export <dir>`,
e.g. `.venv\Scripts\python.exe cli\run_workflow.py bank-reconciliation --sample --export out\bank`.
Other flags: repeatable `--input key=value` (instead of `--sample`),
`--config <json-file-or-inline>` for tolerances/thresholds, and `--mock`
(default) / `--real`. The mock provider is the offline default and needs no key.

### Workflow console (React) -- easy launch

The easiest way to open the console for non-technical users (requires that
`frontend/dist/` has been built at least once and the `.venv` exists):

```bat
REM Double-click, or run from a terminal:
scripts\launch_console.cmd
```

The launcher (`scripts/launch_console.cmd` -> `scripts/launch_console.py`):

1. Checks that `.venv` exists and that `frontend/dist/index.html` is present
   (offers to build via `npm run build` when npm is on the PATH).
2. Starts uvicorn on `http://127.0.0.1:8765` (serving the built bundle + API).
3. Polls `/api/health` until the server is ready, then opens the default browser.
4. Shuts down the server cleanly on Ctrl+C.

Optional flags (pass after the `.cmd` name or when calling `launch_console.py`
directly):

```bat
scripts\launch_console.cmd --port 9000         REM use a different port
scripts\launch_console.cmd --no-browser        REM skip the auto-open step
```

Build prerequisite (one-time after a fresh clone or any frontend change):

```bat
cd frontend
npm install
npm run build
cd ..
```

#### Console screens

The React console (`http://127.0.0.1:8765` via the launcher, or
`http://127.0.0.1:8000` when started manually) includes:

| Screen | Nav label | What it shows |
| --- | --- | --- |
| Home | Home | Workflow picker and status strip |
| Run wizard | Run a workflow | Four-step guided wizard (upload -> file check -> run -> review) |
| Review Run | (reached from wizard) | Findings, AI trust boundary, artifacts, audit trail |
| History | History | All past runs with status and link to Review Run |
| Settings | Settings | Read-only display of local settings from `app_settings.json` (city name, default actor, AI provider/mode, tolerances, export dir, role, retention category) |
| AI usage | AI usage | Cross-run table of every AI interaction: workflow, model, prompt version, validation status, draft/final state, source rows cited -- newest first |
| Redaction assist | Redaction assist | Paste text, scan for PII patterns (SSN, email, phone, credit card, long numbers), see masked previews and redacted output; nothing stored |
| Scheduled runs | Scheduled runs | Read-only list of local schedules (cadence, next due, last run, active); create/trigger via Streamlit for now |
| About | About and safety | Safety model and invariants |

#### Wizard advanced options (GW-10)

On the inputs step, an "Advanced options" section (collapsed by default) lets
you paste an optional config JSON (custom tolerances/thresholds) and review
any suggested column mappings surfaced by the preflight check. Both are posted
to the existing `config` and `column_mappings` multipart fields the API
already accepts.

#### Browser-level end-to-end tests (optional)

```bat
cd frontend
npx playwright test
```

Three Playwright tests drive the full guided loop (home -> workflow selection
-> sample data -> preflight gate -> run -> Review Run page) plus history
navigation. Chromium is required (`npx playwright install chromium`). Optional
in CI -- the suite is documented in `docs/frontend/e2e.md`.

### Legacy Streamlit app (dev / admin only)

The Streamlit app is now the **legacy and development-only** interface.
The React console (above) is the primary UI. The Streamlit app is retained
because it shares the same ledger/audit/export directory as the API (a run
started in either surface appears in the other), and because it provides
developer-accessible surfaces not yet in the console (Export Center, AI diff,
role views). It may be removed in a future release.

```bat
streamlit run app/streamlit_app.py
```

The app has Home, Run Workflow, Workflow History, Review Run, Export Center,
**AI Audit Log**, **Scheduled runs**, **Redaction assist**, Settings, and
About / Safety pages. The AI Audit Log is a searchable/filterable history of
every AI interaction (run, workflow, model, prompt-template version, validation
status, and draft-vs-final approval state) — a CPRA-style review surface for AI
usage; it also **exports the AI usage log** (CSV/JSON) and can **diff two AI
interactions** (prompt-template / model change, summary diff, referenced-row
add/remove). The Settings tolerances and thresholds are applied to new runs (an
uploaded config/threshold file takes precedence).

### Tier 1 capabilities

These near-term extensions are complete and wired into the CLI/UI on synthetic
data only:

- **Retention category** — each run is tagged with a records-retention category
  (`draft_working` default, `transitory`, `administrative_record`,
  `audit_record`, `permanent`); it appears in the run summary, the review packet,
  the run manifest, and Workflow History. Set per-run on Run Workflow; default on
  Settings.
- **Exportable AI usage log** — download every AI interaction as CSV/JSON
  (`src/core/ai_usage_log.py`) from the AI Audit Log page.
- **Prompt/response diffing** — compare two stored AI interactions
  (`src/core/diffing.py`, stdlib `difflib` only).
- **PDF summary export** — generate a text-only PDF of the review packet
  (`src/core/pdf_export.py`, pure-stdlib writer) from Review Run and Export Center.
- **Chart-of-accounts import preset** — `chart_of_accounts` preset maps ERP-style
  COA headers to canonical `account_code` / `account_name` / `normal_balance`.
- **Role-specific views** — a presentation-only role selector (AP clerk,
  Accountant, Finance analyst, Finance director) reorders/emphasizes findings;
  it is not authentication and never hides or deletes data.
- **Redaction assist (prototype)** — a regex-based PII scanner/redactor
  (`src/core/redaction.py`) for SSN, email, phone, credit-card, and long-number
  patterns. A demonstration prototype, **not** a compliance/public-records tool.
- **Scheduled runs** — local, manual-trigger recurring schedules
  (`src/core/scheduler.py`, monthly / quarterly / before-agenda / custom cadence).
  No daemon or cron — schedules are recorded and surfaced as due; the user clicks
  to run.

## Demo path

A 2–3 minute end-to-end walkthrough on synthetic data only:

1. **Start the app.** Preferred: run `scripts\launch_console.cmd` (opens the
   React console at `http://127.0.0.1:8765`). Developer alternative:
   `streamlit run app/streamlit_app.py` (legacy/dev-only Streamlit UI).
2. **Home / About → Safety:** read what the tool does and does not do (no
   chatbot, deterministic calculations, AI is advisory and source-linked).
3. **Run Workflow → Bank reconciliation:** check *Use example files (load
   synthetic data)* and click **Run workflow**. The result shows a **Preflight:
   PASS** badge, then findings count, validation status, and artifact count, with
   a note that the AI draft was validated against the source data.
   - *Preflight demo (optional):* on Run Workflow, click **Check files
     (preflight)** before running to see the capability report (file profiles,
     detected columns, parse confidence). For a **FAIL** example, provide only the
     `bank` file and omit `ledger` (or run
     `python cli/run_workflow.py bank-reconciliation --input bank=data/synthetic/bank_reconciliation/bank.csv`):
     the result is `PREFLIGHT: FAIL` / `STATUS: FAILED (preflight)`, showing the
     blocking condition and next steps with **no AI explanation** (the LLM is not
     called). For a **PARTIAL** example, run the sign-convention fixture
     (`data/synthetic/bank_reconciliation/messy/partial_sign_bank.csv` +
     `partial_sign_ledger.csv`): the run proceeds, is labelled **PARTIAL**, lists
     the `possible_sign_convention_mismatch` condition with a next step, and the
     AI section is constrained to explaining the deterministic findings only.
   - *Import-preset demo (optional):* instead of the example files, upload
     `data/synthetic/bank_reconciliation/erp_style_bank.csv` for the bank
     statement and `ledger.csv` for the ledger, set the bank statement's
     **source format** to *Generic ERP export*, and run. The ERP-style headers
     ("Posting Date", "Memo", "Transaction Amount") are column-aliased to the
     canonical names before analysis; the recorded source-file hash is still the
     original upload, and the applied preset is noted in the run summary, the
     Review Run page, the review packet, and the audit trail. (Without the
     preset the run is rejected — the date column is not auto-detected.) The
     same works for the other two workflows: upload
     `data/synthetic/budget_variance/erp_style_budget.csv` +
     `erp_style_actuals.csv` (set both to *Generic ERP export*) or
     `data/synthetic/report_review/erp_style_report.csv` (set the report table's
     source format) — each reproduces the exact same findings as the standard
     samples, and is rejected without the preset.
4. **Review Run:** inspect the run — validation warnings first, then the
   deterministic findings table (each citing `bank:row` / `ledger:row` source
   refs), the AI draft (labelled DRAFT), input-file hashes, and the audit
   events. Use a per-finding control (e.g. **Approve draft for export** or
   **Needs follow-up**); the AI-draft metric flips from `draft` to
   `final (human-approved)`.
5. **Export Center:** download `review_packet.md` (open it to show the clean
   separation of deterministic vs AI-draft vs validation vs reviewer vs audit)
   and `run_manifest.json` (machine-readable bundle with model metadata, source
   hashes, and export history). Click **Generate review packet** to regenerate
   it reflecting the latest review actions.
6. **AI Audit Log:** filter by workflow / draft status / search to show every
   AI interaction and which drafts a human has approved. Use **Export AI usage
   log** to download the CSV/JSON, then **Compare two AI interactions** to pick
   run A/B and see the prompt-template/model change flags, the summary diff, and
   the referenced-rows added/removed.
7. **Set a retention category:** on Run Workflow, pick a records-retention
   category before running (or set the default on Settings); confirm it shows on
   Review Run, Workflow History, and inside `review_packet.md` / `run_manifest.json`.
8. **Download a PDF summary:** on Review Run (or Export Center), click **Download
   PDF summary** to get a text-only PDF of the review packet.
9. **Scheduled runs:** open the **Scheduled runs** page, add a schedule
   (workflow + cadence: monthly / quarterly / before-agenda / custom), then click
   **Run now** on a due schedule to run it on the synthetic example files and
   advance its next-due date.
10. **Redaction assist (prototype):** open the **Redaction assist** page (note the
    synthetic-only PROTOTYPE warning), paste or seed text, and **Scan / redact**
    to see PII spans replaced with `[REDACTED:<TYPE>]` plus a findings table and
    per-type counts.
11. **Switch role views:** use the sidebar **role** selector (AP clerk /
    Accountant / Finance analyst / Finance director) and revisit Review Run — the
    findings reorder/emphasize for the role (toggle **Show all findings**);
    nothing is hidden or deleted.
12. **Repeat** with Budget variance and Report review (also example files), then
    try **Guided freeform** — note it refuses to run unless the sensitivity
    confirmation is checked.
13. **Tyler-era workflows:** run **Transaction search** (type a plain-English
    query such as "payments to Cascade Paving over $5,000 in March 2026"), **AP
    duplicate review**, **JE upload prep** (loads the valid-draft sample and
    produces a `je_upload.xlsx` artifact), and **PO / invoice review** — all
    using their bundled City of Riverbend synthetic data. The role selector
    (AP clerk, Accountant, Finance analyst, Finance director) suggests the most
    relevant of the eight workflows for the selected role.

To explain the architecture in one line: *deterministic code does all the math
and matching; the LLM only drafts language and must cite source rows; every run
is logged, validated, and exported for a human to approve.*

## How to run tests

### Python tests (pytest)

```bat
.venv\Scripts\python.exe -m pytest
```

To run a single test file, name its path, e.g.
`.venv\Scripts\python.exe -m pytest tests/unit/test_app_imports.py -q`.

To run only the API tests:

```bat
.venv\Scripts\python.exe -m pytest tests/api -p no:cacheprovider --basetemp=.pytest_tmp_api
```

The full suite is **747 tests** (all passing), including the preflight /
capability layer, the per-workflow messy-data fixtures, the four new Tyler-era
workflow unit test suites, integration tests for the CLI/app registry/eval
harness, the Tyler normalizer and readiness tests, and the 38 API endpoint
tests (`tests/api/`, covering health, workflows, runs, settings, AI usage,
redaction, and schedules).

The evaluation harness produces measured per-workflow metrics:

```bat
.venv\Scripts\python.exe -m src.eval.harness --out runs/eval_report.json
```

### Frontend tests (vitest)

```bat
cd frontend
npm test
```

Runs 36 tests across 8 test files (client, TrustBoundary, RunWizardPage,
ReviewRunPage, plus the four new pages: SettingsPage, AiUsagePage,
RedactionPage, SchedulesPage). Also available: `npm run typecheck` (tsc strict)
and `npm run lint` (eslint flat config with typescript-eslint).

For browser-level end-to-end tests see the Playwright section above.

### Scripted end-to-end demo loop (API)

With the API running on port 8000, the following sequence reproduces the
integration validation path:

```bat
REM 1. Health check
curl http://127.0.0.1:8000/api/health

REM 2. List workflows
curl http://127.0.0.1:8000/api/workflows

REM 3. Run ap_duplicate_review on sample data
curl -X POST http://127.0.0.1:8000/api/workflows/ap_duplicate_review/runs ^
  -F "use_sample=true"

REM 4. List runs (get the run_id from step 3)
curl http://127.0.0.1:8000/api/runs?limit=5

REM 5. Post a review action (replace <run_id> with the id from step 3)
curl -X POST http://127.0.0.1:8000/api/runs/<run_id>/review-actions ^
  -H "Content-Type: application/json" ^
  -d "{\"action\":\"mark_reviewed\",\"actor\":\"finance_staff\",\"note\":null,\"finding_id\":null}"

REM 6. Download an artifact
curl -o validation_report.json ^
  http://127.0.0.1:8000/api/runs/<run_id>/artifacts/validation_report.json

REM 7. Audit trail
curl http://127.0.0.1:8000/api/runs/<run_id>/audit
```

Expected outcomes (verified by the integration agent on 2026-06-11):
- ap_duplicate_review: 15 findings, validation passed, 10 artifacts,
  human_review_status pending -> in_review after mark_reviewed
- transaction_search with use_sample: 2 findings, status completed
- je_upload_prep with invalid JE draft: status completed, no je_upload.xlsx
  in artifacts (fail-closed gate)
- Restart test: GET /api/runs/{id} after uvicorn restart returns the full
  RunDetail with all findings, artifacts, and review actions rehydrated from
  the ledger

## Limitations and follow-ups

The following items are known gaps or deliberate deferred decisions. They are
not bugs; they are scope boundaries of the current MVP.

- **No auth or multi-user support.** The tool is local-first and single-user.
  There are no user accounts, roles, sessions, or access-control enforcement.
  The role selector in Streamlit is presentation-only (reorder/emphasize, never
  hide or delete). Adding real auth would require a security design.
- **No real ERP integration.** All data arrives as local CSV/XLSX. There are
  no API calls to Tyler/Munis, OpenGov, or any ERP. The Tyler normalizer column
  aliases are modeled on observed Munis export shapes and have not been validated
  against a real city's configuration.
- **Synchronous run execution.** The API runs workflows synchronously in the
  request handler (workflows complete in seconds on synthetic data). A
  long-running workflow on a large real dataset would need a background task
  queue and a status-polling or WebSocket endpoint.
- **No WebSocket streaming.** Run progress (preflight -> analysis -> LLM ->
  validation -> export) is not streamed; the POST /runs endpoint blocks until
  the full run is done and returns the complete RunDetail.
- **No tenanting.** One SQLite ledger, one audit directory, one export
  directory. Multiple users on the same machine would share run history.
- **No production deployment hardening.** CORS is whitelisted for localhost.
  There is no HTTPS, no rate limiting, and no secret management.
- **Tyler column aliases are illustrative.** The real Munis export column
  names, date/amount locale formats, and XLSX title-block layouts need to be
  validated against a live system before use on real data.
- **JE upload template is synthetic.** The Munis JE import column order and
  header spellings should be confirmed with the city's Tyler contact before
  using je_upload_prep on a real system.
- **PDF export is text-only.** The pure-stdlib PDF writer (`src/core/pdf_export.py`)
  produces a fixed-font text PDF with no formatting, images, or markdown
  rendering. A rich PDF would require a third-party library.
- **Redaction is a prototype, not a compliance tool.** The regex PII scanner
  covers only a handful of patterns and makes no completeness guarantee.
  It is not a public-records or CPRA compliance tool.
- **No vector DB / RAG.** Reference data (chart of accounts, city profile) is
  loaded from files at run time. A large institutional-document corpus would
  benefit from a retrieval layer, but none is needed at current scale.

## Synthetic data disclaimer

Everything in this repository uses **synthetic data only**. There are no real
bank statements, vendor records, employee records, taxpayer data, account
credentials, or sensitive city financial data anywhere in the project. There are
no real secrets, no PII, and no real ERP integrations or authentication. The
sample files under `data/synthetic/` are fabricated for demonstration. Do not
load real sensitive data into this MVP.

## Recruiting narrative

**Problem.** Small-city finance teams repeat manual, error-prone close tasks
(bank reconciliation, budget variance explanation, report consistency review)
that demand exactness and an audit trail.

**User insight.** These users are non-technical and will not trust a black-box
financial output. They need plain-language explanations *plus* source evidence
for every number, not a chatbot answer.

**Scope decision.** I deliberately constrained the MVP: deterministic code does
all financial logic; the LLM is limited to explanation and drafting; no vector
DB, no multi-agent system, no real ERP integration, no production auth. Mock LLM
mode is the default so the whole tool runs offline.

**Architecture.** One shared pipeline — ingest, normalize, deterministic
analysis, LLM assist, validate, human review, export, audit log — with the UI,
CLI, persistence (SQLite run ledger), validation, and LLM wrapper kept separate
so workflows never touch Streamlit or provider code.

**Workflow implementation.** Seven high-value workflows plus a guided-freeform
fallback share that pipeline through a uniform registry — all eight runnable
from the CLI and the Streamlit UI — each producing source-linked findings and a
consolidated review packet (`review_packet.md` + `run_manifest.json`) that
separates deterministic findings, the AI draft, validation, reviewer notes,
approval status, and audit history. A searchable AI Audit Log surfaces every AI
interaction with its model/template metadata and draft-vs-final approval state.

**Validation.** A deterministic validation layer checks every LLM output against
the source data, rejecting or flagging invented references and numeric claims not
present in the deterministic findings; an eval harness runs known-answer datasets
for each workflow.

**Measured result.** The eval harness runs all seven tabular workflows
end-to-end on synthetic data, passing 7/7 known-answer checks with 0 LLM
outputs rejected on the mock path; e.g. bank reconciliation deterministically
yields 4 matched, 1 timing difference, and 3 unmatched items across 13
transactions; AP duplicate review flags exactly 15 findings across D1-D8
against the City of Riverbend dataset; JE upload prep produces an upload-ready
workbook with 2 round-dollar warnings for the valid draft; PO/invoice review
flags 9 exceptions (P1-P8 + P3b).

**Reflection.** Keeping financial logic deterministic and the model
language-only is what makes the output auditable and trustworthy; the same
constraint makes the system honest about its limits and straightforward to
extend (RAG, more adapters, orchestration) without rewriting the core pipeline.
