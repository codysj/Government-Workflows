# Municipal Finance AI Workflow Tool — Updated Project Outline

## 0. Project Definition

### 0.1 Product thesis

Build a local-first AI workflow tool for small municipal finance departments that turns recurring, error-prone finance tasks into auditable, source-linked review workflows.

The system must not behave like a general chatbot. It must behave like a controlled workflow runner:

```text
input files → deterministic processing → AI-assisted explanation/drafting → validation → human review → exportable packet → audit log
```

### 0.2 Target users

Primary users:

* Finance director
* Accountants
* Accounts payable staff
* Finance analysts
* Administrative staff supporting financial reporting

User assumptions:

* Users are non-technical.
* Users should not need prompt engineering knowledge.
* Users will not trust black-box financial outputs.
* Users need plain-language explanations, source evidence, and review controls.

### 0.3 Core principle

The model may explain, summarize, draft, classify, and flag.

The model must not:

* Perform authoritative financial calculations.
* Decide final transaction matches.
* Invent account numbers, fund names, vendors, amounts, dates, or official policy.
* Produce final report language without human review.
* Handle real PII or sensitive financial data during development.

Deterministic code must perform:

* Parsing
* Cleaning
* Matching
* Validation
* Calculations
* Source-row tracking
* Export formatting
* Audit logging

---

## 1. Non-Negotiable Constraints

### 1.1 Data constraints

Use only:

* Synthetic data
* Scrubbed sample data
* Public sample documents
* Mock city finance exports

Do not use:

* Real bank statements
* Real vendor records
* Real employee records
* Real taxpayer data
* Real account credentials
* Sensitive city financial data

### 1.2 AI constraints

Every LLM output must be treated as advisory.

Every LLM output must be tied to:

* The workflow run ID
* The input files used
* The deterministic data objects used
* The prompt template version
* The model provider and model name
* The validation result
* The human review status

### 1.3 Architecture constraints

The MVP must avoid unnecessary complexity.

Do not include in the MVP unless explicitly justified:

* Vector database
* Multi-agent architecture
* Autonomous agents
* Full ERP integration
* Production authentication system
* Full public-records request management
* Full budget-book publishing
* Full local-GPU inference deployment

The system should be built so these features can be added later without rewriting the core pipeline.

---

## 2. Intended MVP Shape

The MVP must include more than one workflow.

The initial MVP should include:

1. A shared workflow execution pipeline.
2. A workflow run ledger.
3. A Streamlit UI.
4. A CLI for developer testing.
5. A model-provider wrapper.
6. A validation layer.
7. Synthetic datasets.
8. A small eval harness.
9. At least three high-value workflow modes:

   * Bank reconciliation
   * Two additional workflows selected during the research phase

If the research phase does not identify better alternatives, use these default MVP workflows:

1. Bank reconciliation
2. Budget-to-actual variance review and commentary
3. Financial report consistency / error-flagging review

---

# Build Phases

---

## Phase 0 — Repository Setup and Project Guardrails

### Goal

Create a clean, testable Python project structure that can support multiple finance workflows through one shared core pipeline.

### Required outputs

Create the following structure:

```text
municipal-finance-ai/
  README.md
  pyproject.toml
  .env.example
  .gitignore

  app/
    streamlit_app.py

  cli/
    run_workflow.py

  src/
    core/
      workflow_runner.py
      run_ledger.py
      audit_log.py
      validation.py
      exports.py
      schemas.py

    ingest/
      csv_loader.py
      excel_loader.py
      pdf_loader.py

    normalize/
      cleaning.py
      matching.py
      finance_rules.py

    llm/
      provider.py
      prompts.py
      schemas.py

    workflows/
      bank_reconciliation.py
      budget_variance.py
      report_review.py
      freeform.py

    context/
      context_loader.py
      chart_of_accounts.py
      city_profile.py

  data/
    synthetic/
      bank_reconciliation/
      budget_variance/
      report_review/

  tests/
    unit/
    integration/
    fixtures/

  docs/
    decisions.md
    research/
      workflow_research.md
      workflow_selection_scorecard.md
    workflow_specs/
      bank_reconciliation.md
      budget_variance.md
      report_review.md
    evaluation.md
    pilot_plan.md
```

### Implementation requirements

