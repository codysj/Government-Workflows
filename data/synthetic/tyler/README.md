# Synthetic Tyler/Munis-style dataset — City of Riverbend (FY2026)

**Everything in this directory is synthetic.** The "City of Riverbend", all
vendors, addresses, phone numbers, invoices, checks, POs, accounts, and
amounts were fabricated for demonstration and testing. There is no real city,
vendor, taxpayer, or financial data here, and no real Tyler/Munis schema —
the files only *imitate* common Munis-style export shapes (local CSV/XLSX
only; no ERP integration, no credentials).

Fiscal year 2026 runs 2025-07-01 .. 2026-06-30. Funds: 100 General,
200 Water, 300 Capital Projects. Orgs: 5100 Public Works Admin (fund 100),
5200 Parks (fund 100), 6100 Water Ops (fund 200), 7100 Capital Engineering
(fund 300). Objects include 4010 charges for services, 5010 office supplies,
5110 salaries and wages, 5230 utilities, 5240 motor fuel, 5310 professional
services, 5410 road materials, 5999 obsolete expense (Inactive), 6100
capital outlay.

The machine-readable manifest of every planted anomaly (exact rows, ids,
amounts, expected search criteria, and per-file row counts) is
[`known_answers.json`](known_answers.json). Row indices in that file are
0-based data-row positions, i.e. the `source_row_index` values produced by
`src/ingest/tyler.py`.

## Files

| File | Rows | Contents |
| --- | --- | --- |
| `gl_detail.csv` | 127 | GL transaction detail (Year, Period, Journal, Eff Date, Src [API/GEN/POE/CRP], Fund, Org, Object, Project, Account Description, Description/Vendor, Debit, Credit, PO/Check/Invoice numbers). AP-sourced rows tie out to `ap_invoice_detail.csv`. Includes monthly payroll, encumbrances, cash receipts, and the Q4 search rows. Amounts span $12.50 .. $48,000.00. |
| `gl_detail.xlsx` | 127 | Same data as `gl_detail.csv`, with a 3-row Munis-style title block above the header row (exercises deterministic header detection; header is sheet row 4 / 0-based row 3). |
| `ap_invoice_detail.csv` | 66 | AP invoice detail. Contains all of D1-D8 and the invoice sides of P1-P6 and P8. Qty/Unit Price are populated for PO-related rows (the P1 invoice deliberately bills a lump sum with blank Qty/Unit Price). Two invoices are Open/unpaid (true negatives). |
| `vendor_list.csv` | 25 | Vendor master (V-1001..V-1025). Contains the D3 similar-name pair and the D6 Inactive vendor. V-9999 is deliberately absent. |
| `check_register.csv` | 64 | One row per check, consistent with AP (amount = sum of its invoices; `Invoice Number(s)` is `;`-joined). Includes one Void check (50100, reissued as 50101), one batch check paying two invoices (50115), the two D1 checks, and the D5 check dated before its invoice. |
| `purchase_orders.csv` | 18 | PO lines (PO-3000..PO-3015; PO-3005/PO-3006 have two lines). P1-P8 planted; all other POs reconcile cleanly as true negatives. Includes a `Last Activity Date` column (carries the P4 close-activity date). |
| `budget_to_actual.csv` | 18 | Budget vs actual by fund/org/object. YTD Actual equals the GL net (Src API/GEN); Encumbrances equal the GL POE rows; Available/Percent Used computed deterministically. |
| `budget_to_actual.xlsx` | 18 | Clean Excel variant of `budget_to_actual.csv` (header on row 1). |
| `chart_of_accounts.csv` | 21 | Every fund/org/object combination used anywhere in this dataset, plus the planted Inactive account 100-5100-5999 "Obsolete Expense". |
| `je_upload_template.csv` / `.xlsx` | 0 | Empty Munis-style JE upload templates (headers only): Journal, Line, Eff Date, Fund, Org, Object, Account Description, Debit, Credit, Line Description, Reference. |
| `known_answers.json` | — | Machine-readable known answers (built by code from the generated CSVs). |

## Planted AP duplicate / suspicious-payment anomalies (D1-D8)

