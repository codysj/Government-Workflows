# Architecture & Implementation Decisions

This file logs design decisions made when a spec point was ambiguous and the
simplest defensible implementation was chosen.

## Budget-to-Actual Variance workflow (Workflow 2)

- **Integration contract unavailable.** The Foundation agent's integration
  contract was truncated in this agent's task (`API Error: Stream idle timeout`),
  and the referenced `src/workflows/bank_reconciliation.py` did not exist at
  implementation time. There was also no `provider.py`, `workflow_runner.py`,
  `validation.py`, `exports.py`, or any registry in the repo. To avoid blocking,
  the budget variance module was made **self-contained and dependency-light**:
  it relies only on the already-present shared schemas
  (`src/core/schemas.py`), cleaning utilities (`src/normalize/cleaning.py`),
  and CSV/Excel loaders (`src/ingest/`).

- **Workflow protocol.** A `Workflow` is defined locally as a class exposing
  `workflow_type` plus methods: `run_deterministic(inputs)`,
  `build_llm_output(provider, det)`, `validate_llm(det, llm)`, and
  `export_artifacts(result, out_dir)`. A `run(inputs, provider=None)`
  convenience runs the full deterministic + (mock) LLM + validation chain and
  returns a `WorkflowResult`. If the Integration agent's real protocol differs,
  these methods are small adapters around the deterministic core, which is the
  load-bearing, fully-tested part.

- **LLM provider is duck-typed.** `build_llm_output` accepts any object with
  `generate_structured_response(prompt, schema)`; when `None` is passed it uses
  a built-in `MockLLMProvider` that returns deterministic JSON derived ONLY from
  the deterministic findings (no calculation, no invented values, cites source
  rows). Mock mode is the default and needs no API key or internet.

- **Registration mechanism (no edits to other modules).** The module exposes a
  module-level `WORKFLOW_REGISTRY` dict and a `register_workflow` decorator,
  and registers `BudgetVarianceWorkflow` under `workflow_type =
  "budget_variance"` at import time. The Integration agent must add one import
  line where it assembles the global registry, e.g.
  `from src.workflows import budget_variance` (or merge
  `budget_variance.WORKFLOW_REGISTRY`). No existing module is modified by this
  agent.

- **Join keys.** Budget and actuals are joined on the composite key
  `(fund, account, department, object)`. Any key column absent from BOTH files
  is dropped from the key; columns present are normalized to snake_case and
  stringified for a stable join. This matches the spec's
  "by fund/account/department/object" without requiring all four to exist.

- **Variance sign convention.** `dollar_variance = actual - budget`.
  `pct_variance = dollar_variance / budget * 100` when budget != 0; when
  budget == 0 and actual != 0, pct is reported as `None` (undefined) and the
  line is flagged as actual-only / over-budget rather than via a percentage.

- **Thresholds.** Defaults: `dollar_threshold = 10000`, `pct_threshold = 10.0`
  (percent). A line is flagged when `abs(dollar_variance) >= dollar_threshold`
  OR (`pct_variance` is defined AND `abs(pct_variance) >= pct_threshold`).
  Thresholds are overridable via a config dict / `thresholds.json`.

## Workflow 3 (report_review) integration decisions

- The Foundation/Integration contract handed to the report_review agent was
  truncated (stream error), and the shared pipeline modules it referenced
  (`src/llm/provider.py`, `src/core/validation.py`, `src/core/exports.py`,
  `src/core/workflow_runner.py`, `src/workflows/bank_reconciliation.py`) were not
  yet on disk. Decision: make `src/workflows/report_review.py` self-contained but
  plug-in friendly. It reuses the existing committed contracts (`src/core/schemas`,
  `src/ingest/csv_loader`, `src/normalize/cleaning`) and exposes a duck-typed
  `run(inputs, *, provider, ledger, audit, run_id, actor, export_dir, config)`
  entry point plus `WORKFLOW_TYPE = "report_review"`, `register(registry)`, and a
  `WORKFLOW` metadata dict. When the shared provider/ledger/audit are injected they
  are used (by the same method names the ledger/audit modules already expose);
  otherwise a local mock LLM, validation, and export fallback keep mock mode (the
  default) runnable with no API key and no internet.
- The report deterministic output object exposes `.findings` and `.summary`, the
  same shape `src/llm/prompts.py` already consumes, so the shared LLM layer can use
  it unchanged. The report-review prompt builder lives in the workflow module
  rather than editing `src/llm/prompts.py` (which is outside this agent's write
  scope); Integration may move it into `PROMPT_TEMPLATES` later if desired.

## Guided Freeform Mode (Phase 5) integration decisions

