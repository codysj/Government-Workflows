# Preflight & Workflow Capabilities

Implements the master spec's preflight / capability layer. This document
describes, as implemented, how each workflow decides whether an uploaded set of
files can be run, what messy-data handling is supported, and what remains
unsupported. It references the real code: `src/core/preflight.py` (engine),
`src/core/schemas.py` (preflight models), `src/normalize/cleaning.py` (messy-data
helpers), and the per-workflow `CAPABILITY` / `detect_conditions` in
`src/workflows/*.py`.

## What preflight does

Before any workflow runs, the runner profiles the uploaded inputs against the
workflow's declared capability and returns a structured **preflight report**
(`PreflightReport`). The report is a deterministic capability determination — it
does parsing, column detection, parse-confidence scoring, and messy-data
detection in code, never with the model. It produces one of three statuses:

- **PASS** — the inputs satisfy the workflow's requirements; run normally.
- **PARTIAL** — the workflow can run its supported deterministic logic, but one
  or more conditions it does not fully handle were detected; those conditions
  are flagged for human review and the LLM may only explain the deterministic
  findings (it may not resolve the flagged conditions).
- **FAIL** — the inputs are missing something required (a file, a column) or are
  ambiguous/unparseable on a required column; the workflow does **not** run, the
  **LLM is never called**, and a structured report with concrete next steps is
  returned.

The report is recorded in the run ledger, surfaced in the CLI and Streamlit UI,
and written into the review packet (`preflight_report.json` +
`preflight_summary.md`) for every PASS/PARTIAL run; a FAIL writes only those two
preflight files.

## PASS / PARTIAL / FAIL — the exact rules

A preflight finding (`PreflightFinding`) carries a condition `code`, a
`severity`, a `message`, the affected input/column, a `suggested_action`, and a
`blocks_run` flag. Status is derived purely from the findings:

- **FAIL** if **any** finding has `blocks_run=True`. The engine sets
  `blocks_run=True` for:
  - a missing **required** input file (`MISSING_REQUIRED_FILE`),
  - an unsupported file type on a **required** input (`UNSUPPORTED_FILE_TYPE`),
  - a missing **required** semantic column (`MISSING_REQUIRED_COLUMN`),
  - an ambiguous mapping on a **required** column (`AMBIGUOUS_COLUMN_MAPPING`),
  - low date/amount parse confidence on a **required** column
    (`LOW_DATE_PARSE_CONFIDENCE` / `LOW_AMOUNT_PARSE_CONFIDENCE`, below the
    workflow's `min_date_confidence` / `min_amount_confidence`, default `0.8`),
  - an unresolved required mapping (`NEEDS_HUMAN_CONFIGURATION`). On the same
    required column the engine emits both `MISSING_REQUIRED_COLUMN` and
    `NEEDS_HUMAN_CONFIGURATION`.
- **PARTIAL** if there is no blocking finding but at least one non-blocking,
  non-`INFO` finding — e.g. a domain `possible_*` condition, optional-column
  ambiguity/low-confidence, an unsupported file type on an **optional** input,
  or `UNSUPPORTED_PATTERN_DETECTED`.
- **PASS** if there are no findings beyond purely informational (`Severity.INFO`)
  ones.

Derived report fields follow from the status:
`llm_allowed = (status != FAIL)`; `partial = (status == PARTIAL)`;
`supported_checks` = the workflow's `supported_patterns` (populated on
PASS/PARTIAL, empty on FAIL); `unsupported_conditions` = the distinct condition
code values present; `next_steps` = de-duplicated `suggested_action`s with the
blocking ones first.

## Why workflows fail closed (and never silently hand off to the LLM)

A FAIL is a deliberate refusal, not a fallback. When required inputs are missing
or unparseable, the deterministic logic cannot produce correct findings, so:

- the workflow logic is **not run**, and
- the **LLM is never called** — `llm_allowed=False`, no prompt is built, no draft
  is generated, and nothing is stored as an LLM response.

