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
  | (Streamlit controls)    |   needs follow-up / note / reject AI / approve
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
modules are kept separate. The workflow modules contain no Streamlit and no
provider-specific code.

## Setup

The project uses a local virtual environment at `.venv\Scripts\python.exe`
(Windows). To create it and install the dependencies:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install pandas pydantic openpyxl streamlit pytest
```

Use `.venv\Scripts\python.exe` for all Python and pytest invocations below.
Built and tested on Python 3.12. No other dependencies are required; the tool
runs fully offline (the mock LLM provider is the default).

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

## How to run the CLI

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

## How to run Streamlit

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

1. **Start the app:** `streamlit run app/streamlit_app.py`.
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

```bat
.venv\Scripts\python.exe -m pytest
```

To run a single test file, name its path, e.g.
`.venv\Scripts\python.exe -m pytest tests/unit/test_app_imports.py -q`.

The full suite is **705 tests** (all passing), including the preflight /
capability layer, the per-workflow messy-data fixtures, the four new Tyler-era
workflow unit test suites, integration tests for the CLI/app registry/eval
harness, and the Tyler normalizer and readiness tests.

The evaluation harness produces measured per-workflow metrics:

```bat
.venv\Scripts\python.exe -m src.eval.harness --out runs/eval_report.json
```

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
