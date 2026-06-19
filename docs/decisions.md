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

## Preflight / capability layer

A reusable preflight / capability layer (`src/core/preflight.py`, preflight
models in `src/core/schemas.py`, messy-data helpers in
`src/normalize/cleaning.py`) lets each workflow decide whether an uploaded file
set can be run before any deterministic logic or LLM call. Key decisions and
tradeoffs:

- **Fail-closed by default.** A FAIL is a refusal, not a fallback: the workflow
  does not run and the **LLM is never called**. The deterministic core cannot
  produce correct findings from missing/unparseable required inputs, so the model
  must not be given a chance to guess at calculations, matches, or values. On
  FAIL the user receives a structured report (`PreflightReport`) with file
  profiles, blocking conditions, and de-duplicated next steps instead of any AI
  text. The validation layer reinforces this: `validate_with_preflight` flags any
  LLM output present on a FAIL run as an error.

- **Three-state PASS / PARTIAL / FAIL rule derived purely from findings.** Status
  is a function of the `PreflightFinding`s, not bespoke per-workflow logic. **FAIL**
  iff any finding has `blocks_run=True` (missing required file, unsupported type
  on a required input, missing/ambiguous required column, low date/amount parse
  confidence on a required column, or an unresolved required mapping —
  `NEEDS_HUMAN_CONFIGURATION`). **PARTIAL** iff no blocking finding but ≥1
  non-blocking, non-`INFO` finding (a domain `possible_*` condition, optional-column
  ambiguity/low-confidence, optional-input unsupported type, or
  `UNSUPPORTED_PATTERN_DETECTED`). **PASS** iff only informational findings remain.
  `llm_allowed = (status != FAIL)`, `partial = (status == PARTIAL)`.

- **Conservative messy-data handling — flag, do not silently fix.** The cleaning
  helpers normalize columns, detect semantic columns, score date/amount parse
  confidence, clean currency/comma/parenthesis/negative amount formatting, and
  detect repeated headers, footer/total rows, and duplicate rows — all while
  preserving positional source-row indices. Structural ambiguities that would
  change a calculation (sign conventions, batch deposits, embedded subtotals,
  pivoted layouts) are **surfaced as PARTIAL conditions for human review, never
  auto-resolved**. Detectors are conservative: they return no findings on clean
  data, never block a passing demo, and a crashing detector is caught by the
  engine so it can never break preflight.

- **Human-approved column mapping only when ambiguous.** Confidently
  auto-detected columns (confidence ≥ `0.85`, unambiguous) are mapped silently and
  are never surfaced for manual mapping. A mapping UI / `--mappings` override is
  requested only for a required semantic column that is missing or ambiguous. Only
  `source == "human"` mappings are forced onto the workflow; the engine continues
  to auto-detect the rest, which preserves the existing "ERP file without a
  preset is rejected" behavior rather than masking it.

- **The LLM never takes over failed logic; PARTIAL is constrained.** On PARTIAL
  the LLM is allowed but may only explain the deterministic findings; it may not
  claim to have resolved a flagged unsupported condition
  (`validate_partial_resolution_claims` flags a resolution verb tied to an
  unsupported-condition topic). On FAIL it is not invoked at all.

- **Guided Freeform stays draft-only and is not an automatic fallback.** A FAIL
  never silently routes to Guided Freeform. The UI may offer it behind a
  deliberate, clearly-labeled user action stating it is a separate exploratory
  draft-only mode, not a re-run of the failed workflow. Freeform itself fails
  closed (blocking `NEEDS_HUMAN_CONFIGURATION` when sensitivity is unconfirmed or
  task type is blank).

- **Preflight findings represented as deterministic findings on PARTIAL.** When a
  PARTIAL run proceeds, the non-blocking preflight findings are converted to
  `DeterministicFinding`s (`finding_type=OTHER`, `rule_used="preflight:<code>"`,
  `requires_human_review=True`) and appended to the workflow result, so they flow
  through the same review/audit/export path as the workflow's own findings rather
  than living in a parallel structure.

- **One engine, per-workflow capability spec.** Each workflow exposes a
  module-level `CAPABILITY: CapabilitySpec` and a
  `detect_conditions(profiles, mappings, inputs, config)` that adds its domain
  findings; the shared `run_preflight(...)` emits the generic findings and applies
  the status rules. This keeps the three-state logic in one place and free of
  Streamlit / provider code (the workflow modules contain neither). The runner
  (`app/workflow_registry.run_workflow`) is the single owner of the
  FAIL/PARTIAL/PASS branch; the CLI and UI inherit it rather than re-deriving it.

### Intentionally NOT built (preflight)

- **No new workflows, agents, vector DBs, ERP integrations, OCR, or auth.** The
  preflight layer adds capability detection only; it does not add data sources or
  parsing modes.
- **No automatic data repair.** Messy-data handling detects and flags; it does not
  silently rewrite values, infer missing columns, un-pivot reports, or resolve
  rollups/batches/sign conventions on the user's behalf.
