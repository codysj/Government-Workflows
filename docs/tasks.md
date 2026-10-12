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

**Next ID:** `GW-54`

**Status legend:** `[ ]` not started/scoped &nbsp; `[~]` in progress &nbsp;
`[!]` blocked &nbsp; `[x]` done

---

## Active — in progress / not finished (higher priority)

_(none)_

---

## Backlog — next steps / ambitions not yet scoped or started

Ordered by importance (top = do next). Each needs scoping (acceptance criteria,
file plan) before implementation — move to *Active* and set `Status: scoped`
once that is done.

### GW-50 — Verify the ChatGPT integration live, in a workspace
- **Status:** blocked
- **Priority:** P1
- **Area:** infra
- **Why:** The Claude Desktop half was verified live on 2026-10-11 (docs/mcp.md
  "What has been verified"). ChatGPT needs a Business/Enterprise workspace with
  developer mode and a Platform organization with a Secure MCP Tunnel.
- **Acceptance:** The server through the tunnel in ChatGPT runs a sample
  workflow and renders the run view, including one attached CSV (record the
  observed download host and adjust the `GOVWF_DOWNLOAD_HOSTS` default if
  needed). Results noted in docs/mcp.md.
- **Files:** `docs/mcp.md`, `mcp_server.py` (only if a host needs a change)
- **Blocked by:** access to a ChatGPT Business/Enterprise workspace.

### GW-52 — Confirm site-specific Munis layouts with a city's own system
- **Status:** blocked
- **Priority:** P1
- **Area:** data
- **Why:** Public Munis output settled the general layout (docs/tyler_assumptions.md
  section 0), but four things vary by site or were not public: what Invoice
  Central's "Vendor" column holds, the vendor-master export, the site-generated
  Standard Excel journal template, and whether Excel exports keep report
  furniture. A wrong journal template column order is a silent import error.
- **Acceptance:** One real (scrubbed) export of each kind, or a Munis
  administrator's answer, checked against section 0.4; aliases, fixtures, and the
  `je_upload.xlsx` columns adjusted if needed.
- **Files:** `docs/tyler_assumptions.md`, `src/ingest/tyler.py`, `src/workflows/je_upload_prep.py`
- **Blocked by:** external input from a city using Munis.

### GW-51 — Read printed Munis reports (PDF / fixed-width text)
- **Status:** not_started
- **Priority:** P2
- **Area:** core
- **Why:** Much Munis output is printed reports identified by a Program ID
  (`glytdbud`, `apcshdsb`, `appdwarr`, `glacthst`); today they must be re-exported
  to Excel first. Layouts are documented in docs/tyler_assumptions.md section 0.2.
- **Acceptance:** A deterministic parser per supported Program ID (column
  positions from the repeated header line, report furniture dropped and counted),
  pinned by tests on synthetic reports in the printed layout; fail closed on an
  unknown layout.
- **Files:** `src/ingest/` (new), `src/core/preflight.py`

### GW-47 — Validator: check amount signs and vendor/fund names
- **Status:** not_started
- **Priority:** P2
- **Area:** core
- **Why:** The claim validator (docs/decisions.md ADR-2) rejects invented
  numbers, dates, identifiers, and rows, but ignores a dropped minus sign and
  does not check names without digits (an invented vendor name passes). Both
  were accepted limits in the M2 review; revisit if pilot drafts show them.
- **Acceptance:** Signed amounts compared where the data is signed; vendor and
  fund names in prose checked against the findings' names, with replay tests
  showing no new false positives on `tests/fixtures/real_llm`.
- **Files:** `src/core/validation.py`, `tests/unit/test_validation.py`

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

---

## Recently completed

Condensed log, newest first (one line per task). Full rationale and Done detail
live in `docs/decisions.md` (dated sections) and git history.

**GW-53 — Optional AI settings in the Claude extension (2026-10-11)** — API key (secure storage), provider, model, endpoint; with a key the tool's own drafts and answers use that model (still validated); verified with a real model through the packed extension

**GW-37 — Tyler/Munis assumptions checked against real Munis output (2026-10-11)** — public Munis reports, Tyler docs and knowledge base: verbatim headers and report furniture now handled and tested (`test_munis_layouts.py`); GL sign convention consistent; "Description/Vendor" is not a Munis column; the JE template is site-generated, so site checks moved to GW-52 (docs/tyler_assumptions.md section 0)

**GW-49 — Claude and ChatGPT integrations (2026-10-11)** — one stdio MCP server for both: Claude Desktop extension (`scripts/build_claude_extension.py`), ChatGPT via Secure MCP Tunnel with attached-file support, an MCP Apps run view, data-folder confinement, tool annotations; see docs/mcp.md and ADR-8

**GW-48 — AP duplicate review at 50k rows under 10 s (2026-10-11)** — 12.3 s → 6.9 s; findings and preflight reports hash-identical to before on all 7 workflows at 20k rows

**GW-46 — portfolio hardening goal (2026-10-11)** — one pipeline, real-model guardrails, Streamlit retired, 50k-row scale, MCP server, CI; see `docs/goal.md`
- GW-45 — README test counts now current (and stated where CI checks them)
- GW-43 — closed by design: the launcher serves in-process, so there is no child server to orphan
- GW-38 — `LLM_PROVIDER=anthropic` selects the Messages transport (tested)
- GW-28 — closed: the import presets were removed (UI-only); their header variants now live in preflight's synonyms, so there is one canonical name per dimension

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
- GW-7 — Streamlit marked legacy/dev-only (retained; API depends on it) — superseded by GW-46: Streamlit retired 2026-10-11
- GW-6 — Double-click launcher (`scripts/launch_console.*`)

**GW-0..GW-5 — FastAPI seam + React console + migration loose ends (2026-06-12)**
- GW-5 — `frontend/dist` build-artifact policy (gitignored + documented)
- GW-4 — Silenced the Starlette TestClient deprecation warning
- GW-3 — Concurrency-safe review-status transition (guarded ledger update)
- GW-2 — Unified review-status across Streamlit + API (shared helper)
- GW-1 — Wizard sample-data driven by live backend metadata
- GW-0 — FastAPI seam + React/Vite guided workflow console (the migration)