- The Foundation integration contract handed to the freeform agent was truncated
  (stream idle timeout), and `src/context/context_loader.py`, `provider.py`, and
  `workflow_runner.py` were not all on disk. Decision: mirror the established
  self-contained-but-plug-in-friendly pattern of `report_review.py`. The module
  exposes `run(inputs, *, provider, ledger, audit, context_loader, run_id, actor,
  export_dir, discovery_log_path)`, plus `WORKFLOW_TYPE = "freeform"`,
  `register(registry)`, `WORKFLOW` metadata, and a module-level
  `WORKFLOW_REGISTRY` self-registration. Integration wires it with one import
  line: `from src.workflows import freeform`.
- **Structured, not chat.** Inputs are the six spec fields (`task_type`,
  `uploaded_files`, `desired_output`, `relevant_context`,
  `sensitivity_confirmation`, `human_review_confirmation`) collected as a
  `FreeformRequest` dataclass, NOT a free chat box.
- **Fail-closed sensitivity gate.** `sensitivity_confirmation` defaults to False;
  `run_deterministic` raises `SensitivityNotConfirmedError` (and emits a
  `run_failed` audit event) when it is not given, so no real-sensitive-data run
  can proceed and no discovery/export side effects occur.
- **Tabular-protocol adaptation.** Freeform has no input tables, so the
  deterministic step produces a MINIMAL finding set: one `OTHER` finding for the
  structured request (SourceRowRef `freeform_request:0`) plus one per attached
  file's metadata (`freeform_files:i`). This lets freeform flow through the same
  validation/audit/ledger/export path as the tabular workflows without forking
  the pipeline. File handling records METADATA only (name/type/size/sha256); it
  does not parse content.
- **Context auto-injection** uses an injected `context_loader` or the shared
  `src.context.context_loader` if present (trying `load_context` /
  `load_available_context` / `load` / `get_context`); otherwise it falls back to
  an empty dict so mock mode runs with no internet.
- **Discovery log** at `docs/research/freeform_task_observations.md` is
  append-only; `run()` records `task_type` + lightweight metadata there for
  future workflow discovery. Tests target a tmp_path copy so the committed log is
  not polluted.


## Evaluation harness + metrics (Phase 7)

- **Drives workflows through the shared registry, not bespoke wiring.** The eval
  harness (`src/eval/harness.py`) runs each MVP workflow via
  `src.workflows.registry.get_spec(name).run(...)` with the default mock LLM
  (no API key / no internet), an in-memory `RunLedger(':memory:')`, and a
  non-JSONL `AuditLog`. This reuses the exact uniform `run` contract the CLI
  uses, so the harness never duplicates pipeline logic and never imports
  Streamlit or provider code. (The `app/workflow_registry.run_workflow` adapter
  marks bank_reconciliation `available=False`; the harness uses
  `src.workflows.registry` instead, which exposes all three implemented MVP
  workflows uniformly.)

- **Metrics derivation is deterministic-only.** All Phase-7 metrics
  (transactions processed, rows matched/unmatched, findings generated,
  validation warnings, LLM outputs rejected, manual overrides, export packets,
  runtime) are computed in `src/eval/metrics.py` from the deterministic
  findings + summary + validation result. Nothing is asked of the LLM.
  `manual_overrides` is fixed at 0 (automated run, no human in the loop);
  `llm_outputs_rejected` is 1 iff validation did not pass (0 on the mock path).
  `rows_matched`/`rows_unmatched` are derived from FindingType values, so they
  are meaningful for bank_reconciliation and harmless (0/low) elsewhere.

- **Known-answer expectations are captured from the deterministic output and are
  reproducible by construction** (`KNOWN_ANSWERS` in `src/eval/metrics.py`).
  For bank_reconciliation the duplicate Acme payment surfaces as an unmatched
  bank item under the configured tolerances (amount_tolerance=0,
  date_tolerance_days=3); the known-answer set reflects the actual deterministic
  behavior (matched=4, timing=1, unmatched_bank=2, unmatched_ledger=1) rather
  than re-deriving it.

- **Report written to `runs/eval_report.json`** by default (CLI:
  `.venv/Scripts/python.exe -m src.eval.harness`; programmatic:
  `src.eval.run_eval(out_dir=...)`). Tests route all output under `tmp_path`.

## Cross-cutting architecture decisions (Phase 8 reconciliation)

The notes above are per-workflow integration decisions made under truncated
contracts. This section logs the project-level architecture choices the spec
(Phase 8) asks to document. Each references the code that implements it.

- **Streamlit instead of a full frontend.** The MVP UI is a single Streamlit
  app (`app/streamlit_app.py`) because the target users are non-technical
  finance staff and the goal is a local-first review tool, not a production web
  product. Streamlit gives uploaders, tables, and per-finding review controls
  with no separate build/deploy stack. To keep this from leaking into core
  logic, all workflow driving lives in `app/workflow_registry.py`, which
  contains NO Streamlit imports and is unit-tested in isolation
  (`tests/unit/test_app_imports.py`); `streamlit_app.py`'s dispatcher only runs
  under the `__name__ == "__main__"` guard, so importing the module starts no
  server. A full SPA frontend was rejected as unjustified MVP complexity (spec
  section 1.3).

