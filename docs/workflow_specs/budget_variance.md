# Workflow Spec — Budget-to-Actual Variance Review

Implements spec section "Workflow 2 — Budget-to-Actual Variance Review".

## Purpose

Help finance staff identify and explain significant budget variances using
deterministic calculations and source-linked AI commentary. The LLM never
calculates a variance.

## workflow_type

`budget_variance`

## Inputs

| input | required | accepted as |
| --- | --- | --- |
| budget | yes | `ParsedTable` or path to `budget.csv` |
| actuals | yes | `ParsedTable` or path to `actuals.csv` |
| chart_of_accounts | no | `ParsedTable` or path; enables missing-account detection |
| prior_actuals | no | reserved (not yet used by deterministic core) |
| thresholds | no | dict or path to `thresholds.json` |

## Deterministic processing (LLM does NONE of this)

1. Join budget and actuals on the intersection of
   `(fund, account, department, object)` present in both files.
2. `dollar_variance = actual - budget`.
3. `pct_variance = dollar_variance / budget * 100` when `budget != 0`;
   `None` (undefined) when `budget == 0`.
4. Flag a line when `abs(dollar_variance) >= dollar_threshold`
   OR (`pct_variance` defined AND `abs(pct_variance) >= pct_threshold`).
5. Flag budget-only accounts (in budget, not actuals).
6. Flag actual-only / new accounts (in actuals, not budget).
7. Flag missing accounts (in chart of accounts, in neither file).
8. Group totals by fund and by department.

Every finding carries `SourceRowRef`s back to the originating file rows.

### Thresholds

Defaults: `dollar_threshold = 10000`, `pct_threshold = 10.0` (percent).
Override via a `thresholds` dict or `thresholds.json`.

## LLM tasks (advisory, draft-only)

Draft variance commentary, translate findings to plain English, identify which
variances need staff explanation, suggest follow-up questions for department
heads, and group variances into themes. Every claim cites source-row ids. The
default provider is a deterministic mock that runs with no API key / no
internet; it derives output ONLY from the deterministic findings.

## Validation

`validate_llm` confirms every referenced source-row id exists among the
deterministic findings' source rows (`invented_reference_detected`), and that
required output keys are present.

## Export artifacts

`variance_summary.md`, `flagged_variances.csv`,
`variance_commentary_draft.md`, `validation_report.json`, `audit_log.json`.

## Synthetic known-answer dataset

Under `data/synthetic/budget_variance/` (`budget.csv`, `actuals.csv`,
`chart_of_accounts.csv`, `thresholds.json`).

| key (fund/account/dept/object) | budget | actual | dollar var | pct var | classification |
| --- | --- | --- | --- | --- | --- |
| General/5001/Police/Salaries | 50000 | 50500 | +500 | +1.0% | matched, not flagged |
| General/5002/Fire/Salaries | 200000 | 235000 | +35000 | +17.5% | **flagged — large dollar** |
| General/5003/Parks/Supplies | 2000 | 3000 | +1000 | +50.0% | **flagged — large pct** |
| Water/6001/Utilities/Maintenance | 80000 | 79000 | -1000 | -1.25% | matched, not flagged |
| General/5010/Library/Books | 15000 | — | — | — | **budget-only** |
| General/5020/Recreation/Equipment | — | 12000 | — | — | **actual-only (new)** |
| General/5099/Finance/Audit | — | — | — | — | **missing (COA only)** |

Expected counts: joined lines = 4, flagged variances = 2, budget-only = 1,
actual-only = 1, missing accounts = 1.

## Preflight & unsupported conditions

This workflow exposes a module-level `CAPABILITY: CapabilitySpec` and a
`detect_conditions(profiles, mappings, inputs, config)` consumed by the shared
preflight layer (`src/core/preflight.py`); see
[`docs/workflow_capabilities.md`](../workflow_capabilities.md) for the engine and
the PASS / PARTIAL / FAIL rules.

`CAPABILITY`:

- required inputs: `budget`, `actuals`; optional: `chart_of_accounts`
- accepted file types: `csv`, `xlsx`
- required semantic columns: `budget` → `fund`, `amount`; `actuals` → `fund`,
  `amount`; optional: `budget`/`actuals` → `account_code`, `department`, `object`;
  `chart_of_accounts` → `fund`, `account_code`, `department`, `object`
- supported patterns: join by fund/account/department/object, dollar variance,
  pct variance, threshold flags, budget-only / actual-only / missing
- partially supported (flagged as PARTIAL): `possible_account_rollup`,
  `possible_budget_basis_mismatch`
- unsupported: `account_rollup_hierarchies`,
  `budget_basis_conversion_annual_vs_ytd`

`detect_conditions` emits only **non-blocking** (PARTIAL) findings; it returns
`[]` on the clean demo and when a required file is absent:

- `POSSIBLE_ACCOUNT_ROLLUP` — a budget/actuals row's amount equals (within ~0.5%)
  the sum of ≥2 other rows (a subtotal/parent embedded in the detail rows, which
  the flat join would double-count).
- `POSSIBLE_BUDGET_BASIS_MISMATCH` — one side's row count is ≥3× the other
  (different aggregation level) or total magnitudes differ ≥8× (an annual-vs-YTD
  basis signal).

A FAIL (e.g. no `fund`/`amount` columns) refuses the run before the LLM is called
and returns the structured report with next steps. A human-approved
`column_mappings` override lets a user map the `amount` and join-key columns
(`fund` / `account_code` / `department` / `object`) when auto-detection is
ambiguous.

## Registration

The module exposes `WORKFLOW_REGISTRY` and a `register_workflow` decorator and
self-registers `BudgetVarianceWorkflow` at import time. The Integration agent
wires it into any global registry by importing the module
(`from src.workflows import budget_variance`); no edits to this module are
needed.
