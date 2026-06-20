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
6. **When you finish a task:** set `Status: done` and record it as a **one-line
   entry** in *Recently completed* (`GW-NN — title — YYYY-MM-DD — one-line
   result`). Full rationale and implementation detail live in `docs/decisions.md`
   (dated sections) and git history — do not duplicate the whole task block here.
7. **Backlog is ordered by importance** (most important / most actionable at the
   top). Keep each task atomic with the field order `Status`, `Priority`, `Area`,
   `Why`, `Acceptance`, `Files` (+ `Blocked by:` when blocked).
8. **Triage periodically:** merge duplicates, re-order by importance, and condense
   the completed log. **Do not invent scope** — only add tasks traceable to code,
   a review finding, or an explicit product decision.

**Next ID:** `GW-38`

**Status legend:** `[ ]` not started/scoped &nbsp; `[~]` in progress &nbsp;
`[!]` blocked &nbsp; `[x]` done

---

## Active — in progress / not finished (higher priority)

_(none — the FastAPI + React migration loose ends are all cleared.)_

---

## Backlog — next steps / ambitions not yet scoped or started

Ordered by importance (top = do next). Each needs scoping (acceptance criteria,
file plan) before implementation — move to *Active* and set `Status: scoped`
once that is done.

### GW-25 — Remove unused app.state.schedule_store from api/main.py
- **Status:** not_started
- **Priority:** P1
- **Area:** backend
- **Why:** Schedule routes now build a fresh `ScheduleStore` per request
  (GW-19), so the startup-built `app.state.schedule_store` is dead code.
  Leaving it in place implies a long-lived store the routes no longer consult,
  which is misleading. Trivial, high-clarity cleanup right after the batch that
  created it.
- **Acceptance:** `app.state.schedule_store` initialization removed from
  `create_app`; all schedule route tests still pass; no runtime error.
- **Files:** `api/main.py`

### GW-32 — Surface due schedules as a reminder banner in the console
- **Status:** not_started
- **Priority:** P1
- **Area:** frontend
- **Why:** The backend `GET /api/schedules/due` (GW-18) and the
  `getDueSchedules(asOf?)` client function are already built, but nothing in the
  console shows which schedules are due — the reminder loop is half-finished. A
  small banner closes it with little effort.
- **Acceptance:** A banner/callout on the Home or Scheduled Runs page shows
  "X scheduled run(s) are due" when the due count is > 0, and clicking navigates
  to Scheduled Runs. No finance arithmetic in the frontend.
- **Files:** `frontend/src/pages/SchedulesPage.tsx`,
  `frontend/src/pages/HomePage.tsx`, `frontend/src/api/client.ts`

### GW-27 — Add Anthropic Messages API transport preset for RealLLMProvider
- **Status:** not_started
- **Priority:** P2
- **Area:** core
- **Why:** The default transport in `RealLLMProvider` (GW-14) is
  OpenAI-compatible. Wiring Anthropic — the project's likely provider — requires
  a hand-written custom `transport=` callable today. A named preset makes the
  opt-in real-provider path turnkey, so GW-14 is actually usable.
- **Acceptance:** A preset transport callable (or named factory) in
  `src/llm/provider.py` that sends an Anthropic Messages API request (x-api-key,
  anthropic-version headers; content[].text parsing) and returns the canonical
  completion text. Documented in README + decisions.md. No live call in tests
  (use the injectable-transport seam).
- **Files:** `src/llm/provider.py`, `tests/unit/test_llm_provider.py`

### GW-33 — Add pause/activate and delete controls for schedules
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** Schedules render Active/Paused and the core `ScheduleStore` supports
  update/remove, but the console can only create and run-now. Editing active
  state or deleting a stale schedule still requires Streamlit or a direct JSON
  edit.
- **Acceptance:** Each schedule row gets an activate/pause toggle and a delete
  button. Backend PATCH (active toggle) and DELETE endpoints added. Tests cover
  both.
- **Files:** `frontend/src/pages/SchedulesPage.tsx`, `api/routes/schedules.py`,
  `api/schemas/models.py`

### GW-22 — Add e2e smoke tests for the four newer console pages
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** The settings, ai-usage, redaction, and schedules pages have vitest
  unit coverage but no Playwright e2e flow. A navigation smoke test would confirm
  each loads and renders key elements against the live API.
- **Acceptance:** Four Playwright tests (one per page) navigate via the nav link
  and assert at least one key element (table heading, banner text). Runs in the
  existing `npx playwright test` suite.
