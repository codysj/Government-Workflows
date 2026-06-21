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

**Next ID:** `GW-46`

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

### GW-38 — Wire anthropic_messages_transport into get_provider() for LLM_PROVIDER=anthropic
- **Status:** not_started
- **Priority:** P1
- **Area:** core
- **Why:** `anthropic_messages_transport` was added in GW-27, but `get_provider()`
  always uses `_default_httpx_transport` even when `LLM_PROVIDER=anthropic`. The
  preset exists but config-driven wiring is absent, so `LLM_PROVIDER=anthropic`
  silently sends OpenAI-format requests and gets a 401.
- **Acceptance:** `get_provider()` passes `anthropic_messages_transport` as the
  transport when `LLM_PROVIDER=anthropic`; a test covers the env-driven dispatch
  path (injectable transport; no live call).
- **Files:** `src/llm/provider.py`, `tests/unit/test_llm_provider.py`

### GW-40 — Persist history filters in the URL query string
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** GW-34 filters live only in component state, so a filtered history view
  cannot be bookmarked/shared and is lost on reload. Syncing filters to the URL
  (`useSearchParams`) would make the filtered view linkable.
- **Acceptance:** Filter state is read from and written to the URL query string
  (`?workflow_type=...&status=...` etc.). Navigating to a URL with filter params
  pre-fills the dropdowns and fetches the filtered result immediately.
- **Files:** `frontend/src/pages/HistoryPage.tsx`

### GW-41 — Add a unit test for the GW-32 due-runs banner on HomePage
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** `HomePage.tsx` has no test file; the new `getDueSchedules` banner
  (count > 0 render, failure-hides) is untested at the component level. A small
  render test mirroring the other page tests would cover it.
- **Acceptance:** A `HomePage.test.tsx` with at least two cases: banner renders
  when due count > 0; banner is absent when the fetch fails or returns 0.
- **Files:** `frontend/src/pages/HomePage.test.tsx` (new)

### GW-26 — Allow scheduled runs to carry persisted input sets (not just samples)
- **Status:** not_started
- **Priority:** P2
- **Area:** backend
- **Why:** `POST /api/schedules/{id}/run` can only trigger bundled sample inputs
  because `Schedule` stores no file references. Real recurring runs (monthly
  reconciliation over the latest export) need a stable input source tied to a
  schedule. Deferred (YAGNI): schedule inputs are sample-only until a real
  recurring dataset exists; revisit on real need.
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
  developers mixing them. Deferred (YAGNI): harmless for synthetic-only data; a
  `# ponytail: GW-28` comment in `presets.py` and `docs/tyler_assumptions.md`
  Section 4 document the conflict and upgrade path; revisit before first real
  Tyler dataset.
- **Acceptance:** One canonical name per dimension, used consistently in both the
  preset and the dataset-type registry. Alias maps and fixtures updated. No change
  to deterministic workflow logic.
- **Files:** `src/ingest/presets.py`, `src/ingest/tyler.py`,
  `data/synthetic/tyler/`, `docs/tyler_assumptions.md`

### GW-42 — Run GW-22 Playwright nav smoke tests in CI to close the e2e loop
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** The four nav smoke tests added in GW-22 are verified to exist in the
  spec but have not been executed end-to-end (no CI pipeline; no live-server run
  yet). Executing them in CI would confirm the pages load and render their h1.
- **Acceptance:** `npx playwright test` runs green against a live API + built
  bundle in a CI environment (or documented manually if CI is still absent).
- **Files:** `frontend/e2e/core-loop.spec.ts`, CI config when available

### GW-43 — Harden launch_console.py stop_server to avoid stray uvicorn on Windows
- **Status:** not_started
- **Priority:** P2
- **Area:** infra
- **Why:** During the live smoke pass a background uvicorn spawn reported a failed
  exit while the real server was serving on :8000, suggesting a transient duplicate
  process can be left behind. `stop_server` in `scripts/launch_console.py` should
  reliably reap the started process on Windows.
- **Acceptance:** `stop_server` sends SIGTERM (or `proc.terminate()`) and waits for
  the process to exit, with a timeout fallback to `proc.kill()`. No duplicate
  uvicorn processes survive a clean shutdown.
- **Files:** `scripts/launch_console.py`

### GW-44 — Add a history-filter debounce for the search param
- **Status:** not_started
- **Priority:** P2
- **Area:** frontend
- **Why:** GW-34 refetches from the ledger on every search keystroke. This is
  fine while the ledger is in-memory, but the ponytail comment in
  `api/services/runs.py` flags it as a ceiling: if the ledger ever goes remote
  (paginated DB query), per-keystroke fetches will cause noticeable latency.
- **Acceptance:** A debounce (200-300ms) delays the search query param from
  updating until the user pauses typing. No behavior change otherwise.
- **Files:** `frontend/src/pages/HistoryPage.tsx`

### GW-45 — Update README test-count to reflect current passing total
- **Status:** not_started
- **Priority:** P2
- **Area:** docs
- **Why:** README says "758 tests" (a stale count); the current passing total is
  786 Python + 50 vitest. The README frontend test count (45) is also stale (now
  50). Both should reflect the actual baseline.
- **Acceptance:** README "How to run tests" section updated with correct counts;
  vitest test file count updated (9 files, 50 tests).
- **Files:** `README.md`

---

## Recently completed

Condensed log, newest first (one line per task). Full rationale and Done detail
live in `docs/decisions.md` (dated sections) and git history.

**GW-22..GW-39 — P1/P2 batch + docs (2026-06-20)**
- GW-39 — api_contract.md updated: PATCH/DELETE schedule endpoints and GET /api/runs 4 filter params documented

**GW-22..GW-35 — P1/P2 batch (2026-06-19)**
- GW-35 — React Router v7 future-flag warnings suppressed in vitest (App.tsx + 7 test MemoryRouters)
- GW-34 — Server-side history filtering: 4 optional filter params on GET /api/runs; HistoryPage wired
- GW-33 — Schedule pause/activate (PATCH) + delete (DELETE) endpoints + console Actions column
- GW-32 — Due-runs reminder banner on HomePage (getDueSchedules; non-fatal; links to /schedules)
- GW-30 — Removed 4 dead TYLER_MUNIS_STYLE aliases (gl_amount/journal_amount/je_amount/invoice); documented
- GW-29 — Optional-alias-missing warning added to TylerNormalizedExport.warnings after apply_aliases
- GW-27 — anthropic_messages_transport preset added to src/llm/provider.py (x-api-key; content[].text)
- GW-25 — Deleted dead app.state.schedule_store init and unused ScheduleStore import from api/main.py
- GW-23 — Launcher webbrowser.open monkeypatched test in tests/test_launcher.py (2 cases)
- GW-22 — 4 nav smoke tests added to e2e/core-loop.spec.ts (Settings/AI usage/Redaction/Schedules)
- GW-24 — closed: no CI exists; .gitignore already covers test-results/ and playwright-report/; no action needed
- GW-36 — closed: not reproducible; 37-minute run never recurred; reopen if it recurs

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
