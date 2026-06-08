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

All four are exposed through the CLI, the registry
(`src/workflows/registry.py`), and the Streamlit UI — every workflow, including
bank reconciliation, is runnable end-to-end from the app on the bundled
synthetic sample data.

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
**AI Audit Log**, Settings, and About / Safety pages. The AI Audit Log is a
searchable/filterable history of every AI interaction (run, workflow, model,
prompt-template version, validation status, and draft-vs-final approval state) —
a CPRA-style review surface for AI usage. The Settings tolerances and thresholds
are applied to new runs (an uploaded config/threshold file takes precedence).

## Demo path

A 2–3 minute end-to-end walkthrough on synthetic data only:

1. **Start the app:** `streamlit run app/streamlit_app.py`.
2. **Home / About → Safety:** read what the tool does and does not do (no
   chatbot, deterministic calculations, AI is advisory and source-linked).
3. **Run Workflow → Bank reconciliation:** check *Use example files (load
   synthetic data)* and click **Run workflow**. The result shows findings count,
   validation status, and artifact count, with a note that the AI draft was
   validated against the source data.
   - *Import-preset demo (optional):* instead of the example files, upload
     `data/synthetic/bank_reconciliation/erp_style_bank.csv` for the bank
     statement and `ledger.csv` for the ledger, set the bank statement's
     **source format** to *Generic ERP export*, and run. The ERP-style headers
     ("Posting Date", "Memo", "Transaction Amount") are column-aliased to the
     canonical names before analysis; the recorded source-file hash is still the
     original upload, and the applied preset is noted in the run summary and
     audit trail. (Without the preset the run is rejected — the date column is
     not auto-detected.)
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
   AI interaction and which drafts a human has approved.
7. **Repeat** with Budget variance and Report review (also example files), then
   try **Guided freeform** — note it refuses to run unless the sensitivity
   confirmation is checked.

To explain the architecture in one line: *deterministic code does all the math
and matching; the LLM only drafts language and must cite source rows; every run
is logged, validated, and exported for a human to approve.*

## How to run tests

```bat
.venv\Scripts\python.exe -m pytest
```

To run a single test file, name its path, e.g.
`.venv\Scripts\python.exe -m pytest tests/unit/test_app_imports.py -q`.

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

**Workflow implementation.** Three high-value workflows plus a guided-freeform
fallback share that pipeline through a uniform registry — all four runnable from
the CLI and the Streamlit UI — each producing source-linked findings and a
consolidated review packet (`review_packet.md` + `run_manifest.json`) that
separates deterministic findings, the AI draft, validation, reviewer notes,
approval status, and audit history. A searchable AI Audit Log surfaces every AI
interaction with its model/template metadata and draft-vs-final approval state.

**Validation.** A deterministic validation layer checks every LLM output against
the source data, rejecting or flagging invented references and numeric claims not
present in the deterministic findings; an eval harness runs known-answer datasets
for each workflow.

**Measured result.** The eval harness runs all three MVP workflows end-to-end on
synthetic data, passing 3/3 known-answer checks with 0 LLM outputs rejected on
the mock path; e.g. bank reconciliation deterministically yields 4 matched, 1
timing difference, and 3 unmatched items across 13 transactions, each run
completing in well under a second.

**Reflection.** Keeping financial logic deterministic and the model
language-only is what makes the output auditable and trustworthy; the same
constraint makes the system honest about its limits and straightforward to
extend (RAG, more adapters, orchestration) without rewriting the core pipeline.
