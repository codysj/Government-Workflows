# Workflow Spec — Bank Reconciliation

Implements master spec "Workflow 1 — Bank Reconciliation". Module:
`src/workflows/bank_reconciliation.py`. Contains no Streamlit and no
provider-specific code; plugs into the shared pipeline the same way the other
workflows do. This spec documents the workflow **as implemented**.

## Purpose

Help finance staff compare bank-statement records against ledger records and
produce a reviewable exception packet. All matching, calculation, and exception
detection are deterministic; the LLM never performs matching.

## workflow_type

`bank_reconciliation`  (prompt template version `bank_reconciliation.v1`)

## Inputs

`run(inputs, ...)` reads these keys from `inputs`:

| key | required | accepted as |
| --- | --- | --- |
| `bank` | yes | `ParsedTable` or path to the bank-statement CSV |
| `ledger` | yes | `ParsedTable` or path to the ledger-export CSV |
| `chart_of_accounts` | no | reserved (not used by the deterministic core) |
| `reconciliation_config` | no | path to a tolerance JSON (see Config) |

Column names are normalized to snake_case. The amount and date columns are
auto-detected from candidate names:

- date: `date`, `txn_date`, `transaction_date`, `posted_date`, `post_date`
- amount: `amount`, `value`, `txn_amount`, `transaction_amount`
- description (optional): `description`, `memo`, `payee`, `vendor`, `details`,
  `narrative`

If amount/date columns cannot be located in both files, the run raises
`ValueError`.

## Config (deterministic tolerances)

`ReconciliationConfig` carries `amount_tolerance` (Decimal) and
`date_tolerance_days` (int). Defaults: `amount_tolerance = 0`,
`date_tolerance_days = 0`. Override via the `config` argument (a
`ReconciliationConfig`, a dict, or a path to JSON) or via
`inputs['reconciliation_config']`; the explicit `config` argument wins when both
are given.

## Deterministic processing (LLM does NONE of this)

The authoritative matcher is `src.normalize.matching` (`match_records`,
`detect_duplicates`). `reconcile(bank, ledger, config)` produces, as
`DeterministicFinding`s:

| finding_type | rule_used | severity | meaning |
|--------------|-----------|----------|---------|
| `matched` | `exact_amount_and_date_match` | info | bank row matched a ledger row on amount and date |
| `timing_difference` | `amount_match_within_date_tolerance` | medium | amount matches but dates differ within tolerance |
| `unmatched_bank` | `bank_item_without_ledger_match` | high | a bank row with no ledger match |
| `unmatched_ledger` | `ledger_item_without_bank_match` | high | a ledger row with no bank match |
| `duplicate` | `duplicate_within_bank` / `duplicate_within_ledger` | medium | a within-table duplicate pair (same amount + date) |

Every finding carries `source_rows` (`SourceRowRef` back to the originating
bank/ledger row), `computed_values`, `rule_used`, and `requires_human_review`
(true for all exception types; false for clean matches).

### Summary totals

`reconcile` also returns a `summary` dict: `bank_rows`, `ledger_rows`,
`matched`, `timing_differences`, `unmatched_bank`, `unmatched_ledger`,
`duplicate_bank_pairs`, `duplicate_ledger_pairs`, `amount_tolerance`,
`date_tolerance_days`, `total_bank_amount`, `total_ledger_amount`,
`requires_human_review`. Result tables `matched_transactions`,
`unmatched_bank_items`, and `unmatched_ledger_items` are produced for export.

## LLM tasks (advisory, draft-only)

The LLM may summarize the unmatched/timing/duplicate exceptions, draft a
plain-language explanation of likely causes, group exceptions into categories,
suggest human review steps, and draft reconciliation memo language. It must cite
the deterministic source-row ids and must not match, calculate, or invent any
account/amount/date. The structured output contract has keys: `summary`,
`categorized_exceptions` (`{category, description, referenced_source_rows}`),
`referenced_source_rows`, `suggested_review_steps`, `draft_memo`.

The default provider is `MockLLMProvider` — a deterministic mock that runs with
no API key and no internet. Its output is derived ONLY from the deterministic
findings and cites their real source-row ids.

## Validation (deterministic guardrail)

`validate_llm_output(response_json, det)` builds the set of valid source-row ids
from the deterministic findings and:

- errors (fail) if any cited reference is not among them
  (`invented_reference_detected = True`),