- **No LLM-driven capability decisions.** PASS/PARTIAL/FAIL and all column
  detection are deterministic; the model is not consulted to decide whether a run
  can proceed.
- **No Guided-Freeform auto-fallback.** Freeform remains a separate, deliberately
  chosen, draft-only mode.

## Tyler ERP enablement and four review workflows (2026-06-11)

This section records the design decisions made when the four Tyler/Munis-era
workflows were added. All changes were additive; no existing workflow logic,
test, or schema was changed.

### Tyler normalizer design (`src/ingest/tyler.py`)

**Dataset-type registry.** `TYLER_DATASET_TYPES` is a frozen-dataclass registry
keyed by `dataset_type` string. Each entry carries: required and optional column
tuples, a `aliases` dict (snake_cased Munis variant -> canonical name), date and
amount column tuples, and the ERP module name. There are eight types: `gl_detail`,
`ap_invoice_detail`, `vendor_list`, `check_register`, `purchase_orders`,
`budget_to_actual`, `chart_of_accounts`, `je_upload`. Keeping them in a typed
registry (rather than branched if/else code) lets detect/normalize/validate run
uniformly across all types with no per-type dispatch in the normalizer itself.

**Header detection.** `detect_dataset_type` snake_cases and alias-resolves all
headers, then computes a confidence score per registered type
(0.8 * required-hit-ratio + 0.2 * optional-hit-ratio). Returns the best type
only when score >= 0.7 AND margin above the second-best candidate >= 0.05.
Below either threshold it returns `None` (fail-closed: a caller that does not
pass `dataset_type` explicitly and gets None raises a `ValueError`). For Excel
files with Munis-style title blocks (e.g. a 3-row title block before the real
header), the normalizer scores the first 10 rows as candidate headers and picks
the row with the highest confidence; `header_row_used` records this 0-based
sheet row so the absolute file row of every data row can be reconstructed.

**Debit/credit derivation.** When separate debit and credit columns are present
and no signed amount column exists, the normalizer derives
`amount = debit - credit` (Decimal math; blank side treated as 0; both-blank
stays blank). Original columns are kept. This matches the implicit sign
convention in Tyler GL detail and JE upload exports. Decision: derive at
normalizer time (not in each workflow) so every downstream consumer sees a
consistent signed amount without duplicating the debit/credit logic. The same
derivation was back-ported to `src/normalize/cleaning.derive_signed_amount` for
use by the generic preflight engine on non-Tyler files.

**Traceability.** The raw file is SHA-256 hashed into `InputFile` before any
normalization. The resulting `TylerNormalizedExport` carries `input_file`,
`dataset_type`, `table_name`, `dataframe`, `header_row_used`, `applied_aliases`,
`warnings`, and `detection_scores`. Every data row in the frame carries
`source_row_index` (0-based data-row position). Footer-total and repeated-header
rows are flagged in `warnings` but never dropped.

**`source_ref_for_row` helper.** Constructs a `SourceRowRef` from an export and
a `source_row_index` value so workflows can cite specific rows without
duplicating the file_id/table_name plumbing.

**Fail-closed.** Unknown `dataset_type`, undetectable type, missing required
columns, unsupported file extension, unparseable file, or an unparseable
non-blank date/amount cell (outside flagged footer/repeated-header rows) all
raise a `ValueError` with "fail closed" in the message. No partial output is
returned on error.

### Natural-language transaction search: LLM boundary decision

The transaction search workflow has two stages:

1. **Stage 1 (intent parse):** the LLM (or the deterministic mock parser on the
   offline path) proposes a `SearchCriteria` pydantic v2 model. The proposal is
   schema-validated (module enum, ISO dates, non-negative amounts), range-sanity-
   checked (date_from <= date_to, amount_min <= amount_max), and invalid fields
   are dropped to `unparsed_terms` without crashing. If nothing parseable is
   extracted, the workflow returns a structured failure report without executing a
   search.

2. **Stage 2 (execution):** the validated `SearchCriteria` is applied as
   deterministic pandas filters. Filter order: module, vendor (casefold substring
   or difflib fuzzy >= 0.85), invoice/PO/check number (exact after casefold),
   fund/department/object (exact after casefold), date range, amount range (on
   absolute value of best amount column), keywords (any keyword is a substring of
   any text column -- OR semantics). Results are capped at `max_results` (default
   200) with an explicit truncation finding.

**Why schema-validated criteria, not free execution.** The LLM proposing search
criteria that are then deterministically executed (rather than the LLM executing
the search itself) is the correct boundary: it lets the model handle the natural-
language-to-structured-criteria translation task it does well, while keeping the
actual row filtering (which must be reproducible and auditable) in deterministic
code. The `SearchCriteria` model is the explicit contract; it is exported in
`search_criteria.json` so a reviewer can see exactly what criteria were applied.

**Mock parser.** The offline path uses a regex + keyword mock parser. Amount
patterns run on the raw query (before punctuation normalization) so "$5,000" is
not fragmented. Vendor names are extracted by substring match against a
hard-coded list (Riverbend dataset vendors). OR semantics on keywords mean a
query with one common keyword does not false-positive too broadly; the 200-result
cap mitigates runaway searches.

