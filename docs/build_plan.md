# Build Plan — Municipal Finance AI Workflow Tool (MVP)

Concise implementation checklist mapped to the master spec
(`docs/Project_Outline_Master.md`). Source of truth is that spec; this file
tracks execution order and done-state.

## Workflow selection (Phase 1)
Research is treated as inconclusive → apply the spec's **default decision rule**:
1. Bank reconciliation
2. Budget-to-actual variance review and commentary
3. Financial report consistency / error-flagging review

Justification recorded in `docs/research/workflow_selection_scorecard.md` and
`docs/research/mvp_workflow_recommendation.md`.

## Execution order & checklist

- [x] 1. Project structure + configuration (pyproject, .gitignore, .env.example, README skeleton)
- [x] 2. Core schemas (`src/core/schemas.py`) — all 12 data contracts
- [x] 3. Ingestion + normalization utilities (csv/excel/pdf loaders; cleaning/matching/finance_rules)
- [x] 4. SQLite run ledger (`src/core/run_ledger.py`)
- [x] 5. Append-only audit log (`src/core/audit_log.py`)
- [x] 6. LLM provider wrapper w/ mock mode (`src/llm/provider.py`, `prompts.py`, `schemas.py`)
- [x] 7. LLM output validation (`src/core/validation.py`)
- [x] 8. Workflow runner + exports (`src/core/workflow_runner.py`, `exports.py`)
- [x] 9. Bank reconciliation workflow (deterministic) + synthetic known-answer data
- [x] 10. CLI support (bank reconciliation, `--sample`)
- [x] 11. Streamlit support (bank reconciliation review)
- [x] 12. Export packet generation (md/csv/json artifacts)
- [x] 13. Budget variance workflow + data + tests
- [x] 14. Report review workflow + data + tests
- [x] 15. Guided freeform mode (structured, not chatbot)
- [x] 16. Workflow history + review UI (all workflows)
- [x] 17. Human review actions (mark reviewed/resolved/follow-up/note/reject/approve)
- [x] 18. Synthetic datasets + known-answer fixtures (all workflows)
- [x] 19. Evaluation metrics + eval harness (`docs/evaluation.md`)
- [x] 20. Documentation (README, decisions, research, workflow_specs, pilot_plan)
- [x] 21. Final verification (`pytest` 103 passed + 3 CLI sample runs exit 0 + Streamlit import clean)
- [x] 22. Structural consolidation: `src/context/` populated; validation/exports/runner logic moved to `src/core/`; inline `MockLLMProvider`s subclass the canonical wrapper (see `docs/decisions.md`)

## Invariants (enforced in every module)
- Deterministic code does parsing, cleaning, matching, calculation, validation,
  source-row tracking, export, audit logging.
- LLM only explains/summarizes/drafts/classifies/flags — never calculates or
  decides matches; must cite source-row refs.
- Every normalized record retains a `SourceRowRef`.
- Mock LLM mode is the default path; runs without API key or internet.
- SQLite run ledger + append-only audit log for every run.
- Validation rejects/flags invented refs, unsupported numeric claims, final
  approval language, missing refs, invalid JSON.
- Only synthetic data; no secrets, no PII, no real integrations.

## Post-MVP polish pass (Tier 1)
- [x] Bank reconciliation runnable from the Streamlit UI (all 4 workflows in-app)
- [x] Settings tolerances/thresholds thread into runs (uploaded configs win)
- [x] Per-run export isolation (`export_dir/<run_id>/`)
- [x] Consolidated review packet (`review_packet.md` + `run_manifest.json`)
- [x] Uniform export-artifact recording across all workflows
- [x] Consistent, non-duplicated audit lifecycle (single owner)
- [x] AI Audit Log page + `RunLedger.list_llm_interactions()` (draft-vs-final)
- [x] Import presets / ERP-style column aliases + sample dataset
- [x] Import presets wired into Run Workflow UI (per-upload "source format" selector)
- [x] Import presets extended to budget + report (ERP samples for all 3 workflows)
- [x] Applied preset surfaced on Review Run + review packet (§2) + run manifest
- [x] UX polish (spinners, result metrics, draft status, empty/error states)
- [x] Tests: 147 passed; AppTest manual verification of all 8 pages + format selectors; PII sweep clean

## Tier 1 near-term extensions (complete)
- [x] Retention category (`RetentionCategory` enum + safe ledger column migration; in summary/packet/manifest/History)
- [x] Exportable AI usage log (`src/core/ai_usage_log.py`; CSV/JSON download)
- [x] Prompt/response diffing (`src/core/diffing.py`, stdlib `difflib`; compare two AI interactions)
- [x] Redaction assist prototype (`src/core/redaction.py`; regex SSN/email/phone/credit-card/long-number — not a compliance tool)
- [x] Chart-of-accounts import preset (`chart_of_accounts` in `src/ingest/presets.py` + synthetic sample)
- [x] Scheduled runs (`src/core/scheduler.py`; local, manual-trigger, no daemon/cron)
- [x] Role-specific views (`app/role_views.py`; presentation-only, no auth)
- [x] PDF summary export (`src/core/pdf_export.py`; pure-stdlib PDF writer, text-only)
- [x] New Streamlit pages wired (Scheduled runs, Redaction assist) + AppSettings `role` / `default_retention_category`
- [x] Final verification: `pytest` **235 passed**; 3 CLI sample runs exit 0 (Validation PASSED); all 10 Streamlit pages render via AppTest; secret/PII sweep clean

