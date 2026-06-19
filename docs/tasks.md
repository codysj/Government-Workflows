# Project Tasks

Single source of truth for outstanding work on the Municipal Finance AI Workflow
Tool. This file is meant to be read and updated by both humans and LLM agents.

---

## How to use this file (rules for humans and LLMs)

1. **Each task has a stable ID** (`GW-NN`). IDs are never reused or renumbered.
   When you add a task, use the next free number (see `Next ID` below) and then
   increment `Next ID`.
2. **Status is a field, section is a grouping.** A task lives under exactly one
   of the three sections (`Active`, `Backlog`, `Recently completed`). When a
   task's state changes, **move the whole entry** to the matching section and
   update its `Status` field.
3. **Allowed `Status` values:** `not_started`, `scoped`, `in_progress`,
   `blocked`, `done`. (`scoped` = planned/has acceptance criteria but no code
   yet; `blocked` must include a `Blocked by:` line.)
4. **Allowed `Priority` values:** `P0` (do next), `P1` (soon), `P2` (someday).
5. **Allowed `Area` values:** `core`, `backend` (FastAPI `api/`), `frontend`
   (`frontend/`), `streamlit` (`app/`), `cli`, `docs`, `infra`, `data`.
6. **When you finish a task:** set `Status: done`, add a `Done:` line with the
   date (YYYY-MM-DD) and a one-line result, and move it to *Recently completed*.
   Do not delete it — the history is useful context.
7. **Keep each task atomic** and keep the field order: `Status`, `Priority`,
   `Area`, `Why`, `Acceptance`, `Files`. Add `Blocked by:` / `Done:` only when
   relevant.
8. **Do not invent scope.** Only add tasks that are real and traceable to code,
   a review finding, or an explicit product decision.

**Next ID:** `GW-25`

**Status legend:** `[ ]` not started/scoped &nbsp; `[~]` in progress &nbsp;
`[!]` blocked &nbsp; `[x]` done

---

## Active — in progress / not finished (higher priority)

These are loose ends from the FastAPI + React migration and known minor defects.
They are small, well-understood, and should be cleared before new ambitions.

---

## Backlog — next steps / ambitions not yet scoped or started

Larger or lower-certainty work. Each needs scoping (acceptance criteria,
file plan) before implementation — move to *Active* and set `Status: scoped`
once that is done.

### GW-13 — Count-based runs-list query for history performance
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** `GET /api/runs` returns a `limit`-bounded list; there is no total
  count or pagination. Fine now; matters if run history grows large.
- **Acceptance:** The runs endpoint optionally returns a total count and
  supports offset/pagination without changing the default response shape. Test
  covers it.
- **Files:** `api/routes/runs.py`, `api/services/runs.py`,
  `src/core/run_ledger.py` (reference)

### GW-14 — Real LLM provider wiring (beyond the offline mock)
- **Status:** not_started
- **Priority:** P2
- **Area:** core
- **Why:** The system runs on the deterministic offline mock by default
  (`src/llm/provider.py`); the real-provider path is a stub. A real advisory
  provider would improve narrative quality without touching deterministic
  logic. Must preserve: advisory-only, schema validation, source-citation
  guardrails, mock-by-default.
- **Acceptance:** Scoped first. A real provider behind the existing
  `LLMProvider` interface, key from env only, opt-in via config; all validation
  guardrails still applied; mock remains default and offline. No frontend
  finance math regardless.
- **Files:** `src/llm/provider.py`, `docs/decisions.md`

### GW-15 — Validate synthetic Tyler/Munis assumptions against real templates
- **Status:** not_started
- **Priority:** P2
- **Area:** data
- **Why:** The four Tyler-style workflows run on synthetic, Tyler-*shaped*
  exports with modeled (not vendor-confirmed) header aliases. Real Munis export
  templates, locale/date formats, JE upload column order, and the GL sign
  convention need confirmation before any non-synthetic use. (Carried over from
  the Tyler enablement work; see `docs/decisions.md`.)
- **Acceptance:** Documented confirmation (or correction) of header spellings,
  JE template column order, and sign convention against a real template or city
  contact; alias maps and fixtures updated accordingly. Still synthetic data
  only in the repo.
- **Files:** `src/ingest/tyler.py`, `src/ingest/presets.py`,
  `data/synthetic/tyler/`, `docs/decisions.md`