* Use Python.
* Use pandas for tabular data work.
* Use Streamlit for the MVP UI.
* Use pydantic or dataclasses for internal schemas.
* Use pytest for tests.
* Keep LLM provider calls behind one wrapper.
* Keep workflow logic independent from Streamlit UI code.
* Read API keys from `.env`.
* Ensure the app can run without an LLM key by using mock LLM responses.

### Validation

Before moving on:

* `pytest` must run.
* The CLI must execute a placeholder workflow.
* The Streamlit app must start.
* The project must contain no real secrets or sensitive data.

---

## Phase 1 — Research and Workflow Selection

### Goal

Research the best municipal finance tasks to include in the MVP as immediate high-value workflows. The MVP must not stop at bank reconciliation.

This phase should identify which recurring finance tasks are:

* Frequent
* Painful
* Safe for AI assistance
* Easy to validate
* Source-data-driven
* Useful to non-technical finance staff
* Demonstrable with synthetic data
* Strong as a recruiting artifact

### Research tasks

The coding agent should research:

1. Common small-city finance department workflows.
2. Monthly and quarterly close tasks.
3. Bank reconciliation pain points.
4. Budget-to-actual variance reporting.
5. Accounts payable review.
6. Purchase order review.
7. Staff report / council memo finance sections.
8. ACFR support tasks.
9. Public-records and audit implications of AI use.
10. Existing govtech products and their workflow patterns.

### Candidate workflow list

Evaluate at least these workflow candidates:

```text
Bank reconciliation
Budget-to-actual variance review
Financial report consistency review
Accounts payable invoice coding support
Purchase order / invoice mismatch review
Monthly close checklist assistant
Council agenda finance memo drafting
Grant reimbursement packet review
Cash receipt anomaly review
Vendor payment duplicate detection
ACFR note support
Public-records redaction support
```

### Workflow scoring rubric

Score each candidate from 1 to 5 on:

```text
Frequency
Staff pain level
Safety for AI assistance
Ease of deterministic validation
Ease of synthetic demo creation
Need for source-linked explanation
Recruiting technical depth
Differentiation from generic ChatGPT
Differentiation from ERP systems
MVP implementation cost
```

Calculate:

```text
workflow_score =
  frequency
  + staff_pain_level
  + safety_for_ai_assistance
  + deterministic_validation
  + synthetic_demo_feasibility
  + source_linking_need
  + recruiting_depth
  + generic_ai_differentiation
  + erp_differentiation
  - implementation_cost
```

### Required research deliverables

Create:

```text
docs/research/workflow_research.md
docs/research/workflow_selection_scorecard.md
docs/research/mvp_workflow_recommendation.md
```

The recommendation document must choose:

```text
MVP Workflow 1: Bank reconciliation
MVP Workflow 2: selected by research
MVP Workflow 3: selected by research
Post-MVP Workflow Candidates: ranked list
Rejected Workflows: with reasons
```

### Default decision rule

If research is inconclusive, use this MVP workflow set:

```text
1. Bank reconciliation
2. Budget-to-actual variance review and commentary
3. Financial report consistency / error-flagging review
```

Rationale:

* All three are finance-specific.
* All three can be demonstrated with synthetic CSV/Excel/PDF files.
* All three can use deterministic calculations before LLM explanation.
* All three produce source-linked review packets.
* All three are safer than official filing automation.
* All three are more defensible than a generic chatbot.

---

## Phase 2 — Data Contracts and Core Schemas

### Goal

Define the internal data structures used by every workflow.

The system must preserve source traceability from input file to final output.

### Required schemas

Create shared schemas for:

```text
WorkflowRun
InputFile
ParsedTable
SourceRowRef
NormalizedRecord
DeterministicFinding
LLMRequest
LLMResponse
ValidationResult
HumanReviewAction
ExportArtifact
AuditEvent
```

### Required fields

`WorkflowRun` must include:

```text
run_id
workflow_type
created_at
created_by
input_files
status
deterministic_results
llm_results
validation_results
human_review_status
export_artifacts
```

`InputFile` must include:

```text
file_id
file_name
file_type
file_hash
uploaded_at
parser_used
row_count
column_names
```

`SourceRowRef` must include:

```text
file_id
table_name
row_index
column_names
source_values
```

`DeterministicFinding` must include:

```text
finding_id
finding_type
severity
description
source_rows
computed_values
rule_used
requires_human_review
```

