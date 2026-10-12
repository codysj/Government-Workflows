# Architecture decisions

One entry per decision: what was decided and why. The full working notes these
were condensed from (1,800 lines, June 2026) are in git history:
`git show 4a5767a:docs/decisions.md`. Current work is tracked in
[`goal.md`](goal.md); measurements are in [`evaluation.md`](evaluation.md).

## ADR-1 Code computes, the model writes, a human approves
Every parse, match, total, variance, and check is deterministic Python. The LLM
only explains, summarizes, and drafts, and must cite the source rows behind each
claim. Output is always a draft until a human approves it. *Why:* finance staff
and auditors must be able to trace every number; a model that might invent an
account or miscalculate a variance can't be the source of record.

## ADR-2 The validator rejects, it doesn't warn (2026-10)
Every number, date, identifier, and `table:row` reference in a draft must exist
in what the model was shown (summary + findings), after normalizing both sides
the same way; otherwise the draft fails. Claim kinds stay apart ("$50" can't
borrow support from row 50). *Why:* the original validator compared text
tokens, ran in 3 of 8 workflows, and only warned, so it passed a real
hallucinated invoice number. Tuned on real model output, confirmed on held-out
batches, and attacked by a review agent (see evaluation.md).

## ADR-3 One pipeline, thin front ends (2026-10)
`src/core/pipeline.run_workflow` is the only run path; `src/workflows/registry.py`
is the only workflow registry. The React console (via `api/`), the CLI, the MCP
server, and the eval harness all call it. *Why:* three registries and two
runners had drifted (the eval harness skipped preflight), and a guardrail added
once must apply everywhere.

## ADR-4 Mock LLM by default; real models opt-in by environment
The offline mock needs no key or network, so the whole tool and test suite run
offline. `LLM_MODE=real` (from the environment or `.env`) switches drafts and
Q&A to any OpenAI-compatible or Anthropic endpoint. A provider failure keeps
the deterministic results and marks the draft unavailable. Tests clear `LLM_*`.

## ADR-5 Fail-closed preflight
Before any workflow or LLM call, preflight profiles the files: PASS runs,
PARTIAL runs the supported checks and flags the rest, FAIL stops with
plain-language next steps (upload label, the file's real columns, "may not be
the right file"). Columns a human chose, or that preflight matched
confidently (exact names or known synonyms such as an ERP's "Posting Date"),
are passed to the workflow; uncertain guesses are not, because the workflow's
own detection knows more (e.g. which column of a combined budget-to-actual
file is the budget). A review caught the earlier version silently using a
guess and losing every variance on such a file.

## ADR-6 Local-first, single-user
SQLite run ledger, append-only audit log, files on the user's machine, no
accounts or auth. The launcher runs the server in-process so closing the window
stops everything. *Why:* the users are a small finance team piloting the tool;
a hosted, multi-user deployment needs procurement, auth, and data-handling
review this MVP deliberately avoids.

## ADR-7 React console replaced Streamlit
Streamlit proved the workflows and audit model, but its dense forms made it
hard for non-technical staff to tell what step they were on or what the AI
wrote. The four-step wizard (upload, check, run, review) with a visually
distinct AI section replaced it; Streamlit was retired in 2026-10. The console
never computes a value, even a display-only sum.

## ADR-8 One stdio MCP server for Claude and ChatGPT (2026-10)
`mcp_server.py` exposes the workflows as MCP tools over stdio only, and both
approved platforms reach that same process: Claude Desktop starts it from a
`.mcpb` extension; ChatGPT reaches it through OpenAI's Secure MCP Tunnel
(outbound only). One MCP Apps view renders the run in both. *Why:* no public
endpoint on a laptop with finance files, no second integration to maintain,
and not Custom GPT Actions (being retired). Tools read files only inside a
chosen data folder, and chat attachments are fetched only over https from
allowlisted hosts. The host model's own prose is outside the validated
boundary (see mcp.md).

## ADR-9 Tyler/Munis exports via a deterministic normalizer
`src/ingest/tyler.py` detects the export type by header overlap, parses amounts
as `Decimal`, derives signed amounts from debit/credit, hashes inputs, and keeps
source row indices. Assumptions not yet confirmed against a real export are
listed in [`tyler_assumptions.md`](tyler_assumptions.md) (GW-37).

## ADR-10 JE upload prep fails closed
If any blocking rule fails, no upload workbook is written, only an error
report. *Why:* a wrong journal-entry file imported into the ERP is worse than
no file.

## ADR-11 Transaction search: the model proposes, code filters
The plain-English query becomes a schema-validated `SearchCriteria`; the search
itself is deterministic pandas filtering. The model never selects rows.

## ADR-12 Built for 50k-row files (2026-10)
Every check is near-linear: amount-indexed matching, per-key row indexes
instead of table rescans, cached source-row lookups, preflight once per run,
and prompts capped at 150 findings with the rest summarized by count. The
matcher, budget rollup, and footer detection are pinned by randomized
equivalence tests; the other rewrites were checked old-vs-new during review.
Split payments (D8) are reported once per cluster, not once per overlapping
window.

## ADR-13 What was deliberately not built
No vector database or RAG (reference data is small and loaded per run), no
multi-agent orchestration, no autonomous actions, no live ERP connection, no
production auth, no hosted deployment, no background job queue. Each would add
risk or operating cost without a pilot need yet.
