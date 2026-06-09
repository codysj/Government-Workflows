# Workflow 3 — Financial Report Consistency / Error-Flagging Review

Implements the master spec section "Workflow 3 — Financial Report Consistency /
Error-Flagging Review". Module: `src/workflows/report_review.py`. Contains no
Streamlit and no provider-specific code; plugs into the shared pipeline.

## Purpose
Help finance staff review draft reports / schedules / internal packets for
inconsistencies before human finalization. The LLM never decides whether the
report is correct — it only explains the deterministically flagged issues.

## Inputs
| key | required | description |
|-----|----------|-------------|
| `report_table` | yes | report CSV: `section, account_code, account_name, line_type, amount` (`line_type` in line_item / subtotal / grand_total) |
| `chart_of_accounts` | optional | valid `account_code` list |
| `prior_version` | optional | prior report (same schema) for change detection |
| `checklist_config` | optional | JSON: required sections, thresholds, column names |

Column names are normalized to snake_case before processing.

## Deterministic checks (all calculations live here)
| rule_used | severity | what it flags |
|-----------|----------|---------------|
| `subtotal_equals_line_item_sum` | high | section subtotal != sum of its line items |
| `grand_total_ties_out` | high | grand total != sum of subtotals (only when a grand_total row exists) |
| `required_section_present` | high | a required section is missing |
| `no_duplicate_account_lines` | medium | an exact-duplicate line-item row |
| `no_unexpected_negative_values` | medium | a negative value in a section where negatives are not allowed |
| `account_code_in_chart_of_accounts` | high | an account code absent from the chart of accounts |
| `large_change_from_prior_version` | medium | a line whose change vs prior >= pct threshold and >= min-amount floor |
| `consistent_account_naming` | medium | one account code labeled with >=2 distinct names |

Every finding is a `DeterministicFinding` carrying `source_rows`
(`SourceRowRef`), `computed_values`, `rule_used`, `requires_human_review`.

## LLM tasks (advisory only)
Summarize inconsistencies, draft a review checklist, explain why each flagged
issue may matter, suggest human follow-up, draft a plain-language review memo.
Must cite source-row ids; must not calculate, invent values, or produce approval
language. Prompt builder `build_report_review_prompt`; template
`report_review.v1`. Mock mode is the default path (no API key / no internet).

## Validation (deterministic, Phase 2 rules)
`validate_llm_output` rejects invented source-row references, omitted required
references, and final-approval language; warns on numeric claims not present in
deterministic computed values. Sets `invented_reference_detected` and
`numeric_claims_checked`.

## Export artifacts
`report_review_summary.md`, `flagged_issues.csv`, `review_checklist.md`,
`validation_report.json`, `audit_log.json` (via `export_artifacts`).

## Integration
- `WORKFLOW_TYPE = "report_review"` is the registry key.
- `run(inputs, *, provider=None, ledger=None, audit=None, run_id=None, actor=, export_dir=None, config=None)` is the entry point. Injected provider/ledger/audit are used when present (duck-typed against existing pipeline method names); otherwise self-contained mock/validation/export fallbacks keep it runnable.
- `register(registry)` supports both `registry[type] = run` and `registry.register(type, run)` styles. `WORKFLOW` dict exposes metadata.

## Preflight & unsupported conditions

This workflow exposes a module-level `CAPABILITY: CapabilitySpec` and a
`detect_conditions(profiles, mappings, inputs, config)` consumed by the shared
preflight layer (`src/core/preflight.py`); see
[`docs/workflow_capabilities.md`](../workflow_capabilities.md) for the engine and
the PASS / PARTIAL / FAIL rules.

`CAPABILITY`:

- required inputs: `report_table`; optional: `chart_of_accounts`, `prior_version`
- accepted file types: `csv`, `xlsx`
- required semantic columns: `report_table` → `section`, `line_type`, `amount`;
  optional: `report_table` → `account_code`, `account_name`; `chart_of_accounts` →
  `account_code`, `account_name`; `prior_version` → `account_code`, `amount`
- supported patterns: long layout (section/line_type/amount), subtotal vs line
  item, missing required section, duplicate account line, invalid account code,
  prior-version change, inconsistent account naming
- partially supported (flagged as PARTIAL): `unrecognized_line_type_values`,
  `single_level_rollup_with_extra_labels`
- unsupported: `wide_pivoted_report`, `multi_level_nested_rollup`

`detect_conditions` emits only **non-blocking** (PARTIAL) findings; it returns
`[]` on clean data and when the required file is absent/unreadable (the engine
handles blocking):

- `POSSIBLE_UNKNOWN_REPORT_STRUCTURE` — missing section/line_type structure, or a
  `line_type` column with zero recognized values.
- `POSSIBLE_ACCOUNT_ROLLUP` — nested-subtotal labels (e.g. `sub_subtotal`,
  `rollup`) or a numeric `level`/`indent`/`depth`/`tier` column with ≥3 distinct
  depths (a multi-level nested rollup).
- `UNSUPPORTED_PATTERN_DETECTED` — a wide/pivoted layout: no single long `amount`
  column but ≥3 mostly-numeric columns.

A FAIL (e.g. the required `amount` column absent) refuses the run before the LLM
is called and returns the structured report with next steps. A human-approved
`column_mappings` override lets a user map `section` / `line_type` / `amount` /
`account_code` / `account_name` when auto-detection is ambiguous. Recognized
line-type values (`line_item` / `subtotal` / `grand_total` / ...) never trip the
PARTIAL detector.

## Synthetic known-answer data — `data/synthetic/report_review/`
`report_table.csv`, `chart_of_accounts.csv`, `prior_version.csv`,
`checklist_config.json`. Engineered expected answers:

| issue | location | expected |
|-------|----------|----------|
| subtotal mismatch | Expenditures | stated 700000 vs computed 810000 |
| invalid account code | line `5099 Contingency` | 5099 not in chart of accounts |
| duplicate line | two identical `5030 Supplies 80000` rows | one duplicate finding |
| missing section | required `Fund Balance` absent | one missing-section finding |
| inconsistent naming | code `5040` as "Professional Services" and "Prof. Services" | one naming finding |
| large prior change | `5010` salaries 250000 -> 400000 (+60%) | one large-change finding |
| control (no flag) | Revenues subtotal 1000000 ties out | not flagged |

Tests: `tests/unit/test_report_review.py` (on Windows run with a project-local
`--basetemp` to avoid the global temp-dir ACL).
