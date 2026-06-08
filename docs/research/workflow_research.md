# Workflow Research — Small-City Finance Department Tasks

Implements master spec "Phase 1 — Research and Workflow Selection". These are
analytical notes on the twelve candidate workflows. They are grounded in the
spec's product thesis (a controlled, source-linked, auditable workflow runner —
not a chatbot) and the core principle that deterministic code does all parsing,
matching, calculation, and validation while the LLM only explains, summarizes,
drafts, classifies, and flags.

No external citations are fabricated. Where a claim is a general observation
about municipal finance practice it is stated as an analytical assumption, not a
cited fact.

## Evaluation lens

For each candidate the research asks the eight Phase-1 questions: is the task
**frequent**, **painful**, **safe for AI assistance**, **easy to validate
deterministically**, **source-data-driven**, **useful to non-technical staff**,
**demonstrable with synthetic data**, and **strong as a recruiting artifact**.
The two over-arching constraints that shape every answer:

- **Safety.** A task is "safe for AI" only when the authoritative output is a
  deterministic calculation and the LLM is confined to language. Tasks whose
  core deliverable is a number the LLM would have to produce, or an official
  filing, score low on safety regardless of how painful they are.
- **Determinism / validation.** A task is easy to validate when its correct
  answer is a closed-form computation over tabular inputs (a match, a variance,
  a tie-out). Tasks whose "correctness" is a matter of narrative judgment are
  hard to validate and therefore hard to defend.

---

## Candidate notes

### 1. Bank reconciliation
Recurring monthly (often per-account), and a well-known pain point: staff hand-
match bank lines against ledger lines, chase timing differences, and hunt
duplicate payments. The authoritative work — matching by amount/date within
tolerances, flagging unmatched items, duplicates, and timing differences — is
pure deterministic computation, so the LLM stays safely on the explanation side
(summarize exceptions, draft the reconciliation memo). Every exception ties back
to a specific bank/ledger row, so source-linking is natural and necessary.
Trivially demonstrable with two synthetic CSVs. High recruiting depth (matching
algorithm, tolerances, exception taxonomy). Spec-mandated MVP Workflow 1.

### 2. Budget-to-actual variance review
Monthly/quarterly close staple. Pain comes from re-keying variance tables and
writing the same commentary repeatedly. The math (join on
fund/account/department/object, dollar and percent variance, threshold flagging,
budget-only / actual-only / missing-account detection) is deterministic and
closed-form; the LLM drafts the commentary and follow-up questions only. Strong
source-linking (every flagged line points at its budget and actual rows).
Easy synthetic demo. High differentiation from a generic chatbot (a chatbot
cannot reliably compute or tie a variance to a source row) and from an ERP (ERPs
produce the table but not the drafted, source-cited narrative). Spec default
MVP Workflow 2.

### 3. Financial report consistency / error-flagging review
Pre-finalization review of draft schedules and packets. Pain: subtotal/tie-out
errors, duplicate lines, missing sections, and invalid account codes are caught
late and embarrass the department. All checks are deterministic rules
(subtotal == sum of line items, grand total ties out, required-section presence,
duplicate detection, account-code-in-chart-of-accounts, large change vs prior
version, naming consistency). The LLM only explains why a flagged issue matters
and drafts a review checklist — it never decides whether the report is
"correct." Excellent validation story (known-answer datasets per rule). Spec
default MVP Workflow 3.

### 4. Accounts payable invoice coding support
Frequent and painful, but the core ask — assigning the correct GL account /
fund to an invoice — is fundamentally a **decision** about an account number.
Letting the LLM propose codes risks inventing account numbers, which the core
principle forbids. A safe version is narrow (the LLM suggests from a
deterministic shortlist of valid codes), which dilutes the value. Validation is
weaker because "the right code" is policy/judgment, not a closed-form
computation. Good Post-MVP candidate once the chart-of-accounts context layer is
mature enough to constrain suggestions deterministically.

### 5. Purchase order / invoice mismatch review (2-/3-way match)
Strong fit structurally: matching PO vs invoice vs receipt by amount/quantity is
the same deterministic matching engine as bank reconciliation, and mismatches
are source-linked exceptions the LLM can summarize. Safe and validatable. The
reason it is not an MVP pick is **differentiation overlap**: it reuses the bank-
reconciliation matching pattern, so it adds breadth but little new architectural
depth for the recruiting artifact. Highest-ranked Post-MVP workflow.

