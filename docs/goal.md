# Goal: Portfolio Hardening (GW-46) — completed 2026-10-11

Make the tool genuinely useful for the active municipal pilot and a standout
portfolio piece. Done on branch `goal/portfolio-hardening` (baseline `4a5767a`).
The full working log (goal spec, milestone notes, review findings, spend log)
is in git history: `git log -p -- docs/goal.md`. Decisions are in
[`decisions.md`](decisions.md), measurements in [`evaluation.md`](evaluation.md).

## Outcome

| Milestone | Commit | Result |
| --- | --- | --- |
| Setup | `b36f47d`, `f5b4805` | Fresh-clone crash fixed (machine-specific settings file) |
| M1 One pipeline | `bc482c4` | One registry, one `run_workflow`; API, CLI, eval share it |
| M2 Real-model guardrails | `c7077db` | Claim validator: invented numbers/dates/ids/rows are errors; a real hallucination caught |
| M3 Retire Streamlit | `475418d`, `bc482c4` | `app/` removed |
| M4 Real-use robustness | `7d3d078`, `cb93457` | 50k rows in 4-12 s; one-command launcher; plain-language preflight |
| M5 MCP front end | `459f83e` | `mcp_server.py`, 5 tools, protocol tests, recorded real session |
| M6 Portfolio polish | `02a228b`, `ab0e455` | CI, README, ADR index, lock file, review-driven doc corrections |

Every milestone was reviewed by a fresh-eyes agent before moving on. Each
review found real problems (a CLI regression, 12 validator bypasses, a silent
0-variance result, overstated README claims); all were fixed and pinned by tests.

## Definition of Done

- [x] One registry, one `run_workflow`; the API, CLI, and eval harness share it
      (`tests/unit/test_registry_surface.py::test_only_one_run_workflow_exists`)
- [x] Validator passes the adversarial set built from recorded real responses;
      GW-38 fixed; `.env.example` accurate (`tests/integration/test_real_llm_replay.py`)
- [x] Real-model eval numbers recorded; total OpenRouter spend logged and ≤ $1.50
      (final batch 22/24 as run, 23/24 after a fix; spend $0.125)
- [x] `app/` (Streamlit) removed
- [x] Launcher shuts down cleanly; 50k-row timings recorded; plain-language
      FAIL/PARTIAL guidance tested (`tests/test_launcher.py`,
      `tests/integration/test_preflight_guidance.py`, evaluation.md "Scale")
- [x] MCP server with tests and one recorded real call
      (`tests/integration/test_mcp_server.py`, `tests/fixtures/real_llm/sessions/`)
- [x] CI file valid; all suites green from a clean checkout; every README claim
      backed by a command (clean clone at `ab0e455`: 902 pytest, eval 7/7,
      typecheck, lint, 57 vitest, build; README audited claim by claim)
- [x] Non-test source lines below baseline: 27,886 vs 30,229
- [x] No pilot-identifying info and no secrets anywhere in the branch diff or
      commit messages (grep check at `ab0e455`: 0 hits)

## Baselines → final

| Metric | Baseline (`4a5767a`) | Final (`ab0e455`) |
| --- | --- | --- |
| Non-test source lines (Python + TS) | 30,229 | 27,886 |
| pytest | 801 | 902 |
| vitest | 57 | 57 |
| Offline eval | 7/7 | 7/7 (now through the real pipeline) |
| Workflow registries / runners | 3 / 2 | 1 / 1 |
| Workflows whose drafts had numbers checked | 3 of 8 (warnings only) | 8 of 8 (errors) |
| Slowest workflow at 50k rows | > 10 min | 12 s |

Measured with:

```bash
git ls-files '*.py' '*.ts' '*.tsx' | grep -v -E '^tests/|^frontend/e2e/|\.test\.tsx?$|^frontend/src/test/|\.config\.ts$' | xargs cat | wc -l
```

## Real-model spend

| Purpose | Cost |
| --- | --- |
| Smoke tests (reasoning budget, then JSON mode) | $0.0027 |
| Tuning set + recorded fixtures v1 (8) | $0.0023 |
| Held-out batches 1-2 (24 + 16) | $0.0295 |
| Fixtures v2 + held-out 3 (8 + 24) | $0.0274 |
| Final batch after review fixes (24) | $0.0185 |
| AP fixture re-record + MCP session | $0.0063 |
| Debugging calls and counter lag | ~$0.04 |
| **Total (OpenRouter `usage` at the end)** | **$0.125 of the $1.50 cap** |

Model: `deepseek/deepseek-v4.1-flash` only.

## Open items (backlog)

GW-37 (confirm Tyler/Munis assumptions against a real export: needs the pilot),
GW-47 (validator: signs and names), GW-48 (AP review at 50k: 12 s → <10 s),
plus the older GW-26/40/41/42/44. See [`tasks.md`](tasks.md).