- **SQLite instead of a cloud database.** The run ledger
  (`src/core/run_ledger.py`) is a single-file SQLite database (the spec's
  recommended MVP choice), with `:memory:` supported for tests. This is
  local-first, needs no network or credentials, and fits the "no production
  auth, synthetic data only" constraint. A cloud DB would add auth, secrets, and
  network dependencies the MVP explicitly avoids. The ledger interface
  (create/update/list/read runs; store findings, LLM responses, validation
  results, human review actions, export artifacts) is narrow enough to swap for
  a hosted store later without touching workflow code.

- **Deterministic matching instead of LLM matching.** All transaction matching
  and every calculation are done in code, never by the model — bank
  reconciliation matching in `src/workflows/bank_reconciliation.py`, variance
  math in `src/workflows/budget_variance.py`, consistency checks in
  `src/workflows/report_review.py`, with shared normalization in
  `src/normalize/`. This is the core principle (spec 0.3): deterministic results
  are reproducible and auditable, and the model cannot invent a match or a
  number. The LLM only explains the deterministic findings, and
  `ValidationResult` (`src/core/schemas.py`, enforced per workflow) rejects/flags
  any LLM claim referencing a row or value not in those findings.

- **No vector database in the MVP.** The only structured reference data is small
  (chart of accounts, city profile, checklist configs) and is loaded directly
  from files via `src/context/` and the workflow inputs. Retrieval over a vector
  store is unnecessary at this scale and would add infrastructure for no benefit;
  the spec explicitly says not to use RAG for small structured reference data.
  RAG is left as a documented post-MVP extension for a large institutional-
  document corpus.

- **No multi-agent system in the MVP.** A single deterministic pipeline drives
  every workflow through one uniform `run(...)` contract
  (`src/workflows/registry.py`), used identically by the CLI
  (`cli/run_workflow.py`), the UI adapter (`app/workflow_registry.py`), and the
  eval harness (`src/eval/harness.py`). Multiple cooperating agents would add
  nondeterminism and debugging surface with no MVP value; the controlled,
  staged pipeline is the deliberate alternative to agentic behavior.

- **No full ERP integration in the MVP.** Inputs are local CSV/Excel files
  (`src/ingest/`); there is no live connection to Tyler/Munis, OpenGov, or any
  ERP. Real integration would require API access, authentication, schema
  mapping, and a security review — all out of scope and disallowed for a
  synthetic-data MVP. The file-based ingest layer is designed so ERP-export
  adapters can be added later without changing the pipeline.

- **Why the provider wrapper exists.** All model calls go through one uniform,
  duck-typed provider interface (`generate_structured_response` /
  `mock_response`) so provider-specific code never appears in the pipeline
  drivers (spec 0.3 / 3.6). In this build the interface is realized as a built-in
  `MockLLMProvider` inside each workflow module (e.g.
  `src/workflows/bank_reconciliation.py`) rather than a single
  `src/llm/provider.py` file; versioned prompt templates live in
  `src/llm/prompts.py`. This lets mock mode be the default offline path (no API
  key, no internet) and makes the provider swappable (a real cloud or local
  model object with the same two methods) without rewriting workflows. The CLI
  (`cli/run_workflow.py`) and UI adapter (`app/workflow_registry.py`) both pass
  `provider=None` to select the built-in mock provider. (A single shared
  `src/llm/provider.py` wrapper is the natural consolidation point and was left
  as a follow-up because each workflow was delivered self-contained.)

- **Why audit logging is included early.** Auditability is a primary requirement
  for government finance, not a later add-on, so the append-only audit log
  (`src/core/audit_log.py`) and run ledger were built into the shared pipeline
  from the start. Every stage emits an event (run created, file parsed,
  deterministic analysis, LLM request/response, validation, human review action,
  export, completed/failed), and human review actions route through a single path
  (`record_human_review_action` in `app/workflow_registry.py`) so ledger
  persistence and audit stay in sync. Retrofitting an audit trail onto a finished
  pipeline would be far harder and less trustworthy.

- **Why the selected workflows were chosen.** The MVP implements bank
  reconciliation, budget-to-actual variance review, and financial report
  consistency review (plus a guided-freeform fallback). All three are
  finance-specific, demonstrable with synthetic CSV/Excel files, driven by
  deterministic calculation before any LLM explanation, and produce source-linked
  review packets — making them safer than official-filing automation and more
  defensible than a generic chatbot. They are the spec's default high-value set
  and the workflow research/scorecard (`docs/research/`) confirmed them. Each is
  validated by known-answer datasets in the eval harness.

- **Why rejected workflows were rejected.** Candidates such as public-records
  redaction, full council-memo drafting, full ACFR note generation, and AP/PO
  matching against live systems were left out of the MVP because they are either
  harder to validate deterministically, depend on real or sensitive data that the
  synthetic-only constraint forbids, require ERP/document-store integration that
  is out of scope, or drift toward producing final official language without a
  clean human-review gate. They are recorded as post-MVP candidates in
  `docs/research/` rather than implemented now.