This is the core safety property: the model must never "take over" failed
workflow logic by guessing at calculations, matches, or values it has no
deterministic basis for. On FAIL the user gets the structured report (file
profiles, blocking conditions, and next steps) instead of any AI text. The
validation layer enforces this even if an LLM output were somehow present: on a
FAIL run any LLM output is flagged as an error (`validate_with_preflight`).

On PARTIAL the LLM is allowed, but constrained: it may only explain the
deterministic findings and must not claim to have resolved any flagged
unsupported condition. A resolution claim made against an unsupported-condition
topic is flagged by `validate_partial_resolution_claims`.

## Why Guided Freeform is NOT an automatic fallback

Guided Freeform is a separately-labeled, draft-only exploratory mode. It is
**never** triggered automatically when a formal workflow FAILs. On a FAIL the UI
may *offer* Guided Freeform behind a deliberate, clearly-labeled user action
("Optional: Guided Freeform (separate, draft-only mode)"), with copy stating it
is not a re-run of the failed workflow and produces an advisory draft only.
Switching to it requires an explicit click and never inherits the failed
workflow's data or pretends to complete it. Freeform itself fails closed too: its
`detect_conditions` emits a blocking `NEEDS_HUMAN_CONFIGURATION` when the
sensitivity confirmation is missing or the task type is blank, so the runner
refuses before the LLM is called.

## Messy-data handling supported (only when safe)

The preflight engine and the cleaning helpers (`src/normalize/cleaning.py`)
detect and conservatively normalize common messy-data conditions. Every helper
operates on a `normalize_columns()`-ed frame and **preserves source-row
indices** (positional indices in the parsed frame) through all transformations,
so each finding can still cite its originating row.

- **Column normalization** — headers are snake_cased / normalized before any
  detection (`normalize_columns`, `to_snake_case`).
- **Semantic column detection** — `best_semantic_column` ranks candidate columns
  for a semantic name (e.g. `date`, `amount`, `description`, `fund`,
  `account_code`, `section`, `line_type`) using a synonym map
  (`SEMANTIC_SYNONYMS`) plus, for `date`/`amount`, a parse-confidence tiebreaker.
  A near-tie between two strong candidates lowers confidence so the engine treats
  it as ambiguous rather than guessing.
- **Date/amount parse confidence** — `date_parse_confidence` /
  `amount_parse_confidence` report the fraction of non-blank cells that parse via
  `parse_date` / `parse_amount`. A required column below the workflow's threshold
  (default `0.8`) blocks the run; the engine records per-column confidence in the
  `FileProfile`.
- **Currency / comma / parenthesis / negative cleanup** — `clean_amount_series`
  strips `$`, commas, and spaces, converts parentheses to a negative sign, and
  normalizes a unicode minus to ASCII; blanks pass through unchanged (no rows are
  dropped).
- **Repeated-header detection** — `detect_repeated_header_rows` finds data rows
  equal to the (snake_cased) header.
- **Footer / total-row detection** — `detect_footer_total_rows` finds
  total / subtotal / grand-total label rows, or mostly-blank rows carrying a lone
  amount.
- **Duplicate-row detection** — duplicate row groups are recorded positionally in
  the `FileProfile`.
- **Description normalization** — `normalize_description` collapses whitespace and
  strips (kept human-readable; not case-folded).
- **Source-row preservation** — every normalized record keeps a `SourceRowRef`;
  the row indices in `FileProfile` (repeated headers, footers, duplicates) and in
  all findings are 0-based positional indices in the parsed frame.

These are detection-and-flagging aids. The engine never silently "fixes" data in
a way that would change a calculation without the user's knowledge — messy
structural conditions (subtotals embedded in detail, sign conventions, batch
deposits) are surfaced as PARTIAL conditions for human review, not auto-resolved.

## What remains UNSUPPORTED (per workflow)