- **Files:** `frontend/e2e/core-loop.spec.ts` (extend) or a new spec file

### GW-23 — Add a webbrowser smoke check for the launcher
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** `scripts/launch_console.py`'s `webbrowser.open()` call is the one
  launcher branch never exercised in automated tests. A monkeypatched unit test
  closes the gap without spawning a real browser.
- **Acceptance:** A test patches `webbrowser.open` and asserts it is called with
  the expected URL when `--no-browser` is not set, reusing the existing
  `wait_for_health` / `stop_server` helpers to avoid a full subprocess.
- **Files:** `scripts/launch_console.py`, `tests/` (new test file)

### GW-35 — Suppress React Router v7 future-flag warnings in vitest
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** Every page test logs two React Router v6->v7 future-flag warnings
  (v7_startTransition, v7_relativeSplatPath) to stderr. Tests pass, but the noise
  clutters CI logs and could mask a real warning.
- **Acceptance:** The two future flags are set on the test (or app) router so the
  warnings are suppressed. No test behavior change.
- **Files:** `frontend/src/test/` setup or the router configuration

### GW-34 — Add server-side filtering for the history endpoint
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** History filters (workflow/status/review/search) operate only on the
  client-loaded rows, so filtering can miss matches in not-yet-loaded pages. With
  large ledgers a backend filter param makes results complete rather than
  page-limited.
- **Acceptance:** `GET /api/runs` gains optional filter params (workflow_type,
  status, human_review_status, search); `total` reflects the filtered count; the
  history filter passes them when set. No change to client-side display logic
  (totals stay strings, no arithmetic).
- **Files:** `api/routes/runs.py`, `api/services/runs.py`,
  `src/core/run_ledger.py` (may need a filtered query),
  `frontend/src/pages/HistoryPage.tsx`

### GW-26 — Allow scheduled runs to carry persisted input sets (not just samples)
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** `POST /api/schedules/{id}/run` can only trigger bundled sample inputs
  because `Schedule` stores no file references. Real recurring runs (monthly
  reconciliation over the latest export) need a stable input source tied to a
  schedule.
- **Acceptance:** Scoped first. The Schedule model (or a companion record) can
  store an input-set reference; the trigger endpoint uses it when present, falling
  back to sample inputs. Any `src/core/scheduler.py` change is weighed against the
  frozen-path rule.
- **Files:** `src/core/scheduler.py`, `api/routes/schedules.py`,
  `api/schemas/models.py`

### GW-28 — Resolve org/object canonical name conflict (preset vs tyler normalizer)
- **Status:** not_started
- **Priority:** P2
- **Area:** core
- **Why:** `TYLER_MUNIS_STYLE` maps org->department and object->account_code,
  while `TYLER_DATASET_TYPES` keeps org and object as canonicals. The two paths
  produce different column names for the same Munis dimensions, which can confuse
  developers mixing them. (Tyler-normalizer hygiene; low urgency while
  synthetic-only.)
- **Acceptance:** One canonical name per dimension, used consistently in both the
  preset and the dataset-type registry. Alias maps and fixtures updated. No change
  to deterministic workflow logic.
- **Files:** `src/ingest/presets.py`, `src/ingest/tyler.py`,
  `data/synthetic/tyler/`, `docs/tyler_assumptions.md`

### GW-29 — Warn when an optional alias fails to resolve (invoice_number_s case)
- **Status:** not_started
- **Priority:** P2
- **Area:** core
- **Why:** The `invoice_number_s -> invoice_numbers` alias is fragile. If the real
  Munis header differs slightly (Invoice Numbers, Invoices, Invoice No(s)) the
  alias silently fails and the optional column is absent with no signal.
- **Acceptance:** When an optional alias produces no column match, a warning is
  emitted (to `TylerNormalizedExport.warnings` or a preflight finding) rather than
  silently skipping. Tests cover the warning path.
- **Files:** `src/ingest/tyler.py`, `src/ingest/presets.py`

### GW-30 — Remove or add fixture coverage for dead TYLER_MUNIS_STYLE aliases
- **Status:** not_started
- **Priority:** P2
- **Area:** data
- **Why:** Several `TYLER_MUNIS_STYLE` aliases (`invoice`, `gl_amount`,
  `journal_amount`, `je_amount`) have no synthetic fixture exercising them and no
  counterpart in `TYLER_DATASET_TYPES` — dead-code risk that may behave
  confusingly if a real file matches.