### GW-16 — Pin a reproducible Python dependency set
- **Status:** not_started
- **Priority:** P1
- **Area:** infra
- **Why:** There is no lock file; `pyproject.toml` deps are largely unpinned, so
  `pip install` pulls latest and a Python-version change can break compiled
  wheels (this already caused a numpy/pandas ABI break — see the README
  troubleshooting note). A pinned set makes setup reproducible.
- **Acceptance:** A pinned dependency set (e.g. a `requirements.txt` or a lock
  tool) that reproduces a known-good environment on the supported Python
  version; README setup updated to use it; existing install path still works.
- **Files:** `pyproject.toml`, new lock/requirements file, `README.md`

### GW-17 — POST /api/schedules + trigger endpoint (create and run scheduled runs)
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** GET /api/schedules only lists; the roadmap wants users to create
  schedules and launch a due run. Needs a create endpoint (over
  `ScheduleStore.add` / `make_schedule`) and a trigger that kicks the existing
  run flow + `ScheduleStore.mark_run`. Deferred from the read-only GW-11 batch.
- **Acceptance:** POST /api/schedules creates a schedule and returns the new
  ScheduleInfo. POST /api/schedules/{id}/run triggers the workflow on sample
  data, advances next_due, and returns a RunDetail. Tests cover both. Frontend
  schedules page can create and trigger.
- **Files:** `api/routes/schedules.py`, `api/schemas/models.py`,
  `api/services/schedules.py`, `api/main.py`

### GW-18 — GET /api/schedules/due for due-now reminders
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** `ScheduleStore.due(as_of)` already computes which active schedules
  are due on or before a date; surfacing it would let the console show "due now"
  reminders without the frontend reimplementing the date logic.
- **Acceptance:** GET /api/schedules/due?as_of=YYYY-MM-DD returns the subset of
  schedules whose next_due <= as_of. as_of defaults to today. Test covers it.
- **Files:** `api/routes/schedules.py`, `api/schemas/models.py`

### GW-19 — Reload schedule_store per-request or add a refresh hook
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** `schedule_store` is loaded once at `create_app`, so schedules written
  after startup (e.g. via Streamlit) do not appear in GET /api/schedules until
  the API restarts. A lightweight per-request reload or a refresh endpoint
  would make the listing live once GW-17 (create/trigger) lands.
- **Acceptance:** The schedules listing reflects schedules added after API
  startup without a restart. Either per-request reload (if cheap) or a
  POST /api/schedules/refresh that re-reads the JSON store.
- **Files:** `api/main.py`, `api/services/schedules.py`
- **Blocked by:** GW-17 (only urgent once write endpoints land)

### GW-20 — Surface suggested column mappings in the API PreflightResponse
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** `src/core/preflight.py` computes `column_mappings` but the API seam
  does not expose them in PreflightResponse. The GW-10 wizard mapping UI is
  therefore never rendered (it is gated on `suggested_mappings?: SuggestedMapping[]`
  which the backend never sends). Core already does the work; only the seam
  needs updating.
- **Acceptance:** PreflightResponse gains a `suggested_mappings` field populated
  from the core preflight result. The wizard mapping UI renders when the field
  is non-empty. Test covers the field in the preflight response.
- **Files:** `api/schemas/models.py`, `api/services/runs.py` (preflight call),
  `frontend/src/types/api.ts`, `frontend/src/pages/RunWizardPage.tsx`

### GW-21 — Add create/trigger UI for scheduled runs in the console
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** The GW-11 schedules page is read-only. Once GW-17 lands a backend
  create/trigger endpoint, the console should surface it so staff can create
  schedules and run them without opening Streamlit.
- **Acceptance:** A form on the Scheduled Runs page creates a new schedule
  (workflow, cadence, label). A "Run now" button on a due schedule triggers it
  and links to the resulting run. Blocked by GW-17.
- **Files:** `frontend/src/pages/SchedulesPage.tsx`, `frontend/src/api/client.ts`,
  `frontend/src/types/api.ts`
- **Blocked by:** GW-17

### GW-22 — Add e2e smoke tests for the four new console pages
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** The new pages (settings, ai-usage, redaction, schedules) have vitest
  unit coverage but no Playwright e2e flow. A navigation smoke test would
  confirm each page loads and renders key elements against the live API.
- **Acceptance:** Four Playwright tests (one per page) navigate to the page via
  the nav link and assert at least one key element (e.g. table heading, banner
  text). Runs in the existing `npx playwright test` suite.
