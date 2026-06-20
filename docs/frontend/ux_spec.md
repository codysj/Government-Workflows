# UX Spec - Guided Workflow Console (React/Vite/TS)

This is the buildable specification for `frontend/`. Follow it verbatim. It maps 1:1 to API
Contract v1 (`docs/frontend/api_contract.md`, base `http://127.0.0.1:8000`, all routes under
`/api`). Rationale lives in `docs/research/ui_ux_principles.md`.

Hard rules (restated; violating any is a defect):
1. The frontend NEVER computes financial values, matches, or verdicts. It renders backend
   data and posts human actions. Currency/date display formatting of backend-provided values
   is allowed; computing sums/differences/percentages is not.
2. AI content renders ONLY inside the AI container defined in section 9. Deterministic
   content never uses that treatment.
3. Run IDs, source references, validation status, and export artifacts are visible (possibly
   collapsed, never absent) on every run-related screen.
4. All copy in this spec is exact. ASCII only.

---

## 1. Information architecture and navigation

Persistent LEFT navigation rail (collapsible to icons at < 1024px; bottom tab bar is NOT
required - desktop-first internal tool, minimum supported width 1024px).

Nav items (top to bottom), with route and icon name (lucide-react):

| Label | Route | Icon | Page |
| --- | --- | --- | --- |
| Home | `/` | `home` | Home |
| Run a workflow | `/run` | `play` | Run wizard |
| History | `/history` | `history` | Run history |
| Scheduled runs | `/schedules` | `archive` | Scheduled runs (list + create + run-now, GW-11/17/18/19) |
| AI usage | `/ai-usage` | `sparkles` | AI usage log (read-only, GW-9) |
| Redaction assist | `/redaction` | `shield-check` | Redaction assist (GW-11) |
| Settings | `/settings` | `table` | Settings (read-only, GW-8) |
| About and safety | `/about` | `shield` | About/Safety |

The four secondary pages (Scheduled runs, AI usage, Redaction assist, Settings) sit between
the core flow (Home/Run/History) and About. AI usage and Settings are read-only (Settings is
display-only this batch - no PUT). Redaction assist is a stateless local scan that stores
nothing. Scheduled runs is interactive (GW-17): it can create a schedule and trigger a manual
"Run now", but those actions only call existing local endpoints - no finance logic lives here.

Review Run is route `/runs/:runId` (reached from the wizard finish, History, and Home's
recent-runs list; it is not a nav item - it always belongs to a specific run).

Nav footer (always visible, small text):
- App name "Municipal Finance AI Workflow Tool" + version from `GET /api/health`.
- LLM mode badge: when `llm_mode` is `"mock"` show a neutral badge `Offline AI (built-in)`;
  when `"real"` show badge `Live AI connected`. Tooltip for mock: "Using the built-in
  offline AI. Practice data only - never upload real records."
- Line: "All work stays on this computer."

If `GET /api/health` fails on app load, render a full-page system error (section 12) with:
heading "Cannot reach the local service", body "The workflow service on this computer is not
responding. Start the API (uvicorn) and reload this page. Your data was not changed.",
button "Retry".

### Page: Home (`/`)

Order, top to bottom:
1. Heading "Municipal Finance AI Workflow Tool"; subheading "Auditable finance reviews.
   Calculations by code. AI drafts the explanations - you stay in charge."
2. Primary button "Run a workflow" -> `/run`.
3. "Recent runs" card list: latest 5 from `GET /api/runs?limit=5` rendered as compact rows
   (workflow_title, created_at formatted "Jun 12, 2026, 9:14 AM", status pill, severity-free
   text "N findings", review-status chip). Row click -> `/runs/{run_id}`.
   - Empty state (no runs): icon `inbox`, text "No runs yet. Run your first workflow on
     sample data - no files needed.", button "Run a workflow".
   - Loading: 3 skeleton rows.
   - Failure: inline error card "Could not load recent runs." + "Retry" button.
4. Two side-by-side info cards (static copy):
   - Card "What this tool does": "Turns recurring finance tasks into auditable,
     source-linked reviews. All math and matching is done by code. AI only explains and
     drafts - and every AI claim is checked against your files."
   - Card "What it never does": "It is not a chatbot. The AI never calculates, never decides
     matches, and never invents accounts, vendors, amounts, or dates. Nothing is final
     without your review."
5. Warning banner (amber, icon `alert-triangle`): "Use practice or scrubbed data only. Do
   not upload real bank statements, vendor records, employee or taxpayer data."

### Page: About and safety (`/about`)

Static page. Sections with headings:
- "How work is divided": two-column list - "Done by code (always)": parsing, matching, all
  calculations, validation, source-row tracking, exports, activity logging. "Done by AI
  (advisory only)": plain-language explanations, summaries, draft memos - always citing
  source rows, always checked, always a draft until a person approves it.
- "The file check": "Before any run, your files are checked: are the required files and
  columns there, do the dates and amounts parse, is anything in the data something this tool
  cannot handle? If the check fails, nothing runs and the AI writes nothing."