- errors if the required `summary` key is missing,
- warns if exceptions are present but the LLM cited no source rows.

It returns a `ValidationResult` with `passed`, `errors`, `warnings`,
`checked_source_refs`, `invented_reference_detected`, and
`numeric_claims_checked`.

## Export artifacts

`export_artifacts(...)` writes the six spec-required files and returns
`ExportArtifact` manifests (each with a sha256 of its contents):
`reconciliation_summary.md`, `matched_transactions.csv`,
`unmatched_bank_items.csv`, `unmatched_ledger_items.csv`,
`validation_report.json`, `audit_log.json`.

## Integration

- `WORKFLOW_TYPE = "bank_reconciliation"` is the registry key.
- `run(inputs, *, provider=None, ledger=None, audit=None, run_id=None,
  actor="system", export_dir=None, config=None)` is the entry point. When a
  `provider` is None the mock LLM is used (default offline path). When `ledger`
  (RunLedger) and `audit` (AuditLog) are injected, findings / LLM response /
  validation / events / export artifacts are persisted via the shared method
  names. `export_dir` triggers artifact generation.
- `register(registry)` supports both `registry.register(type, run)` and
  `registry[type] = run` styles. `WORKFLOW` exposes metadata; `WORKFLOW_REGISTRY`
  self-registers at import.

## Synthetic known-answer dataset

Bundled under `data/synthetic/bank_reconciliation/`. Under the bundled config
(`amount_tolerance = 0`, `date_tolerance_days = 3`) the expected deterministic
output is:

| metric | expected |
|--------|----------|
| bank_rows | 7 |
| ledger_rows | 6 |
| matched | 4 |
| timing_differences | 1 |
| unmatched_bank | 2 |
| unmatched_ledger | 1 |
| findings generated | 8 |

The two unmatched bank items include a duplicate Acme payment that, under these
tolerances, surfaces as an unmatched bank item rather than a cleared duplicate.
These expectations are encoded in the evaluation harness
(`src/eval/metrics.py`, `KNOWN_ANSWERS["bank_reconciliation"]`).

## Preflight & unsupported conditions

This workflow exposes a module-level `CAPABILITY: CapabilitySpec` and a
`detect_conditions(profiles, mappings, inputs, config)` consumed by the shared
preflight layer (`src/core/preflight.py`); see
[`docs/workflow_capabilities.md`](../workflow_capabilities.md) for the engine and
the PASS / PARTIAL / FAIL rules.

`CAPABILITY`:

- required inputs: `bank`, `ledger`; optional: `chart_of_accounts`
- accepted file types: `csv`, `xlsx`
- required semantic columns: `bank` → `date`, `amount`; `ledger` → `date`,
  `amount`; optional: `bank`/`ledger` → `description`
- supported patterns: exact 1:1 amount+date match within tolerance, potential
  timing difference, within-table duplicate detection, unmatched bank items,
  unmatched ledger items
- partially supported (flagged as PARTIAL): `possible_sign_convention_mismatch`,
  `possible_batch_matching`, `possible_prior_period_items`
- unsupported: `many_to_one_batch_matching`, `multi_currency_reconciliation`

`detect_conditions` emits only **non-blocking** (PARTIAL) findings; it returns
`[]` on clean data and never blocks a passing run:

- `POSSIBLE_SIGN_CONVENTION_MISMATCH` — ≥4 shared magnitudes with ≥60%
  opposite-signed between bank and ledger.
- `POSSIBLE_BATCH_MATCHING` — one side's total equals a subset-sum of ≥3 items on
  the other (a many-to-one batch deposit, which the deterministic 1:1 matcher does
  not handle).
- `POSSIBLE_PRIOR_PERIOD_ITEM` — dates more than ~45 days outside the bulk
  inter-quartile date window.

A FAIL (e.g. the `amount` column missing on `bank` or `ledger`) refuses the run
before the LLM is called and returns the structured report with next steps. A
human-approved `column_mappings` override (`{input_key: {semantic: column}}`,
e.g. via `--mappings`) lets a user point the workflow at the right `date` /
`amount` / `description` column when auto-detection is ambiguous.

## Verified run

`.venv\Scripts\python.exe cli\run_workflow.py bank-reconciliation --sample`
runs end-to-end, Validation PASSED, and (with `--export <dir>`) writes the six
artifacts above.