Each workflow declares `partially_supported_patterns` (detected and flagged as
PARTIAL) and `unsupported_patterns` (out of scope for the deterministic core).
Conditions that are detected but not handled include:

- **Bank reconciliation** — many-to-one **batch matching** (one deposit covering
  several ledger items) and **multi-currency** reconciliation are unsupported;
  sign-convention mismatches, likely batch deposits, and prior-period items are
  flagged as PARTIAL.
- **Budget variance** — **account rollup hierarchies** (subtotals/parents
  embedded in detail) and **budget-basis conversion** (annual vs YTD) are
  unsupported; likely rollups and basis mismatches are flagged as PARTIAL.
- **Report review** — **wide / pivoted** report layouts and **multi-level nested
  rollups** are unsupported; unrecognized line-type values and single-level
  rollups with extra labels are flagged as PARTIAL.
- **Unknown / pivoted report structures** — when no single long amount column can
  be identified (e.g. a pivoted report with several numeric period columns), the
  structure is flagged (`UNSUPPORTED_PATTERN_DETECTED` /
  `POSSIBLE_UNKNOWN_REPORT_STRUCTURE`) rather than mis-parsed.

These detectors are conservative: on clean data they return no findings and never
block a passing run, and a crashing detector is caught by the engine so it can
never break preflight.

## How to fix a failed (or partial) run

The report's `next_steps` give the concrete actions. In general:

- **Missing required file** — provide the missing input (e.g. the `ledger` file
  for bank reconciliation).
- **Missing / unparseable required column** — add the required column to the
  file, or **approve a column mapping**: tell the workflow which existing column
  is the `date` / `amount` / `fund` / etc. Human-approved mappings are accepted
  via `--mappings` on the CLI or the mapping UI on the Streamlit Run Workflow
  page, and are forced on the workflow (the engine auto-detects the rest).
- **Ambiguous column** — approve the mapping for the ambiguous semantic so the
  workflow does not have to guess between two candidates.
- **Low parse confidence on amounts/dates** — clean the data so the column parses
  (consistent date format, plain numbers with the currency/paren cleanup the tool
  already applies), or map to a cleaner column.
- **PARTIAL conditions** — review the flagged condition manually; the
  deterministic results for the supported checks are still produced and the AI
  draft explains only those findings.

A human-approved column mapping is requested **only when a required column is
ambiguous or unresolved** — confidently auto-detected columns are mapped silently
and are not surfaced for manual mapping.

## Per-workflow capability table

The values below are the workflows' `CAPABILITY` specs as implemented
(`src/workflows/*.py`). All four accept `csv` / `xlsx` data files
(freeform also accepts `txt` / `md` / `pdf` / `json` as metadata-only uploads).

### bank_reconciliation

| field | value |
| --- | --- |
| required inputs | `bank`, `ledger` |
| optional inputs | `chart_of_accounts` |
| required semantic columns | `bank` → `date`, `amount`; `ledger` → `date`, `amount` |
| optional semantic columns | `bank` → `description`; `ledger` → `description` |
| supported patterns | exact 1:1 amount+date match within tolerance, potential timing difference, within-table duplicate detection, unmatched bank items, unmatched ledger items |
| partial (flagged) patterns | `possible_sign_convention_mismatch`, `possible_batch_matching`, `possible_prior_period_items` |
| unsupported patterns | `many_to_one_batch_matching`, `multi_currency_reconciliation` |

### budget_variance

| field | value |
| --- | --- |
| required inputs | `budget`, `actuals` |
| optional inputs | `chart_of_accounts` |
| required semantic columns | `budget` → `fund`, `amount`; `actuals` → `fund`, `amount` |
| optional semantic columns | `budget`/`actuals` → `account_code`, `department`, `object`; `chart_of_accounts` → `fund`, `account_code`, `department`, `object` |
| supported patterns | join by fund/account/department/object, dollar variance, pct variance, threshold flags, budget-only / actual-only / missing |
| partial (flagged) patterns | `possible_account_rollup`, `possible_budget_basis_mismatch` |
| unsupported patterns | `account_rollup_hierarchies`, `budget_basis_conversion_annual_vs_ytd` |