- **Files:** `frontend/e2e/core-loop.spec.ts` (extend) or a new spec file

### GW-23 — Add a webbrowser smoke check for the launcher
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** `scripts/launch_console.py`'s `webbrowser.open()` call is the one
  launcher branch never exercised in automated tests. A monkeypatched unit test
  would close the gap without spawning a real browser.
- **Acceptance:** A test in `tests/` patches `webbrowser.open` and asserts it
  is called with the expected URL when `--no-browser` is not set. The existing
  `wait_for_health` / `stop_server` helpers can be reused to avoid a full
  subprocess.
- **Files:** `scripts/launch_console.py`, `tests/` (new test file)

### GW-24 — Wire frontend/test-results and playwright-report into CI (or confirm ignored)
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** Playwright writes `frontend/test-results/` and
  `frontend/playwright-report/` on each run. These are gitignored but CI should
  either upload them as artifacts on failure or confirm the ignore so they never
  land in commits.
- **Acceptance:** A CI step uploads `frontend/test-results/` as a build artifact
  on failure, or the gitignore is confirmed sufficient and this task is closed
  as "no action needed". Documented.
- **Files:** `.github/workflows/` (or CI config), `frontend/.gitignore`

---

## Recently completed

Kept for context. Newest first. Move finished tasks here with a `Done:` line.

### GW-12 — Browser-level end-to-end tests for the console
- **Status:** done
- **Priority:** P2
- **Area:** frontend
- **Why:** Frontend coverage is unit/component-level (vitest). The full guided
  loop (choose -> inputs -> preflight gate -> run -> review -> export) is only
  verified via the API e2e script, not through the rendered UI.
- **Acceptance:** A Playwright (or equivalent) test drives the wizard against a
  live API on sample data and asserts the review screen renders findings, the
  AI trust boundary, validation status, and a downloadable artifact. Documented
  run command. Optional in CI.
- **Files:** `frontend/playwright.config.ts`, `frontend/e2e/core-loop.spec.ts`,
  `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`,
  `docs/frontend/e2e.md`
- **Done:** 2026-06-19 - Three Playwright tests pass (home loads, core loop with FindingsSection/TrustBoundary/AI-safety-check/artifact link, history nav); chromium installed; suite optional in CI; vitest/tsc exclusions wired.

### GW-11 — Redaction Assist and Scheduled Runs surfaces
- **Status:** done
- **Priority:** P2
- **Area:** frontend
- **Why:** Both exist in the core (`src/core/redaction.py`,
  `src/core/scheduler.py`) and Streamlit but are not in the console.
- **Acceptance:** Each gets an API route over the existing core module and a
  console page, read/trigger only, local-first.
- **Files:** `api/routes/redaction.py`, `api/routes/schedules.py`,
  `api/services/redaction.py`, `api/services/schedules.py`,
  `api/schemas/models.py`, `frontend/src/pages/RedactionPage.tsx`,
  `frontend/src/pages/SchedulesPage.tsx`, `tests/api/test_redaction.py`,
  `tests/api/test_schedules.py`
- **Done:** 2026-06-19 - POST /api/redaction/scan (stateless, masked previews only, 422 on empty) and GET /api/schedules (read-only list) delivered; Redaction and Scheduled Runs console pages live. Create/trigger deferred to GW-17/GW-21.

### GW-10 — Column-mapping and optional config inputs in the wizard
- **Status:** done
- **Priority:** P2
- **Area:** frontend
- **Why:** The backend already accepts `config` and `column_mappings`; the
  wizard did not collect them, so custom thresholds and messy-file mapping were
  not reachable from the new UI.
- **Acceptance:** The wizard optionally collects a config JSON and human-approved
  column mappings, posts them through the existing API multipart fields, and
  surfaces preflight's suggested mappings. Progressive disclosure.
- **Files:** `frontend/src/pages/RunWizardPage.tsx`, `docs/frontend/ux_spec.md`
- **Done:** 2026-06-19 - "Advanced options" collapsible added (config JSON textarea always present; mapping select rendered when backend sends suggested_mappings). Mapping UI gated on optional API field not yet sent by backend -- degrades gracefully. Config textarea fully wired. GW-20 tracks the API-side gap.

### GW-9 — AI Usage / Audit Log screen in the React console
- **Status:** done
- **Priority:** P2
- **Area:** frontend
- **Why:** The console had no cross-run AI usage view.
- **Acceptance:** A page lists AI usage records from `src/core/ai_usage_log.py`
  via a new API route. Read-only.