### 6. Monthly close checklist assistant
Useful and frequent, but it is primarily a stateful **orchestration** of the
other workflows (run reconciliation, then variance, then report review, track
approvals). That is explicitly a Tier-2 post-MVP item in the spec ("Monthly
close workflow orchestration"). As a standalone MVP workflow it has little
deterministic computation of its own and weak source-linking, so it scores low
on differentiation and depth until the underlying workflows exist. Post-MVP.

### 7. Council agenda finance memo drafting
High staff pain (writing is slow), but this is the **least safe** candidate: the
deliverable is official-adjacent narrative language, and the LLM would be the
primary author. There is little deterministic computation to anchor it and weak
source-linking, so validation is largely "does a human like the prose."
Differentiation from a generic chatbot is low because it is essentially drafting.
Rejected for MVP; a constrained, source-linked version belongs in Tier-2
"Regulation-aware document drafting," always draft-only.

### 8. Grant reimbursement packet review
Genuinely valuable and reasonably safe (checking that expenditures tie to a
grant budget and that required documents are present is rule-based). But the
inputs are heterogeneous (budgets, expenditure ledgers, supporting docs, grant
terms) and grant-specific, which makes a convincing **synthetic demo** expensive
and the rules less general. Good Post-MVP candidate once the report-review and
variance engines can be composed. Lower MVP priority due to demo cost.

### 9. Cash receipt anomaly review
Frequent, and anomaly flagging (unusual amounts, out-of-pattern dates, gaps in
receipt numbering) is deterministic and safe. However "anomaly" without a
labeled baseline is fuzzy to validate, and a compelling synthetic dataset needs
a realistic receipt history to make anomalies meaningful. Moderate
differentiation. Reasonable Post-MVP candidate; not deep enough to displace the
three defaults.

### 10. Vendor payment duplicate detection
Safe and validatable — duplicate detection by vendor/amount/date is exactly the
deterministic duplicate logic already built for bank reconciliation. That is
also its weakness as a standalone MVP pick: it is a **subset** of the
reconciliation feature set, so it adds little new depth. Best delivered as a
capability folded into reconciliation and AP review rather than its own
workflow. Post-MVP / absorbed.

### 11. ACFR note support
High-prestige but high-effort and lower-frequency (annual). ACFR notes are
narrative, judgment-laden, and tightly regulated; the safe deterministic surface
is thin and the synthetic-demo cost is high (you must model realistic statements
and notes). Validation is weak (correctness is professional judgment). Rejected
for MVP; possible Tier-2 drafting assist, draft-only.

### 12. Public-records redaction support
Important and AI-relevant, but it sits in a different problem domain
(document/PII handling) than the structured-finance pipeline, and the spec
explicitly scopes redaction as a Tier-1 *prototype* and warns against building a
public-records platform. Worse, it pushes toward handling sensitive data, which
the non-negotiable constraints forbid during development. Low safety in the
development context, weak source-linking to finance tables. Rejected for MVP.

---

## Cross-cutting observations

- **The strongest MVP workflows share one shape:** a closed-form deterministic
  computation over tabular CSV/Excel inputs whose every result row references a
  source row, with the LLM confined to drafting the human-readable wrapper. The
  three defaults (bank reconciliation, budget variance, report review) all fit
  this shape; the rejected candidates fail it on safety, validation, or demo
  cost.
- **Matching-family workflows cluster.** Bank reconciliation, PO/invoice match,
  and vendor duplicate detection reuse one deterministic matching engine.
  Shipping bank reconciliation first delivers that engine; the others become
  cheap Post-MVP extensions rather than independent MVP bets.
- **Drafting-family workflows are the riskiest.** Council memos, ACFR notes, and
  grant narratives put the LLM in the authoring seat with little deterministic
  anchor, conflicting with the core principle. They belong in a later,
  source-linked, draft-only tier.
- **Govtech / ERP differentiation.** ERPs already produce ledgers and variance
  tables; they do not produce a validated, source-cited, auditable *review
  packet* with a drafted plain-language explanation. The MVP's defensibility is
  precisely the review-and-audit layer on top of deterministic output, not the
  computation itself.