## Preflight / capability robustness pass
Reusable preflight / capability layer so each workflow can determine whether an
uploaded file set is PASS / PARTIAL / FAIL before running (and before any LLM
call). Fail-closed; conservative messy-data handling; Guided Freeform stays
draft-only and is not a fallback. See `docs/workflow_capabilities.md` and the
"Preflight / capability layer" section of `docs/decisions.md`.

- [x] Preflight engine + schemas (`src/core/preflight.py`, preflight models in `src/core/schemas.py`)
- [x] Messy-data + parse-confidence + semantic-detection helpers (`src/normalize/cleaning.py`)
- [x] Per-workflow `CAPABILITY` + `detect_conditions` for all 4 workflows; optional `column_mappings` override
- [x] Runner branch (FAIL → no workflow/no LLM; PARTIAL → supported logic + flags; PASS → normal) in `app/workflow_registry.py`
- [x] Ledger persistence (`store_preflight` / `get_preflight`), validation rules (`validate_with_preflight`), review-packet artifacts (`preflight_report.json` + `preflight_summary.md`)
- [x] CLI surfacing (`PREFLIGHT:` / `STATUS:` lines, `--mappings`, `--preflight-only`) + Streamlit preflight views / mapping UI
- [x] Synthetic messy-data fixtures per workflow (PASS / PARTIAL / FAIL)
- [x] New doc `docs/workflow_capabilities.md` (rules + per-workflow capability table)
- [x] Final verification: `pytest` **317 passed**, no regressions; 3 CLI sample runs PREFLIGHT PASS / exit 0; all 10 Streamlit pages render via AppTest; mock-LLM offline default + secret/PII sweep clean

## Tyler/Munis ERP enablement + four review workflows

Four new workflows added on top of the preflight-hardened MVP. All changes are
additive; no existing workflow logic, test, or schema was altered in a
breaking way.

- [x] Tyler/Munis-style export normalizer (`src/ingest/tyler.py`): 8 dataset
  types (gl_detail, ap_invoice_detail, vendor_list, check_register,
  purchase_orders, budget_to_actual, chart_of_accounts, je_upload); deterministic
  header detection; Decimal/date parsing; debit/credit -> signed amount derivation;
  SHA-256 input file hashing; source_row_index traceability; fail-closed on
  unknown type / missing required columns / unparseable file.
- [x] `TYLER_MUNIS_STYLE` preset extended with Munis-style column aliases
  (`src/ingest/presets.py`; additive only).
- [x] `FindingType` extended with SEARCH_MATCH, DUPLICATE_PAYMENT,
  VENDOR_ANOMALY, PO_MISMATCH, JE_VALIDATION, MISSING_REFERENCE
  (`src/core/schemas.py`; additive only).
- [x] Synthetic City of Riverbend dataset (`data/synthetic/tyler/`): 8 files,
  planted D1-D8/P1-P8/Q1-Q4/JE anomalies, `known_answers.json`.
- [x] `transaction_search` workflow: two-stage (LLM proposes SearchCriteria,
  execution is deterministic pandas filters); 50 unit tests.
- [x] `ap_duplicate_review` workflow: D1/D1b/D2-D8 checks; 92 unit tests.
- [x] `je_upload_prep` workflow: 11 blocking rules, 3 warnings; fail-closed
  upload workbook gate; 82 unit tests.
- [x] `po_invoice_review` workflow: P1-P8 + P3b; 70 unit tests.
- [x] Tyler readiness for original 3 workflows: derive_signed_amount,
  combined budget-to-actual file, Excel load path, exact-name semantic
  precedence; 26 unit tests (`test_tyler_readiness.py`).
- [x] Registry wiring: all 8 workflows in `src/workflows/registry.py` (CLI),
  `app/workflow_registry.py` (Streamlit), eval harness, integration tests.
- [x] Role views extended: suggested_workflows per role; AP/vendor emphasis
  types for AP clerk and analyst.
- [x] Eval harness extended: 7/7 tabular workflows, 7/7 known-answer checks pass.
- [x] Integration tests added: `test_cli.py`, `test_app_registry.py`,
  `test_eval.py` extended for all 4 new workflows.
- [x] Workflow spec docs written (4 new files under `docs/workflow_specs/`).
- [x] Final verification: `pytest` **705 passed**; all 8 CLI `--sample` runs
  PREFLIGHT PASS / STATUS PASS / Validation PASSED; eval harness 7/7
  known-answer pass; no regressions.

## Architecture decision log
Major decisions recorded in `docs/decisions.md` as implemented.
