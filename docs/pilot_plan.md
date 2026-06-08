# Pilot Plan

Implements master spec "Phase 7 — Human pilot metrics". A simple, repeatable
process to measure whether the tool helps real finance staff. It is consistent
with the measures defined in `docs/evaluation.md`. Synthetic data only — no real
PII or sensitive financial data, per the non-negotiable data constraints.

## Goal

Measure, on realistic-but-synthetic tasks, whether the tool (1) saves time
versus the current manual process and (2) produces output that finance staff
find clear and trustworthy enough to keep using.

## Participants

- 3–6 participants in small-municipal finance roles (finance director,
  accountant, AP staff, finance analyst). Non-technical; no prompt-engineering
  knowledge assumed.
- Each participant completes the same task set so results are comparable.

## Pilot tasks

One task per MVP workflow, each using a bundled synthetic dataset:

1. **Bank reconciliation** — reconcile a synthetic bank statement against a
   synthetic ledger and identify the exceptions requiring follow-up.
2. **Budget-to-actual variance review** — identify the significant variances in
   a synthetic budget vs actuals and produce review commentary.
3. **Report consistency review** — find the inconsistencies (subtotal mismatch,
   invalid account code, duplicate line, missing section, naming) in a synthetic
   draft report.

## Procedure (per participant, per task)

1. **Baseline (before tool).** The participant performs the task with their
   current manual process (spreadsheet / eyeballing). The facilitator times it
   end-to-end.
2. **With tool.** The participant runs the same task in the tool (Streamlit
   "Run Workflow" page, "Use example files" to load the synthetic data), reviews
   the deterministic findings and the AI draft explanation, uses the human-review
   controls, and exports the review packet. The facilitator times it end-to-end.
3. **Debrief.** The participant gives the five ratings and qualitative feedback.

To reduce ordering bias, alternate which task a participant does manually first
versus with-tool first across participants.

## Measures captured

Exactly the `docs/evaluation.md` human-pilot measures:

| measure | scale |
|---------|-------|
| Task completion time before tool | minutes |
| Task completion time with tool | minutes |
| Confidence | 1–5 |
| Clarity | 1–5 |
| Would keep using | 1–5 |
| Qualitative feedback | free text |

The automated harness (`src/eval/harness.py`) is run before the pilot to confirm
every known-answer check passes, so participants review correct deterministic
output and the pilot isolates the human-experience question.

## Data handling

- Synthetic datasets only; no real bank, vendor, employee, or taxpayer data.
- No secrets, credentials, or network calls — the tool runs in mock LLM mode
  (the default offline path).
- Pilot notes record only role and ratings, not participant identity.

## Success criteria

- **Time:** median time-with-tool < median time-before-tool on each task.
- **Experience:** median confidence, clarity, and would-keep-using each ≥ 4/5.
- **Trust:** qualitative feedback indicates the source-linked, validated packet
  was reviewable and that staff understood the deterministic-vs-AI split.

## Outputs

- A short results table (per task: before/after time, the three medians).
- A synthesis of qualitative feedback, with recurring confusion points routed
  to the backlog. Freeform-mode observations logged during the pilot are added
  to `docs/research/freeform_task_observations.md` to inform future workflow
  templates.
