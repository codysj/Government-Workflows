# Guided Freeform — messy preflight fixtures

Tiny synthetic STRUCTURED-request fixtures exercising the PREFLIGHT / CAPABILITY
layer for the `freeform` workflow. No real PII. Freeform inputs are NOT tabular
files — they are the structured control fields (`task_type`,
`sensitivity_confirmation`, etc.), so each fixture is a small JSON request that
the test loads and passes to `run_preflight(CAPABILITY, inputs, ...)`.

- `pass_clean_request.json` — sensitivity confirmed + a draft-oriented task_type
  with no authoritative wording. Preflight emits no findings -> **PASS**.

- `fail_missing_sensitivity_request.json` — `sensitivity_confirmation` is
  `false` (the existing fail-closed rule). The detector emits
  `NEEDS_HUMAN_CONFIGURATION` with `blocks_run=True` -> **FAIL** (the runner must
  refuse and NOT call the LLM).

- `partial_authoritative_request.json` — sensitivity confirmed, but the request
  wording ("reconcile", "calculate", "approve") reads like an attempt to obtain
  an authoritative answer / take over a failed formal workflow. The detector
  emits a non-blocking `POSSIBLE_UNKNOWN_REPORT_STRUCTURE` -> **PARTIAL**
  (freeform stays draft-only; it never calculates, matches, or approves).