`LLMResponse` must include:

```text
response_id
prompt_template_version
model_provider
model_name
response_json
referenced_source_rows
created_at
```

`ValidationResult` must include:

```text
validation_id
passed
errors
warnings
checked_source_refs
invented_reference_detected
numeric_claims_checked
```

### Validation rules

The system must reject or flag any LLM output that:

* References a source row that does not exist.
* References an account code not present in the source/context.
* Claims a numeric value not present in deterministic results.
* Produces final approval language.
* Omits required source references.
* Returns invalid JSON when schema output is required.

---

## Phase 3 — Core Workflow Infrastructure

### Goal

Build the reusable pipeline used by all workflows.

### Shared pipeline

Implement:

```text
ingest → normalize → deterministic analysis → LLM assist → validate → human review → export → audit log
```

### 3.1 Ingestion layer

Support:

```text
CSV
Excel
Tabular PDF when feasible
Plain text / markdown context files
```

For MVP, prioritize CSV and Excel.

PDF support may be partial unless required by the selected workflows.

### 3.2 Normalization layer

Implement shared utilities for:

```text
Column name normalization
Date parsing
Amount parsing
Currency cleaning
Whitespace cleanup
Duplicate row detection
Source row preservation
Schema validation
```

Every normalized record must retain a source-row reference.

### 3.3 Workflow runner

Create a generic workflow runner that:

1. Creates a run ID.
2. Saves input file metadata.
3. Calls the selected workflow module.
4. Saves deterministic findings.
5. Calls the LLM provider only when needed.
6. Validates the LLM response.
7. Saves audit events.
8. Returns a structured result object.
9. Allows export generation.

### 3.4 Run ledger

Implement a local run ledger.

Acceptable MVP storage:

```text
SQLite
JSONL
Local file-backed database
```

Recommended MVP choice:

```text
SQLite
```

The run ledger must support:

```text
Create run
Update run status
List runs
Read run detail
Store input file hashes
Store findings
Store LLM outputs
Store validation results
Store human review actions
Store export artifact paths
```

### 3.5 Audit log

Create append-only audit events for:

```text
Workflow run created
File uploaded
File parsed
Deterministic analysis completed
LLM request sent
LLM response received
Validation completed
Human review action taken
Export generated
Run completed
Run failed
```

Each audit event must include:

```text
event_id
run_id
timestamp
event_type
actor
details
```

### 3.6 LLM provider wrapper

Create one provider interface.

Required methods:

```text
generate_structured_response(prompt, schema)
generate_text_response(prompt)
mock_response(prompt, schema)
```

Provider requirements:

* API keys must come from environment variables.
* Provider choice must be config-driven.
* Mock mode must work without internet or API keys.
* Prompt templates must be versioned.
* Provider-specific code must not appear inside workflow modules.

---

## Phase 4 — MVP Workflow Implementation

### Goal

Implement at least three workflow modes using the shared pipeline.

---

## Workflow 1 — Bank Reconciliation

### Purpose

Help finance staff compare bank statement records against ledger records and produce a reviewable exception packet.

### Inputs

```text
Bank statement CSV or Excel
Ledger export CSV or Excel
Optional chart of accounts CSV
Optional reconciliation configuration file
```

### Deterministic processing

Implement:

```text
Exact match by amount and date
Configurable date tolerance
Configurable amount tolerance
Duplicate transaction detection
Unmatched bank items
Unmatched ledger items
Potential timing differences
Potential duplicate payments/deposits
Summary totals
```

The LLM must not perform matching.

### LLM tasks

The LLM may:

```text
Summarize unmatched items
Draft plain-language explanation of likely causes
Suggest human review steps
Group exceptions into categories
Draft reconciliation memo language
```

The LLM must cite source row IDs for each claim.

### UI requirements

Show:

```text
Matched transactions
Unmatched bank items
Unmatched ledger items
Potential duplicates
Summary totals
LLM explanation
Validation warnings
Human review controls
Export button
```

### Export artifacts

Generate:

```text
reconciliation_summary.md
matched_transactions.csv
unmatched_bank_items.csv
unmatched_ledger_items.csv
validation_report.json
audit_log.json
```

---

## Workflow 2 — Budget-to-Actual Variance Review

### Purpose

Help finance staff identify and explain significant budget variances using deterministic calculations and source-linked AI commentary.