- "Your audit trail": "Every run is recorded with its input files (and their fingerprints),
  every finding, the AI draft, the safety-check result, your review actions, and a full
  activity log. Export the review packet for any run at any time."
- "Data safety": same warning banner copy as Home.
- Footer line: "Version {version} - {llm_mode badge} - local only, no accounts, no cloud."
  (values from `GET /api/health`).

---

## 2. Terminology table (binding)

Use the UI label everywhere user-facing. Internal terms may appear only as small secondary
"code" text where this spec says so.

| Internal term | UI label |
| --- | --- |
| run | Run (kept - short, matches History/CLI; never "session") |
| workflow | Workflow |
| preflight | File check |
| preflight status pass/partial/fail | Ready / Partly supported / Cannot run (pill text PASS / PARTIAL / FAIL stays as the pill label; the phrase is the accompanying sentence - see section 11) |
| deterministic findings | Checks ("What the checks found") |
| finding | Item / flagged item |
| LLM / AI response | AI-drafted commentary |
| validation (of AI text) | AI text safety check |
| validation passed/warnings/failed | Safety check passed / passed with warnings / failed |
| invented_reference_detected | "mentions a row that is not in your files" |
| export artifact | Export file |
| review packet (`review_packet.md` + `run_manifest.json`) | Complete review packet |
| audit events | Activity log |
| run ledger | (never named in UI; "saved with this run") |
| actor | Your name / Reviewed by |
| human_review_status pending / in_review / approved / rejected | Not yet reviewed / In review / Approved / Rejected |
| retention_category draft_working / transitory / administrative / audit_record / permanent | Records category: Draft / Transitory / Administrative / Audit record / Permanent |
| mock mode / provider mock | Offline AI (built-in) |
| sample / example files | Sample data |
| source_rows / SourceRowRef | Source rows ("row {row_index} of {file}") |
| column mapping | Column match |
| rule_used | (plain-language rule name per section 8; raw value as small code text) |

Workflow display names and categories (from `GET /api/workflows`; `title` is authoritative
at runtime - these are the expected values and the one-line card descriptions to use):

| workflow_type | category | Card title | Card one-liner |
| --- | --- | --- | --- |
| bank_reconciliation | review | Bank reconciliation | Match a bank statement to your ledger and flag what does not line up. |
| budget_variance | review | Budget vs. actuals | Compare budget to actuals and flag lines over your thresholds. |
| report_review | review | Report consistency check | Check a draft report for subtotal errors, bad account codes, and missing sections. |
| ap_duplicate_review | review | Duplicate payment review | Scan AP invoices for duplicates and suspicious payments. |
| po_invoice_review | review | PO and invoice match | Find invoices that do not match their purchase orders. |
| transaction_search | search | Find transactions | Search GL, AP, checks, and POs in plain English. |
| je_upload_prep | prep | Journal entry upload prep | Validate a draft journal entry and build an upload-ready file. |
| freeform | other | Guided freeform task | Describe a one-off task with structured fields. Draft output only. |

Category section headings on the wizard's step 1: "Review and reconcile" (review), "Search"
(search), "Prepare" (prep), "Other" (other).

---

## 3. The run wizard (`/run`)

A 4-step wizard. Step indicator across the top: numbered circles + labels
"1 Choose workflow", "2 Provide inputs", "3 File check", "4 Run". Current step highlighted;
completed steps show a check; future steps are not clickable. Back is always allowed (except
during the step-4 in-flight request). Changing anything in step 2 invalidates a completed
step 3 (state resets to "not checked").

Wizard state to keep in memory: `workflow_type`, selected `WorkflowInfo`, file per upload
key, text values per text-input key, `use_sample` boolean, latest `PreflightResponse` or
null.

### Step 1 - Choose workflow

- Fetch `GET /api/workflows`. Render cards grouped by `category` under the headings above.
- Card: title, one-liner (use `description`'s first sentence if it fits in 2 lines,
  otherwise the card one-liner from the table in section 2), small chips for required
  uploads count ("2 files required" / "No files required" for transaction_search and
  freeform), and "Sample data available" chip when `has_sample`.
- The freeform card additionally shows a small neutral chip "Draft-only mode".
- Click selects and advances to step 2.
- Loading: skeleton card grid. Failure: system error card "Could not load the workflow
  list." + Retry.

### Step 2 - Provide inputs

Header: workflow title + full `description` paragraph + "Change workflow" link (back to
step 1).

Sample path (render FIRST when `has_sample`):
- A bordered callout: heading "Try it with sample data", body `sample_description` from the
  API (fallback copy: "Run this workflow on the bundled practice files - nothing to
  upload."), button "Use sample data".
- Clicking sets `use_sample=true`, marks every upload slot as "Sample file will be used",
  pre-fills each text input with its `example` (e.g. the transaction_search query
  "payments to Cascade Paving over $5,000 between March and May 2026"), and enables
  "Check files". Uploading any real file afterwards clears `use_sample` (with toast
  "Sample data turned off - using your files.").

Required uploads (full-size, in `uploads[]` order, only `required:true`):
- Each is a labeled dropzone: label from `uploads[].label`, sublabel "Accepts: {file_types
  joined ', '}", `help` text below when present. Drag-drop + click-to-browse
  (keyboard-operable button). After selection show file name, size, and a remove button
  labeled "Remove {file name}".