## Final verification fixes (verification agent)

- **Pytest basetemp pinned to a repo-local dir.** `pyproject.toml` sets
  `addopts = "-q --basetemp=.pytest_tmp"`. Tests already use `tmp_path` and
  in-memory ledgers (no shared on-disk DB), but pytest's *default* basetemp is
  the global `%TEMP%\pytest-of-<user>` directory, which is shared across parallel
  agent worktrees and intermittently raised `PermissionError` during concurrent
  runs. Pinning basetemp into the repo makes a bare `pytest` invocation
  deterministic and isolated. `.pytest_tmp/` is git-ignored.

- **CLI now creates the parent run-ledger row.** The workflow modules persist
  child records (findings / LLM responses / validation / export artifacts) against
  a `run_id`, but did not insert the parent `runs` row themselves; that row was
  only created on the Streamlit path (`app/workflow_registry.run_workflow`). The
  CLI (`cli/run_workflow.py`) now calls `RunLedger.create_run(...)` up front with a
  shared `run_id` and marks the run `completed`/`failed` afterward, so a CLI run
  with `--db` is discoverable via `list_runs()`/`get_run()` (DoD item 8) on both
  entry points. Bookkeeping only — no calculation moved into the CLI.

- **Added the shared LLM provider wrapper (`src/llm/provider.py`).** The spec
  (Phase 0 structure, Phase 3.6) requires one provider wrapper exposing
  `generate_structured_response`, `generate_text_response`, and `mock_response`,
  with config-driven provider selection, env-var-only API keys, and mock as the
  default offline path. Each workflow previously carried its own inline
  `MockLLMProvider` but the canonical module was missing. The wrapper centralizes
  the interface (`MockLLMProvider`, `RealLLMProvider`, `get_provider`): the mock is
  deterministic and derives output only from the deterministic findings in the
  prompt (cites real source-row ids, never invents), and the real provider reads
  its key from the environment and refuses rather than fabricating when unset.
  Workflows still default to `provider=None` (their own mock); the wrapper is the
  single seam for wiring a real provider later without touching workflow modules.

## Structural consolidation into `src/core` and `src/context` (separation-of-concerns remediation)

The MVP was functionally complete and green (73 passing tests), but several
spec-required modules from the Phase 0 file tree were missing and their logic
was duplicated inside the workflow modules. This pass consolidated that logic
into the canonical homes WITHOUT changing any workflow's deterministic logic,
known-answer datasets, or the spec-named export files. After it, the suite is
**103 passed** (73 prior + 30 new unit tests for the new core/context modules).

- **`src/context/` was empty; now populated.** Added the three spec-named
  modules: `chart_of_accounts.py` (`load_chart_of_accounts(path) -> ChartOfAccounts`
  with `.codes` / `.names` / `.is_valid_code`; auto-detects the code column so it
  works with both synthetic COA schemas — `account_code` for report_review and
  `account` for budget_variance), `city_profile.py` (`CityProfile` dataclass with
  defaults `city_name="Sample City"`, `default_actor="finance_staff"`, loadable
  from dict/JSON/env), and `context_loader.py` (`load_context(context_dir, coa_path)
  -> dict` of available references, plus `get_context` / `load_available_context`
  / `load` aliases). Freeform's existing duck-typed lookup (it tries
  `load_context` / `load_available_context` / `load` / `get_context` with no args)
  now resolves to a real function, so Phase 5 context auto-injection works.

- **`src/core/validation.py` is now THE canonical validator.** Each workflow had
  its own `validate_llm_output` / `validate_llm` with overlapping logic. The
  canonical validator implements all Phase 2 rules (invented source row, account
  code not in source/context, numeric value not in deterministic results,
  final-approval language, omitted required references, invalid JSON when schema
  output is required) and returns the spec `ValidationResult`. Each workflow now
  keeps a **thin wrapper** with its original name/signature that delegates to the
  core validator, passing knobs that preserve each workflow's historical
  strictness: report_review uses the strict defaults (missing refs + approval
  language are hard errors, numeric claims are warnings); bank_reconciliation /
  budget_variance / freeform treat missing references as a warning and skip the
  numeric-claim check, matching their prior behavior. budget_variance receives an
  `LLMResponse` (not a bare dict), so its wrapper merges `LLMResponse
  .referenced_source_rows` into the response JSON before delegating.

- **`src/core/exports.py` holds the shared write primitives.** `write_markdown`,
  `write_csv` (DataFrame or list-of-rows, preserving source rows/columns),
  `write_json`, `sha256_text` / `sha256_file`, and `package_run(run_id, artifacts,
  exports_root)`. Each workflow still decides WHAT to write (content legitimately
  differs), but the file-writing + hashing now go through these primitives. While
  refactoring, a latent cross-platform bug was fixed: the writers now write with
  newline translation disabled (`newline=""`), so the bytes on disk equal the
  source text on every OS and the recorded `sha256` matches both `sha256_text`
  and `sha256_file` (previously, Windows translated `\n` -> `\r\n` on disk while
  the hash was computed from the `\n` text, so the manifest hash did not match the
  file). report_review's CSV stays on `pandas.to_csv` to preserve its column
  header on an empty frame; its md/json writes go through the core primitives.

