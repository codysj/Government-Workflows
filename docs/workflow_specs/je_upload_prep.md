# Workflow — Journal Entry Upload Prep

Module: `src/workflows/je_upload_prep.py`. Contains no Streamlit and no
provider-specific code; plugs into the shared pipeline.

## Purpose

Validate a draft journal entry file against the chart of accounts and fiscal
configuration, then produce a ready-to-upload Excel/CSV workbook (or a
structured error report if any blocking validation fails). Strictly fail-closed:
no upload workbook is written unless every blocking check passes.

## Inputs

| key | required | description |
|-----|----------|-------------|
| `je_draft` | yes | Draft JE CSV or XLSX using Munis template headers or aliases (see `je_upload` dataset type in `src/ingest/tyler.py`) |
| `chart_of_accounts` | yes | Tyler COA CSV or XLSX (columns: `Fund`, `Org`, `Object`, `Status`, ...) |
| `gl_detail` | optional | Tyler GL detail CSV or XLSX for fund/org/object combo-plausibility history |
| `config` | optional | Path to `je_config.json` (see config keys below) |

Draft column names are normalized via the `je_upload` Tyler dataset type
(accepts aliases: `Eff Date` -> `date`, `eff_date`, `je_date`; see full alias
list in `TYLER_DATASET_TYPES["je_upload"]`).

## Config keys (`je_config.json`)

| key | type | default | description |
|-----|------|---------|-------------|
| `fiscal_period_start` | ISO date string | `"2025-07-01"` | Earliest valid Eff Date |
| `fiscal_period_end` | ISO date string | `"2026-06-30"` | Latest valid Eff Date |
| `allow_inactive_accounts` | bool | `false` | When `true`, Inactive account codes do not block upload |
| `round_dollar_warning_threshold` | string/Decimal | `"10000.00"` | Round-dollar amounts >= this emit a warning |
| `min_description_length` | int | `3` | Line descriptions shorter than this emit a warning |

## Deterministic validation (all calculations live here)

### Blocking errors (any one prevents upload workbook generation)

| `rule_used` | severity | what it flags |
|-------------|----------|---------------|
| `debits_equal_credits_per_journal` | critical | Total debits != total credits within one journal number (Decimal, 2dp) |
| `debits_equal_credits_overall` | critical | Total debits != total credits across all journals |
| `eff_date_required` | high | Eff Date is blank or missing |
| `eff_date_valid` | high | Eff Date string cannot be parsed as a calendar date (handled by Tyler normalizer fail-close) |
| `eff_date_in_fiscal_period` | high | Eff Date falls outside configured `fiscal_period_start`..`fiscal_period_end` |
| `required_fields_present` | high | Missing required field: Fund, Org, Object, or neither Debit nor Credit populated |
| `account_in_coa` | high | Fund, Org, or Object code absent from chart of accounts |
| `no_inactive_account` | high | Account `fund-org-object` has `Status=Inactive` (unless `allow_inactive_accounts=true`) |
| `no_both_debit_and_credit` | high | Both Debit and Credit are populated on the same line |
| `no_negative_amount` | high | Debit or Credit value is negative |
| `no_duplicate_journal_line` | high | Duplicate `(Journal, Line)` combination |

### Warnings (do not block; `requires_human_review=true`)

| `rule_used` | severity | what it flags |
|-------------|----------|---------------|
| `combo_plausibility` | medium | `fund/org/object` combination not in COA or GL history (each segment is valid, but the combination is new) |
| `description_too_short` | low | Line Description shorter than `min_description_length` characters |
| `round_dollar_large_amount` | low | Round-dollar line amount >= `round_dollar_warning_threshold` |

## Fail-closed behavior

If ANY blocking error is found:
- `upload_ready=false` in the result summary.
- `je_upload.xlsx` and `je_upload.csv` are **NOT written**.
- Only `je_validation_errors.csv`, `validation_report.json`,
  `je_prep_summary.md` (stating INVALID and reasons), `audit_log.json`, and
  preflight/review-packet artifacts are written.