- Client-side: reject wrong extensions with inline message "This slot accepts {types}
  files." No other client validation - the file check does the real work.

Text inputs (`text_inputs[]`, e.g. transaction_search `query`):
- Labeled single-line text input with `help` below and, when `example` is non-null, helper
  line: "Example: {example}" plus a link "Use this example" that fills the field.
- transaction_search expected rendering: label "What are you looking for?" (use the API
  `label`), the example helper, and its data files all under Optional context since none are
  individually required (show the note "Provide at least one data file - or use sample
  data." pinned above the optional section for this workflow, sourced from the upload
  `help`).

Optional context (collapsed `<details>`-style section, label "Optional context ({n} items)"):
- All `required:false` uploads and any optional text fields, same components at compact
  size. Top line inside: "Skipping these is fine - checks that need them are skipped and
  noted in your results."

Advanced options (GW-10) - collapsed `Collapsible` disclosure, heading "Advanced options",
closed by default so it never clutters the default flow (progressive disclosure). It is
ALWAYS present on the inputs step (it does not depend on optional inputs existing). Opening
it reveals:
- A muted lead line: "Optional. Most runs do not need anything here - the defaults from your
  local settings are used unless you override them."
- Configuration (JSON): a labeled `<textarea>` (monospace) for a JSON object of workflow
  tolerances/thresholds. Placeholder shows an example shape. Left blank, nothing is sent and
  the configured defaults apply. When non-empty, its trimmed value is posted as the multipart
  `config` field that the preflight and run endpoints already accept. Editing it invalidates
  any completed file check (the check re-runs with the new config).
- Column mappings (GW-20): shown ONLY when the file check surfaced suggested mappings
  (`PreflightResponse.suggested_mappings`, a list the backend now always includes - empty when
  the deterministic preflight engine resolved nothing to surface). Each entry mirrors
  `src.core.schemas.ColumnMapping`: `input_key`, `semantic_name`, `mapped_column` (the chosen
  column or null), `confidence`, `source` (`auto`/`human`/`unmapped`), and `candidates` (ranked
  column names). For each entry, render a `<select>` labeled by the sentence-cased
  `semantic_name` (with a "(needs a column)" hint when `source` is `unmapped`). Its first option
  keeps the engine's match ("Use suggested ({mapped_column})" or "Not matched" when null); the
  remaining options are the `candidates`. Chosen overrides are serialized to a JSON string and
  posted as the multipart `column_mappings` field that the preflight and run endpoints already
  accept; the value is shaped `{ input_key: { semantic_name: column } }`. Empty selections are
  omitted; if nothing is chosen the field is not sent. Changing a mapping invalidates a
  completed file check (it re-runs with the new mapping). This is display + forwarding only -
  the frontend never decides which column is "right"; the deterministic engine does.
- No finance math here - the frontend only forwards the strings the user types/picks.

Freeform specifics: render its structured text fields as provided by `text_inputs[]`
(task type, desired output, relevant context) plus two required checkboxes with exact
labels: "This contains no real or sensitive data - practice or scrubbed data only." and
"I understand the output is a draft that requires human review." Continue stays disabled
until both are checked.

Footer bar: secondary "Back", primary "Check files" (disabled until every required upload
has a file OR `use_sample` is true; for freeform the button is "Continue" and skips to
step 4 confirm state since freeform has no preflight - see below). Disabled-state tooltip:
"Add the required files first, or use sample data."

### Step 3 - File check

On entry, POST `multipart/form-data` to `/api/workflows/{workflow_type}/preflight` with
`use_sample`, one file part per provided upload key, and text fields by key.

- In-flight: centered spinner + text "Checking your files... This does not run anything
  yet." (typically < 2s; no progress bar needed).
- 422 response: render `detail` as an inline error card with button "Back to inputs".
- Network/500: system error card (section 12) with Retry.

Result layout, top to bottom:
1. Status pill (section 11) + one-sentence meaning:
   - pass: "Ready to run. Everything we need was found."
   - partial: "Partly supported. We can run every check we support; a few things in your
     files need a person to look at - they are flagged below."
   - fail: "Cannot run. We did not run anything and the AI wrote nothing. Fix the items
     below, then check the files again."
2. Files table from `files[]`: columns File (the matching upload label + `file_name`),
   Found ("Yes"/"No" with icon), Rows (`row_count`, em-dash when absent).
3. Findings as fix-it cards from `findings[]`, blocking first (`blocks_run:true`, error
   styling), then non-blocking (warning styling): primary line = `message` verbatim;
   secondary small code text = `code` (+ "file: {affected_input}" when present).
4. "What to do next": numbered list of `next_steps` verbatim (omit section when empty).
5. Collapsed section "What this workflow checks" listing `supported_checks` as chips.