### JE upload prep: fail-closed contract

The JE upload prep workflow is strictly fail-closed with respect to the upload
workbook: `je_upload.xlsx` and `je_upload.csv` are **not written** unless every
blocking validation rule passes. This is a one-way gate: a partial upload file
(some lines valid, some not) is more dangerous than no file at all, because a
finance staff member might not notice the invalid lines were omitted. On a
blocking error, only the error report artifacts are written
(`je_validation_errors.csv`, `je_prep_summary.md`, `validation_report.json`,
`audit_log.json`), and the summary carries `upload_ready=false`.

**Blocking vs warning split.** Rules that are absolute constraints (balance,
valid date, active account, no negative amounts, no duplicate journal-line) are
blocking. Rules that are advisory (combo plausibility, short description,
round-dollar large amount) are warnings that do not block the upload but appear
in the error report. This split was chosen to match the risk level: a
debit-credit imbalance is always wrong; a round-dollar amount over $10,000 is
unusual but not necessarily incorrect.

**Tyler normalizer integration.** The JE draft is loaded via
`normalize_tyler_export` with `dataset_type="je_upload"`, which handles the Munis
"Eff Date" alias and the debit/credit columns. The chart of accounts is loaded
with `dataset_type="chart_of_accounts"`. This gives the workflow the same
traceability (SHA-256 hash, `source_row_index`) as the other Tyler-era workflows
with no extra parsing code.

### AP duplicate review: deterministic checks and thresholds

Nine deterministic checks (D1, D1b, D2-D8) are implemented as pure Python/pandas
operations on Tyler-normalized AP exports. Key design decisions:

- **D1b (multi-check detection)** uses the check register's `invoice_numbers`
  column, which Tyler formats as semicolon-joined multiple invoice references on
  batch checks. Void checks (Status=Void) are excluded: a void-and-reissue pair
  is a normal workflow, not a suspicious multi-payment.
- **D3 (similar vendor names)** uses difflib SequenceMatcher after stripping
  common legal suffixes (LLC, Inc, Co, Company, Corp) so suffix variations do
  not prevent a match. Threshold default 0.88 (configurable).
- **D8 (split payments)** uses a sliding window (default 3 days) over date-sorted
  same-vendor invoices. O(n^2) per vendor, which is fine at typical AP file sizes.
- **Void checks excluded from D1b** but not from other checks: a check in the
  register may be void, but a payment-before-invoice-date (D5) check on a voided
  check is irrelevant because the payment did not proceed. The implementation
  filters void checks specifically for D1b and D5.
- **INFO findings for absent optional files.** When vendor_list or check_register
  is not provided, the checks that depend on them (D6/D7, D1b/D5 respectively)
  emit an explicit INFO finding rather than silently skipping. This ensures a
  reviewer knows which checks ran and which did not.

### PO/invoice mismatch review: deterministic checks and thresholds

Eight deterministic checks (P1-P8) plus a P3b (blank PO over threshold variant)
join Tyler purchase-order and AP invoice exports. Key design decisions:

- **P1 uses PO-level totals.** Total invoiced against a PO number is compared to
  the sum of all PO line amounts, not to individual lines. This is the correct
  comparison for Munis-style exports where a single invoice can reference a
  multi-line PO.
- **P5/P6 use best-matching PO line.** For invoices with qty/unit-price detail,
  the best matching PO line is the one whose qty matches the invoice qty, falling
  back to line 1. This is a heuristic; a PO with identical-price lines could
  theoretically match the wrong line, though the synthetic dataset is designed so
  this does not occur.
- **P7 is informational.** "Received not invoiced" (received_qty > 0, invoiced_qty
  == 0, no AP invoice) is a likely accrual candidate, not a payment error. It has
  severity LOW and `requires_human_review=False`.
- **P3 split into P3a and P3b.** P3a flags invoices referencing a PO number that
  does not exist in the PO file (hard missing). P3b flags invoices with a blank PO
  number and amount >= threshold (missing reference over threshold). These are
  distinct conditions: P3a is always an error; P3b is policy-dependent.

### Tyler/Munis readiness for the three original workflows

The three original workflows (bank reconciliation, budget variance, report review)
were extended to accept Tyler-normalized exports alongside the generic CSV/Excel
path:

- **Debit/credit -> signed amount.** `src/normalize/cleaning.derive_signed_amount`
  derives a signed amount column from separate debit/credit columns when the file
  has no amount column already. Wired into `src/core/preflight.profile_input`
  (derivation noted in FileProfile) and `bank_reconciliation.reconcile`. Source
  row refs still cite the pre-derivation frame so audit values match the raw file.
- **Exact-name semantic precedence.** `best_semantic_column` now breaks a near-tie
  in favor of a column whose snake_cased name exactly equals the semantic name
  (e.g. derived/literal `amount` beats the `debit`/`credit` synonyms) instead of
  lowering confidence to ambiguous.
