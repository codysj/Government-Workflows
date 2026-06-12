# Workflow — Natural-Language Transaction Search

Module: `src/workflows/transaction_search.py`.  
No Streamlit, no provider-specific code; plugs into the shared pipeline.

## Purpose

Lets finance staff describe the transactions they want to find in plain English
("payments to Cascade Paving over $5,000 between March and May 2026") and
returns matched rows with full source-row traceability.  The LLM's role is
limited to proposing structured ``SearchCriteria``; all filtering is
deterministic Python/pandas.

## Two-stage flow

### Stage 1 — Intent Parse (LLM-proposed, deterministically validated)

The LLM (or the deterministic mock parser on the offline path) is asked to
propose a ``SearchCriteria`` JSON object.  The proposal is:

1. **Schema-validated** by pydantic v2 (`SearchCriteria` model):
   - `module` must be one of `gl`, `ap`, `ap_invoice_detail`, `checks`,
     `check_register`, `po`, `purchase_orders`.
   - `date_from` / `date_to` must be ISO dates.
   - `amount_min` / `amount_max` must be non-negative decimals.
2. **Range-sanity-checked** deterministically: `date_from <= date_to`,
   `amount_min <= amount_max`.
3. **Invalid fields** are dropped to `unparsed_terms` (never silently
   executed); the run continues with the surviving criteria.

If **nothing parseable** is extracted the workflow returns a structured report
(`criteria_parse_failed=True`) without executing a search.

### Stage 2 — Deterministic Execution

Files are loaded via `normalize_tyler_export` (free SHA-256 + `source_row_index`
traceability).  Filters applied in order:

| Criterion | Filter rule |
|-----------|-------------|
| `module`  | Skip files whose dataset_type does not match |
| `vendor`  | Casefold substring match **or** fuzzy (difflib ratio ≥ 0.85) |
| `invoice_number` | Exact after casefold / punctuation strip |
| `po_number` | Exact after casefold |
| `check_number` | Exact after casefold |
| `fund` | Exact after casefold |
| `department` | Exact after casefold (mapped to `org`) |
| `object_code` | Exact after casefold (mapped to `object`) |
| `date_from` / `date_to` | Row date column falls within range |
| `amount_min` / `amount_max` | Absolute value of best amount column within range |
| `keywords` | ALL keywords are substrings of any text column (casefold) |

Findings are capped at `max_results` (default 200) with an explicit SUMMARY
truncation finding.

## Inputs

| key | required | description |
|-----|----------|-------------|
| `query` | **yes** (non-blank str) | Plain-English search query |
| `gl_detail` | optional | Tyler GL detail CSV/XLSX |
| `ap_invoices` | optional | Tyler AP invoice detail CSV/XLSX |
| `checks` | optional | Tyler check register CSV/XLSX |
| `purchase_orders` | optional | Tyler purchase orders CSV/XLSX |

At least one data file is required.  Both constraints are enforced by
`detect_conditions` (blocking FAIL finding).

## Config dict keys

| key | type | default | description |
|-----|------|---------|-------------|
| `max_results` | int | 200 | Cap on `SEARCH_MATCH` findings |
| `fuzzy_threshold` | float | 0.85 | difflib SequenceMatcher ratio cutoff |
| `missing_po_threshold` | str | "5000.00" | Carried for cross-workflow consistency |

## SearchCriteria fields

```
vendor           str | null    — vendor name fragment
invoice_number   str | null    — exact invoice number
po_number        str | null    — exact PO number
check_number     str | null    — exact check number
fund             str | null    — fund code (e.g. "300")
department       str | null    — department / org code
object_code      str | null    — object code
account          str | null    — full account string
module           str | null    — one of gl/ap/ap_invoice_detail/checks/check_register/po/purchase_orders
date_from        str | null    — ISO date YYYY-MM-DD
date_to          str | null    — ISO date YYYY-MM-DD
amount_min       str | null    — non-negative decimal
amount_max       str | null    — non-negative decimal
keywords         list[str]     — description keyword substrings (ALL must match)
unparsed_terms   list[str]     — tokens rejected by validation
llm_proposed_fields list[str] — which fields came from the LLM vs defaulted
```

