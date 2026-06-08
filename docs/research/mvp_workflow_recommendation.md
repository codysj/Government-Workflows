# MVP Workflow Recommendation

Implements master spec "Phase 1 — Required research deliverables". This document
records the workflow selection that follows from `workflow_research.md` and the
scores in `workflow_selection_scorecard.md`. The rubric independently reproduced
the spec's default decision-rule set, so the MVP adopts it.

## Selected MVP workflows

**MVP Workflow 1: Bank reconciliation** (score 41)
Deterministic matching of bank vs ledger by amount/date within configurable
tolerances; flags unmatched bank items, unmatched ledger items, timing
differences, and potential duplicates; produces a source-linked exception packet.
The LLM only summarizes exceptions and drafts the reconciliation memo.
Implemented in `src/workflows/bank_reconciliation.py`; spec at
`docs/workflow_specs/bank_reconciliation.md`.

**MVP Workflow 2: Budget-to-actual variance review and commentary** (score 40)
Deterministic join on fund/account/department/object; computes dollar and
percent variance; flags lines above thresholds plus budget-only, actual-only,
and missing accounts. The LLM drafts source-cited variance commentary and
follow-up questions only. Implemented in `src/workflows/budget_variance.py`;
spec at `docs/workflow_specs/budget_variance.md`.

**MVP Workflow 3: Financial report consistency / error-flagging review**
(score 39) Deterministic checks — subtotal vs line-item sums, grand-total
tie-out, required sections, duplicate lines, unexpected negatives, account codes
in chart of accounts, large change vs prior version, naming consistency. The LLM
only explains flagged issues and drafts a review checklist; it never decides
whether the report is correct. Implemented in
`src/workflows/report_review.py`; spec at
`docs/workflow_specs/report_review.md`.

These three share the winning shape: a closed-form deterministic computation over
tabular inputs, every result row carrying a `SourceRowRef`, with the LLM confined
to drafting the human-readable wrapper. All three run on synthetic CSV/Excel,
produce a validated source-linked review packet, and are defensible against both
a generic chatbot and an ERP.

A controlled **guided freeform mode** (`src/workflows/freeform.py`, spec Phase 5)
ships alongside them as a logged, validated fallback for tasks that are not yet
formal workflows. It is not one of the three scored MVP workflows; it routes
structured input through the same audit/validation pipeline and feeds
`docs/research/freeform_task_observations.md` for future workflow discovery.

## Post-MVP workflow candidates (ranked)

1. **Purchase order / invoice mismatch review** (36) — reuses the
   reconciliation matching engine for 2-/3-way match; highest-value, lowest-cost
   extension.
2. **Vendor payment duplicate detection** (32) — a direct extension of the
   reconciliation duplicate logic; best folded into reconciliation and AP review.
3. **Grant reimbursement packet review** (31) — compose variance + report-review
   engines once they are mature; higher synthetic-demo cost.
4. **Accounts payable invoice coding support** (30) — viable once the
   chart-of-accounts context layer can constrain LLM suggestions to a
   deterministic shortlist of valid codes.
5. **Cash receipt anomaly review** (29) — deterministic anomaly flags; needs a
   realistic synthetic receipt history to be convincing.
6. **Monthly close checklist assistant** (27) — the stateful orchestration of
   the three MVP workflows (spec Tier-2 "Monthly close workflow orchestration");
   becomes valuable once the underlying workflows exist.

## Rejected workflows (with reasons)

- **Council agenda finance memo drafting** (21) — Rejected: the deliverable is
  official-adjacent narrative authored primarily by the LLM, with little
  deterministic anchor and weak source-linking. Conflicts with the core
  principle (no authoritative authoring, draft-only). Belongs in a later,
  source-linked, draft-only drafting tier.
- **ACFR note support** (20) — Rejected: low frequency (annual), narrative and
  judgment-laden, tightly regulated, thin deterministic surface, and expensive
  to demo convincingly with synthetic data. Possible Tier-2 draft-only assist.
- **Public-records redaction support** (18) — Rejected: different problem domain
  (document/PII handling) than the structured-finance pipeline; pushes toward
  sensitive-data handling that the non-negotiable data constraints forbid in
  development. The spec scopes redaction only as a Tier-1 prototype and warns
  against a public-records platform.

## Decision-rule conformance

The spec's default decision rule applies only "if research is inconclusive."
Here research was conclusive: the rubric ranked the same three workflows at the
top independently. The MVP set is adopted on the strength of the scores, and it
coincides with the default — there is no conflict to resolve.