- **Combined budget-to-actual file.** Budget variance now accepts the same file
  as both `budget` and `actuals` inputs. The `budget` side reads `revised_budget`
  (preferred) or `original_budget`; the `actuals` side reads `ytd_actual`.
  Decision: deliberately did NOT alias `revised_budget`/`ytd_actual` onto
  `*_amount` in the Tyler preset -- doing so would create a two-candidate amount
  ambiguity that preflight correctly blocks on; the preference-list path avoids
  that trap.
- **Excel made real.** `src/ingest/excel_loader.load_table` dispatches CSV vs
  XLSX; all three original workflows use it so `.xlsx` inputs are fully
  supported end-to-end (not just accepted in the capability spec).

### What is simulated vs what is real

The synthetic data, column aliases, and detection logic are modeled on observed
Munis-style export shapes, but they are not validated against a real Tyler/Munis
system or a real city's configuration. Specifically:

- Column names and header aliases in `src/ingest/tyler.py` are based on common
  Munis export conventions, not confirmed vendor documentation.
- The "City of Riverbend" dataset is entirely fabricated; all anomalies were
  planted deterministically and the known answers are generated from the same
  CSV files (no independent oracle).
- The JE upload template headers (`Journal, Line, Eff Date, ...`) match a common
  Munis format but have not been confirmed against a real city's JE import spec.
- The `data/synthetic/tyler/gl_detail.xlsx` title-block exercise (3-row block,
  header at sheet row 4) is synthetic; real Munis XLSX exports may have different
  block structures.

### Intentionally NOT built (Tyler ERP enablement)

- **No direct ERP write-back.** The JE upload prep workflow produces a validated
  upload file; it does NOT upload that file to Munis or any other ERP. The human
  takes the `je_upload.xlsx` and uploads it through the ERP's normal import
  interface. This is a deliberate audit boundary: a human reviews and approves
  the validated file before it enters the ERP. An automated write-back would
  bypass that gate.
- **No real Tyler API calls.** There are no HTTP requests, no OAuth flows, no
  Tyler API credentials, and no Tyler SDK in this codebase. All data arrives as
  local CSV/XLSX files.
- **No production authentication.** The tool has no user accounts, roles, or
  access-control enforcement. Role views in the Streamlit UI are presentation-
  only (reorder/emphasize, never hide or delete data) and explicitly not
  authentication.
- **No real credentials or sensitive data.** Consistent with the synthetic-data
  constraint of the entire MVP.

### Remaining Tyler-specific requirements

The following items are not implemented and require external input or confirmed
real data before they can be addressed:

1. **Real Munis export templates.** Column header spellings, date and amount
   locale formats, and title-block layouts need to be validated against actual
   Munis export files from a real city. The current aliases (in
   `TYLER_DATASET_TYPES` and `src/ingest/presets.py` `TYLER_MUNIS_STYLE`) are
   modeled, not vendor-confirmed.
2. **City/vendor field-mapping confirmation.** Before using the JE upload prep
   workflow on a real Munis system, the exact column order and header spelling
   for the JE import template must be confirmed with the city's Tyler contact.
3. **GL cash-account sign convention.** The normalizer derives `amount = debit -
   credit`; the bank reconciliation extension assumes this convention. A city
   should confirm this is correct for their GL before reconciling real exports.
4. **Combined budget-to-actual basis.** The code deterministically prefers
   `revised_budget` over `original_budget` when both are present. A city must
   confirm which basis they want (original vs revised) before using the budget
   variance workflow on a real Munis budget-to-actual export; a column mapping
   can override the preference.
5. **Tyler .xlsx exports with multi-row title blocks.** Files like
   `data/synthetic/tyler/gl_detail.xlsx` (header at sheet row 4) require routing
   through `src.ingest.tyler.normalize_tyler_export`; the generic preflight
   file profiler assumes header at row 0 and will profile the title-block rows
   as data. Tyler-style workflows handle this internally, but a generic upload
   of such a file through the non-Tyler workflow path would need a UI/CLI
   warning or pre-processing step.
6. **Legacy .xls (BIFF) exports.** The normalizer supports `.csv` and `.xlsx`
   only (no `xlrd` dependency). Real Munis exports saved as old-format `.xls`
   must be re-saved as `.xlsx` before use.
7. **Real LLM provider for transaction search Stage 1.** The offline mock parser
   uses regex + keyword heuristics. A real LLM provider would improve coverage
   of edge-case queries; the two-stage architecture supports this without changes
   to Stage 2 (the deterministic execution is unchanged regardless of how
   `SearchCriteria` is populated).
8. **Tyler API / SaaS integration.** Scheduled data pulls, webhook-triggered
   runs, and direct ERP query are out of scope for this local-file-only MVP.
   Any such integration would require API docs, credentials, authentication, and
   a security review.

## FastAPI seam + React workflow console (2026-06-11)

This section records the design decisions made when the FastAPI adapter
(`api/`) and React/Vite/TS guided console (`frontend/`) were added on top of
the full MVP (Streamlit + CLI + 705 tests).