- **Files:** `api/routes/ai_usage.py`, `api/services/ai_usage.py`,
  `api/schemas/models.py`, `frontend/src/pages/AiUsagePage.tsx`,
  `tests/api/test_ai_usage.py`
- **Done:** 2026-06-19 - GET /api/ai-usage returns newest-first rows (run_id, workflow, model, prompt version, validation status, draft status, source rows cited); AiUsagePage live in console nav.

### GW-8 — Settings screen in the React console
- **Status:** done
- **Priority:** P2
- **Area:** frontend
- **Why:** The console had no equivalent to Streamlit's settings display.
- **Acceptance:** A Settings page reads (display-only) the same local settings
  without breaking Streamlit. Tolerances shown as strings (no arithmetic).
- **Files:** `api/routes/settings.py`, `api/services/settings.py`,
  `api/services/settings_view.py`, `api/schemas/models.py`,
  `frontend/src/pages/SettingsPage.tsx`, `tests/api/test_settings.py`
- **Done:** 2026-06-19 - GET /api/settings returns all AppSettings fields; tolerances are strings; editable=false; PUT returns 405; SettingsPage live in console nav.

### GW-7 — Reach parity, then retire the Streamlit app
- **Status:** done
- **Priority:** P2
- **Area:** frontend
- **Why:** Keeping two UIs long-term is maintenance drag; the React console now
  covers the core demo loop and the nice-to-have surfaces.
- **Acceptance:** A documented decision marks Streamlit deprecated; `app/` is
  clearly labeled dev-only. Tests and CLI unaffected.
- **Files:** `app/streamlit_app.py`, `docs/decisions.md`, `README.md`
- **Done:** 2026-06-19 - Prominent st.warning legacy/dev-only banner added to app/streamlit_app.py main(); decisions.md section appended; README updated with console screens, launcher, e2e, and legacy-Streamlit note. app/ retained (API depends on app.workflow_registry); removal deferred to a future refactor batch. 18/18 app import tests still pass.

### GW-6 — Packaging for non-technical users (one double-click launch)
- **Status:** done
- **Priority:** P1
- **Area:** infra
- **Why:** Target users are non-technical municipal finance staff on Windows.
  "Run uvicorn, then npm" is not acceptable for them.
- **Acceptance:** A documented, reproducible way to start the API (serving the
  built React bundle) and open a browser without a terminal.
- **Files:** `scripts/launch_console.py`, `scripts/launch_console.cmd`,
  `scripts/README.md`
- **Done:** 2026-06-19 - scripts/launch_console.cmd (double-click) + scripts/launch_console.py (stdlib-only: checks dist, starts uvicorn on :8765, polls /api/health, opens browser, clean shutdown) + scripts/README.md delivered. PyInstaller .exe documented but not built (CI friction). Launcher verified: /api/health 200, clean Ctrl+C shutdown.

### GW-5 — Decide and document the `frontend/dist` build-artifact policy
- **Status:** done
- **Priority:** P1
- **Area:** infra
- **Why:** `api/main.py` mounts `frontend/dist` at `/` when present (one-server
  deploy), but `dist/` is a build output. It must not be committed as source,
  yet the "single server serves the UI" instructions assume it exists. Need an
  explicit build-then-serve step and a gitignore decision so a fresh clone is
  reproducible.
- **Acceptance:** `frontend/dist` is gitignored; README documents
  `cd frontend && npm install && npm run build` as the step that produces the
  bundle the API serves; a fresh clone can reproduce the served UI from
  documented commands.
- **Files:** `frontend/.gitignore` (or root `.gitignore`), `README.md`,
  `api/main.py` (reference only)
- **Done:** 2026-06-12 - `frontend/dist` confirmed gitignored by `frontend/.gitignore:2`; README subsection added with cold-clone reproduction sequence and mode table; `api/main.py` static-mount guard (`is_dir()`) documented.

