# Goal: Portfolio Hardening (GW-46)

Living tracker for the current long-running goal. **Agents: read this whole file
at the start of every session or after a context reset, then update the Status,
Progress, and Log sections as you work.** The goal text below is the spec; the
sections after it are the state.

**How to update this file**
- Status block: rewrite in place whenever the current milestone changes.
- Progress table: set `Status` to `not_started` / `in_progress` / `self_review` /
  `done`; fill `Commit` when a milestone is committed.
- Definition of Done: tick `[x]` only when you've verified the check by running it.
- Spend log, Log: **append-only**, newest at the bottom.
- Never write pilot-identifying details or secrets here (this file is committed).

---

## Status

| Field | Value |
| --- | --- |
| Current milestone | M1 One pipeline |
| Branch | `goal/portfolio-hardening` |
| Baseline commit | `4a5767a` |
| OpenRouter spend so far | $0.00 of $1.50 cap |
| Last updated | 2026-10-11 |

---

## The goal

Make this repo both a genuinely useful tool for the active municipal pilot and a
standout portfolio piece. Work autonomously through the milestones below. You
decide when to pause and self-review; the user reviews only once, at the end.
Finish when every Definition of Done item is verifiably checked.

### Hard constraints

- **Confidentiality:** the pilot city, its staff, results, timelines, data shapes,
  and config never appear in any committed file, commit message, test, fixture, or
  doc. The public repo stays synthetic-data-only. Before every commit, grep the
  staged diff for pilot-identifying terms.
- **Trust boundary:** deterministic code does all math, matching, and validation.
  The LLM only writes prose that cites source rows. Nothing may weaken this, and
  trust-boundary tests can only grow.
- **Deletion over addition.** Build only what a milestone requires. Log ideas as
  tasks in `docs/tasks.md` (next free GW-ID).
- **Git:** work on branch `goal/portfolio-hardening`. Commit after each
  milestone, only with all checks green. Never push and never open PRs; the
  user reviews the local branch at the end.
- **Checks before each commit:** `pytest`; frontend `npm test`, `typecheck`,
  `lint`, `build`; offline eval harness (`python -m src.eval.harness`). Report
  real counts and never claim a pass you didn't see.
- **Resumability:** keep this file current so the work can resume after any
  context reset.

### Real-LLM budget (OpenRouter, $2 hard limit on the key)

- Credentials are in the gitignored repo `.env` (`LLM_API_KEY`, `LLM_BASE_URL`,
  `LLM_MODEL`, `LLM_PROVIDER`). The app does **not** load `.env` itself. Load it
  explicitly only for real-model commands, and set `LLM_MODE=real` only on those
  commands. `pytest` must never touch the network.
- Model: `deepseek/deepseek-v4.1-flash` only (or a cheaper model if it's
  unavailable). Never a frontier/pro model.
- Spend cap: **$1.50 total** for the whole goal. Check spend for free with
  `GET https://openrouter.ai/api/v1/key` (bearer key) before and after each real
  run, and append it to the Spend log. Cap `max_tokens` and run the smallest
  sample that answers the question.
- Record real model responses (on synthetic data) as fixtures, then replay them
  in offline tests. Pay once, test forever.
- Never print, log, commit, or document the key. If the budget runs out, finish
  all offline work and note it in the Log.

### Milestones (in order)

**M1. One pipeline**
- Move the real orchestrator (`run_workflow`, review-status helpers,
  descriptors) out of `app/workflow_registry.py` into `src/core/`.
- Merge `WorkflowSpec` (`src/workflows/registry.py`) and `WorkflowDescriptor`
  into one registry. Delete `src/core/workflow_runner.py`.
- The API, CLI, and eval harness all call the same entry point. The harness
  exercises preflight, presets, and config merging exactly as the API does.
- Done when: one `run_workflow` exists, a grep finds no other runner, all checks
  pass, and the net number of lines goes down.

**M2. Guardrails that hold up against a real model**
- Fix numeric-claim validation in `src/core/validation.py`. Normalize `$`,
  commas, `%`, dates, and rounding. Compare values as numbers, not text tokens.
  Scan every prose field, including `categorized_exceptions` descriptions.