### report_review

| field | value |
| --- | --- |
| required inputs | `report_table` |
| optional inputs | `chart_of_accounts`, `prior_version` |
| required semantic columns | `report_table` → `section`, `line_type`, `amount` |
| optional semantic columns | `report_table` → `account_code`, `account_name`; `chart_of_accounts` → `account_code`, `account_name`; `prior_version` → `account_code`, `amount` |
| supported patterns | long layout (section/line_type/amount), subtotal vs line item, missing required section, duplicate account line, invalid account code, prior-version change, inconsistent account naming |
| partial (flagged) patterns | `unrecognized_line_type_values`, `single_level_rollup_with_extra_labels` |
| unsupported patterns | `wide_pivoted_report`, `multi_level_nested_rollup` |

### freeform

| field | value |
| --- | --- |
| required inputs | none (inputs are structured fields, not tabular files) |
| optional inputs | none (optional uploads are metadata-only) |
| required semantic columns | none |
| optional semantic columns | none |
| supported patterns | structured request logging, attached-file metadata capture, draft-only plain-language output, clarifying questions & review steps, task-type discovery logging |
| partial (flagged) patterns | `possible_unknown_report_structure` |
| unsupported patterns | `authoritative_financial_answers`, `taking_over_a_failed_formal_workflow`, `calculation_or_matching` |

### transaction_search

The `query` is a string input, not a file; the data inputs are optional. The
two blocking conditions below are emitted by `detect_conditions`, not the
generic engine (the generic engine only knows about file inputs).

| field | value |
| --- | --- |
| required inputs | none (query is a string field) |
| optional inputs | `gl_detail`, `ap_invoices`, `checks`, `purchase_orders` |
| accepted file types | csv, xlsx |
| required semantic columns | none (Tyler normalizer handles column detection per dataset type) |
| supported patterns | vendor substring/fuzzy match, invoice/PO/check number exact match, fund/org/object exact match, date range, amount range, description keyword (OR semantics), multi-file search |
| partial (flagged) patterns | `multi_module_cross_search` |
| unsupported patterns | `free_text_narrative_match_without_keywords`, `semantic_similarity_search` |
| blocking FAIL conditions | blank/empty `query`; no data file provided (these are domain conditions, not generic preflight findings) |

### ap_duplicate_review

| field | value |
| --- | --- |
| required inputs | `ap_invoices` |
| optional inputs | `vendor_list`, `check_register`, `purchase_orders` |
| accepted file types | csv, xlsx |
| required semantic columns | `ap_invoices` -> `vendor_number`, `vendor_name`, `invoice_number`, `invoice_amount` |
| optional semantic columns | `ap_invoices` -> `invoice_date`, `po_number`, `check_number`, `check_date`; `vendor_list` -> `vendor_number`, `status`; `check_register` -> `check_number`, `check_date`, `vendor_number` |
| supported patterns | duplicate_invoice_number (D1), invoice_paid_by_multiple_checks (D1b), same_vendor_same_amount_near_date (D2), similar_vendor_names_same_amount (D3), missing_po_over_threshold (D4), payment_before_invoice_date (D5), inactive_vendor_payment (D6), unknown_vendor_payment (D7), split_payment_pattern (D8) |
| partial (flagged) patterns | none (absent optional files produce INFO findings, not PARTIAL) |
| unsupported patterns | `multi_currency_payments`, `inter_fund_transfer_detection` |

When an optional file is absent, checks that depend on it are explicitly skipped
with an `INFO` finding. Void checks are excluded from D1b (void + reissue is a
normal workflow).

### je_upload_prep

