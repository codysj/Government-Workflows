# Workflow — AP Duplicate / Suspicious Payment Review

Module: `src/workflows/ap_duplicate_review.py`. Contains no Streamlit and no
provider-specific code; plugs into the shared pipeline.

## Purpose

Help finance staff detect duplicate payments, suspicious payment patterns, and
policy violations in Tyler/Munis-style Accounts Payable exports before payments
are finalized or reconciled. The LLM never decides whether fraud occurred; it
only summarizes and drafts plain-language review notes for the deterministically
flagged findings.

## Inputs

| key | required | description |
|-----|----------|-------------|
| `ap_invoices` | yes | Tyler ap_invoice_detail CSV/XLSX |
| `vendor_list` | optional | Tyler vendor_list CSV/XLSX (needed for D6/D7) |
| `check_register` | optional | Tyler check_register CSV/XLSX (needed for D1b/D5) |
| `purchase_orders` | optional | Tyler purchase_orders CSV/XLSX (reserved for future PO checks) |
| `config` | optional | path to JSON config or dict of threshold overrides |

When an optional file is absent its checks are skipped with an explicit
`INFO` finding — never silently.

## Config keys (all optional)

| key | default | description |
|-----|---------|-------------|
| `near_date_window_days` | 7 | window for D2 same-vendor/amount near-date check |
| `amount_tolerance` | 0.01 | Decimal tolerance for "same amount" comparisons |
| `split_threshold` | 5000.00 | per-invoice threshold for D4 (missing PO) and D8 (split payment) |
| `split_window_days` | 3 | date window for D8 split-payment group |
| `missing_po_min_amount` | 5000.00 | minimum invoice amount to trigger D4 |
| `vendor_similarity_threshold` | 0.88 | difflib ratio (0–1) for D3 vendor-name similarity |

## Deterministic checks

| rule_used | check | severity | requires optional |
|-----------|-------|----------|-------------------|
| `duplicate_invoice_number` | D1: same vendor_number + invoice_number on 2+ AP rows | high | — |
| `invoice_paid_by_multiple_checks` | D1b: one invoice number appears in 2+ non-void check register rows | high | check_register |
| `same_vendor_same_amount_near_date` | D2: same vendor + same amount + different invoice_number within `near_date_window_days` | medium | — |
| `similar_vendor_names_same_amount` | D3: two vendor numbers whose names have difflib ratio >= threshold, with same-amount invoices in window | medium | — |
| `missing_po_over_threshold` | D4: invoice >= `missing_po_min_amount` with blank/missing PO number | medium | — |
| `payment_before_invoice_date` | D5: check date (from check_register) earlier than invoice date | medium | check_register |
| `inactive_vendor_payment` | D6: invoice from a vendor with Status=Inactive in vendor_list | high | vendor_list |
| `unknown_vendor_payment` | D7: vendor_number on invoice absent from vendor_list entirely | high | vendor_list |
| `split_payment_pattern` | D8: 2+ invoices from same vendor each < `split_threshold`, totaling >= `split_threshold`, within `split_window_days` | high | — |

Every finding is a `DeterministicFinding` carrying `source_rows`
(`SourceRowRef` per involved AP row), `computed_values` (including a stable
`group_id` for grouped findings), `rule_used`, and `requires_human_review=True`.

Notes:
- Void checks (Status=Void in check_register) are excluded from D1b detection;
  a void + its reissue is a normal workflow, not a suspicious multi-payment.
- D3 vendor-name comparison strips common legal suffixes (LLC, Inc, Co, Company,
  Corp) before computing the difflib ratio.

## LLM tasks (advisory only)

Summarize the flagged issues, draft plain-language review notes for each
exception category, suggest human follow-up steps, and draft a review memo.
Must cite source-row ids; must not conclude fraud, calculate new figures, or
produce approval language. Prompt builder `build_prompt`; template
`ap_duplicate_review.v1`. Mock mode is the default path (no API key / no internet).

## Validation (deterministic, Phase 2 rules)

`validate_llm_output` rejects invented source-row references and
final-approval language; missing references are treated as warnings (not
errors) because INFO/skip findings carry no source rows.

## Export artifacts

`flagged_payments.csv`, `duplicate_groups.csv`, `ap_review_summary.md`,
`review_notes_draft.md`, `validation_report.json`, `audit_log.json`
(via `export_artifacts`).

## Integration

- `WORKFLOW_TYPE = "ap_duplicate_review"` is the registry key.
- `run(inputs, *, provider=None, ledger=None, audit=None, run_id=None,
  actor="system", export_dir=None, config=None) -> dict` is the entry point.
  Injected provider/ledger/audit are used when present (duck-typed); otherwise
  self-contained mock/validation/export fallbacks keep it runnable offline.
- `register(registry)` supports both `registry[type] = run` and
  `registry.register(type, run)` styles.
- `WORKFLOW` dict exposes metadata.
- `SAMPLE_INPUTS` dict provides repo-relative default sample paths for the
  wiring agent / demo UI.

## Preflight and unsupported conditions

`CAPABILITY` (a `CapabilitySpec`):

- required inputs: `ap_invoices`; optional: `vendor_list`, `check_register`, `purchase_orders`
- accepted file types: `csv`, `xlsx`
- required semantic columns: `ap_invoices` -> `vendor_number`, `vendor_name`,
  `invoice_number`, `invoice_amount`
- optional semantic columns: `ap_invoices` -> `invoice_date`, `po_number`,
  `check_number`, `check_date`; `vendor_list` -> `vendor_number`, `status`;
  `check_register` -> `check_number`, `check_date`, `vendor_number`
- supported patterns: duplicate_invoice_number,
  same_vendor_same_amount_near_date, similar_vendor_names_same_amount,
  missing_po_over_threshold, payment_before_invoice_date, inactive_vendor_payment,
  unknown_vendor_payment, split_payment_pattern
- unsupported patterns: multi_currency_payments,
  inter_fund_transfer_detection

A FAIL (e.g. the required `ap_invoices` file absent) refuses the run and
returns a structured report with next steps; the LLM is never called.

## Synthetic known-answer data

`data/synthetic/tyler/` contains the City of Riverbend dataset with planted
anomalies D1-D8 (see `data/synthetic/tyler/known_answers.json`).

`data/synthetic/ap_duplicate_review/` contains:
- `review_config.json` — default thresholds for use in demos and CI
- `clean_ap_invoices.csv` — a minimal 3-row clean AP file for true-negative tests

Tests: `tests/unit/test_ap_duplicate_review.py` (run with
`--basetemp=.pytest_tmp_ap_dup` to avoid concurrent-agent temp-dir collisions).
