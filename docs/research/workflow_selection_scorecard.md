# Workflow Selection Scorecard

Implements master spec "Phase 1 — Workflow scoring rubric". Each of the twelve
candidate workflows is scored 1–5 on the ten rubric dimensions. The composite is

```
workflow_score =
    frequency
  + staff_pain
  + safety_for_ai
  + deterministic_validation
  + synthetic_demo_feasibility
  + source_linking_need
  + recruiting_depth
  + generic_ai_differentiation
  + erp_differentiation
  - implementation_cost
```

i.e. the sum of the nine value dimensions **minus** implementation cost. Higher
is better. Maximum possible = (9 × 5) − 1 = 44; minimum = (9 × 1) − 5 = 4.

Scores are analytical judgments grounded in `workflow_research.md` and the
core principle (deterministic computation + LLM-as-language-only). They are
relative rankings, not measured data.

## Dimension keys

| short | dimension |
|-------|-----------|
| Freq | Frequency |
| Pain | Staff pain level |
| Safe | Safety for AI assistance |
| Valid | Ease of deterministic validation |
| Demo | Ease of synthetic demo creation |
| Src | Need for source-linked explanation |
| Depth | Recruiting technical depth |
| vGPT | Differentiation from generic ChatGPT |
| vERP | Differentiation from ERP systems |
| Cost | MVP implementation cost (subtracted) |

## Scores

| # | Candidate | Freq | Pain | Safe | Valid | Demo | Src | Depth | vGPT | vERP | Cost | **Score** |
|---|-----------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | **Bank reconciliation** | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 4 | 3 | **41** |
| 2 | **Budget-to-actual variance review** | 5 | 5 | 5 | 5 | 5 | 5 | 4 | 5 | 4 | 3 | **40** |
| 3 | **Financial report consistency review** | 4 | 4 | 5 | 5 | 5 | 5 | 5 | 5 | 4 | 3 | **39** |
| 5 | Purchase order / invoice mismatch review | 4 | 4 | 5 | 5 | 4 | 5 | 4 | 4 | 4 | 3 | **36** |
| 8 | Grant reimbursement packet review | 3 | 4 | 4 | 4 | 3 | 5 | 4 | 4 | 4 | 4 | **31** |
| 4 | Accounts payable invoice coding support | 5 | 4 | 3 | 3 | 4 | 4 | 4 | 4 | 3 | 4 | **30** |
| 9 | Cash receipt anomaly review | 4 | 3 | 4 | 3 | 3 | 4 | 4 | 4 | 4 | 4 | **29** |
| 10 | Vendor payment duplicate detection | 4 | 3 | 5 | 5 | 4 | 4 | 3 | 3 | 3 | 2 | **32** |
| 6 | Monthly close checklist assistant | 4 | 4 | 4 | 3 | 3 | 3 | 3 | 3 | 4 | 4 | **27** |
| 7 | Council agenda finance memo drafting | 3 | 5 | 2 | 2 | 3 | 2 | 2 | 2 | 3 | 3 | **21** |
| 11 | ACFR note support | 2 | 4 | 2 | 2 | 2 | 3 | 3 | 3 | 4 | 5 | **20** |
| 12 | Public-records redaction support | 2 | 3 | 2 | 2 | 2 | 2 | 3 | 3 | 3 | 4 | **18** |

(Sorted within tiers; row `#` is the candidate's number from the Phase-1 list.)

## Ranked result

1. Bank reconciliation — **41**
2. Budget-to-actual variance review — **40**
3. Financial report consistency review — **39**
4. Purchase order / invoice mismatch review — **36**
5. Vendor payment duplicate detection — **32**
6. Grant reimbursement packet review — **31**
7. Accounts payable invoice coding support — **30**
8. Cash receipt anomaly review — **29**
9. Monthly close checklist assistant — **27**
10. Council agenda finance memo drafting — **21**
11. ACFR note support — **20**
12. Public-records redaction support — **18**

## Interpretation

The top three by composite score are exactly the three the spec's default
decision rule names: **bank reconciliation (41), budget-to-actual variance
review (40), and financial report consistency review (39)**. They separate
cleanly from the next tier (PO/invoice match at 36) because all three combine
the highest safety, validation, source-linking, and differentiation scores — the
dimensions that matter most under the project's core principle — at a moderate
implementation cost.

The next two highest scorers (PO/invoice match, vendor duplicate detection) score
well largely because they **reuse** the bank-reconciliation matching engine; that
overlap is why they are the leading Post-MVP extensions rather than independent
MVP picks. The lowest scorers (memo drafting, ACFR notes, redaction) fall on the
**Safe** and **Valid** dimensions: their core deliverable is narrative judgment
or sensitive-document handling, which conflicts with the principle that the LLM
must not author authoritative output or handle sensitive data in development.

Research is therefore **not inconclusive** — the rubric independently confirms
the spec's default set, so the MVP adopts it.
