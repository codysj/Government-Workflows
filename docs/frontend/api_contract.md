# API Contract v1 (implemented)

Base URL: `http://127.0.0.1:8000` - all routes under `/api`. JSON unless noted.

Start the server:

```
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

This document describes what `api/` ACTUALLY implements. It is the single
source of truth for the frontend.

## Architecture guarantees

- The API is a thin seam over the existing core. Every run goes through
  `app.workflow_registry.run_workflow` (the same pipeline Streamlit and the
  CLI use); persistence goes through the shared `RunLedger` (SQLite) and
  `AuditLog`. No workflow logic is reimplemented.
- The LLM is advisory-only and the offline deterministic mock is the default
  provider (`llm_mode: "mock"` in `/api/health`).
- Default storage locations are SHARED with the Streamlit app, so run history
  appears in both UIs: `runs/ledger.db`, `runs/audit/`, the `app_settings.json`
  `export_dir` (default `runs/exports/`), and `runs/uploads/`.
- No auth, no tenanting, local-first. CORS allows `http://localhost:5173` and
  `http://127.0.0.1:5173` (Vite dev server).
- If `frontend/dist/` exists at startup it is mounted as static files at `/`
  (single-server deployment); its absence is fine.

## Serialization conventions

- **Datetimes** are ISO-8601 strings with a `+00:00` UTC offset, exactly as
  the core persisted them (e.g. `"2026-06-12T18:02:11.123456+00:00"`). The API
  never re-parses them.
- **Decimals**: any `Decimal` the core produced arrives as a STRING (pydantic
  v2 JSON serialization), e.g. `computed_values: {"amount": "1234.56"}`. The
  frontend formats these for display but NEVER recalculates.
- `result_tables` (in-memory pandas DataFrames) are intentionally NOT part of
  the API surface; they are never persisted. Tabular outputs are available as
  CSV export artifacts instead.
- Errors are always `{"detail": "<plain-language string>"}` (FastAPI style).

---

## GET /api/health

```
200 {"status": "ok", "app": "municipal-finance-ai", "version": "0.1.0", "llm_mode": "mock"}
```

`llm_mode` is `"mock"` unless `app_settings.json` has `mock_mode: false`.

## GET /api/workflows

`200 {"workflows": [WorkflowInfo]}` - all 8 workflows in display order.

`WorkflowInfo`:

```json
{
  "workflow_type": "ap_duplicate_review",
  "title": "AP duplicate / suspicious payment review",
  "description": "...",
  "note": null,
  "category": "review",
  "uploads": [
    {"key": "ap_invoices", "label": "AP invoice detail (CSV/XLSX)",
     "required": true, "file_types": ["csv", "xlsx"], "help": ""}
  ],
  "text_inputs": [],
  "has_sample": true,
  "sample_description": "Synthetic Tyler/Munis-style AP invoice export ..."
}
```

- `category` is one of `review` (bank_reconciliation, budget_variance,
  report_review, ap_duplicate_review, po_invoice_review), `search`
  (transaction_search), `prep` (je_upload_prep), `other` (freeform).
- `note` is `null` when the descriptor has no note.
- `text_inputs` (non-file inputs sent as multipart text fields):
  - `transaction_search`: `query` (required; `example` carries the bundled
    sample query `"payments to Cascade Paving over $5,000 between March and
    May 2026"`).
  - `freeform`: `task_type` (required), `desired_output` (required),
    `relevant_context` (optional), `sensitivity_confirmation` (required, send
    the string `"true"`), `human_review_confirmation` (required, send
    `"true"`). The two confirmations are booleans on the wire as text.
- `has_sample` is `false` only for `freeform`.

## GET /api/workflows/{workflow_type}

`200 WorkflowInfo` | `404 {"detail": "Unknown workflow '...'"}`.

## POST /api/workflows/{workflow_type}/preflight  (multipart/form-data)

Capability check ONLY: nothing is written to the ledger, the workflow does
not run, and the LLM is never called. Uses the same core preflight engine as
the CLI's `--preflight-only`.

Form fields:

- `use_sample`: `"true"`/`"false"` - resolve the bundled synthetic example
  files for this workflow.
- One file part per upload key (see `WorkflowInfo.uploads`). Uploaded files
  override sample files for the same key.
- Optional text fields by `text_inputs` key (e.g. `query`).
- Optional `config`: a JSON OBJECT string (workflow tolerances/thresholds).

Responses:

- `200 PreflightResponse`:

