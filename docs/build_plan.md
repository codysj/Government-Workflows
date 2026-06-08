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
- [x] UX polish (spinners, result metrics, draft status, empty/error states)
- [x] Tests: 140 passed; AppTest manual verification of all 8 pages + format selectors; PII sweep clean

## Architecture decision log
Major decisions recorded in `docs/decisions.md` as implemented.