- **Acceptance:** Each alias is either removed (if truly unused) or covered by a
  fixture that exercises it end-to-end. Decision documented.
- **Files:** `src/ingest/presets.py`, `data/synthetic/tyler/`

### GW-24 — Wire frontend/test-results and playwright-report into CI (or confirm ignored)
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** Playwright writes `frontend/test-results/` and
  `frontend/playwright-report/` per run. They are gitignored, but a CI pipeline
  (which does not yet exist) should upload them on failure. Conditional on CI
  being set up.
- **Acceptance:** A CI step uploads `frontend/test-results/` as a build artifact
  on failure, or the gitignore is confirmed sufficient and the task is closed as
  "no action needed". Documented.
- **Files:** `.github/workflows/` (or CI config), `frontend/.gitignore`

### GW-36 — Investigate the one-off 37-minute backend pytest wall time
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** A first run of `tests/api` once took ~2227s vs ~18s on an identical
  re-run. If reproducible this points to a flaky fixture or IO bottleneck (e.g.
  RunLedger SQLite temp-dir contention under --basetemp) worth pinning down for
  predictable CI timing. Speculative — may not reproduce.
- **Acceptance:** Root cause identified and documented; fix applied and
  re-measured if needed, or closed with a "not reproducible" note.
- **Files:** `tests/api/` fixtures, `pyproject.toml` (basetemp config)

### GW-37 — Validate Tyler / JE upload assumptions against a real city or Munis export
- **Status:** blocked
- **Priority:** P1
- **Area:** data
- **Why:** `docs/tyler_assumptions.md` documents what is modeled vs confirmed. The
  three highest-risk items — the JE upload template **column order**, the GL
  **sign convention** (signed amount = debit - credit), and the combined
  "Description/Vendor" GL column — must be confirmed before any non-synthetic use;
  a wrong JE column order is a silent import misconfiguration. (Absorbs the former
  GW-31, which covered the JE column order alone.)
- **Acceptance:** The three highest-risk assumptions confirmed or corrected via a
  real Munis export or a city/vendor contact; the `Verified?` column in
  `docs/tyler_assumptions.md` updated; alias maps / `je_upload_template.csv` /
  `tyler.py` corrected if needed. If no contact is available, close with a
  documented "requires external input" note.
- **Files:** `docs/tyler_assumptions.md`, `src/ingest/tyler.py`,
  `data/synthetic/tyler/je_upload_template.csv`
- **Blocked by:** external input — a real Munis export template or a city/vendor
  contact (not available in-repo; synthetic data only).

---

## Recently completed

Condensed log, newest first (one line per task). Full rationale and Done detail
live in `docs/decisions.md` (dated sections) and git history.

**GW-13..GW-21 — backlog batch 2 (2026-06-19)**
- GW-21 — Schedule create/trigger UI in the console
- GW-20 — `suggested_mappings` in PreflightResponse (unblocked the wizard mapping UI)
- GW-19 — Per-request schedule-store reload (live listing without restart)
- GW-18 — `GET /api/schedules/due` (due-now query)
- GW-17 — `POST /api/schedules` + trigger endpoint
- GW-16 — Pinned `requirements.txt` for reproducible installs
- GW-15 — `docs/tyler_assumptions.md` assumptions register (real-template validation tracked in GW-37)
- GW-14 — `RealLLMProvider` wired (opt-in, env key; mock stays default; not exercised live)
- GW-13 — Runs pagination (`total`/`limit`/`offset`)

**GW-6..GW-12 — console screens + packaging (2026-06-19)**
- GW-12 — Playwright core-loop e2e test
- GW-11 — Redaction + Scheduled Runs console pages
- GW-10 — Wizard advanced options (config + mapping disclosure)
- GW-9 — AI usage screen
- GW-8 — Settings screen (read-only)
- GW-7 — Streamlit marked legacy/dev-only (retained; API depends on it)
- GW-6 — Double-click launcher (`scripts/launch_console.*`)

**GW-0..GW-5 — FastAPI seam + React console + migration loose ends (2026-06-12)**
- GW-5 — `frontend/dist` build-artifact policy (gitignored + documented)
- GW-4 — Silenced the Starlette TestClient deprecation warning
- GW-3 — Concurrency-safe review-status transition (guarded ledger update)
- GW-2 — Unified review-status across Streamlit + API (shared helper)
- GW-1 — Wizard sample-data driven by live backend metadata
- GW-0 — FastAPI seam + React/Vite guided workflow console (the migration)