## On success (all blocking checks pass)

- `je_upload.xlsx` — openpyxl workbook, sheet `"JE Upload"`, exactly the Munis
  template headers: `Journal, Line, Eff Date, Fund, Org, Object, Account Description, Debit, Credit, Line Description, Reference`.
- `je_upload.csv` — identical content, CSV format.
- `source_mapping.csv` — maps every upload row to its draft-file `source_row_index`.
- `je_prep_summary.md` — validation summary.
- `je_validation_errors.csv` — all findings (including warnings).
- `validation_report.json`, `audit_log.json`.

All artifacts are hashed as `ExportArtifact` with sha256.

## LLM tasks (advisory only)

Drafts a plain-language review summary of the deterministic validation findings
(clearly labelled DRAFT; cites source rows). The LLM never sees or edits the
upload workbook contents and never fixes data. Prompt builder
`build_je_upload_prep_prompt`; template `je_upload_prep.v1`. Mock mode is the
default path (no API key / no internet).

## Validation (deterministic, Phase 2 rules)

`validate_llm_output` rejects invented source-row references and final-approval
language. Numeric-claim checking is disabled (amounts in the summary are
sufficient coverage). Sets `invented_reference_detected` and `numeric_claims_checked`.

## Export artifacts

`je_upload.xlsx` (success only), `je_upload.csv` (success only),
`source_mapping.csv` (success only), `je_prep_summary.md`,
`je_validation_errors.csv`, `validation_report.json`, `audit_log.json`.

## Integration

- `WORKFLOW_TYPE = "je_upload_prep"` is the registry key.
- `run(inputs, *, provider=None, ledger=None, audit=None, run_id=None, actor="system", export_dir=None, config=None)` is the entry point. Injected provider/ledger/audit are used when present (duck-typed); otherwise self-contained mock/validation/export fallbacks keep it runnable.
- `register(registry)` supports both `registry[type] = run` and `registry.register(type, run)` styles. `WORKFLOW` dict and `SAMPLE_INPUTS` dict expose metadata.

## Preflight & unsupported conditions

This workflow exposes `CAPABILITY: CapabilitySpec` and `detect_conditions(...)`.
The preflight engine handles missing required files/columns (blocking). The
domain `detect_conditions` returns `[]` (no advisory conditions; blocking
conditions are handled by the deterministic validator, not preflight).

`CAPABILITY`:
- required inputs: `je_draft`, `chart_of_accounts`; optional: `gl_detail`, `config`
- accepted file types: `csv`, `xlsx`
- required semantic columns: `je_draft` -> `journal, line, fund, org, object`;
  `chart_of_accounts` -> `fund, org, object`
- supported patterns: full list of blocking validation rules above
- partially supported: `combo_plausibility_warning` (requires `gl_detail`)

A FAIL (e.g. required file absent) refuses the run before the LLM is called and
returns a structured report with next steps.

## Synthetic known-answer data — `data/synthetic/je_upload_prep/`

`je_draft_valid.csv`, `je_draft_invalid.csv`, `je_draft_warnings.csv`,
`je_config.json`.

| file | expected result | planted items |
|------|-----------------|---------------|
| `je_draft_valid.csv` | `upload_ready=true`; xlsx+csv written; debits==credits | 3 journals, 7 rows, all balanced with Active accounts |
| `je_draft_invalid.csv` | `upload_ready=false`; NO upload files; all 7 defects fire | See README.md for exact row-by-row defect list |
| `je_draft_warnings.csv` | `upload_ready=true`; 1 warning (combo_plausibility 100/5200/5240) | Combo not in COA or GL history |

Tests: `tests/unit/test_je_upload_prep.py` (run with project-local `--basetemp`
to avoid collisions with concurrent agents on Windows).