- **D1 duplicate_invoice_number** — V-1001 "Acme Office Supply LLC",
  invoice INV-10234 ($1,250.00) appears on two AP rows, paid by checks
  50012 and 50044.
- **D2 same_vendor_same_amount_near_date** — V-1003 "Cascade Paving Inc":
  INV-20881 $8,400.00 (2026-03-10) and INV-20890 $8,400.00 (2026-03-13),
  3 days apart (two releases against blanket PO-3015 so they do not also
  trip the missing-PO check).
- **D3 similar_vendor_names** — V-1002 "Riverbend Electric Co" and V-1019
  "Riverbend Electric Company" are separate vendor records; each billed
  invoice ref "5521" for $2,200.00 (2026-04-02 and 2026-04-04).
- **D4 missing_po_over_threshold** — V-1004 "Summit IT Services" INV-44510
  $12,000.00 with a blank PO number (default threshold $5,000.00). This is
  the only over-threshold invoice without a PO.
- **D5 payment_before_invoice_date** — V-1006 INV-55003 dated 2026-05-20,
  paid by check 50190 dated 2026-05-12.
- **D6 inactive_vendor** — V-1005 "Old Town Hardware" is Inactive in
  `vendor_list.csv` yet has paid invoice INV-66001 $730.00 (check 50120).
- **D7 unknown_vendor** — INV-77001 $4,000.00 references V-9999 "Unknown
  Vendor Services", which does not exist in `vendor_list.csv`.
- **D8 split_payments** — V-1007 invoices INV-88001/INV-88002/INV-88003,
  each $4,900.00, all dated 2026-02-17 (three checks 50125/50126/50127,
  each just under the $5,000 threshold, same day).

## Planted PO / invoice mismatches (P1-P8)

- **P1 invoice_exceeds_po** — PO-3001 (V-1003) totals $10,000.00; INV-90011
  bills $11,500.00 against it (lump sum; Qty/Unit Price blank).
- **P2 wrong_vendor** — PO-3002 was issued to V-1004, but INV-90022
  referencing PO-3002 is from V-1008.
- **P3 missing_po** — INV-90033 references PO-3999, which does not exist in
  `purchase_orders.csv`.
- **P4 closed_po_usage** — PO-3004 is Closed (last activity 2026-01-31);
  INV-90044 dated 2026-03-05 bills against it.
- **P5 unit_price_mismatch** — PO-3005 line 1: qty 100 at $25.00; INV-90055
  bills qty 100 at $27.50. (PO-3005 has a second, undelivered line so the
  invoice does not also exceed the PO total.)
- **P6 quantity_mismatch** — PO-3006 line 1: qty 50 at $40.00; INV-90066
  bills qty 65 at $40.00.
- **P7 received_not_invoiced** — PO-3007 line 1: received 80, invoiced 0,
  and no AP invoice exists.
- **P8 invoiced_not_received** — PO-3008 line 1: received 10 but invoiced 40
  (via INV-90088).

All other POs reconcile cleanly (true negatives), including the Closed
PO-3000 whose invoice predates its close.

## Transaction-search known answers (Q1-Q4)

- **Q1** "payments to Cascade Paving over $5,000 between March and May 2026"
  -> the two D2 invoices (robust to using invoice date or check date).
- **Q2** "invoice INV-10234" -> the two D1 AP rows (and two GL rows).
- **Q3** "checks to Acme Office Supply in February 2026" -> checks 50122 and
  50128 in `check_register.csv`.
- **Q4** "pothole repair charges in fund 300" -> three GEN journal rows in
  `gl_detail.csv` (journals 9010/9011/9012) whose descriptions contain
  "pothole repair" / "asphalt"; no pothole/asphalt rows exist outside
  fund 300.

## Journal-entry known answers

`chart_of_accounts.csv` covers every fund/org/object combination used in the
dataset and plants one Inactive account, **100-5100-5999 "Obsolete
Expense"**, for fail-closed JE validation tests. The JE upload templates are
headers-only.

## True negatives

Void check 50100 (reissued as 50101), batch check 50115 paying two telecom
invoices, two Open/unpaid June invoices, and all the clean POs listed in
`known_answers.json` under `true_negatives`.