| field | value |
| --- | --- |
| required inputs | `je_draft`, `chart_of_accounts` |
| optional inputs | `gl_detail`, `config` |
| accepted file types | csv, xlsx |
| required semantic columns | `je_draft` -> `journal`, `line`, `fund`, `org`, `object`; `chart_of_accounts` -> `fund`, `org`, `object` |
| optional semantic columns | `je_draft` -> `date`, `debit`, `credit`, `description`, `reference` |
| supported patterns | debits_equal_credits_per_journal, debits_equal_credits_overall, eff_date_required, eff_date_in_fiscal_period, required_fields_present, account_in_coa, no_inactive_account, no_both_debit_and_credit, no_negative_amount, no_duplicate_journal_line |
| partially supported patterns | `combo_plausibility_warning` (requires `gl_detail`) |
| unsupported patterns | none declared |

The domain `detect_conditions` returns `[]` (no advisory conditions). Blocking
conditions are enforced by the deterministic validator, not the preflight engine.
`je_upload.xlsx` and `je_upload.csv` are never written unless all blocking checks
pass (strict fail-closed gate).

### po_invoice_review

| field | value |
| --- | --- |
| required inputs | `purchase_orders`, `ap_invoices` |
| optional inputs | `vendor_list`, `check_register` |
| accepted file types | csv, xlsx |
| required semantic columns | `purchase_orders` -> `po_number`, `vendor_number`, `status`, `line`, `qty`, `unit_price`, `line_amount`; `ap_invoices` -> `vendor_number`, `invoice_number`, `invoice_amount` |
| optional semantic columns | `purchase_orders` -> `last_activity_date`, `po_date`, `received_qty`, `invoiced_qty`; `ap_invoices` -> `po_number`, `qty`, `unit_price`, `invoice_date` |
| supported patterns | invoice_exceeds_po (P1), wrong_vendor (P2), missing_po (P3a), missing_po_over_threshold (P3b), closed_po_usage (P4), unit_price_mismatch (P5), quantity_mismatch (P6), received_not_invoiced (P7), invoiced_not_received (P8) |
| partial (flagged) patterns | none |
| unsupported patterns | `multi_currency_po`, `framework_agreement_releases` |

When an optional file is absent, dependent sub-checks are explicitly skipped with
an `INFO` finding. P7 (received_not_invoiced) is informational (severity LOW,
`requires_human_review=False`): it flags likely accrual candidates, not payment
errors.

## Condition codes

The condition codes (`PreflightConditionCode` in `src/core/schemas.py`):
`MISSING_REQUIRED_FILE`, `UNSUPPORTED_FILE_TYPE`, `MISSING_REQUIRED_COLUMN`,
`AMBIGUOUS_COLUMN_MAPPING`, `LOW_DATE_PARSE_CONFIDENCE`,
`LOW_AMOUNT_PARSE_CONFIDENCE`, `POSSIBLE_SIGN_CONVENTION_MISMATCH`,
`POSSIBLE_BATCH_MATCHING`, `POSSIBLE_PRIOR_PERIOD_ITEM`,
`POSSIBLE_ACCOUNT_ROLLUP`, `POSSIBLE_BUDGET_BASIS_MISMATCH`,
`POSSIBLE_UNKNOWN_REPORT_STRUCTURE`, `UNSUPPORTED_PATTERN_DETECTED`,
`NEEDS_HUMAN_CONFIGURATION`. Each value is the snake_case of the member name.

## Source-row evidence (cross-workflow review surface)

Independent of preflight: every deterministic finding any workflow produces
carries source-row references (`DeterministicFinding.source_rows`, each a
`SourceRowRef` with the parsed `source_values`). On the review page these are
turned into reviewer-facing evidence by `src.core.evidence.build_finding_evidence`
— rows grouped by source document, the finding's salient fields highlighted, full
provenance (file name + recorded hash + absolute row index), and a clear missing-
counterpart signal for one-sided findings. It is deterministic and read-only (no
re-parsing, no matching, no LLM) and works for any finding that carries source
rows; see `docs/decisions.md` ("Source-row evidence viewer for findings").