## LLM tasks (advisory only)

- **Stage 1**: propose `SearchCriteria` JSON from the plain-English query.
  Must not calculate, must not invent vendor names or account numbers, must
  leave fields null when not stated.
- **Stage 2** (summary): explain what was found, suggest review steps.
  Must cite source-row ids; must not compute new figures.

Prompt template version: `transaction_search.v1`.  Mock is the default path.

## Validation (deterministic, Phase 2 rules)

`validate_llm_output` rejects invented source-row references (rule a), warns on
numeric claims not in deterministic findings (rule c), and rejects final-
approval language (rule d).  Missing references are an error when there are
matches (a non-empty result set implies the LLM should cite rows), a warning
otherwise.

## Preflight & unsupported conditions

`CAPABILITY` (CapabilitySpec):
- required inputs: *(none — query is a string, not a file)*
- optional inputs: `gl_detail`, `ap_invoices`, `checks`, `purchase_orders`
- accepted file types: `csv`, `xlsx`

`detect_conditions` emits **blocking FAIL** findings (non-expressible in
CapabilitySpec) for:
- blank / empty `query`
- no data file provided

FAIL → do NOT run, do NOT call the LLM; return structured report with
`next_steps`.

Partially supported:
- `multi_module_cross_search` — searching across >1 file type simultaneously
  (supported deterministically but the LLM commentary may be less precise)

Unsupported:
- `free_text_narrative_match_without_keywords` — pure narrative text search
  with no extractable criteria
- `semantic_similarity_search` — embedding / ML-based similarity

## Export artifacts

| file | description |
|------|-------------|
| `search_criteria.json` | Validated `SearchCriteria` dict, `unparsed_terms`, `llm_proposed_fields` |
| `search_results.csv` | One row per match: source_file, source_row_index, module, matched_fields, all columns |
| `search_summary.md` | Human-readable summary + AI draft + unparsed terms |
| `validation_report.json` | `ValidationResult` JSON |
| `audit_log.json` | Audit events for this run |

All written via `src.core.exports` shared primitives (SHA-256 hashed,
`ExportArtifact` manifests stored in the run ledger).

## Integration

- `WORKFLOW_TYPE = "transaction_search"` is the registry key.
- `run(inputs, *, provider=None, ledger=None, audit=None, run_id=None, actor="system", export_dir=None, config=None) -> dict`
  is the entry point; returns `run_id`, `workflow_type`, `findings`, `summary`,
  `validation`, `preflight`, and (when `export_dir` is set) `export_paths` +
  `export_artifacts`.
- `register(registry)` supports both `registry[type] = run` and
  `registry.register(type, run)` styles.
- `WORKFLOW` dict exposes metadata; `SAMPLE_INPUTS` gives repo-relative default
  sample paths.

## Synthetic known-answer data — `data/synthetic/tyler/`

The four Tyler CSV files (gl_detail, ap_invoice_detail, check_register,
purchase_orders) and `known_answers.json` (Q1-Q4) serve as the ground truth for
this workflow.

| query | expected module | expected row count |
|-------|----------------|--------------------|
| Q1 — Cascade Paving over $5k Mar-May 2026 | ap_invoice_detail | 2 (rows 48, 49) |
| Q2 — invoice INV-10234 | ap_invoice_detail | 2 (rows 46, 47) |
| Q3 — Acme Office Supply checks Feb 2026 | check_register | 2 (rows 45, 49) |
| Q4 — pothole repair fund 300 | gl_detail | 3 (rows 71, 72, 73) |

Tests: `tests/unit/test_transaction_search.py`.