Footer bar: "Back" (to step 2), "Check again" (re-POSTs preflight), and primary
"Run workflow":
- enabled when status is pass or partial;
- on partial the button is labeled "Run anyway (flagged items will be noted)";
- on fail the button renders disabled with tooltip "Fix the file check first." and a
  secondary option appears: "Try it with sample data instead" (sets use_sample, re-checks).

Freeform exception: freeform skips step 3 entirely; the wizard shows step 3 as "File check -
not needed for this workflow" in the step indicator and goes from 2 to 4.

### Step 4 - Run

On entry (user clicked Run), POST the same multipart shape to
`/api/workflows/{workflow_type}/runs` (plus optional `actor` field when the user has set a
name - see section 10).

- In-flight: full-step state with spinner, heading "Running...", body "Code is doing the
  checks and calculations, then the AI drafts an explanation. Usually under a minute.";
  disable Back during flight.
- Success (200 RunDetail): if `status` is `"completed"`, immediately navigate to
  `/runs/{run_id}` (no intermediate screen; the Review Run page shows a one-time success
  toast "Run complete - review the results below.").
- `status:"failed_preflight"`: navigate to `/runs/{run_id}`; the Review Run page renders the
  failed-check layout (section 7). Toast: none (the page itself explains).
- `status:"failed"`: navigate to `/runs/{run_id}`; Review Run renders the failed-run layout.
- 422: inline error card with the `detail` text and "Back to inputs".
- Network/500: system error card, buttons "Try again" and "Back to inputs". Body adds:
  "If this keeps happening, run it on sample data to confirm the tool works, then check
  your files."

---

## 4. Review Run (`/runs/:runId`)

Data: `GET /api/runs/{run_id}` (single fetch; re-fetch after posting a review action is NOT
needed - the POST response carries updated `human_review_status` + `review_actions`).
Audit: lazy-load `GET /api/runs/{run_id}/audit` when the Activity log section is first
expanded.

- Loading: skeleton (summary strip bar + 3 section blocks).
- 404: empty-state page: icon `file-question`, heading "Run not found", body "This run is
  not in the local history. It may have been removed.", button "Go to History".
- Network/500: system error card + Retry.

Layout for a `completed` run, top to bottom:

### 4.1 Header + summary strip

- Breadcrumb "History / {workflow_title}".
- H1: `{workflow_title}` ; subline metadata (small, muted): "Run {run_id} - started by
  {created_by} - {created_at formatted} - Records category: {retention label}".
- Summary strip: a single horizontal band of stat tiles (this is the first thing the eye
  hits; keep it above ALL sections):
  - "Items flagged: {n}" where n = count of findings with severity high or critical,
    sublabel "{finding_count} total checks results" (counting delivered array items is
    grouping, not calculation).
  - Status pill for `preflight.status` labeled "File check" (omit tile when `preflight` is
    null, e.g. legacy runs).
  - "AI text safety check" badge: from `validation` - `passed:true` and no warnings ->
    green "Passed"; `passed:true` with warnings or warnings-only -> amber "Passed with
    warnings"; `passed:false` -> red "Failed"; `validation:null` -> grey "Not applicable".
  - Review status chip: `human_review_status` mapped per terminology table.
  - "Export files: {artifacts.length}" tile that anchors-scrolls to the Exports section.

### 4.2 Section "What the checks found"

Deterministic findings only. Section intro line: "Computed by code from your files. Every
item links to the exact source rows."

- Group findings by `severity` in order critical, high, medium, low, info. Each group is a
  collapsible block headed "{Severity label} ({count})" with the severity badge. Default
  open: critical and high; others collapsed. Empty severities are omitted.
- Within a group, cluster items sharing `rule_used` under a subheading: plain-language rule
  name (section 8 map; fallback = `description` of the first item; never show a bare
  snake_case string as a heading) + small code text `{rule_used}`.
- Each finding row: `description` verbatim, severity badge, and when
  `requires_human_review:true` a small chip "Needs your review".
- Expanding a finding reveals:
  - Evidence table from `source_rows[]`: caption "Source rows", one row per ref showing
    "{table_name}, row index {row_index}" and the `source_values` key/value pairs as a
    two-column table. When `source_rows` is empty: text "This is a file-level note - it
    does not point at a specific row."
  - `computed_values` as a compact key/value list titled "Computed values" (render values
    as sent; format currency-looking numbers for display only).
  - Per-finding review actions (section 10).
- Findings whose description starts with `[preflight]` render in their severity group with a
  chip "From the file check" and the `[preflight] ` prefix stripped from the displayed text.
- Empty state (findings array empty, run completed): green check icon, "No issues found.
  Every check this workflow supports came back clean." (Do NOT show an empty table.)

### 4.3 Section "AI-drafted commentary"

The ONLY place AI text appears. Apply the AI container treatment (section 9) to the whole
section body.

- Container header row: robot/sparkle icon `sparkles`, label "Draft - written by AI, verify
  before use" (always visible, never collapsed away), the AI safety-check badge (same value
  as the summary strip), and small text "Generated by {model_provider}/{model_name}".
