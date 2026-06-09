# Evaluation

Implements master spec "Phase 7 — Validation and Evaluation Harness". This
document describes (1) the automated evaluation metrics the harness tracks and
(2) the human pilot measurement process. It references the real implementation.

## Evaluation harness

Code: `src/eval/metrics.py` (metrics + known-answer expectations),
`src/eval/harness.py` (runner + report), tests
`tests/integration/test_eval.py` (12 tests).

The harness runs each MVP workflow on its bundled synthetic known-answer dataset
through the **shared** `src.workflows.registry` run entry point, using the
default mock LLM provider (no API key / no internet), an in-memory `RunLedger`,
and a non-JSONL `AuditLog`. It reuses the real pipeline (deterministic analysis →
mock LLM → validation → export) and leaves no shared on-disk state. It imports no
Streamlit and no provider-specific code.

### Run it

Programmatic:

```python
from src.eval.harness import run_eval
report = run_eval(out_dir=Path("runs"))   # writes runs/eval_report.json
```

CLI:

```
.venv\Scripts\python.exe -m src.eval.harness [--out runs/eval_report.json] [--workflow <name>] [--no-export]
```

Exit code is 0 iff all known-answer checks pass.

## Metrics tracked (Phase 7 "Evaluation metrics")

All metrics are derived by **deterministic** code from each workflow's findings +
summary + `ValidationResult`. Nothing is asked of the LLM. They live in
`WorkflowMetrics` / `compute_metrics` (`src/eval/metrics.py`):

| metric | meaning / source |
|--------|------------------|
| `transactions_processed` | source rows the deterministic step processed (bank: `bank_rows + ledger_rows`; budget: `joined_lines + budget_only + actual_only`; report: `rows_processed` when exposed) |
| `rows_matched` | count of `matched` findings |
| `rows_unmatched` | count of `unmatched_bank` / `unmatched_ledger` / `timing_difference` / `duplicate` findings |
| `findings_generated` | total deterministic findings |
| `validation_warnings` | length of `ValidationResult.warnings` |
| `llm_outputs_rejected` | 1 iff validation did not pass; 0 on the mock path |
| `manual_overrides` | fixed 0 (automated run, no human in the loop) |
| `export_packets_generated` | count of export artifacts written |
| `runtime_seconds` | wall-clock per workflow |

Plus context fields for defensibility: `validation_passed`,
`invented_reference_detected`, `findings_by_type`.

### Known-answer checks (Phase 7 "Synthetic test datasets")

`KNOWN_ANSWERS` in `src/eval/metrics.py` encodes the expected, reproducible
deterministic output for each workflow's synthetic dataset; `known_answer_check`
produces a per-field pass/fail breakdown. All three pass on the mock path:

- **bank_reconciliation** — matched=4, timing_difference=1, unmatched_bank=2,
  unmatched_ledger=1, findings=8, transactions_processed=13 (7 bank + 6 ledger).
- **budget_variance** — flagged_variances=2 (Fire Salaries +35000 dollar, Parks
  Supplies +50% pct), joined_lines=4, budget_only=1, actual_only=1,
  missing_accounts=1, findings=5.
- **report_review** — total_findings=7; by rule: subtotal mismatch=1, invalid
  account code=1, duplicate line=1, missing section=1, inconsistent naming=1,
  large change vs prior=2.

Freeform is excluded from the default eval set (no tabular known-answer dataset);
`--workflow freeform` is accepted explicitly if ever wanted.

### Report shape

`run_eval` returns and writes a JSON report:
`{generated_at, provider_mode, totals{workflows_evaluated, workflows_ran,
workflows_known_answer_passed, all_passed, total_findings, total_export_packets,
total_runtime_seconds}, workflows{<type>: {ran, run_id, metrics,
known_answer{passed, checks[{check, expected, actual, passed}]}}}}`.

### Supporting automated tests