### Motivation: UX pain points of the Streamlit interface

Streamlit was the correct choice for the initial MVP: it gave the project a
working, end-to-end runnable UI with uploaders, tables, and per-finding review
controls in a very short cycle, which validated that the workflow logic, audit
model, and trust-boundary concepts worked. For developer exploration it still
works well.

For non-technical municipal finance staff, however, the Streamlit layout has
several concrete problems:

- A blank "Run Workflow" form with no indication of what step to do first. A
  first-time user sees file uploaders, toggles, and a Run button but no
  sequencing guidance.
- The AI draft, deterministic findings, validation warnings, and source-row
  evidence all appear on the same page in Streamlit's widget stack. The visual
  separation is present but easy to miss on a dense page.
- Action intent is unclear: the review controls (mark reviewed, reject AI,
  approve draft) are a set of selectboxes; it is not obvious what happens
  when you change one.
- The preflight report, run status, and export links are scattered across
  headings and `st.expander` blocks, so a reviewer who wants to download an
  artifact has to hunt for the Export Center.

The React console addresses these with: a four-step wizard (upload -> file
check -> run -> review), progressive disclosure (summary strip first, evidence
one click deep), an always-visible, always-labeled AI container
(TrustBoundary), and explicit per-finding review action buttons.

### Contract-first approach

The API contract (`docs/frontend/api_contract.md`) was written before `api/`
was coded. The contract defines the exact JSON shapes, status codes, and
behavioral guarantees that the frontend can rely on. The API was then
implemented to match the contract, and the frontend was built against the
contract rather than the implementation. This means:

- The API's RunDetail is always rehydrated from the ledger (not from any
  in-memory state), so a POST /runs response and a GET /runs/{id} after a
  process restart are produced by exactly the same code path and cannot drift.
- The contract explicitly labels findings as deterministic and ai as advisory
  draft, so the frontend has a typed signal for which container to render
  content in; it never has to guess.
- All deviations from the initial draft contract are documented in the contract
  file itself (two clarifications: interrupted-run status mapping, freeform
  confirmation fields), so the frontend can be built against a stable document.

### What was intentionally NOT built

- **No authentication or authorization.** The tool is local-first, single-user,
  synthetic-data-only. Adding real auth requires a security design, secret
  management, session handling, and multi-user data isolation that are all out of
  scope for this MVP. The role selector in Streamlit is presentation-only and is
  not replicated as an auth mechanism in the API or React console.
- **No tenanting.** One SQLite ledger, one audit directory, one export
  directory. Run history is shared by all surfaces (API, Streamlit, CLI).
- **No ERP integration.** The API receives local file uploads; it does not pull
  from Tyler/Munis or any ERP. The file-based ingest layer is designed so ERP
  adapters can be added later without touching the pipeline.
- **No vector DB.** Reference data is small enough to load from files at run
  time. RAG over a large document corpus is documented as a post-MVP path.
- **No multi-agent product behavior.** There is one deterministic pipeline
  driving every workflow through one uniform registry. The LLM is called once
  per run for the language tasks defined by the workflow.
- **No WebSocket streaming.** Workflows complete in seconds on synthetic data,
  so the POST /runs endpoint blocks synchronously and returns the full RunDetail.
  If run times grow significantly, a background task queue and polling/streaming
  endpoint would be the correct extension.
- **No "agentic" LLM behavior.** The LLM is never given tools, never executes
  actions, and never calls back into the pipeline. Its output is schema-validated
  by deterministic code before it is recorded.

### Why runs execute synchronously

All eight workflows complete in well under ten seconds on the bundled synthetic
data. A background task queue would add a polling loop or WebSocket endpoint,
a background worker process, and cross-process ledger coordination. None of
that is warranted for a local-first tool where the user is waiting at the
keyboard. The synchronous design also simplifies error handling: if the run
fails, the 200/500 response carries the reason immediately, with no "check back
later" state to manage. If file sizes or workflow complexity grow, adding an
async endpoint is straightforward because run execution already goes through a
single function (`app.workflow_registry.run_workflow`) and the ledger already
tracks intermediate state.

### Why Streamlit is retained

Streamlit is the richer surface for developer exploration and for features that
did not get a React screen in this pass (AI Audit Log, Export Center, Scheduled
runs, Redaction assist, role views, Settings). It shares the same ledger, audit
log, and export directory as the API, so a run started through the React console
is immediately visible in Streamlit's History and Export Center. There is no
data migration and no divergence. The plan is to surface these capabilities
progressively in the React console rather than to remove Streamlit prematurely.

### The frontend no-finance-math invariant

The React console must never compute financial values, transaction matches, or
validation verdicts -- even display-only sums or percentages. The concrete rule:
if a JS expression produces a number that a user might interpret as a financial
result, it belongs in the API, not the frontend. The only arithmetic allowed in
the frontend is display formatting (e.g. formatting a file size in bytes as
"12.4 KB") on values that have no financial significance.

How this is enforced in practice:

- `src/types/api.ts` types Decimal fields as `string`, not `number`, so the
  TypeScript compiler prevents arithmetic on monetary amounts.