- Body renders `ai.response` keys in this order when present: `summary` (paragraph),
  then collapsible subsections for `draft_memo` ("Draft memo"), `draft` ("Draft"),
  `review_checklist` ("Review checklist"), `categorized_exceptions` ("Items needing staff
  explanation" - list each `description` with its `category` chip and
  `referenced_source_rows` chips), `follow_up_questions` ("Suggested follow-up questions" -
  bullet list). Any other keys go under a collapsed "Full AI response" JSON view
  (pretty-printed, read-only).
- Cited rows: `ai.referenced_source_rows` render as a chip row labeled "Rows this draft
  cites:"; clicking a chip scrolls to and flash-highlights the finding containing that
  source row (match on `{table_name}:{row_index}` string form; if no finding matches, the
  chip is non-interactive with tooltip "Cited row - see source files").
- Validation detail: when `validation.warnings` is non-empty, an amber list titled "Safety
  check warnings" inside the container, each warning verbatim. When `validation.errors` is
  non-empty or `invented_reference_detected:true`, a red banner at the TOP of the container:
  "Safety check failed: this draft mentions a row that is not in your files. Do not use it
  without checking every claim." followed by the errors verbatim. When passed clean, a green
  line: "Checked against your files: no invented references; {numeric_claims_checked} number
  claims verified." (omit the numbers clause when `numeric_claims_checked` is 0).
- Draft status line + actions: "Draft status: {Draft | Approved | Rejected}" derived ONLY
  from `review_actions` (an `approve_draft` action present -> Approved; else
  `reject_ai_explanation` present -> Rejected; else Draft). Buttons "Approve draft for use"
  (action `approve_draft`) and "Reject this draft" (action `reject_ai_explanation`) post per
  section 10. When validation failed, "Approve draft for use" requires a confirm dialog:
  "The safety check failed on this draft. Approve anyway?" buttons "Approve anyway" /
  "Cancel".
- `ai:null` (LLM never called - failed preflight or no-LLM path): replace the entire
  container with a plain (non-AI-styled) note card: "The AI was not asked to write anything
  for this run." Never render an empty AI container.
- `ai.available:false` with no response: same plain note card.

### 4.4 Section "Your review"

- Intro: "Record your review. Everything you save here goes into this run's permanent
  activity log and the review packet."
- Name input labeled "Your name" (prefilled from local settings, persisted to
  localStorage; default "finance_staff").
- Note textarea labeled "Note (optional)".
- Run-level action buttons rendered from `allowed_review_actions` (intersect with this label
  map; never invent actions): `mark_reviewed` "Mark reviewed", `mark_resolved` "Mark
  resolved", `needs_follow_up` "Needs follow-up", `add_note` "Save note". (`approve_draft`
  and `reject_ai_explanation` are surfaced ONLY in the AI container.)
- Recorded actions list ("Review history"): from `review_actions[]`, newest first:
  "{action label} - {actor} - {created_at formatted}" + note text when present + finding
  reference chip when `finding_id` is set. Empty state: "No review actions recorded yet."

### 4.5 Section "Exports"

- Intro: "Files generated by this run. Everything is also summarized in the complete review
  packet."
- List from `artifacts[]`: each row = file-type icon (by `artifact_type`: markdown, csv,
  json, xlsx, zip, other), `file_name`, type label, "Download" button hitting
  `download_url`. Order: `review_packet.md` then `run_manifest.json` first (labeled with a
  chip "Complete review packet"), then the rest in API order. Show the sha256 behind a
  hover/expand "fingerprint" affordance, first 12 chars + copy button.
- Real artifact names you will encounter (for icon/label QA, not hardcoding):
  bank_reconciliation: `reconciliation_summary.md`, `matched_transactions.csv`,
  `unmatched_bank_items.csv`, `unmatched_ledger_items.csv`, `validation_report.json`,
  `audit_log.json`. budget_variance: `variance_summary.md`, `flagged_variances.csv`,
  `variance_commentary_draft.md`. report_review: `report_review_summary.md`,
  `flagged_issues.csv`, `review_checklist.md`. transaction_search: `search_criteria.json`,
  `search_results.csv`, `search_summary.md`. ap_duplicate_review: `flagged_payments.csv`,
  `duplicate_groups.csv`, `ap_review_summary.md`, `review_notes_draft.md`.
  je_upload_prep: `je_upload.xlsx`, `je_upload.csv`, `source_mapping.csv`,
  `je_prep_summary.md`, `je_validation_errors.csv`. po_invoice_review:
  `po_invoice_exceptions.csv`, `matched_po_invoices.csv`, `po_review_summary.md`,
  `review_notes_draft.md`. freeform: `freeform_summary.md`, `freeform_request.json`,
  `freeform_draft.md`. Plus per-run `review_packet.md`, `run_manifest.json`; failed
  preflight runs have only `preflight_report.json` + `preflight_summary.md`.
- Files with names ending `_draft.md` or named `variance_commentary_draft.md` /
  `review_notes_draft.md` / `freeform_draft.md` get an additional chip "Contains AI draft".
