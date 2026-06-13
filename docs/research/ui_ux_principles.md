# UI/UX Principles for the Guided Workflow Console

Audience: the frontend agent building `frontend/` (React/Vite/TS) and anyone reviewing its
output. Every principle below ends with "Applied here:" - the specific, binding implication
for THIS product. The buildable screen-by-screen spec is `docs/frontend/ux_spec.md`; this
document explains the *why* behind its decisions. All examples use the real workflow names,
field keys, finding types, and artifact names from the codebase.

Source grounding: `app/streamlit_app.py`, `app/workflow_registry.py`, `app/preflight_views.py`,
`app/role_views.py`, `docs/workflow_capabilities.md`, `src/core/review_packet.py`,
`src/core/schemas.py`.

---

## 1. Design for the busy, non-technical, accountable user

Municipal finance staff (AP clerks, accountants, analysts, directors) are domain experts but
not software experts. They are personally accountable for the numbers they sign off on, work
under audit and public-records scrutiny, and use the tool occasionally - not daily - so they
re-learn it every time.

**Applied here:** Every screen must be self-explanatory on a cold open. No jargon from the
codebase leaks into the UI (`preflight`, `deterministic`, `LLM`, `validation result`,
`artifact`, `actor`, `mock mode` are all internal terms - see the terminology table in the
spec). Default paths must be obvious: one primary button per screen, sample data one click
away, and the next step always stated in words ("Fix the items below, then check the files
again").

**Example:** the Streamlit history table shows `run_id` truncated to 8 hex chars as the first
column. The React History page leads with the workflow title and date ("AP duplicate review -
Jun 12, 2026, 9:14 AM") and shows the run ID as secondary metadata, because staff recognize
tasks and dates, not hashes.

## 2. Progressive disclosure: lead with the decision, bury the evidence one click deep

Show the minimum needed to decide "is this fine / does this need attention", and put detail
(source rows, hashes, JSON, audit events) behind expanders. Never delete information -
everything stays reachable, which matters for audit.

**Applied here:** The Review Run screen opens with a summary strip (finding counts by
severity, AI safety-check badge, review status) before any table. Each finding is a one-line
card; clicking expands the source-row evidence (`source_rows[].table_name`, `row_index`,
`source_values`). The full AI response JSON, file hashes, and the audit trail are collapsed
sections, exactly as Streamlit already does with `st.expander` - keep that instinct, but make
the first screenful far less dense than Streamlit's wall of `st.dataframe`s.

**Example:** a `bank_reconciliation` run with 3 high-severity unmatched items and 40
`matched` info findings should show "3 items need attention" prominently and "40 matched
items" as a collapsed group, mirroring `role_views.order_findings_for_role` (severity
emphasis without destructive hiding).

## 3. Wizard for the run flow: one decision per step, with a hard gate

Multi-input tasks with a validity gate are wizard-shaped. A wizard prevents the #1 failure
mode of the current Streamlit Run Workflow page: a single long form where the preflight
check, the column-mapping UI, the retention selector, and the Run button all compete at once.

**Applied here:** Four steps: 1 Choose workflow, 2 Provide inputs, 3 File check (preflight),
4 Run and review. Step 3 is a *gate*, not a suggestion: the Run button does not exist until a
preflight has been run, and it is disabled on `status:"fail"`. This mirrors the backend's
fail-closed contract (FAIL = workflow never runs, LLM never called) so the UI never promises
something the backend will refuse.

**Example:** `je_upload_prep` requires both `je_draft` and `chart_of_accounts`. Step 2 will
not let the user advance until both required slots have a file (or sample data is selected);
step 3 then explains any `missing_required_column` finding in plain language with the
backend's `next_steps`.

## 4. Reduce form overload: required first, optional behind a fold

Most workflows have 1-2 required uploads and 2-3 optional ones plus an optional JSON config.
Showing 5 upload boxes at equal weight (as Streamlit does) makes the task look harder than it
is and causes people to upload files they do not need.

**Applied here:** Step 2 renders only the required uploads at full size. Optional uploads and
config files live in a collapsed "Optional context" section with a one-line value statement
per field, taken from the descriptor help text (e.g. `je_upload_prep.gl_detail`: "Optional:
enables the fund/org/object combination plausibility warning"). `ap_duplicate_review` thus
shows ONE required upload (`ap_invoices`) and folds `vendor_list`, `check_register`,
`purchase_orders`, `config` away - with a note that skipped optional files simply skip the
checks that need them (the backend already emits INFO findings for those skips).

## 5. One-click sample path

A user's first run should require zero files. The synthetic example files
(`data/synthetic/...`) already exist for every workflow except freeform, and
`transaction_search` has a bundled example query ("payments to Cascade Paving over $5,000
between March and May 2026").

**Applied here:** Step 2 has a prominent "Use sample data" toggle/button driven by
`WorkflowInfo.has_sample`. Choosing it fills all slots, shows what was filled ("Sample bank
statement, sample ledger export"), pre-fills the example query for `transaction_search`, and
goes straight to the file check. This is the demo path and the training path; it must be the
easiest path on the screen.

## 6. Review-queue design: group by severity, name rules in plain language

A findings list is a work queue. Reviewers triage: worst first, similar items together,
clear "what do I do with this" actions on each item.

**Applied here:** Findings on Review Run are grouped by severity (`critical`, `high`,
`medium`, `low`, `info` - the real `Severity` enum), each group collapsible, worst group open
by default. Within a group, findings with the same `rule_used` cluster under a plain-language
rule heading. The raw `finding_type` / `rule_used` strings appear only as secondary code text.

**Example (ap_duplicate_review):** instead of a table row `duplicate_payment | D1 |
duplicate_invoice_number`, render a group header "Same invoice number billed twice (3
items)" with severity badge, and per-item lines like "INV-1042 from Cascade Paving, $5,200,
appears on rows 14 and 87 of your AP invoice file" - the amounts and row numbers come from
`computed_values` and `source_rows` (backend-provided; the frontend never recomputes them).
Suggested plain names for the D-rules: D1 "Same invoice number billed twice", D1b "One
invoice paid by more than one check", D2 "Same vendor, same amount, dates close together",
D3 "Similar vendor names, same amount", D4 "Large payment with no purchase order",
D5 "Paid before the invoice date", D6 "Payment to an inactive vendor", D7 "Payment to a
vendor not on the vendor list", D8 "Possible split payment".

## 7. Evidence on demand: every claim shows its source rows

Trust in this tool rests on traceability: every finding and every AI sentence cites the
source rows it came from (`SourceRowRef`: file, table, row index, column values).

**Applied here:** Every expanded finding shows an evidence table built directly from
`source_rows[].source_values` with the file name and "row N of <file>" phrasing (row indices
are 0-based positional indices in the parsed file - display them as given, labelled "row
index", do not add 1). The AI commentary panel lists its `referenced_source_rows` as chips;
clicking one scrolls to / highlights the matching finding. No source rows = say so plainly
("This is a file-level note - it does not point at a specific row", which is exactly what
preflight-derived findings look like).

## 8. Trust boundary: AI content is visually and structurally quarantined

Users must never mistake AI-drafted prose for computed results. This is the product's core
safety promise (deterministic code computes; the model only explains) and it must be visible,
not just true.

**Applied here:** One rule, no exceptions: AI text appears ONLY inside a single visually
distinct container ("AI-drafted commentary") with (a) a persistent header label "Draft -
written by AI, verify before use", (b) a distinct background/border treatment used nowhere
else in the app, (c) the AI safety-check (validation) badge adjacent to the header, and (d)
the model/provider line ("Generated by mock/mock-model" in default mode). Deterministic
sections never use that treatment. AI text is never interleaved into findings, summaries, or
export lists. When `ai` is null (fail-closed run), the container is replaced by a plain note:
"The AI was not asked to write anything for this run." Never render an empty AI box that
implies missing content.

## 9. The safety check on AI text is a first-class verdict, not a footnote

The backend validates every AI draft against the source data (invented references, numeric
claims). The Streamlit app surfaces this well ("Invented source reference detected - AI
output rejected by the validator") but buries it mid-page.

**Applied here:** The validation verdict renders as a badge in the Review Run summary strip
AND inside the AI container header: "AI text safety check: passed" / "passed with warnings" /
"failed". On `invented_reference_detected:true`, the AI container gets an error banner:
"Safety check failed: this draft mentions a row that is not in your files. Do not use it
without checking every claim." - and the approve-draft action gets a confirmation step.
Numeric grounding is stated positively when passed: "All N dollar amounts in this draft were
checked against your files."

## 10. Human review is explicit, recorded, and visibly consequential

Review actions (`mark_reviewed`, `mark_resolved`, `needs_follow_up`, `add_note`,
`reject_ai_explanation`, `approve_draft`) are the product's accountability mechanism; each
persists to the ledger and audit log. The UI must make taking them feel deliberate and show
that they were recorded.

**Applied here:** A dedicated "Your review" section: reviewer name field (prefilled from
settings `default_actor`), optional note, and the action buttons with plain labels.
`approve_draft` and `reject_ai_explanation` live in the AI container (they act on the
draft); the rest act on findings or the run. After posting, the response's
`human_review_status` and `review_actions` re-render immediately - the user sees their action
in the recorded list with their name and timestamp. Draft status is derived ONLY from
recorded actions (`derive_draft_status`): "Draft", "Approved by <actor>", or "Rejected".
The frontend never computes or caches its own status.

## 11. File-upload + preflight UX: check early, explain failures as fixes

Users do not know what columns a workflow needs. The preflight engine does, and it returns
structured findings with `suggested_action` and `next_steps`. The UX job is to convert
"missing_required_column" into "here is exactly what to do".

**Applied here:** Step 3 ("File check") runs `POST .../preflight` and renders:
a big pass/partial/fail status pill with one-sentence meaning; per-file rows (name, found,
row count) from `files[]`; and findings as fix-it cards - plain message first, machine code
(`missing_required_column`) in small secondary text, and the backend's `next_steps` rendered
as a numbered "What to do next" list. Partial is framed honestly: "We can run every check we
support, but we found things in your files we cannot fully handle. They will be flagged for
you." Fail keeps the Run button disabled and offers two recoveries: fix and re-check, or
"Try it with sample data instead".

## 12. Error, warning, and validation-state design: one severity language everywhere

Users should learn ONE color/badge system and see it everywhere: preflight findings, run
findings, validation results, and run status all express severity or state.

**Applied here:** Two fixed scales defined once in the spec and reused on every screen:
(a) the severity badge scale (critical/high/medium/low/info) for findings, and (b) the status
pill scale (pass/partial/fail) for the file check and run-level outcomes. HTTP/network errors
are a separate, visually distinct system-error treatment ("Something went wrong on this
computer or the local service - your data was not changed") so users never confuse "the tool
broke" with "your files have a problem". Domain failure (`failed_preflight`) is NOT a system
error and renders as the structured file-check report.

## 13. Accessibility basics (WCAG AA-ish) are non-negotiable in public-sector tools

Internal government tools serve employees with the same accessibility needs as the public,
and many agencies require Section 508/WCAG conformance even internally.

**Applied here (binding minimums):**
- Every input has a real `<label>`; upload dropzones are keyboard-operable buttons.
- Color is never the only signal: severity badges and status pills always pair color with a
  text label and an icon/shape (PASS check, PARTIAL triangle, FAIL octagon).
- Text contrast 4.5:1 minimum; interactive elements have visible focus rings.
- The wizard is fully keyboard navigable; step changes move focus to the step heading and
  announce via `aria-live`.
- Expanders are `<button aria-expanded>`; data tables are real `<table>` with `<th scope>`.
- Async results (preflight done, run done, action recorded) announce in an `aria-live`
  region.

## 14. Plain-language writing: rewrite the developer voice

The current Streamlit copy is accurate but written for the implementer. Rewrite for an AP
clerk. Rules: verbs over nouns, second person, name the user's object not the system's
("your bank statement file", not "the input"), state what happened then what to do, keep
machine codes visible but secondary.

**Applied here - concrete rewrites of real current strings:**

| Current (Streamlit) | Rewrite (React console) |
| --- | --- |
| "Preflight FAILED - the workflow did NOT run and no AI explanation was produced." | "File check failed. We did not run anything and the AI wrote nothing. Fix the items below, then check the files again." |
| "Validator flagged an invented source reference in the AI draft - review carefully before use." | "Safety check failed: this AI draft mentions a row that is not in your files. Do not use it without checking every claim." |
| "PARTIAL run: the workflow ran its supported checks only. Unsupported conditions were flagged below..." | "Partly checked: we ran every check we support. A few things in your files need a person to look at - they are flagged below." |
| "`missing_required_column`: required column 'amount' not found - input `bank`" | "Your bank statement file is missing an amount column." (code `missing_required_column` shown small underneath) |
| "Records-retention category - Tags this run for public-records / retention-schedule purposes. Deterministic metadata only - the AI never sets it." | "Records category - how long your office keeps this under its records schedule." |
| "source = how the column was resolved: auto (confident detection), human (you approved it), or unmapped..." | "How we matched this column: found automatically, chosen by you, or left for auto-detection." |
| "Mock LLM mode is the default. Synthetic data only." | "Using the built-in offline AI. Practice data only - never upload real records." |
| "Run failed: <exception>. Check that the uploaded files have date/amount (or account) columns and try the example files first." | "We could not finish this run. Try the sample data first to see a working example, then check that your file has date and amount columns." (technical detail behind "Show details") |
| "Actions persist to the run ledger and the audit log." | "Everything you record here is saved to this run's permanent activity log." |
| "Run complete. Run ID: 4f9c..." | "Done - your results are ready to review." (run ID shown as small metadata) |

## 15. Show the trust scaffolding without demanding attention

Run IDs, file hashes, audit events, retention categories, and export manifests exist for
auditors and records officers. Everyday users should see that they exist (it builds trust)
without reading them.

**Applied here:** Every Review Run screen has a quiet metadata row (run ID, started by,
date, records category) and collapsed "Activity log" and "Input files" sections. Exports are
listed with file name, type, and a download button; `review_packet.md` and
`run_manifest.json` are labeled "Complete review packet - everything about this run in one
file". Nothing audit-related is ever more than one click away, and nothing audit-related
is ever in the user's face while triaging findings.

## 16. The frontend renders; it never computes

(Product invariant restated as a UX principle because it shapes every screen.) Sums,
variances, match decisions, counts, and validation verdicts come from the backend. The
frontend may format ("1234.5" -> "$1,234.50"), sort, group, filter, and count *items it was
given for display purposes* - it must never derive a financial figure, a match, or a
pass/fail verdict that the API did not send.

**Applied here:** The summary strip uses `finding_count`, severity tallies counted from the
delivered findings array (grouping, not calculation), `validation.passed`, and
`human_review_status` exactly as returned. If a number is missing from the API response, the
UI shows an em-dash and never fills the gap.

---

## Implementation status (2026-06-11)

`frontend/` is built and validated. `npm run typecheck`, `npm run lint`,
`npm run test` (4 files / 19 tests), and `npm run build` all pass clean.
The built bundle is served by the API process when `frontend/dist/` is
present (single-server mode). The API is implemented in `api/` per
`docs/frontend/api_contract.md` and shares the run ledger and audit log with
the Streamlit app. See `docs/decisions.md` "FastAPI seam + React workflow
console (2026-06-11)" for the full design rationale and `docs/build_plan.md`
for the implementation checklist.