Per Phase 7 "Automated tests", the suite covers CSV/Excel ingestion, date/amount
parsing, source-row preservation, bank matching, variance calculations, report
consistency checks, LLM mock responses, LLM validation, run-ledger writes,
audit-log writes, and export generation. The CLI integration test
(`tests/integration/test_cli.py`) and the app-import test
(`tests/unit/test_app_imports.py`) exercise the end-to-end and UI-contract paths.

> Windows note: the default pytest temp root is ACL-blocked in this sandbox; run
> tests with a project-local basetemp, e.g.
> `.venv\Scripts\python.exe -m pytest tests/integration/test_eval.py -q --basetemp=.pytmp_eval`.

### Preflight / messy-data test fixtures

The preflight / capability layer adds its own deterministic test coverage on top
of the known-answer eval (the full suite is **317 tests**). These tests exercise
the PASS / PARTIAL / FAIL determination and the conservative messy-data handling,
all on synthetic data only:

- `tests/unit/test_preflight.py` (19 tests) — the shared engine contract:
  status rules, blocking vs non-blocking findings, column mapping (auto / human /
  unmapped), parse-confidence gating, source-row preservation, and a crashing
  domain detector being caught.
- Per-workflow preflight tests —
  `tests/unit/test_bank_reconciliation_preflight.py` (10),
  `tests/unit/test_budget_variance_preflight.py` (12),
  `tests/unit/test_report_review_preflight.py` (13),
  `tests/unit/test_freeform_preflight.py` (11) — each asserts the `CAPABILITY`
  shape, PASS on clean data, FAIL on a missing required file/column (with the
  expected blocking codes and empty `supported_checks`), PARTIAL on the workflow's
  unsupported conditions, that `detect_conditions` returns `[]` on clean data,
  and that a human `column_mappings` override is honored while `None` matches
  current auto-detect behavior.
- `tests/integration/test_preflight_integration.py` (7 tests) — end-to-end through
  the runner: PASS recorded in the ledger and review packet; FAIL not running the
  workflow or LLM (no stored LLM response, failed-preflight packet = only the two
  preflight files, blocked audit lifecycle); PARTIAL merging preflight findings
  into the deterministic findings and rejecting LLM resolution-claims.

Synthetic messy-data fixtures live under
`data/synthetic/<workflow>/messy/` and cover, per workflow: a FAIL case (missing
required column/file), PARTIAL cases (bank sign-convention mismatch and batch
deposit; budget embedded-subtotal rollup; report unrecognized line types / wide
pivot), and a clean PASS case. Each fixture directory's `README.md` states the
data is synthetic only with no real PII.

## Human pilot metrics (Phase 7 "Human pilot metrics")

The automated harness measures correctness and throughput; it cannot measure
whether the tool actually helps a finance staff member. The pilot measures that
with a small, repeatable process. For each pilot task the facilitator records:

| measure | how captured | scale |
|---------|--------------|-------|
| Task completion time **before** tool | stopwatch on the participant's current manual process | minutes |
| Task completion time **with** tool | stopwatch on the same task using the tool's review packet | minutes |
| User **confidence** rating | post-task self-report | 1–5 |
| User **clarity** rating | post-task self-report (were the findings/explanations clear?) | 1–5 |
| Would-**keep-using** rating | post-task self-report | 1–5 |
| Qualitative feedback | short structured debrief (what helped, what was confusing, what was missing) | free text |

All pilot data uses synthetic datasets only — no real PII or sensitive financial
data, consistent with the non-negotiable data constraints. The detailed protocol
(participants, tasks, script, consent, data handling) is in
`docs/pilot_plan.md`.

## What "good" looks like

- Every known-answer check passes (`all_passed = true` in the eval report).
- Zero invented source references on the mock path
  (`invented_reference_detected = false`, `llm_outputs_rejected = 0`).
- Pilot: time-with-tool < time-before-tool on the matched tasks, and median
  confidence / clarity / would-keep-using ratings ≥ 4 of 5, with qualitative
  feedback that the source-linked packet was trustworthy and reviewable.