- Empty state: "No export files were recorded for this run."

### 4.6 Collapsed sections (bottom, in order)

- "File check report" (when `preflight` non-null): the same rendering as wizard step 3
  result (status pill, files table, fix-it cards, next steps, supported checks).
- "Run summary details": the `summary` object as a read-only key/value list (pretty keys:
  snake_case -> sentence case). This is where `detected_columns`, `column_mappings_used`,
  `parse_confidence`, `source_formats` etc. live - no special rendering required beyond
  readable formatting.
- "Activity log": lazy `GET /api/runs/{run_id}/audit`; table with columns Time
  (`timestamp`), Event (`event_type` snake_case -> sentence case, e.g. "Run created",
  "Llm response received" -> special-case map: `llm_request_sent` "AI request sent",
  `llm_response_received` "AI response received", `validation_completed` "AI safety check
  completed", `deterministic_analysis_completed` "Checks completed", `human_review_action`
  "Review action recorded", `export_generated` "Exports generated", `file_uploaded` "File
  recorded", `file_parsed` "File parsed", `run_created` "Run created", `run_completed`
  "Run completed", `run_failed` "Run failed"), Who (`actor`), and an expand for `details`.
  Loading: 3 skeleton rows. Failure: "Could not load the activity log." + Retry. Empty:
  "No activity recorded."

---

## 5. Review Run - `failed_preflight` layout

When `RunDetail.status` is `"failed_preflight"`:
- Header + metadata as normal; summary strip shows only: File check pill (FAIL), review
  status chip, exports tile.
- Full-width red-bordered card directly under the strip: heading "File check failed -
  nothing was run", body "We did not run the workflow and the AI wrote nothing. The report
  below explains what to fix.", then the full step-3-style preflight rendering from
  `preflight` (files table, blocking cards, "What to do next").
- Then ONLY: the plain note card "The AI was not asked to write anything for this run."
  (no AI container), the Exports section (will contain `preflight_report.json` +
  `preflight_summary.md`), "Your review" (notes are still recordable), and Activity log.
- Primary action button in the red card: "Fix and run again" -> `/run` with the same
  workflow preselected at step 2.

## 6. Review Run - `failed` layout

When `status` is `"failed"` (unexpected error): red card heading "This run failed", body
"Something went wrong while running. No results were produced. Try again with sample data to
confirm the tool works, then check your files." Button "Run again" -> `/run` step 2 with the
workflow preselected. Below: Exports (if any), Your review, Activity log. No findings or AI
sections.

## 7. History (`/history`)

- Fetch `GET /api/runs?limit=50&offset=0` (GW-13). The response is a page:
  `{ runs, total, limit, offset }`. `runs` is the page; `total` is the full ledger count
  (a display-only integer - never a finance value, never used in arithmetic on amounts).
- Heading "History"; sub "Every run recorded on this computer, newest first."
- Filter row (client-side filtering of the delivered list only): workflow select (All +
  titles present), status select (All / Completed / File check failed / Failed), review
  status select (All / Not yet reviewed / In review / Approved / Rejected), text box
  "Search runs" matching workflow_title and run_id substring.
- Table (real `<table>`): columns Workflow (`workflow_title`, bold, row link), Date
  (`created_at` formatted), Status (status pill: completed -> green "Completed";
  failed_preflight -> red "File check failed"; failed -> red "Failed"), Findings
  (`finding_count`), AI safety check (badge from `validation_passed`: true green "Passed",
  false red "Failed", null grey em-dash), Review (`human_review_status` chip), Exports
  (`artifact_count`).
- Row click / Enter -> `/runs/{run_id}`.
- Empty state: icon `inbox`, "No runs yet. Run your first workflow on sample data - no files
  needed.", button "Run a workflow".
- Filtered-to-zero state: "No runs match these filters." + "Clear filters" link.
- Pager footer (GW-13): a muted count line plus a "Load more" button.
  - Count line: with no active filter, "Showing {loaded} of {total}" where `loaded` is the
    number of rows fetched so far and `total` comes from the page response. With a filter
    active, "Showing {filtered} of {loaded} loaded ({total} total)" so the reviewer can tell
    client-side filtering from server-side paging.
  - "Load more" is shown only while `loaded < total`. Clicking it fetches the next page at
    `offset = loaded` (the count already fetched) and APPENDS the rows; the button shows
    "Loading..." and is disabled in flight. On a load-more failure, keep the rows already
    shown and render an inline retry line "More runs did not load. Try Load more again."
    (the page does not fall back to the full-page error state once initial data is present).
- Loading (initial): skeleton table (6 rows). Initial failure: system error card + Retry.

---

## 7a. Scheduled runs (`/schedules`)

Configured recurring runs on this computer. Local-first; creating a schedule records a
recurrence only - it never runs anything on its own. The listing live-reflects schedules
written after the API started (GW-19), so a newly created schedule appears on refresh without
an API restart.

- Heading "Scheduled runs"; sub "Recurring runs configured on this computer."
- Info banner (exact intent): schedules are reminders only; creating one does not run anything;
  use "Run now" to run a workflow on its bundled sample data right away; everything stays on
  this computer.