### Inputs

```text
Budget CSV or Excel
Actuals CSV or Excel
Optional prior-period actuals CSV
Optional chart of accounts CSV
Optional variance thresholds config
```

### Deterministic processing

Implement:

```text
Join budget and actuals by fund/account/department/object
Calculate dollar variance
Calculate percentage variance
Flag variances above configured thresholds
Flag missing accounts
Flag accounts appearing in actuals but not budget
Flag accounts appearing in budget but not actuals
Group results by fund and department
```

The LLM must not calculate variances.

### LLM tasks

The LLM may:

```text
Draft variance commentary
Translate variance table into plain English
Identify which variances need staff explanation
Suggest follow-up questions for department heads
Group variances into themes
```

The LLM must reference deterministic variance findings.

### UI requirements

Show:

```text
Variance summary by fund
Variance summary by department
Flagged line items
Threshold settings
LLM draft commentary
Source rows
Human edit/review controls
Export button
```

### Export artifacts

Generate:

```text
variance_summary.md
flagged_variances.csv
variance_commentary_draft.md
validation_report.json
audit_log.json
```

---

## Workflow 3 — Financial Report Consistency / Error-Flagging Review

### Purpose

Help finance staff review draft reports, schedules, or internal finance packets for inconsistencies before human finalization.

### Inputs

```text
Report table CSV or Excel
Optional prior version CSV or Excel
Optional narrative draft text
Optional chart of accounts CSV
Optional report checklist config
```

### Deterministic processing

Implement checks for:

```text
Totals that do not tie out
Subtotals that do not equal line-item sums
Missing required sections
Duplicate account lines
Negative values where not expected
Account codes not in chart of accounts
Large unexplained changes from prior version
Inconsistent fund/account naming
```

The LLM must not decide whether a report is correct. It may only explain flagged issues.

### LLM tasks

The LLM may:

```text
Summarize detected inconsistencies
Draft a review checklist
Explain why each flagged issue may matter
Suggest human follow-up steps
Draft a plain-language review memo
```

The LLM must reference deterministic findings and source rows.

### UI requirements

Show:

```text
Detected issues
Severity level
Source rows
Rule that triggered each issue
LLM explanation
Human status per issue
Export button
```

### Export artifacts

Generate:

```text
report_review_summary.md
flagged_issues.csv
review_checklist.md
validation_report.json
audit_log.json
```

---

## Phase 5 — Guided Freeform Mode

### Goal

Provide a controlled fallback for tasks that are not yet formal workflows.

Freeform mode must not be an open chatbot.

It should collect structured user inputs and route them through the same logging and validation system.

### Required input fields

```text
Task type
Uploaded files
Desired output
Relevant context
Sensitivity confirmation
Human review confirmation
```

### Required behavior

Freeform mode should:

1. Ask the user to describe the task in plain language.
2. Ask what files should be used.
3. Ask what output format is desired.
4. Auto-inject available city/context references.
5. Generate a structured prompt.
6. Call the LLM provider.
7. Validate source references when possible.
8. Log the interaction.
9. Save the task type for future workflow discovery.

### Prohibited behavior

Freeform mode must not:

* Accept real sensitive data during development.
* Promise authoritative financial answers.
* Skip audit logging.
* Bypass validation.
* Produce final official language without review.

### Discovery output

Log freeform task patterns to:

```text
docs/research/freeform_task_observations.md
```

Use this document to recommend future workflow templates.

---

## Phase 6 — Streamlit MVP UI

### Goal

Build a usable local interface for non-technical finance staff.

### Navigation

The app should include:

```text
Home
Run Workflow
Workflow History
Review Run
Export Center
Settings
About / Safety
```

### Home page

Show:

```text
What this tool does
What this tool does not do
Supported workflows
Data safety warning
Human review warning
```

### Run Workflow page

Allow the user to select:

```text
Bank reconciliation
Budget-to-actual variance review
Financial report consistency review
Guided freeform task
```

Each workflow should have:

```text
Plain-language description
Required uploads
Optional uploads
Example files
Run button
```

### Review Run page

Show:

```text
Run status
Input files
Deterministic findings
LLM explanation
Validation warnings
Human review controls
Export artifacts
Audit events
```

### Human review controls

For each finding, allow:

```text
Mark reviewed
Mark resolved
Needs follow-up
Add note
Reject AI explanation
Approve draft for export
```