- The summary strip on the Review Run page counts findings by severity. Counting
  findings with a given property (filtering and measuring the length of a
  backend-provided array) is categorized as grouping, not financial calculation;
  the actual amounts and severities come from the API. Computed sums or
  differences of `computed_values` fields are not allowed.
- The frontend source tree was swept for reduce, toFixed, parseFloat, parseInt,
  Number(), and similar arithmetic patterns before the build was accepted. The
  only match was `formatFileSize` in `src/lib/format.ts`, which formats the
  local `File.size` byte count for display in the upload field (not a financial
  value, not from the API).
- The `TrustBoundary` component is the sole rendering path for AI content.
  Deterministic findings never use that component and AI content is never
  rendered outside it.

### Review-status transition fix (integration pass)

During end-to-end integration testing, a bug was found: `POST /api/runs/{id}/review-actions`
recorded the action (ledger + audit) but the run row's `human_review_status`
field never changed from `pending`. The fix (`apply_review_status_transition`
in `api/services/runs.py`) is a deterministic, non-financial bookkeeping
mapping over the existing `RunLedger.update_run_status` seam:

- `approve_draft` -> `approved`; `reject_ai_explanation` -> `rejected` (explicit
  decisions; the latest wins).
- `mark_reviewed` / `mark_resolved` / `needs_follow_up` move `pending` to
  `in_review` but never downgrade an `approved` or `rejected` run.
- `add_note` is status-neutral.

This mapping lives entirely in the API layer; `record_human_review_action`
remains the single recording path so the audit log stays in sync. No core or
app module was changed. The fix is documented in `docs/frontend/api_contract.md`
under POST /api/runs/{run_id}/review-actions.

## Active-task pass GW-1..GW-5 (2026-06-12)

Five small, well-scoped tasks accumulated from the FastAPI + React migration were
closed in a single pass. All five are additive or corrective; no deterministic
workflow logic was changed.

### GW-2 -- Unified human-review-status transition helper

`apply_review_status_transition(ledger, run_id, action) -> str` was relocated
from `api/services/runs.py` into `app/workflow_registry.py`, the module already
imported by both the API and Streamlit. The constants `_REVIEW_STATUS_DECISIONS`
(`approve_draft` -> `approved`, `reject_ai_explanation` -> `rejected`) and
`_REVIEW_ENGAGEMENT_ACTIONS` (`mark_reviewed`, `mark_resolved`,
`needs_follow_up`) moved with it. `api/services/runs.py` now re-exports the
function with a single alias line so the existing route import is unchanged.

The Streamlit `_render_review_controls` (~line 831 of `app/streamlit_app.py`)
previously called `record_human_review_action` but never advanced
`human_review_status`. It now also calls `apply_review_status_transition` and
surfaces the resulting status in the success toast. This was the GW-2 bug: the
same action taken in Streamlit and through the API now produces the same
`human_review_status` outcome, and the policy that governs which action maps to
which status lives in exactly one place.

Decision: the action->status mapping stays in `app/workflow_registry.py` (shared
app layer) rather than being pulled further down into `src/core/`. Core owns the
ledger mechanics (what is persisted); the mapping from a UI action string to a
status value is a policy that belongs one level up, where both surfaces already
meet. This keeps `src/core/` free of any knowledge of the action vocabulary.

### GW-3 -- Concurrency-safe guarded ledger update

Added one additive method to `src/core/run_ledger.py`:
`apply_human_review_status(run_id, new_status, *, expected_current=None) ->
Optional[str]`. It has two modes:

- **Unconditional** (`expected_current=None`): a single `UPDATE runs SET
  human_review_status=? WHERE run_id=?` statement (one round-trip; used for
  explicit decisions where the latest write wins).
- **Guarded** (`expected_current` provided): `UPDATE ... WHERE run_id=? AND
  human_review_status=?` (the update is a no-op if the current value has
  already moved). Returns the value that actually persisted (re-selected on the
  same connection); returns `None` when the run does not exist.

The shared `apply_review_status_transition` was rewritten on top of this
primitive: decisions use the unconditional mode; engagement actions use the
guarded mode with `expected_current="pending"`, so a concurrent engagement call
can never overwrite a terminal status that was set between its read and its
write. The old `get_run` then `update_run_status` read-modify-write is gone.

Decision: the guarded update belongs in the ledger layer because it requires
atomicity within a single database connection -- it cannot be made safe from
the caller side. The guarded mode is strictly additive; all existing methods
and all callers of `update_run_status` are unchanged.

### GW-4 -- Targeted pytest filterwarnings for Starlette TestClient

