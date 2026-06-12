# Workflow -- PO / Invoice Mismatch Review

Module: `src/workflows/po_invoice_review.py`. Contains no Streamlit and no
provider-specific code; plugs into the shared pipeline.

## Purpose

Help finance staff detect PO/invoice mismatches, quantity discrepancies,
closed-PO billings, and missing purchase-order references in Tyler/Munis-style
exports before payments are approved. The LLM never decides whether an invoice
is improper; it only summarizes flagged issues and drafts plain-language review
notes with suggested human follow-up steps.

## Inputs

| key | required | description |
|-----|----------|-------------|
| `purchase_orders` | yes | Tyler purchase_orders CSV/XLSX |
| `ap_invoices` | yes | Tyler ap_invoice_detail CSV/XLSX |
| `vendor_list` | optional | Tyler vendor_list CSV/XLSX (needed for P2 vendor-name similarity note) |
| `check_register` | optional | Tyler check_register CSV/XLSX (reserved for cross-check; absence emits INFO) |
| `config` | optional | path to JSON config or dict of threshold overrides |

When an optional file is absent its dependent sub-checks are skipped with an
explicit `INFO` finding -- never silently.

## Config keys (all optional)

| key | default | description |
|-----|---------|-------------|
| `unit_price_tolerance_pct` | 1.0 | percent tolerance for P5 unit-price comparison |
| `qty_tolerance` | 0 | unit tolerance for P6 quantity comparison |
| `invoice_over_po_tolerance_pct` | 0.0 | percent tolerance for P1 total-invoiced vs PO-total |
| `closed_po_grace_days` | 0 | grace days added to last_activity_date for P4 |
| `missing_po_min_amount` | 5000.00 | minimum invoice amount to trigger P3b (blank PO + over threshold) |

## Deterministic checks

| rule_used | check | severity | requires optional |
|-----------|-------|----------|-------------------|
| `invoice_exceeds_po` | P1: total invoiced against a PO > PO total * (1 + tolerance) | high | -- |
| `wrong_vendor` | P2: invoice vendor_number != PO vendor_number; if vendor_list present, notes when names are similar | high | vendor_list (for similarity note) |
| `missing_po` | P3a: invoice references a PO number absent from the PO file | high | -- |
| `missing_po_over_threshold` | P3b: invoice with blank PO number and amount >= missing_po_min_amount | medium | -- |
| `closed_po_usage` | P4: invoice date > PO last_activity_date + grace_days when PO Status=Closed | medium | -- |
| `unit_price_mismatch` | P5: invoice unit price differs from PO line unit price beyond tolerance | medium | -- |
| `quantity_mismatch` | P6: invoice qty > PO line ordered qty beyond qty_tolerance | medium | -- |
| `received_not_invoiced` | P7: PO line received_qty > 0, invoiced_qty == 0, no matching AP invoice (accrual candidate, informational) | low | -- |
| `invoiced_not_received` | P8: PO line invoiced_qty > received_qty | medium | -- |

Notes:
- P1 uses the SUM of all AP invoice amounts for a PO number vs the SUM of all
  PO line_amounts; `comparison_level` in `computed_values` is set to `po_total`.
- P5/P6 use per-invoice qty/unit_price detail when present; `comparison_level`
  is set to `line_level`. The best matching PO line is identified by qty match,
  falling back to line 1.
- P7 is informational (`severity=low`, `requires_human_review=False`); it
  flags likely accrual candidates, not payment errors.
- Every finding carries `SourceRowRef` entries for BOTH the invoice row(s) AND
  the PO row(s) involved, anchored to `source_row_index` (0-based data-row
  position in the normalized export).

## LLM tasks (advisory only)

Summarize the flagged issues, draft plain-language review notes for each
exception category, suggest human follow-up steps (e.g. "confirm receipt with
department"), and draft a review memo. Must cite source-row ids; must not
recalculate, declare an invoice improper, or produce approval language. Prompt
builder `build_prompt`; template `po_invoice_review.v1`. Mock mode is the
default path (no API key / no internet).

## Validation (deterministic, Phase 2 rules)

`validate_llm_output` rejects invented source-row references and
final-approval language; missing references are treated as warnings (not
errors) because INFO/skip findings carry no source rows.

## Export artifacts

`po_invoice_exceptions.csv`, `matched_po_invoices.csv`,
`po_review_summary.md`, `review_notes_draft.md`, `validation_report.json`,
`audit_log.json` (via `export_artifacts`).

## Integration

- `WORKFLOW_TYPE = "po_invoice_review"` is the registry key.
- `run(inputs, *, provider=None, ledger=None, audit=None, run_id=None,
  actor="system", export_dir=None, config=None) -> dict` is the entry point.
  Injected provider/ledger/audit are used when present (duck-typed); otherwise
  self-contained mock/validation/export fallbacks keep it runnable offline.
- `register(registry)` supports both `registry[type] = run` and
  `registry.register(type, run)` styles.
- `WORKFLOW` dict exposes metadata.
- `SAMPLE_INPUTS` dict provides repo-relative default sample paths for the
  wiring agent / demo UI.

## Preflight and fail-closed behaviour

`CAPABILITY` (a `CapabilitySpec`):

- required inputs: `purchase_orders`, `ap_invoices`; optional: `vendor_list`,
  `check_register`
- accepted file types: `csv`, `xlsx`
- required semantic columns: `purchase_orders` -> `po_number`, `vendor_number`,
  `status`, `line`, `qty`, `unit_price`, `line_amount`; `ap_invoices` ->
  `vendor_number`, `invoice_number`, `invoice_amount`
- optional semantic columns: `purchase_orders` -> `last_activity_date`,
  `po_date`, `received_qty`, `invoiced_qty`; `ap_invoices` -> `po_number`,
  `qty`, `unit_price`, `invoice_date`
- supported patterns: invoice_exceeds_po, wrong_vendor, missing_po_number,
  closed_po_usage, unit_price_mismatch, quantity_mismatch,
  received_not_invoiced, invoiced_not_received, missing_po_over_threshold
- unsupported patterns: multi_currency_po, framework_agreement_releases

A FAIL (e.g. a required input file absent) refuses the run, writes
`preflight_report.json` + `preflight_summary.md`, and returns a structured
report with next steps; the LLM is never called.

## Synthetic known-answer data

`data/synthetic/tyler/` contains the City of Riverbend dataset with planted
anomalies P1-P8 (see `data/synthetic/tyler/known_answers.json`).

`data/synthetic/po_invoice_review/` contains:
- `match_config.json` -- default tolerances for use in demos and CI

Tests: `tests/unit/test_po_invoice_review.py` (run with
`--basetemp=.pytest_tmp_po_inv` to avoid concurrent-agent temp-dir collisions).