```json
{
  "status": "pass",
  "llm_allowed": true,
  "files": [{"input_key": "report_table", "file_name": "report_table.csv",
             "present": true, "row_count": 26}],
  "findings": [{"code": "missing_required_column", "severity": "critical",
                "message": "...", "affected_input": "report_table",
                "blocks_run": true}],
  "supported_checks": ["subtotal_consistency", "..."],
  "next_steps": ["..."]
}
```

  `status` is `pass` | `partial` | `fail`. `blocks_run: true` findings force
  `fail` (the run endpoint would refuse to execute the workflow/LLM).
- `404` unknown workflow.
- `422 {"detail": ...}` when neither files nor `use_sample=true` were
  provided, or when `config` is not a valid JSON object string.

## POST /api/workflows/{workflow_type}/runs  (multipart/form-data)

Same form shape as preflight, plus optional `actor` (defaults to the
`app_settings.json` `default_actor`). Runs SYNCHRONOUSLY (workflows complete
in seconds) and returns the full `RunDetail`.

Domain outcomes vs HTTP errors:

- **Preflight FAIL is a domain outcome, not an HTTP error**: `200 RunDetail`
  with `status: "failed_preflight"`, `preflight` populated (findings +
  next_steps), `findings: []`, `ai: null`, `validation: null`. The run IS
  recorded in the ledger with a failed-preflight export packet.
- `404` unknown workflow.
- `422` missing required inputs, with plain-language detail. Checked before
  running: required upload keys (unless `use_sample=true` supplies them),
  required text inputs (`query` for transaction_search; freeform's structured
  fields including both confirmations), and "no files and no sample" for
  non-freeform workflows.
- `500 {"detail": ...}` only for unexpected exceptions; the run is marked
  failed in the ledger and audited before the 500 is returned.

`use_sample=true` for `transaction_search` auto-fills the example `query`
when no `query` field is sent.

## RunDetail shape

```json
{
  "run_id": "1f0c...",
  "workflow_type": "ap_duplicate_review",
  "workflow_title": "AP duplicate / suspicious payment review",
  "created_at": "2026-06-12T18:02:11.123456+00:00",
  "created_by": "finance_staff",
  "status": "completed",
  "human_review_status": "pending",
  "retention_category": "draft_working",
  "summary": { "...": "the workflow's summary dict (JSON-safe)" },
  "preflight": { "...": "PreflightResponse shape, or null" },
  "findings": [
    {
      "finding_id": "ab12...",
      "finding_type": "duplicate_payment",
      "severity": "high",
      "description": "...",
      "rule_used": "exact_duplicate:vendor+invoice+amount",
      "requires_human_review": true,
      "computed_values": {"amount": "1234.56"},
      "source_rows": [
        {"file_id": "...", "table_name": "ap_invoices", "row_index": 17,
         "column_names": ["vendor_name", "amount"],
         "source_values": {"vendor_name": "...", "amount": "1234.56"}}
      ]
    }
  ],
  "ai": {
    "available": true,
    "model_provider": "mock",
    "model_name": "mock-deterministic",
    "response": {"summary": "...", "draft_memo": "..."},
    "referenced_source_rows": ["..."]
  },
  "validation": {
    "passed": true, "errors": [], "warnings": [],
    "invented_reference_detected": false, "numeric_claims_checked": 12
  },
  "artifacts": [
    {"file_name": "findings.csv", "artifact_type": "csv", "sha256": "...",
     "download_url": "/api/runs/1f0c.../artifacts/findings.csv"}
  ],
  "review_actions": [
    {"action": "mark_reviewed", "actor": "finance_staff",
     "note": null, "finding_id": "ab12...", "created_at": "..."}
  ],
  "allowed_review_actions": ["mark_reviewed", "mark_resolved",
    "needs_follow_up", "add_note", "reject_ai_explanation", "approve_draft"]
}
```

Notes for the frontend (trust boundaries):

- `findings` are DETERMINISTIC results. `ai` is DRAFT content from the
  advisory LLM and MUST be rendered visually/structurally separated and
  labeled as AI-drafted, never mixed with deterministic findings.
- `ai` is `null` whenever the LLM was never called (fail-closed paths such as
  failed preflight). `ai.response` is an opaque dict whose keys vary by
  workflow (`summary`, `draft_memo`, `draft`, ...); render known keys, show
  the rest as-is.
- `status` mapping: ledger `completed` -> `completed`; ledger `failed` with a
  preflight `fail` (or `summary.blocked`) -> `failed_preflight`; any other
  ledger state (including a process that died mid-run, leaving
  `created`/`running`) -> `failed`. A freeform sensitivity refusal surfaces
  as `failed_preflight` (freeform's preflight blocks on the missing
  confirmation); the API additionally 422s it before running.