### GW-4 — Silence the Starlette TestClient deprecation warning
- **Status:** done
- **Priority:** P2
- **Area:** backend
- **Why:** The full suite emits one `StarletteDeprecationWarning`
  ("Using httpx with starlette.testclient is deprecated; install httpx2
  instead") from `fastapi.testclient`. Harmless today; becomes churn on a
  future Starlette/FastAPI upgrade.
- **Acceptance:** The warning is resolved or explicitly filtered (documented in
  `pyproject.toml` pytest config) so the suite runs clean. No behavior change.
- **Files:** `pyproject.toml`, `tests/api/conftest.py`
- **Done:** 2026-06-12 - Added targeted `filterwarnings` entry to `pyproject.toml` matching the exact message, `UserWarning` category, and `fastapi.testclient` module; `pytest tests/api` runs with zero Starlette deprecation warnings.

### GW-3 — Make review-action status transition concurrency-safe
- **Status:** done
- **Priority:** P2
- **Area:** backend
- **Why:** `apply_review_status_transition` is a non-transactional
  read-modify-write (`get_run` then `update_run_status`). Two concurrent
  review-action POSTs for the same run could interleave and let an engagement
  action (`mark_reviewed`) overwrite a just-written terminal state
  (`approved`/`rejected`). Harmless for the current single-user local demo;
  a latent bug for any multi-user future.
- **Acceptance:** The status transition is atomic (e.g. a single guarded ledger
  update, or a transaction that re-reads under lock). A test simulating two
  interleaved actions never downgrades a terminal status.
- **Files:** `api/services/runs.py`, `src/core/run_ledger.py`
- **Done:** 2026-06-12 - Added `apply_human_review_status` to `RunLedger` (unconditional + guarded modes); rewrote shared transition helper to use guarded update; 5 unit tests + 2 concurrency tests added.

### GW-2 — Unify human-review status behavior across Streamlit and API
- **Status:** done
- **Priority:** P1
- **Area:** streamlit
- **Why:** Review actions posted through the API advance the run-level
  `human_review_status` (via `apply_review_status_transition` in
  `api/services/runs.py`), but the same action recorded through Streamlit
  (`app/workflow_registry.record_human_review_action`, called ~line 831 of
  `app/streamlit_app.py`) does not. Same data, two behaviors.
- **Acceptance:** A review action taken in Streamlit results in the same
  `human_review_status` transition as the API path. Prefer extracting the
  transition mapping into a shared, non-financial helper both surfaces call so
  the rule lives in one place. No change to deterministic findings or
  validation. Existing tests stay green.
- **Files:** `app/streamlit_app.py`, `app/workflow_registry.py`,
  `api/services/runs.py`
- **Done:** 2026-06-12 - Moved `apply_review_status_transition` + constants into `app/workflow_registry.py`; API re-exports via alias; Streamlit `_render_review_controls` now calls the shared helper and surfaces new status in success toast.

### GW-1 — Wire frontend sample-data flows to live backend metadata
- **Status:** done
- **Priority:** P0
- **Area:** frontend
- **Why:** The wizard's "Use sample data" / "Use this example" paths were
  validated only against test fixtures, not against the real backend
  `sample_description` and `text_inputs[].example` values returned by
  `GET /api/workflows/{type}`. Demo flows must drive off live metadata.
- **Acceptance:** Selecting "Use sample data" in the wizard for every workflow
  populates inputs from the backend response and completes a run; the
  transaction_search example query is shown as the textarea placeholder from
  the API, not hardcoded. A vitest test asserts the wizard reads `example` /
  `sample_description` from a live-shaped `WorkflowInfo`.
- **Files:** `frontend/src/pages/RunWizardPage.tsx`,
  `frontend/src/api/client.ts`, `frontend/src/types/api.ts`
- **Done:** 2026-06-12 - Audit confirmed component is fully metadata-driven with no hardcoded workflow examples; 5 regression vitest tests added asserting fixture-unique strings drive the UI; 24/24 tests pass.

### GW-0 — FastAPI seam + React/Vite workflow console
- **Status:** done
- **Priority:** P0
- **Area:** backend, frontend, docs
- **Done:** 2026-06-12 — Added `api/` (FastAPI seam reusing the core; 10
  endpoints; shares Streamlit's ledger/audit/export) and `frontend/` (React +
  Vite + TS guided console: Home, Run wizard, Review Run, History, About) with
  hard deterministic-vs-AI trust separation. 738 Python tests pass (712 + 26
  API); frontend typecheck/lint/build/19 tests green; live e2e and
  restart-rehydration verified; Streamlit and CLI unbroken. Docs:
  `docs/research/ui_ux_principles.md`, `docs/frontend/ux_spec.md`,
  `docs/frontend/api_contract.md`, README + decisions.md updated.
