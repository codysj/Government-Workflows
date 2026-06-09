# Bank reconciliation — messy preflight fixtures

Tiny synthetic fixtures exercising the PREFLIGHT / CAPABILITY layer for the
`bank_reconciliation` workflow. No real PII.

- `fail_missing_amount_bank.csv` — a bank file with **no amount column** (only
  date + description). Preflight emits `MISSING_REQUIRED_COLUMN` +
  `NEEDS_HUMAN_CONFIGURATION` (blocking) -> **FAIL**. Pair with `ledger.csv`.

- `partial_sign_bank.csv` / `partial_sign_ledger.csv` — same magnitudes but
  systematically opposite-signed (bank shows withdrawals negative, ledger
  positive). The detector emits `POSSIBLE_SIGN_CONVENTION_MISMATCH` -> **PARTIAL**.

- `partial_batch_bank.csv` / `partial_batch_ledger.csv` — several individual
  bank items sum to one ledger total (many-to-one batch deposit). The detector
  emits `POSSIBLE_BATCH_MATCHING` -> **PARTIAL**.

The clean PASS case reuses `../bank.csv` and `../ledger.csv`.