`StarletteDeprecationWarning` ("Using `httpx` with `starlette.testclient` is
deprecated") subclasses `UserWarning`, not `DeprecationWarning`. Added a narrow
entry to `[tool.pytest.ini_options]` in `pyproject.toml`:

    filterwarnings = [
        "ignore:Using `httpx` with `starlette.testclient` is deprecated:UserWarning:fastapi.testclient",
    ]

The filter matches by exact message prefix, `UserWarning` base category, and the
emitting module (`fastapi.testclient`). No other warning is suppressed. Verified:
`pytest tests/api` reports zero warnings under the configured settings; running
with `-W default` (which discards configured filters) re-surfaces the warning as
expected, confirming the filter is doing real work and not masking anything
structural.

Decision: filter rather than resolve because the resolution path (installing
`httpx2`) risks transitive dependency churn and the warning is advisory only.
The narrow filter is documented with a comment in `pyproject.toml` explaining the
`UserWarning` subclass and the filter rationale.

### GW-5 -- frontend/dist build-artifact policy

`frontend/dist` is a build artifact produced by `npm run build` (runs `tsc -b`
then Vite). It must not be committed as source code. It is gitignored by the
frontend-local rule (`frontend/.gitignore` line 2: `dist`) and also by the root
`.gitignore`. `api/main.py` mounts it at `/` only when the directory exists
(line 65: `if settings.frontend_dist.is_dir():`), so a fresh clone with no
`dist/` present starts fine in API-only mode -- the API and all its routes are
fully functional without the static mount.

The reproduction sequence for a cold clone is: `cd frontend && npm install &&
npm run build` (produces `frontend/dist`), then start `uvicorn api.main:app` to
get the single-server mode where the API serves the built bundle. Dev mode
(separate Vite dev server) does not require `dist/` at all.

This policy and the reproduction commands were added to `README.md` in a
dedicated subsection. The root `.gitignore` retained the existing `dist/` rule
with an added inline comment. No `api/main.py` changes were needed.

### GW-1 -- Frontend sample-data flows confirmed metadata-driven; regression tests added

An audit of `frontend/src/pages/RunWizardPage.tsx` confirmed the wizard is fully
metadata-driven: `activateSample()` pre-fills all text inputs from
`selected.text_inputs[].example`; the sample description renders from
`selected.sample_description` with a generic fallback string (not a
workflow-specific one); the "Use this example" span reads `input.example` from
the API prop. No workflow-specific hardcoded example value exists anywhere in the
component.

Five regression tests were added to `frontend/src/pages/RunWizardPage.test.tsx`
in a new `"GW-1: metadata-driven sample / example flow"` describe block. The
fixture uses deliberately unique strings (a synthetic sample description and
example query that do not match any production default) so the tests fail if the
component ignores live metadata. Tests assert: (1) `sample_description` from the
fixture is shown; (2) the example text appears in the helper span; (3) "Use
sample data" pre-fills the text input from the fixture example; (4) "Use sample
data" enables the "Check files" button with no file uploaded; (5) "Use this
example" fills the input from fixture metadata. The four existing gating tests
are untouched.

Frontend validation after the test additions: `npm run typecheck` clean,
`npm run lint` clean, `npm run test` 24/24 passed (was 19), `npm run build`
clean.

## Backlog pass GW-6..GW-12 (2026-06-19)

Seven tasks from the backlog were closed in this pass. All changes are additive
or corrective; no deterministic workflow logic, core modules, or frozen paths
were changed.

### GW-8, GW-9, GW-11 -- New read-only console screens over existing core

Four new console screens and their backing API endpoints were added, each
calling an existing `src/core` function directly with no new business logic.

- **Settings (GW-8, GET /api/settings):** reads `AppSettings.load()` and
  returns all fields as strings/bools. The three tolerance fields
  (`amount_tolerance`, `variance_threshold_pct`, `variance_dollar_threshold`)
  are typed as strings in both the API response model and the TypeScript
  contract so the frontend cannot do arithmetic on them. `editable` is always
  `false`; no PUT/PATCH endpoint exists (405). This is deliberate: settings
  are local JSON on disk and the console is read-only at this stage.

- **AI usage (GW-9, GET /api/ai-usage):** calls `ai_usage_log_rows(ledger)`
  and reverses the result for newest-first order (the ledger stores
  oldest-first). The page is explicitly labeled read-only and advisory; each
  row links to the Review Run page for the associated run ID when present.
  Empty on a fresh ledger (header-only, no crash).

- **Redaction assist (GW-11, POST /api/redaction/scan):** calls
  `redact_text(...)` (not `scan_text`) so findings and redacted output come
  from one core call. Only masked previews are returned; raw matches are never
  echoed in the response. 422 on empty or whitespace-only text. Stateless:
  nothing is stored. The console page carries an explicit "nothing is stored"
  note consistent with the Streamlit prototype warning.

- **Scheduled runs (GW-11, GET /api/schedules):** calls `ScheduleStore.list()`
  on a store whose path (`ApiSettings.schedules_path`) defaults to the same
  `runs/schedules.json` Streamlit writes. Read-only: no create/trigger endpoint
  in this pass. The console page notes that creating or triggering schedules is
  available via the Streamlit admin surface. `schedule_store` is initialized
  once at `create_app` time; a future pass should add a per-request reload or
  refresh endpoint so newly created schedules appear without an API restart.

**Decision: read-only for settings and schedules this batch.** Settings are a
local JSON file with no validation or conflict-resolution story; a write
endpoint would need to handle concurrent writes and schema migration. Schedules
write requires `ScheduleStore.add` + `make_schedule` + a trigger path through
`run_workflow` -- all real work left for a dedicated batch once the create/
trigger UI is designed. Making the read path available now satisfies the
auditability story without prematurely committing to a write contract.

### GW-10 -- Column mapping and config inputs in the wizard

The wizard gained a collapsed "Advanced options" section on the inputs step
with: (a) a config JSON textarea (posted to the existing `config` multipart
field) and (b) a column-mapping `<select>` UI rendered only when the preflight
response surfaces a `suggested_mappings` optional field. Editing either
invalidates a completed file check.

**The API preflight response does not currently expose suggested column
mappings.** Core computes them (`src/core/preflight.py`) but the API seam does
not surface them in `PreflightResponse`. The wizard's mapping UI is gated on
`suggested_mappings?: SuggestedMapping[]` -- an optional field the backend does
not send today -- so it degrades to just the config textarea, which is the
always-available advanced option. Wiring the core result into the API response
model is the correct follow-up (see GW backlog).

### GW-6 -- Easy-launch packaging (scripts/)

Three files under `scripts/` provide the Windows double-click launch path:
`launch_console.py` (pure stdlib: checks dist, starts uvicorn on port 8765,
polls `/api/health`, opens browser, blocks until Ctrl+C, shuts down cleanly),
`launch_console.cmd` (double-clickable wrapper; uses `%~dp0..` so it works
from any location, checks `.venv`, passes `%*` through for flags), and
`scripts/README.md` (setup steps, flags, optional PyInstaller note).

**Why a PyInstaller .exe was documented but not built.** A frozen binary would
bundle Python + all packages into a single portable executable, which is
attractive for genuine non-technical deployment. However, building it requires
a clean `pip install pyinstaller` step, hidden-import flags for pydantic/
pandas, and a signing or allow-listing step for Windows Defender -- each of
which adds environment-specific friction that cannot be verified in a headless
build. The `.cmd` + `.venv` approach is reproducible with the existing
`pyproject.toml` toolchain and is documented in `scripts/README.md` as a
fallback for anyone who wants to attempt the frozen build.

### GW-12 -- Playwright e2e tests (optional in CI)

A three-test Playwright suite (`frontend/e2e/core-loop.spec.ts`) drives the
full guided loop: home loads, core loop (workflow -> sample data -> preflight
gate -> run -> Review Run with FindingsSection / TrustBoundary AI label / AI
safety check / artifact download link), history navigation. All navigation uses
in-page link clicks rather than direct URL navigation because FastAPI's
`StaticFiles` mount returns 404 for SPA paths that have no physical file in
`dist/` and no matching API route.

**Playwright browsers:** Chromium was installed and the suite ran 3/3 green
(26.5s) on the development machine. The suite is optional in CI because
installing browsers in headless CI adds build time; the `e2e` npm script is
separate from `test` (vitest) so it is never accidentally included in the main
test run. Documented in `docs/frontend/e2e.md`.

**`vitest` / `tsc` exclusion:** `frontend/vite.config.ts` excludes `e2e/**`
from vitest collection (Playwright's `test()` is incompatible with vitest's
`test()`) and `frontend/tsconfig.json` excludes `e2e/` from the project
typecheck so the Playwright spec's `@playwright/test` import does not cause
tsc errors.

### GW-7 -- Streamlit marked legacy/dev-only; NOT removed

`app/streamlit_app.py` received a prominent `st.warning(...)` banner at the
top of `main()` stating that the React console is now the primary UI and this
app is retained for development and admin use only. The banner is inside
`main()` so importing the module is side-effect-free and all existing tests
(`tests/unit/test_app_imports.py`, 18 passing including `AppTest` page render
smoke tests) remain green.

**Why `app/` is retained and not removed.** The API layer imports
`app.workflow_registry` directly for the `run_workflow` adapter (the run
endpoint calls `wfr.run_workflow`). Removing `app/` would break the API and
all 38 API tests. The correct follow-up is to extract the
workflow-registry adapter into `api/services/` or `src/core/workflow_runner.py`
and then drop the `app/` dependency -- a non-trivial refactor left for a
dedicated batch. For now the module is retained as shared infrastructure; only
`streamlit_app.py` carries the legacy label.

### What was intentionally NOT built in this pass

- No schedule create/trigger endpoint (GET /api/schedules read-only; a POST
  endpoint requires designing the create/trigger flow and wiring `mark_run`).
- No settings write endpoint (no PUT/PATCH /api/settings; read-only by design).
- No suggested-column-mapping surfacing in the API preflight response (core
  computes them; the API seam was not extended this batch).
- No schedule_store per-request reload (store is loaded once at create_app;
  fine for read-only listing; needed once write endpoints land).
- No PyInstaller frozen binary (documented as optional in scripts/README.md).
- No `app/` removal (blocked by API dependency on app.workflow_registry).