- **`src/core/workflow_runner.py` is the canonical generic runner.** The spec
  Phase 0/3.3 names the generic driver `src/core/workflow_runner.py`; the working
  MVP grew it inside `src/workflows/registry.py` (which also bootstraps import-time
  self-registration). To satisfy the file layout without regressing imports, the
  new module thinly wraps the registry and exposes `run_workflow(name, inputs,
  **kwargs)` (auto-dropping kwargs a given workflow's `run` does not accept),
  `list_workflows` / `list_specs`, and `get_workflow` / `get_spec`. All existing
  imports from `src.workflows.registry` (CLI, `app/`, `src/eval/`, tests) keep
  working unchanged.

- **`MockLLMProvider` consolidated onto the canonical base.** The inline mocks in
  `bank_reconciliation`, `budget_variance`, and `freeform` are NOT functionally
  equivalent to the generic `src.llm.provider.MockLLMProvider`: each produces a
  workflow-specific output contract that its validator and known-answer tests
  depend on (bank cites only exception findings; budget adds `follow_up_questions`
  / `themes` and cites only flagged variances — a test pins exactly 2
  `categorized_exceptions`; freeform emits `draft` / `clarifying_questions` /
  `needs_human_review`). Rather than collapse them (which would regress those
  tests and the export content), each inline mock is now a **thin subclass** of
  the canonical `MockLLMProvider`, overriding only `_build` and inheriting the
  shared method surface, `model_provider` / `model_name`, and offline contract.
  The duplicated `_extract_findings_from_prompt` helpers were removed in favor of
  the canonical one in `src.llm.provider`. The default LLM path remains the
  offline deterministic mock and all three workflows still reach
  Validation: PASSED.

- **Residual deviations consciously left.** (1) `src/workflows/registry.py`
  remains the import-time bootstrap and the source of truth for `WorkflowSpec`s;
  `src/core/workflow_runner.py` wraps it rather than moving the registry wholesale,
  to avoid churning the many existing import sites — a deliberate
  smallest-safe-change choice. (2) report_review's CSV export intentionally still
  uses `pandas.to_csv` (not the shared `write_csv`) so the file keeps its column
  header on an empty frame.

## Run ledger threading: per-operation SQLite connections

**Decision.** `RunLedger` opens a fresh `sqlite3` connection per operation for
file-backed databases (created, used, and closed in the calling thread) instead
of holding one long-lived connection. In-memory (`:memory:`) ledgers keep a
single shared connection because an in-memory DB is private to its connection
and cannot be reopened; that path is test-only and uses
`check_same_thread=False`.

**Why.** Streamlit reruns the script on rotating ScriptRunner threads, and the
ledger is cached with `@st.cache_resource` (shared across reruns/sessions). A
connection created in one thread and reused in another raises
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be used
in that same thread`, which crashed the History, Review Run, and Export Center
pages on `ledger.list_runs()` / `get_run()`. Per-operation connections are the
simplest correct fix for a low-concurrency local audit store and are preferred
over sharing one `Connection` with `check_same_thread=False`.

**Adjacent change.** `AuditLog` previously reached into `ledger.conn` directly
(so the connection escaped the ledger and re-introduced the cross-thread
hazard). Audit reads/writes now go through public `RunLedger.append_audit_event`
/ `list_audit_events`, and child records are read via public
`list_findings` / `list_llm_responses` / `list_validation_results` (etc.)
accessors. No method, schema, or stored data changed. Covered by
`tests/unit/test_run_ledger.py` (round-trips + cross-thread + concurrent-write
regression tests).

## Post-MVP polish pass (Tier 1 roadmap)

Scope: demoability, exports, and audit/CPRA readiness — no Tier 2/3 features, no
new heavy dependencies, no change to deterministic workflow logic.

- **Bank reconciliation re-enabled in the Streamlit UI.** The UI registry
  (`app/workflow_registry.py`) previously marked bank reconciliation
  `available=False` with a stale note ("module not implemented"). The module,
  synthetic data, CLI subcommand, and eval coverage all existed and passed, so
  the flagship MVP workflow was simply hidden from the app. Fixed: descriptor is
  now `available=True` with example files, and a `_run_bank_reconciliation`
  adapter (mirroring report_review/freeform) drives it. All four workflows now
  run end-to-end from the UI.

- **Settings now thread into runs.** `app/workflow_registry.run_workflow` gained
  a `config` parameter; the Settings page builds it (`_settings_to_config`) and
  passes it. `_config_for` maps it onto each workflow (bank tolerances; budget
  thresholds). Uploaded config/threshold files always take precedence over
  Settings. Previously the Settings tolerances/thresholds were saved but never
  used — the page was decorative.

- **Per-run export isolation.** The app wrote every run's artifacts into one flat
  `export_dir`, so runs overwrote each other (Export Center download links for
  older runs pointed at newer content). Runs now write to
  `export_dir/<run_id>/`. (The CLI already used explicit per-invocation dirs and
  is unchanged.)

- **Consolidated review packet (`src/core/review_packet.py`).** Every completed
  run now also gets `review_packet.md` (human-readable) + `run_manifest.json`
  (machine-readable), built deterministically from persisted ledger data — no
  LLM call. They separate run metadata, source-file hashes, deterministic
  findings, AI draft (labelled), validation, reviewer notes, approval status,
  and audit history. Regeneratable from the Export Center for ANY run (reflects
  the latest review actions), replacing the old report_review/freeform-only
  regenerate path. Tradeoff: no PDF — markdown is dependency-free, diffable, and
  demo-friendly; a PDF renderer would add a heavy dependency for little MVP gain
  (left to the roadmap).

- **Uniform export-artifact recording.** budget_variance and report_review wrote
  their artifacts to disk on the app path but never recorded them in the ledger
  (only bank/freeform self-stored). `_ensure_export_artifacts_recorded` now
  records any workflow's artifacts idempotently (by file name, with sha256), so
  Export Center / the manifest see a complete artifact set for every workflow.

- **Consistent audit lifecycle.** bank_reconciliation and freeform emit
  `run_created` / `run_completed` / `export_generated` internally; the registry
  also emitted them, so freeform already had duplicate events. A small
  `_LifecycleSuppressingAudit` proxy makes `run_workflow` the single owner of
  those runner-level events while granular events still flow from the workflow,
  yielding one consistent, non-duplicated audit trail across all four workflows.

- **AI Audit Log (Tier 1: searchable AI interaction history).** New
  `RunLedger.list_llm_interactions()` joins each stored LLM response with run +
  review metadata and derives draft-vs-final status (`approve_draft` -> final,
  `reject_ai_explanation` -> rejected). A new Streamlit page filters by workflow
  / draft status / free-text and shows model provider+name, prompt-template
  version, and validation status. This is a CPRA-style AI-usage review surface,
  not a public-records request platform.

- **Import presets (Tier 1: import adapters).** `src/ingest/presets.py` adds a
  generic, local-file-only column-alias layer mapping ERP-style headers
  ("Posting Date", "GL Date", "Transaction Amount", ...) onto the canonical
  names the workflows expect. Opt-in (does not change the core pipeline) and
  demonstrated by `data/synthetic/bank_reconciliation/erp_style_bank.csv`. The
  vendor presets (Tyler/Munis, OpenGov) are illustrative placeholders, not real
  schemas — no API integration or authentication, per the constraints.

- **UX polish.** Run Workflow shows a spinner plus findings / validation /
  artifact metrics and a validation note; Review Run shows an AI draft-vs-final
  metric; History shows a run count and short ids; Settings notes that
  tolerances apply to new runs; error/empty states give finance-staff-friendly
  guidance.

Manual verification (this pass): `pytest` 135 passed; all 8 Streamlit pages
render via `streamlit.testing.AppTest`; bank reconciliation runs end-to-end from
the Run Workflow page (8 findings, validation passed, 8 artifacts incl. the
packet); a per-finding Approve button and the Export Center packet button work;
secret/PII sweep over source + data + exports is clean.

## Import presets wired into the Run Workflow UI

Follow-up to the post-MVP pass: the column-alias presets (`src/ingest/presets.py`)
are now reachable from the UI, making the import-adapter capability demoable.

- **Per-upload "source format" selector.** Each CSV *data* input on the Run
  Workflow page (not JSON config files, not freeform's mixed uploads) gets a
  small selectbox: *Standard / auto-detect* (default), *Generic ERP export*,
  *Tyler/Munis-style*, *OpenGov-style*. Selecting a preset column-aliases that
  file's headers onto the canonical names before the workflow reads it.

- **File-rewrite strategy (workflow-agnostic).** When a preset is selected,
  `workflow_registry._apply_source_formats` writes an *aliased copy* of the CSV
  (`src.ingest.presets.normalize_csv`) and passes that path to the workflow. All
  three tabular workflows read CSV paths (report_review requires a path), so a
  rewrite is the uniform, lowest-risk choice and the deterministic workflow logic
  is completely unchanged. Presets are applied to uploaded files only — example
  files are already in standard format.

- **Source of record preserved.** `run_workflow` builds input-file metadata
  (SHA-256 hashes) from the ORIGINAL uploads BEFORE aliasing, so the audit
  trail/manifest hash the file the user actually provided. The applied preset is
  recorded in the run summary (`source_formats`) and a `file_parsed` audit event,
  so a reviewer can see both the original file and the normalization applied.
  Tradeoff considered: hashing the aliased copy would be simpler but less honest
  for CPRA review; recording the original + the preset is the defensible choice.

- **Scope.** Only the bank-reconciliation ERP sample
  (`data/synthetic/bank_reconciliation/erp_style_bank.csv`) is shipped/tested
  end-to-end; the selector is available for all CSV inputs but ERP samples for
  budget/report were out of scope. Presets remain generic, local-file column
  maps — no vendor API, no authentication. A preset failure never blocks a run
  (falls back to the raw file).

Tests: `test_presets.normalize_csv*`; `test_app_registry` proves the ERP file
reconciles with the preset, is rejected without it, records `source_formats` +
`file_parsed`, and keeps the original file hash. An AppTest confirms the
selectors render (one per CSV input, four choices each). A test-isolation fixture
redirects the guided-freeform discovery log to a temp file so the integration
tests never append to `docs/research/freeform_task_observations.md`.

## Import presets extended to budget & report + applied-preset surfacing

Follow-up extending the import-preset capability across all three workflows and
making the applied preset visible everywhere it matters.

- **Generic preset now covers budget + report dimensions.** Added 7 purely
  additive aliases to `GENERIC_ERP` (no existing mapping changed): budget join
  dims `account_key→account`, `cost_center→department`, `object_class→object`;
  report dims `statement_section→section`, `account_description→account_name`,
  `row_type→line_type`, `reported_amount→amount`. The account-vs-account_code
  ambiguity (budget uses `account`, report uses `account_code`) is avoided by
  giving each domain a distinct ERP source name (`account_key` vs `account_no`),
  so one generic preset serves all three workflows without conflict.

- **ERP-style samples for all three workflows.**
  `data/synthetic/budget_variance/erp_style_{budget,actuals}.csv` and
  `data/synthetic/report_review/erp_style_report.csv` mirror the standard
  samples with ERP headers. With the preset they reproduce the IDENTICAL
  deterministic findings as the standard files (asserted by equivalence tests);
  without it they fail cleanly (budget → no join keys; report → no `section`
  column), so the demo shows the preset doing real, necessary work.

- **Value-preservation fix in `normalize_csv`.** Plain `pd.read_csv` coerced
  integer-like account codes with blank subtotal rows to floats ("5010" →
  "5010.0"), which then failed to match the chart of accounts / prior version
  (11 false "invalid code" findings instead of 1). Fixed by reading with
  `dtype=str, keep_default_na=False` so aliasing renames columns ONLY and never
  rewrites values; the workflows still parse amounts/dates themselves. This made
  the ERP report run exactly equivalent to the standard run.

- **Applied preset surfaced everywhere.** The review packet's source-files
  section (`review_packet.md` §2) now notes which presets were applied and that
  the listed hashes are the ORIGINAL uploads; `run_manifest.json` gained a
  top-level `source_formats` field; and the Review Run page shows the applied
  presets in the Input files area. (The Run Workflow result already showed them.)

Scope held: presets remain opt-in (never auto-applied), only the standard-data
column contracts are reused (no workflow logic changed), and the new aliases are
additive so existing standard runs and the bank ERP demo are unaffected.
Tests: +7 (147 total) — new-alias coverage, value preservation, budget/report
ERP equivalence + fail-without-preset, and packet/manifest surfacing.

## Tier 1 completion (near-term extensions)

Final Tier 1 feature set, with the key decisions and tradeoffs actually made.
Scope held throughout: no new third-party dependencies, no change to
deterministic workflow logic, synthetic data only, and the deterministic /
LLM-only separation preserved (none of these features lets the model calculate,
match, approve, or write final official language).

- **Retention category (`RetentionCategory` enum).** Each run is tagged with a
  records-retention category. The five values
  (`draft_working` [default], `transitory`, `administrative_record`,
  `audit_record`, `permanent`) are a deliberately small, government-records-
  oriented set rather than a full retention-schedule taxonomy — enough to
  demonstrate the concept and drive the review packet/manifest without modeling a
  jurisdiction-specific schedule. Unknown values fall back to `draft_working`
  (fail-safe). **Safe ledger migration:** the `runs` table gains a
  `retention_category TEXT` column via a guarded `PRAGMA table_info` check +
  `ALTER TABLE ... ADD COLUMN` at DB init, so existing on-disk ledgers migrate
  automatically and NULLs default-fill to `draft_working` on read — no destructive
  rebuild. It is persisted on `WorkflowRun`, mirrored into `summary`, added as a
  `run_created` audit detail, and surfaced in `review_packet.md` §1 and
  `run_manifest.json` (top-level). Code: `src/core/schemas.py`,
  `src/core/run_ledger.py`, `src/core/review_packet.py`,
  `app/workflow_registry.py`.

- **Exportable AI usage log (`src/core/ai_usage_log.py`).** A deterministic
  exporter that flattens each stored LLM interaction (run, workflow, model
  provider/name, prompt-template version, validation status, draft-vs-final
  state, referenced-source-row count) into one row, writing `ai_usage_log.csv`
  and/or `ai_usage_log.json`. Built on the existing
  `RunLedger.list_llm_interactions()` join rather than a new query path. This
  makes the CPRA-style AI-usage surface downloadable for an external reviewer;
  empty ledgers produce a header-only CSV / empty JSON list (no crash).

- **Prompt/response diffing (`src/core/diffing.py`).** Compares two stored AI
  interactions: prompt-template change, model change, a unified text diff of the
  draft summary, and referenced-rows added/removed. **Decision: stdlib `difflib`
  only** — no third-party diff library — keeping the no-new-dependency rule and
  staying fully deterministic. Pure functions; no LLM call (the diff is
  mechanical, not model-judged).

- **PDF summary export (`src/core/pdf_export.py`).** A **pure-Python PDF writer**
  that hand-emits a valid PDF 1.4 file (catalog, pages tree, content streams,
  byte-accurate xref, trailer). **Why hand-written:** the constraints forbid new
  third-party dependencies (no reportlab/fpdf), and a review packet is plain text,
  so a minimal writer is sufficient and dependency-free. **Limits (by design):**
  text-only, single base-14 font (Courier, no font embedding), no images/colors/
  markdown rendering, Latin-1/ASCII only (un-encodable chars become `?`), fixed
  character-count wrapping and fixed lines-per-page pagination. This resolves the
  earlier "no PDF" tradeoff noted in the post-MVP packet decision without taking on
  a heavy dependency.

- **Chart-of-accounts import preset.** Added a `chart_of_accounts` preset to
  `src/ingest/presets.py` mapping ERP-style COA headers
  (e.g. `GL Code`, `Account Title`, `Balance Type`) onto canonical
  `account_code` / `account_name` / `normal_balance` (plus `fund` when present).
  Consistent with the existing opt-in, local-file-only, value-preserving preset
  design (column rename only, never value rewrite); registered in
  `SOURCE_FORMAT_CHOICES`. Synthetic sample:
  `data/synthetic/report_review/erp_style_chart_of_accounts.csv`.

- **Role-specific views (`app/role_views.py`).** A sidebar role selector (AP
  clerk, Accountant, Finance analyst, Finance director) reorders and emphasizes
  findings for the selected role. **Decision: presentation-only, not
  authentication** — it is a non-destructive ordering/emphasis layer that never
  hides, filters out, or deletes data (a "Show all findings" toggle always
  restores full order), so it carries no security/access-control claim. This
  honors the "no production auth" constraint while still demonstrating
  role-tailored review. Lives in the app layer only; no core/workflow code role-
  aware.

- **Redaction assist — regex PROTOTYPE (`src/core/redaction.py`).** A
  deterministic regex scanner/redactor for SSN, email, phone, credit-card, and
  long-number (account-like) patterns, with most-specific-first precedence
  (e.g. 16-digit → credit_card, 9-digit → long_number) and masked previews.
  **Explicitly a demonstration prototype, NOT a compliance/public-records
  redaction tool:** regex PII detection has false positives/negatives, covers only
  a handful of patterns, and makes no completeness guarantee. The UI page carries a
  synthetic-only PROTOTYPE warning. It does not redact stored runs or exports — it
  is an assist surface for review.

- **Scheduled runs (`src/core/scheduler.py`).** Local, **manual-trigger** recurring
  schedules (monthly / quarterly / before-agenda / custom-interval) persisted to a
  JSON file (`runs/schedules.json`). **Decision: no daemon, no cron, no background
  process.** `advance_due` is a pure function that takes the reference date as a
  parameter and never reads the system clock (the UI passes `date.today()` only at
  the app boundary), keeping the core deterministic and testable. Schedules are
  recorded and surfaced as "due"; the user clicks **Run now** to execute — there is
  no autonomous execution, consistent with the local-first, human-in-the-loop,
  no-background-service posture of the MVP.

### Intentionally NOT built

- **No full CPRA / public-records platform.** The AI usage log export and the
  redaction prototype are review/demonstration surfaces, not a public-records
  request intake, tracking, or compliant-redaction system. Compliant redaction,
  request workflows, and legal review remain out of scope.
- **No retention-schedule engine.** `RetentionCategory` is a tag, not a
  disposition/destruction scheduler; the MVP does not auto-expire, archive, or
  destroy records.
- **No PDF rendering engine.** The PDF writer is intentionally text-only with no
  fonts/images/markup; rich rendering is out of scope.
- **No authentication / access control.** Role views are presentation-only.
- **No scheduler daemon / background automation.** Scheduling is manual-trigger
  only; no cron, no service, no autonomous runs.
- **No new third-party dependencies.** Every Tier 1 feature uses the existing
  stack (stdlib + pandas/pydantic/openpyxl/streamlit).
