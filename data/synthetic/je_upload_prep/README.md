# Synthetic JE Upload Prep Data — City of Riverbend (Fictional)

All data in this directory is synthetic and was generated for the fictional
City of Riverbend. No real city, vendor, or financial data is present.

## Files

- **je_draft_valid.csv** — A balanced, multi-journal draft that should pass all
  blocking validations and produce `upload_ready=true`.
- **je_draft_invalid.csv** — A draft with ALL of the following planted blocking
  defects (one per line); produces `upload_ready=false`.
- **je_draft_warnings.csv** — A balanced draft with a fund/org/object combo
  (100-5200-5240) that exists in the COA as individual segments but the
  combo 100/5200/5240 is not present in the chart_of_accounts.csv (Motor Fuel
  is only in 100-5100-5240 and 200-6100-5240). Triggers the plausibility
  warning; produces `upload_ready=true`.
- **je_config.json** — Fiscal period 2025-07-01..2026-06-30,
  `allow_inactive_accounts=false`.

## Planted Defects in je_draft_invalid.csv

| Row (source_row_index) | Rule                          | Description                                          |
|------------------------|-------------------------------|------------------------------------------------------|
| 0,1 (journal 9200)     | debits_equal_credits          | Total debits 500+100+200+300+150+250-75 = 1425; credits 400+250 = 650; imbalance $775 |
| 2 (line 3)             | account_in_coa                | Object 9999 is not present in chart_of_accounts.csv  |
| 3 (line 4)             | eff_date_required             | Eff Date is blank                                    |
| 4 (line 5)             | eff_date_valid                | "2026-13-40" is not a valid calendar date            |
| 5 (line 6)             | no_inactive_account           | Account 100-5100-5999 has Status=Inactive            |
| 6 (line 7)             | no_both_debit_and_credit      | Both Debit=250.00 and Credit=250.00 populated        |
| 7 (line 8)             | no_negative_amount            | Debit=-75.00 is negative                             |

Note: The debit/credit imbalance is computed across the whole journal 9200,
since all lines share the same Journal number.

## Known Answers

- `je_draft_valid.csv` → `upload_ready=true`, debits==credits per journal and
  overall, source_mapping covers all 7 rows.
- `je_draft_invalid.csv` → `upload_ready=false`, 7 or more blocking findings
  (one per defect above, plus the imbalance finding), no upload files written.
- `je_draft_warnings.csv` → `upload_ready=true`, 1 warning finding
  (combo_plausibility for 100/5200/5240).
- Fiscal period boundary: dates 2025-07-01 and 2026-06-30 pass; 2025-06-30
  and 2026-07-01 fail with out_of_fiscal_period.
- `allow_inactive_accounts=true` removes the inactive-account blocking finding
  for line 6 of je_draft_invalid.csv but the other defects remain.