- Fix GW-38. Make `.env.example` match the variables the code actually reads
  (`LLM_MODE`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_PROVIDER`), and
  document OpenRouter as the cheap test path.
- Add a real-model eval mode: run every workflow through the real provider, then
  report per workflow the rejection rate, the false-positive rate (claims the data
  supports but that were flagged), and an example of each.
- Build an adversarial fixture set from the recorded real responses plus
  hand-written invented claims: claims the data supports must pass, and invented
  accounts, rows, and amounts must fail.
- Iterate on prompts and validator until the false-positive rate is near zero
  with no invented claim slipping through, and record the final numbers.

**M3. Retire Streamlit** (approved; no need to ask)
- Remove `app/` and its tests, and move `AppSettings` into `src/` or `api/`.
- Update the README, `decisions.md`, and `tasks.md` (mark GW-7 superseded).

**M4. Real-use robustness**
- Launcher: fix GW-43, so a clean shutdown leaves no stray uvicorn processes. A
  first install-and-run on a fresh Windows machine works from one documented
  command.
- Scale: generate a synthetic 50k-row GL/bank dataset. Time every workflow,
  record the timings, and fix anything over ~10s or anything that fails.
- Messy inputs: every preflight FAIL or PARTIAL tells a non-technical user what
  to do next in plain language. One test per workflow.

**M5. A front end that works with any host**
- Add a thin MCP server over `src/core` (tools: `list_workflows`, `preflight`,
  `run_workflow`, `get_run`, `ask_about_run`) that uses the same ledger, audit
  log, and validation.
- No new business logic. Tests use an in-process client. Document how to connect
  it to an MCP-capable chat host.
- Run one real `ask_about_run` through it on the cheap model to show the boundary
  holds.

**M6. Portfolio polish**
- Add `.github/workflows` CI (pytest, vitest, typecheck, lint, build, offline
  eval). Validate the YAML locally; it is not pushed.
- README: the architecture diagram matches the code, test counts are current, a
  "Measured results" section includes the M2 real-model numbers and M4 timings,
  and the pilot is still mentioned only in the existing vague sentence.
- Docs: condense `docs/decisions.md` into a short ADR-style index (keep the
  history in git). Delete or merge stale planning docs (`build_plan.md`, outdated
  parts of `Project_Outline_Master.md`, the outdated UI references in
  `pilot_plan.md`). When the goal is done, shrink this file to a short completed
  record (outcome + numbers) or fold it into `decisions.md`.
- Untrack `app_settings.json` and `frontend/tsconfig.tsbuildinfo`. Close GW-45.
- Re-capture screenshots and the GIF if the UI changed.

### Self-review (do this; the user won't)

- After each milestone, run a code review of that milestone's diff (a fresh-eyes
  subagent or `/code-review`), fix real findings, re-run all checks, then commit.
  Record the findings and how each was resolved in the Log.
- Judge each milestone against two readers:
  1. A skeptical municipal finance director: "Would I trust these numbers and
     this audit trail?"
  2. A hiring engineer with 3 minutes: "Is the architecture obvious, and is
     every README claim backed by a command?"
  If either answer is "no", fix it before moving on.
- At the end, review the whole branch once more and re-run every check from a
  clean checkout of the branch.

### Final report (in chat, not in the repo)

Give: the branch name and a commit per milestone, test/eval/timing numbers,
real-model rejection and false-positive rates, total spend, everything deleted,
backlog tasks added, self-review findings and how each was resolved, and
anything you chose not to do and why. Close with questions only the pilot can
answer (approved AI platform, real-export checks for GW-37). Keep those
questions in chat, never in the repo.

---

## Progress

| Milestone | Status | Commit | Notes |
| --- | --- | --- | --- |
| Setup: create branch, commit this file + `CLAUDE.md` | done | (setup commit) | venv on Python 3.12; baselines recorded |
| M1 One pipeline | in_progress | | |
| M2 Real-model guardrails | not_started | | |
| M3 Retire Streamlit | not_started | | |
| M4 Real-use robustness | not_started | | |
| M5 MCP front end | not_started | | |
| M6 Portfolio polish | not_started | | |
| Final whole-branch review | not_started | | |

## Definition of Done

- [ ] One registry, one `run_workflow`; the API, CLI, and eval harness share it
- [ ] Validator passes the adversarial set built from recorded real responses;
      GW-38 fixed; `.env.example` accurate
- [ ] Real-model eval numbers recorded; total OpenRouter spend logged and ≤ $1.50
- [ ] `app/` (Streamlit) removed
- [ ] Launcher shuts down cleanly; 50k-row timings recorded; plain-language
      FAIL/PARTIAL guidance tested
- [ ] MCP server with tests and one recorded real call
- [ ] CI file valid; all suites green from a clean checkout; every README claim
      backed by a command
- [ ] Non-test source lines below baseline (see Baselines)
- [ ] No pilot-identifying info and no secrets anywhere in the branch diff (grep
      check)

## Baselines

Recorded at commit `4a5767a` (2026-10-11).

| Metric | Value | Command |
| --- | --- | --- |
| Non-test source lines (Python + TS) | 30,229 | see below |
| pytest passing | 801 | `.venv/Scripts/python.exe -m pytest -o addopts="" -q --basetemp=.pytest_tmp` |
| vitest passing | 57 (11 files) | `cd frontend && npm test` |
| Offline eval | 7/7 known-answer PASS, 0 rejected | `.venv/Scripts/python.exe -m src.eval.harness --out <tmp>/eval.json` |

```bash
git ls-files '*.py' '*.ts' '*.tsx' | grep -v -E '^tests/|^frontend/e2e/|\.test\.tsx?$|^frontend/src/test/|\.config\.ts$' | xargs cat | wc -l
```

## Spend log (append-only)

| Date | Purpose | Model | Before | After | Run cost |
| --- | --- | --- | --- | --- | --- |

## Log (append-only)

Decisions, self-review findings and their fixes, blockers, and anything skipped
and why. One dated bullet per entry.

- 2026-10-11: Goal defined; `.env` configured locally (gitignored) for
  OpenRouter testing; baseline line count recorded.
- 2026-10-11: Setup. Fresh-clone bug found: tracked `app_settings.json` held an
  absolute `export_dir` from another machine, so `import api.main` (module-level
  `create_app()`) crashed with PermissionError. Removed the file from the repo
  (it was already gitignored; missing file -> repo-relative defaults). Also
  untracked `frontend/tsconfig.tsbuildinfo`. Note: pyproject `addopts` already
  has `-q`, so pass `-o addopts=""` to see the pytest summary line.