- Create form (GW-17), card "Create a scheduled run", `POST /api/schedules` with
  `ScheduleCreateRequest` `{ workflow_type, label, cadence, start?, interval_days? }`:
  - Workflow: `<select>` populated from `GET /api/workflows` (option label = `title`,
    value = `workflow_type`); the first workflow auto-selects. A catalog-load failure is
    non-fatal - the list still renders and the picker notes none are available.
  - Label: free text, required (trimmed).
  - Cadence: `<select>` of Monthly / Quarterly / Before agenda / Custom. Choosing Custom
    reveals a required numeric "Interval (days)" field; `interval_days` is sent only for
    custom (null otherwise). `start` is omitted, so the backend defaults it to today.
  - Submit "Create schedule" stays disabled until a workflow + non-empty label exist (and an
    interval, for custom). On success: clear the form, toast that the schedule was created and
    will not run on its own, and refresh the list. On 422: show the backend's plain-language
    detail inline (role="alert").
- List table: columns Label, Workflow, Cadence, Next due (`next_due`, date), Last run
  (`last_run_at` or "Never"), Active ("Active"/"Paused"), and a Run action.
  - Run action (GW-17): per-row "Run now" button -> `POST /api/schedules/{id}/run`. While a
    trigger is in flight, that row's button reads "Starting..." and all Run-now buttons are
    disabled (only one trigger at a time). On success, navigate to `/runs/{run_id}` of the
    returned run (with the standard "Run complete" toast when completed). On error, re-enable
    and toast the backend detail.
- Empty state: icon `history`, "No scheduled runs", "No recurring runs are configured yet.
  Create one above - it will appear here." Loading: skeleton (5 rows). Failure: error card +
  Retry. (The due-schedules endpoint `GET /api/schedules/due?as_of` (GW-18) is available in the
  client as `getDueSchedules` for future reminder surfaces; it is not yet shown on this page.)

---

## 8. Plain-language rule names

Map `rule_used` (and where noted `finding_type`) to group headings. Fallback for unmapped
values: use the finding `description` as the row text and DO NOT render a heading for a
cluster of one; for clusters with an unmapped rule, heading = sentence-cased rule with
underscores replaced by spaces.

| rule_used contains | Heading |
| --- | --- |
| D1 / duplicate_invoice_number | Same invoice number billed twice |
| D1b / multiple_checks | One invoice paid by more than one check |
| D2 / near_date | Same vendor, same amount, dates close together |
| D3 / similar_vendor | Similar vendor names, same amount |
| D4 / missing_po_over_threshold | Large payment with no purchase order |
| D5 / payment_before_invoice | Paid before the invoice date |
| D6 / inactive_vendor | Payment to an inactive vendor |
| D7 / unknown_vendor | Payment to a vendor not on the vendor list |
| D8 / split_payment | Possible split payment |
| P1 / invoice_exceeds_po | Invoice is more than the purchase order |
| P2 / wrong_vendor | Invoice vendor does not match the PO vendor |
| P3 / missing_po | Invoice with no purchase order |
| P4 / closed_po | Charged to a closed purchase order |
| P5 / unit_price_mismatch | Unit price differs from the PO |
| P6 / quantity_mismatch | Quantity differs from the PO |
| P7 / received_not_invoiced | Received but not yet invoiced (informational) |
| P8 / invoiced_not_received | Invoiced but not received |
| exact_match / amount+date match rules | Matched transactions |
| timing_difference | Likely timing difference |
| unmatched_bank (finding_type) | On the bank statement but not in your ledger |
| unmatched_ledger (finding_type) | In your ledger but not on the bank statement |
| duplicate (finding_type) | Possible duplicate entries |
| variance threshold rules / variance (finding_type) | Over your variance threshold |
| missing_account | In one file but not the other |
| subtotal | Subtotal does not add up |
| invalid_account | Account code not in your chart of accounts |
| prior_version / change | Large change from the prior version |
| naming / consistency (finding_type) | Inconsistent account naming |
| je rules (debits_equal_credits etc.) / je_validation (finding_type) | Journal entry validation |
| search_match (finding_type) | Search results |
| preflight:* | From the file check |

## 9. Trust-boundary visual rules for AI content (binding)

1. AI text appears only inside the AI container. The container is: a card with a distinct
   tinted background (e.g. a soft violet/indigo tint), a 1.5px dashed or otherwise unique
   border, and the header row from 4.3. No other component in the app may use this tint or
   border style.
2. The header label "Draft - written by AI, verify before use" is part of the container
   chrome - it remains visible even when subsections are collapsed, and is rendered as text
   (not only an icon).
3. The AI safety-check badge sits in the container header, adjacent to the label.
4. AI-citation chips (referenced rows) appear inside the container only.
5. Deterministic content (findings, summaries, exports, preflight) never appears inside the
   AI container; AI text is never quoted inside deterministic sections.