### Settings page

Support configuration for:

```text
City name
Default actor name
LLM provider
Mock mode
Date tolerance
Amount tolerance
Variance threshold
Export directory
```

Do not build full authentication in MVP.

---

## Phase 7 — Validation and Evaluation Harness

### Goal

Make the project measurable and defensible.

### Synthetic test datasets

Create known-answer datasets for each MVP workflow.

For bank reconciliation:

```text
Known matched transactions
Known unmatched bank items
Known unmatched ledger items
Known duplicates
Known timing differences
```

For budget variance:

```text
Known large dollar variances
Known large percentage variances
Known missing accounts
Known new actual-only accounts
Known budget-only accounts
```

For report review:

```text
Known subtotal mismatch
Known invalid account code
Known duplicate line
Known missing section
Known inconsistent naming
```

### Automated tests

Implement tests for:

```text
CSV ingestion
Excel ingestion
Date parsing
Amount parsing
Source-row preservation
Bank matching
Variance calculations
Report consistency checks
LLM mock responses
LLM validation
Run ledger writes
Audit log writes
Export generation
```

### Evaluation metrics

Track:

```text
Transactions processed
Rows matched
Rows unmatched
Findings generated
Validation warnings
LLM outputs rejected
Manual overrides
Export packets generated
Runtime per workflow
```

### Human pilot metrics

Prepare a simple pilot measurement process:

```text
Task completion time before tool
Task completion time with tool
User confidence rating from 1 to 5
User clarity rating from 1 to 5
User would-keep-using rating from 1 to 5
Qualitative feedback
```

Document in:

```text
docs/evaluation.md
```

---

## Phase 8 — Documentation and Recruiting Narrative

### Goal

Document the project as an engineering artifact, not just a demo.

### Required docs

Create or maintain:

```text
README.md
docs/decisions.md
docs/research/workflow_research.md
docs/research/workflow_selection_scorecard.md
docs/research/mvp_workflow_recommendation.md
docs/workflow_specs/bank_reconciliation.md
docs/workflow_specs/budget_variance.md
docs/workflow_specs/report_review.md
docs/evaluation.md
docs/pilot_plan.md
```

### README must include

```text
Problem
Target users
Why deterministic logic is used
Why the model is limited to language tasks
Supported workflows
Architecture diagram
Setup instructions
How to run CLI
How to run Streamlit
How to run tests
Synthetic data disclaimer
```

### decisions.md must include

Log decisions such as:

```text
Why Streamlit instead of full frontend
Why SQLite instead of cloud database
Why deterministic matching instead of LLM matching
Why no vector database in MVP
Why no multi-agent system in MVP
Why no full ERP integration in MVP
Why provider wrapper exists
Why audit logging is included early
Why selected workflows were chosen
Why rejected workflows were rejected
```

### Recruiting narrative

Maintain a short narrative in the README or docs:

```text
Problem → user insight → scope decision → architecture → workflow implementation → validation → measured result → reflection
```

The final project should support this interview explanation:

```text
I built a local-first AI workflow tool for small municipal finance teams. The key design decision was to keep financial logic deterministic and use the LLM only for explanation and drafting. The MVP supports multiple workflows, logs every run, validates AI outputs against source data, and exports review packets that a human finance staff member can verify.
```

---

# Post-MVP Feature Roadmap

The following are additional features, not MVP requirements.

---

## Tier 1 — Near-Term Extensions

These features are high-value and close to the MVP architecture.

### 1. Enhanced CPRA / AI audit layer

Add:

```text
Searchable AI interaction history
Draft vs final classification
Retention category field
Exportable AI usage log
Prompt/response diffing
Source file hash archive
Redaction-assist prototype
```

Do not build a full public-records request management platform.

### 2. Additional import adapters

Add support for common export formats:

```text
Generic ERP ledger export
Tyler/Munis-style CSV export placeholder
OpenGov-style CSV export placeholder
Bank statement format presets
Chart of accounts presets
```

Use local files only unless real integration access is granted.

### 3. Scheduled recurring runs

Allow configured workflows to run on a schedule:

```text
Monthly reconciliation reminder
Quarterly variance review reminder
Report review before agenda packet deadlines
```

MVP implementation can be local-only and manual-triggered.

### 4. Role-specific views