- `RunDetail` is ALWAYS rehydrated from the ledger - the live POST response
  and a GET after process restart are produced by the same code path, so
  nothing in this shape is lost on restart. The only data that does not
  survive (by design) is the in-memory `result_tables`, which is never part
  of the API.

## GET /api/runs?limit=N

`200 {"runs": [RunListItem]}` newest first. `limit` defaults to 50
(1..500).

```json
{"run_id": "...", "workflow_type": "ap_duplicate_review",
 "workflow_title": "AP duplicate / suspicious payment review",
 "created_at": "...", "status": "completed",
 "human_review_status": "pending", "validation_passed": true,
 "finding_count": 15, "artifact_count": 7}
```

`validation_passed`: `true` when validation passed, `false` when it produced
warnings or errors, `null` when no validation ran (e.g. failed preflight).

## GET /api/runs/{run_id}

`200 RunDetail` | `404`. Works after process restart (rehydrated from the
ledger; see notes above).

## POST /api/runs/{run_id}/review-actions

Request: `{"action": str, "actor": str, "note": str|null, "finding_id": str|null}`

- `action` must be one of `allowed_review_actions` -> otherwise
  `422 {"detail": "Unknown review action ..."}`.
- `404` for an unknown run.
- Recorded via the existing `record_human_review_action`, so the ledger row
  and the `human_review_action` audit event always stay in sync.
- Recording an action also updates the run-level `human_review_status`
  deterministically (pure bookkeeping, no financial logic). The action->status
  policy lives in one shared helper, `app.workflow_registry.
  apply_review_status_transition`, used by BOTH this API and the Streamlit UI,
  so every surface advances status identically. The write is a single atomic,
  concurrency-safe ledger update (no read-modify-write race), so an interleaved
  engagement action can never overwrite a terminal `approved`/`rejected`:
  - `approve_draft` -> `approved`; `reject_ai_explanation` -> `rejected`
    (explicit decisions; an unconditional update, so the latest decision wins).
  - `mark_reviewed` / `mark_resolved` / `needs_follow_up` move a `pending`
    run to `in_review` but never downgrade an `approved`/`rejected` run
    (a guarded `WHERE human_review_status = 'pending'` update).
  - `add_note` never changes the status.

`200 {"human_review_status": str, "review_actions": [ReviewActionInfo]}` -
the updated run-level review status plus the full, updated action list for
the run (sorted by `created_at`).

## GET /api/runs/{run_id}/artifacts

`200 {"artifacts": [ArtifactInfo]}` | `404` unknown run.

## GET /api/runs/{run_id}/artifacts/{file_name}

File download (`FileResponse`) | `404`.

- Path-traversal-safe BY CONSTRUCTION: the path is never built from the
  request. The artifact is looked up by exact recorded `file_name` in the
  ledger, and the RECORDED path must resolve inside the configured export
  directory before it is served. Anything else (including `..\\..` names and
  artifacts recorded under a different export root) is a 404.
- Media types: `text/csv`, `application/json`, `text/markdown`,
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (xlsx),
  `text/plain`, `application/zip`; otherwise `application/octet-stream`.
- Fail-closed is preserved through the API: e.g. an invalid JE draft run
  completes but `je_upload.xlsx` / `je_upload.csv` are never written or
  listed.

## GET /api/runs/{run_id}/audit

`200 {"events": [{"event_type": str, "actor": str, "timestamp": str,
"details": object}]}` | `404`. Events come from the shared append-only audit
trail (`run_created`, `file_uploaded`, `file_parsed`,
`deterministic_analysis_completed`, `llm_request_sent`,
`llm_response_received`, `validation_completed`, `human_review_action`,
`export_generated`, `run_completed`, `run_failed`), oldest first.

---

## Deviations from the v1 contract draft

None in shape. Two clarifications:

1. `RunDetail.status` for interrupted runs (process killed mid-run, ledger
   left at `created`/`running`) is reported as `"failed"` so the status enum
   in the contract holds exactly.
2. Freeform runs additionally require `human_review_confirmation=true`
   (422 otherwise), matching the workflow's structured-input contract; the
   draft contract did not mention freeform's fields explicitly.

## Test surface

`tests/api/` (26 tests) covers every endpoint, the sample/preflight/run
flows, artifact integrity (sha256), traversal safety, fail-closed JE prep,
review actions (including the full status-transition matrix), audit events,
and a restart-simulation rehydration test.
Run them with:

```
.venv\Scripts\python.exe -m pytest tests/api -p no:cacheprovider --basetemp=.pytest_tmp_api
```

Design decisions and integration notes: `docs/decisions.md` section
"FastAPI seam + React workflow console (2026-06-11)".