6. Export rows that contain AI drafts get the "Contains AI draft" chip (4.5).
7. When there is no AI response, render the plain note card - never an empty AI container.
8. Screen-reader equivalence: the container has `role="region"` and
   `aria-label="AI-drafted commentary. Draft written by AI. Verify before use."`.

## 10. Posting review actions

`POST /api/runs/{run_id}/review-actions` body
`{"action": <internal action>, "actor": <name field>, "note": <note or null>, "finding_id": <id or null>}`.

- Per-finding actions (inside an expanded finding): buttons "Mark reviewed",
  "Mark resolved", "Needs follow-up" + the shared note field value; send `finding_id`.
- Run-level actions (section 4.4) send `finding_id:null`.
- Draft actions (AI container) send `finding_id:null` with `approve_draft` /
  `reject_ai_explanation`.
- On 200: update `human_review_status` chip and the Review history list from the response;
  toast "Recorded: {action label}". Announce in the aria-live region.
- On 422: toast error "That action is not available for this run."
- On network failure: toast "Could not save your review action. Check the local service and
  try again." (do not optimistically update).
- While in flight, disable only the clicked button (spinner inside it).

## 11. Badge and pill scales (binding, color + icon + text always)

Status pills (file check + anywhere pass/partial/fail appears):

| Status | Pill text | Color | Icon |
| --- | --- | --- | --- |
| pass | PASS | green (bg green-100, text green-800) | check-circle |
| partial | PARTIAL | amber (bg amber-100, text amber-800) | alert-triangle |
| fail | FAIL | red (bg red-100, text red-800) | octagon-x |

Severity badges:

| Severity | Text | Color | Icon |
| --- | --- | --- | --- |
| critical | Critical | red-700 on red-50, bold | octagon-alert |
| high | High | red-600 on red-50 | alert-circle |
| medium | Medium | amber-700 on amber-50 | alert-triangle |
| low | Low | slate-600 on slate-100 | info |
| info | Info | slate-500 on slate-50 | info |

Run status pills: Completed (green/check), File check failed (red/octagon-x), Failed
(red/x-circle). Review status chips: Not yet reviewed (slate), In review (blue), Approved
(green), Rejected (red). AI safety-check badges: Passed (green/shield-check), Passed with
warnings (amber/shield-alert), Failed (red/shield-x), Not applicable (slate, em-dash).

All pills/badges: text label always rendered; icon `aria-hidden`; color contrast at least
4.5:1 for the text.

## 12. System error treatment (network/5xx only)

A grey-bordered card with icon `plug-zap`, heading per context, body always ending with
"Your data was not changed.", and a Retry button. Never use the red domain-failure styling
for system errors. Domain outcomes (preflight fail, failed run, validation failed) use their
own treatments defined above and never the system-error card.

## 13. Accessibility requirements (binding)

- WCAG 2.1 AA targets: 4.5:1 text contrast, visible focus indicators on every interactive
  element, no information conveyed by color alone (sections 9/11 already enforce
  text+icon+color).
- Semantic structure: one `<h1>` per page; sections are `<section>` with `<h2>`; nav is
  `<nav aria-label="Main">`; tables are `<table>` with `<th scope="col">`.
- Wizard: `aria-current="step"` on the active step; on step change, move focus to the step
  `<h2>`; announce step results ("File check passed", "Run complete") via a polite
  `aria-live` region shared app-wide for async outcomes and toasts.
- Expanders/accordions: `<button aria-expanded aria-controls>`; chevron icons aria-hidden.
- Dropzones: a `<button>` (or label+input) reachable by Tab, activates file picker on
  Enter/Space; drag-drop is an enhancement, never the only path.
- All form controls have programmatic labels; error text is associated via
  `aria-describedby`.
- Toasts auto-dismiss in no less than 6s and are mirrored in the aria-live region; no
  information exists ONLY in a toast (every toast outcome is also visible in page state).
- Respect `prefers-reduced-motion`: no flash-highlight animation, use a static outline
  instead.
- Keyboard: every flow (full wizard, review actions, downloads, filters) completable
  without a mouse.

## 14. Writing rules (binding for any copy not given verbatim above)

1. Second person, active voice, present tense. "Add the bank statement", not "The bank
   statement must be provided".
2. Name the user's object: "your bank statement file", "your ledger export" - not "the
   input" or "the upload".
3. State what happened, then what to do, in that order. Max ~20 words per sentence.
4. Machine codes, run IDs, hashes: visible but secondary (small, muted, monospace).
5. Never use: deterministic, LLM, preflight, artifact, ledger (the database), actor,
   validation (alone), mock, schema, parse (prefer "read"). Allowed: AI, draft, check,
   export, activity log, file check, safety check.
6. Numbers come from the backend verbatim; format currency as "$1,234.56" and dates as
   "Jun 12, 2026" / "Jun 12, 2026, 9:14 AM" for display only.
7. Warnings about data safety always use the exact sentence: "Use practice or scrubbed data
   only. Do not upload real bank statements, vendor records, employee or taxpayer data."
8. AI labeling always uses the exact phrase "Draft - written by AI, verify before use".