Add simple view modes:

```text
AP clerk
Accountant
Finance analyst
Finance director
```

Do not build full role-based authentication unless needed.

### 5. Improved export packets

Generate:

```text
PDF summary
Markdown summary
CSV detail files
JSON audit bundle
Reviewer notes
Approval summary
```

---

## Tier 2 — Strategic Extensions

These features increase defensibility but require more data, more design, or more implementation time.

### 1. Historical archive RAG

Use retrieval for city-specific institutional documents such as:

```text
Council minutes
Prior budgets
ACFRs
Staff reports
Municipal code
Resolutions
Finance policies
```

Only add RAG when there is a large enough document corpus to justify retrieval.

Do not use RAG for small structured reference data like chart-of-accounts files.

### 2. Monthly close workflow orchestration

Build a stateful workflow:

```text
Upload trial balance
Run reconciliation
Run variance review
Run report consistency checks
Generate review packet
Route to finance director
Track approval
Export final packet
```

This is the controlled version of agentic behavior.

### 3. Regulation-aware document drafting

Generate drafts for:

```text
Budget narratives
Council agenda finance sections
Internal finance memos
Variance explanations
Grant reimbursement notes
```

All generated text must be draft-only and source-linked.

### 4. Local model option

Extend the provider wrapper to support:

```text
Cloud API mode
Mock mode
Local model mode through Ollama or similar
```

Local inference should be optional, not required.

Document tradeoffs:

```text
Accuracy
Cost
Latency
Data sovereignty
Deployment complexity
```

---

## Tier 3 — Production / Startup-Level Extensions

These are out of scope for the recruiting MVP.

### 1. Real ERP integration

Potential targets:

```text
Tyler/Munis
OpenGov
Springbrook
CentralSquare
Other local-government ERP systems
```

Requires:

```text
Read-only API access
Authentication
Schema mapping
IT approval
Security review
Vendor documentation
```

### 2. Production authentication and permissions

Add only if moving beyond local demo:

```text
User login
Role-based access control
Admin console
Permissioned workflows
User-level audit history
```

### 3. Production compliance posture

Potential requirements:

```text
Cyber insurance
SOC 2 roadmap
StateRAMP/FedRAMP analysis
Data retention policy
Incident response policy
Vendor risk review
Legal review
```

### 4. Multi-city deployment

Only consider after one city pilot is successful.

Would require:

```text
Tenant separation
Configurable city profiles
Configurable chart of accounts
Configurable report templates
Deployment automation
Support process
```

---

# Explicit Non-Goals

The project should not become:

```text
A chatbot wrapper
A full ERP replacement
A public-records request platform
A resident-facing call center
A budget-book publishing platform
A general city-government AI agent
A multi-agent research demo
A production SaaS product before the MVP is proven
```

---

# Definition of Done for MVP

The MVP is complete when:

1. The app supports at least three workflows.
2. Each workflow uses the shared pipeline.
3. Each workflow runs on synthetic data.
4. Each workflow produces deterministic findings.
5. Each workflow can call the mock LLM provider.
6. Each workflow can call a real LLM provider through the wrapper.
7. Each LLM output is validated against source references where applicable.
8. Each workflow creates a run ledger entry.
9. Each workflow writes audit events.
10. Each workflow has a Streamlit review UI.
11. Each workflow exports a review packet.
12. Tests cover core deterministic logic.
13. The README explains setup and usage.
14. `docs/decisions.md` explains major architecture choices.
15. `docs/research/workflow_selection_scorecard.md` justifies the selected MVP workflows.
16. No real PII or sensitive financial data appears in the repo.
17. The project can be demoed end-to-end from synthetic inputs to exported review packet.

---

# Recommended Immediate Build Order

Use this order unless research changes the workflow selection:

```text
1. Repo setup
2. Core schemas
3. Run ledger
4. Audit log
5. CSV/Excel ingestion
6. Bank reconciliation deterministic logic
7. Bank reconciliation CLI
8. Bank reconciliation Streamlit UI
9. Mock LLM provider
10. LLM validation layer
11. Bank reconciliation export packet
12. Research phase completion
13. Budget variance workflow
14. Report review workflow
15. Guided freeform mode
16. Evaluation harness
17. Documentation polish
```

This order prioritizes the core architecture first, then one deep workflow, then expansion into the additional MVP workflows.
