# Tyler/Munis Assumptions and Verification Register

**GW-15 Status: PARTIAL COMPLETION**

The assumptions documented here are modeled on common Tyler/Munis (Enterprise ERP
powered by Munis) export shapes and on one partially-readable public user guide
(Munis v10.5 General Journal Entry, via docslib.org). They have NOT been validated
against real Munis export files from any live city system, nor against Tyler
Technologies' vendor documentation (which is not publicly available in
machine-readable form). Full completion of GW-15 requires real export templates
and/or confirmation from a city's Tyler contact.

Corroboration status used below:

- PARTIAL CORROBORATION: A public Munis user guide or government-published
  procedure document mentioned this concept or field name in a compatible way.
  This is suggestive but not a field-by-field schema confirmation.
- UNVERIFIED: Modeled from common municipal ERP conventions; no public source
  found that explicitly names this field in this context.

Public sources consulted (see Section 6 for full citations):

- Munis v10.5 General Journal Entry guide (docslib.org)
- Horry County SC Munis Import Journals 2018 (PDF; binary-encoded, not fully readable)
- Framingham MA General Ledger user guide (PDF; binary-encoded, not fully readable)
- Search snippets from Fairfield County OH Enterprise ERP GL Procedures 2021.6
- Search snippets from Muncie IN AP invoice report examples
- Franklin County OH Financial Reporting Training Manual 2016 (search snippet only)

---

## 1. Overview of Dataset Types

The codebase registers eight Tyler/Munis dataset types in `TYLER_DATASET_TYPES`
(`src/ingest/tyler.py`). Each type has a canonical column set the normalizer
enforces and a set of raw-header aliases that map Munis-style spellings onto
those canonical names.

| Dataset Type        | Module Tag | Required Columns (canonical)                                                                             |
|---------------------|------------|----------------------------------------------------------------------------------------------------------|
| gl_detail           | gl         | fiscal_year, period, journal, date, fund, org, object, description, debit, credit                       |
| ap_invoice_detail   | ap         | vendor_number, vendor_name, invoice_number, invoice_date, invoice_amount                                 |
| vendor_list         | vendors    | vendor_number, vendor_name, status                                                                       |
| check_register      | checks     | check_number, check_date, vendor_number, vendor_name, check_amount, status                               |
| purchase_orders     | po         | po_number, po_date, vendor_number, vendor_name, status, line, qty, unit_price, line_amount               |
| budget_to_actual    | budget     | fund, org, object, original_budget, revised_budget, ytd_actual                                           |
| chart_of_accounts   | coa        | account, fund, org, object, account_description, status                                                  |
| je_upload           | je         | journal, line, date, fund, org, object, debit, credit                                                    |

---

## 2. Dataset-Type Registers

### 2.1 GL Detail (`gl_detail`)

**Synthetic fixture headers (actual CSV):**
`Year, Period, Journal, Eff Date, Src, Fund, Org, Object, Project, Account Description, Description/Vendor, Debit, Credit, PO Number, Check Number, Invoice Number`

| Fixture Header      | Snake-Cased        | Canonical Target    | Alias Source        | Required? | Verified? |
|---------------------|--------------------|---------------------|---------------------|-----------|-----------|
| Year                | year               | fiscal_year         | alias: year         | YES       | PARTIAL CORROBORATION -- Franklin County training doc references "YEAR" as a GL column in search snippet |
| Period              | period             | period              | exact match         | YES       | PARTIAL CORROBORATION -- "PER" and "Period" mentioned in GL column lists |
| Journal             | journal            | journal             | exact match         | YES       | PARTIAL CORROBORATION -- Munis journal entries auto-assign a Journal number |
| Eff Date            | eff_date           | date                | alias: eff_date     | YES       | PARTIAL CORROBORATION -- Munis v10.5 guide uses "Effective Date" as the transaction date; "Eff Date" as the abbreviated form appears in GL column search snippet |
| Src                 | src                | source              | alias: src          | NO (opt)  | PARTIAL CORROBORATION -- "SRC" appears in GL column listing in Franklin County search snippet |
| Fund                | fund               | fund                | exact match         | YES       | PARTIAL CORROBORATION -- Fund is a standard Munis account dimension |
| Org                 | org                | org                 | exact match         | YES       | PARTIAL CORROBORATION -- "Org" (Organization) is a standard Munis dimension per multiple guides |
| Object              | object             | object              | exact match         | YES       | PARTIAL CORROBORATION -- "Object" is a standard Munis expenditure dimension |
| Project             | project            | project             | exact match         | NO (opt)  | UNVERIFIED -- common ERP field; spelling not confirmed |
| Account Description | account_description| account_description | exact match (after snake-case) | NO (opt) | PARTIAL CORROBORATION -- field exists in Munis COA |
| Description/Vendor  | description_vendor | description         | alias: description_vendor | YES | UNVERIFIED -- the "Description/Vendor" combined header is a synthetic convention; real Munis exports may use separate "Description" and "Vendor" columns |
| Debit               | debit              | debit               | exact match         | YES       | PARTIAL CORROBORATION -- separate Debit/Credit columns confirmed in Munis v10.5 GL and JE import |
| Credit              | credit              | credit              | exact match         | YES       | PARTIAL CORROBORATION -- as above |
| PO Number           | po_number          | po_number           | exact match (after snake-case) | NO (opt) | UNVERIFIED -- column spelling not confirmed; Munis may use "PO No" or "Purchase Order" |
| Check Number        | check_number       | check_number        | exact match (after snake-case) | NO (opt) | UNVERIFIED |
| Invoice Number      | invoice_number     | invoice_number      | exact match (after snake-case) | NO (opt) | UNVERIFIED |

**Additional aliases registered in code (not used by fixture):**

| Alias (snake-cased) | Canonical Target    | Verified? |
|---------------------|---------------------|-----------|
| fy                  | fiscal_year         | UNVERIFIED |
| fiscal_yr           | fiscal_year         | UNVERIFIED |
| per                 | period              | PARTIAL CORROBORATION -- "PER" appears in GL column listing snippet |
| prd                 | period              | UNVERIFIED |
| jnl                 | journal             | UNVERIFIED |
| journal_number      | journal             | UNVERIFIED |
| effective_date      | date                | PARTIAL CORROBORATION -- Munis v10.5 uses "Effective Date" as the label |
| jrnl_date           | date                | UNVERIFIED |
| gl_date             | date                | UNVERIFIED |
| post_date           | date                | UNVERIFIED |
| posting_date        | date                | UNVERIFIED |
| source_code         | source              | UNVERIFIED |
| line_description    | description         | UNVERIFIED |
| memo                | description         | UNVERIFIED |
| desc                | description         | UNVERIFIED |
| account_desc        | account_description | UNVERIFIED |
| acct_description    | account_description | UNVERIFIED |

**Sign convention:** `amount = debit - credit` (Decimal arithmetic; blank side treated
as 0; both-blank stays None). Original debit/credit columns are kept.
Verification status: UNVERIFIED against a real city's GL sign convention.
The Munis v10.5 JE guide says "Amount: positive numbers only; D/C column indicates
direction", which is compatible with separate debit/credit columns but does not
confirm the derived-amount sign formula used here.

---

### 2.2 AP Invoice Detail (`ap_invoice_detail`)

**Synthetic fixture headers (actual CSV):**
`Vendor Number, Vendor Name, Invoice Number, Invoice Date, Due Date, PO Number, Invoice Amount, Qty, Unit Price, Check Number, Check Date, Status, Fund, Org, Object, Description`

| Fixture Header  | Snake-Cased       | Canonical Target  | Alias Source             | Required? | Verified? |
|-----------------|-------------------|-------------------|--------------------------|-----------|-----------|
| Vendor Number   | vendor_number     | vendor_number     | exact match (snake-case) | YES       | PARTIAL CORROBORATION -- "Vendor Number" appears in Munis AP procedures |
| Vendor Name     | vendor_name       | vendor_name       | exact match (snake-case) | YES       | UNVERIFIED as exact header spelling |
| Invoice Number  | invoice_number    | invoice_number    | exact match (snake-case) | YES       | PARTIAL CORROBORATION -- Muncie IN AP report shows Invoice Number |
| Invoice Date    | invoice_date      | invoice_date      | exact match (snake-case) | YES       | PARTIAL CORROBORATION -- Muncie IN AP report shows Invoice Date |
| Due Date        | due_date          | due_date          | exact match (snake-case) | NO (opt)  | PARTIAL CORROBORATION -- Muncie AP report shows Due Date |
| PO Number       | po_number         | po_number         | exact match (snake-case) | NO (opt)  | UNVERIFIED |
| Invoice Amount  | invoice_amount    | invoice_amount    | exact match (snake-case) | YES       | UNVERIFIED -- "Invoice Net Amount" appears in Muncie data; "Invoice Amount" is a synthetic variant |
| Qty             | qty               | qty               | exact match              | NO (opt)  | UNVERIFIED |
| Unit Price      | unit_price        | unit_price        | exact match (snake-case) | NO (opt)  | UNVERIFIED |
| Check Number    | check_number      | check_number      | exact match (snake-case) | NO (opt)  | UNVERIFIED |
| Check Date      | check_date        | check_date        | exact match (snake-case) | NO (opt)  | PARTIAL CORROBORATION -- "Payment Date" mentioned in Muncie AP report; "Check Date" is alias |
| Status          | status            | status            | exact match              | NO (opt)  | UNVERIFIED as exact header spelling; "Status" is generic |
| Fund            | fund              | fund              | exact match              | NO (opt)  | PARTIAL CORROBORATION |
| Org             | org               | org               | exact match              | NO (opt)  | PARTIAL CORROBORATION |
| Object          | object            | object            | exact match              | NO (opt)  | PARTIAL CORROBORATION |
| Description     | description       | description       | exact match              | NO (opt)  | UNVERIFIED |

**Additional aliases registered in code (not used by fixture):**

| Alias (snake-cased) | Canonical Target  | Verified? |
|---------------------|-------------------|-----------|
| inv_date            | invoice_date      | UNVERIFIED |
| invoice_dt          | invoice_date      | UNVERIFIED |
| invoice_total       | invoice_amount    | UNVERIFIED |
| inv_amount          | invoice_amount    | UNVERIFIED |
| gross_amount        | invoice_amount    | UNVERIFIED |
| quantity            | qty               | UNVERIFIED |
| units               | qty               | UNVERIFIED |
| price               | unit_price        | UNVERIFIED |
| unit_cost           | unit_price        | UNVERIFIED |
| chk_date            | check_date        | UNVERIFIED |
| payment_date        | check_date        | PARTIAL CORROBORATION -- "Payment Date" seen in Muncie AP export |
| invoice_status      | status            | UNVERIFIED |

**Status vocabulary assumed in fixture:** Paid, Open (no code confirms these are
the complete set; real Munis may include Void, Hold, Partial, Approved, etc.)

---

### 2.3 Vendor List (`vendor_list`)

**Synthetic fixture headers (actual CSV):**
`Vendor Number, Vendor Name, DBA, Status, Address, City, State, Zip, Phone, 1099 Flag, Terms`

| Fixture Header | Snake-Cased   | Canonical Target | Alias Source             | Required? | Verified? |
|----------------|---------------|------------------|--------------------------|-----------|-----------|
| Vendor Number  | vendor_number | vendor_number    | exact match (snake-case) | YES       | PARTIAL CORROBORATION |
| Vendor Name    | vendor_name   | vendor_name      | exact match (snake-case) | YES       | PARTIAL CORROBORATION |
| DBA            | dba           | dba              | exact match              | NO (opt)  | UNVERIFIED |
| Status         | status        | status           | exact match              | YES       | UNVERIFIED as exact header spelling |
| Address        | address       | address          | exact match              | NO (opt)  | UNVERIFIED |
| City           | city          | city             | exact match              | NO (opt)  | UNVERIFIED |
| State          | state         | state            | exact match              | NO (opt)  | UNVERIFIED |
| Zip            | zip           | zip              | exact match              | NO (opt)  | UNVERIFIED |
| Phone          | phone         | phone            | exact match              | NO (opt)  | UNVERIFIED |
| 1099 Flag      | 1099_flag     | flag_1099        | alias: 1099_flag         | NO (opt)  | UNVERIFIED as exact Munis header; "1099 Flag" is a plausible Munis label |
| Terms          | terms         | terms            | exact match              | NO (opt)  | UNVERIFIED |

**Additional aliases registered in code (not used by fixture):**

| Alias (snake-cased)  | Canonical Target | Verified? |
|----------------------|------------------|-----------|
| 1099                 | flag_1099        | UNVERIFIED |
| ten99_flag           | flag_1099        | UNVERIFIED |
| vendor_status        | status           | UNVERIFIED |
| doing_business_as    | dba              | UNVERIFIED |
| addr                 | address          | UNVERIFIED |
| address_1            | address          | UNVERIFIED |
| zip_code             | zip              | UNVERIFIED |
| postal_code          | zip              | UNVERIFIED |
| telephone            | phone            | UNVERIFIED |
| phone_number         | phone            | UNVERIFIED |
| payment_terms        | terms            | UNVERIFIED |

**Status vocabulary assumed:** Active, Inactive. Real Munis may use additional values
(e.g. Suspended, Deleted, Hold).

---

### 2.4 Check Register (`check_register`)

**Synthetic fixture headers (actual CSV):**
`Check Number, Check Date, Vendor Number, Vendor Name, Check Amount, Status, Type, Clear Date, Invoice Number(s)`

| Fixture Header    | Snake-Cased        | Canonical Target | Alias Source                   | Required? | Verified? |
|-------------------|--------------------|------------------|--------------------------------|-----------|-----------|
| Check Number      | check_number       | check_number     | exact match (snake-case)       | YES       | UNVERIFIED as exact Munis export header |
| Check Date        | check_date         | check_date       | exact match (snake-case)       | YES       | UNVERIFIED as exact header |
| Vendor Number     | vendor_number      | vendor_number    | exact match (snake-case)       | YES       | PARTIAL CORROBORATION |
| Vendor Name       | vendor_name        | vendor_name      | exact match (snake-case)       | YES       | PARTIAL CORROBORATION |
| Check Amount      | check_amount       | check_amount     | exact match (snake-case)       | YES       | UNVERIFIED -- some jurisdictions use "Warrant Amount" |
| Status            | status             | status           | exact match                    | YES       | UNVERIFIED |
| Type              | type               | type             | exact match                    | NO (opt)  | UNVERIFIED |
| Clear Date        | clear_date         | clear_date       | exact match (snake-case)       | NO (opt)  | UNVERIFIED |
| Invoice Number(s) | invoice_number_s   | invoice_numbers  | alias: invoice_number_s        | NO (opt)  | UNVERIFIED -- parenthetical plural "(s)" is a synthetic convention; real header may differ |

Note: The alias `invoice_number_s` is produced by snake-casing "Invoice Number(s)".
This is a fragile alias: if Munis spells the header slightly differently (e.g.
"Invoice Numbers", "Invoices", or "Invoice No(s)") the alias will not match.
The code registers fallback aliases `invoices` and `invoice_list` but not
`invoice_nos` or `invoice_no_s`.

**Status vocabulary assumed:** Cleared, Void, Outstanding, EFT. The "Type" field
in the fixture uses "Printed" and "EFT". Real Munis check type and status
vocabularies may differ.

**Warrant terminology:** Some states (e.g. Idaho, Illinois) use "Warrant" instead
of "Check". The code registers `warrant_no -> check_number` and
`warrant_amount -> check_amount` as aliases, but the check register's status
vocabulary does not include warrant-specific states. UNVERIFIED whether
warrant-based jurisdictions export a "Check Register" or a "Warrant Register".

---

### 2.5 Purchase Orders (`purchase_orders`)

**Synthetic fixture headers (actual CSV):**
`PO Number, PO Date, Vendor Number, Vendor Name, Status, Line, Description, Qty, Unit Price, Line Amount, Received Qty, Invoiced Qty, Last Activity Date, Fund, Org, Object`

| Fixture Header    | Snake-Cased         | Canonical Target    | Alias Source                | Required? | Verified? |
|-------------------|---------------------|---------------------|-----------------------------|-----------|-----------|
| PO Number         | po_number           | po_number           | exact match (snake-case)    | YES       | UNVERIFIED |
| PO Date           | po_date             | po_date             | exact match (snake-case)    | YES       | UNVERIFIED |
| Vendor Number     | vendor_number       | vendor_number       | exact match (snake-case)    | YES       | PARTIAL CORROBORATION |
| Vendor Name       | vendor_name         | vendor_name         | exact match (snake-case)    | YES       | PARTIAL CORROBORATION |
| Status            | status              | status              | exact match                 | YES       | UNVERIFIED |
| Line              | line                | line                | exact match                 | YES       | UNVERIFIED |
| Description       | description         | description         | exact match                 | NO (opt)  | UNVERIFIED |
| Qty               | qty                 | qty                 | exact match                 | YES       | UNVERIFIED |
| Unit Price        | unit_price          | unit_price          | exact match (snake-case)    | YES       | UNVERIFIED |
| Line Amount       | line_amount         | line_amount         | exact match (snake-case)    | YES       | UNVERIFIED |
| Received Qty      | received_qty        | received_qty        | exact match (snake-case)    | NO (opt)  | UNVERIFIED |
| Invoiced Qty      | invoiced_qty        | invoiced_qty        | exact match (snake-case)    | NO (opt)  | UNVERIFIED |
| Last Activity Date| last_activity_date  | last_activity_date  | exact match (snake-case)    | NO (opt)  | UNVERIFIED |
| Fund              | fund                | fund                | exact match                 | NO (opt)  | PARTIAL CORROBORATION |
| Org               | org                 | org                 | exact match                 | NO (opt)  | PARTIAL CORROBORATION |
| Object            | object              | object              | exact match                 | NO (opt)  | PARTIAL CORROBORATION |

**Status vocabulary assumed:** Open, Closed. Real Munis may include Approved,
Cancelled, On Hold, Encumbered, Liquidated, etc.

---

### 2.6 Budget to Actual (`budget_to_actual`)

**Synthetic fixture headers (actual CSV):**
`Fund, Org, Object, Account Description, Original Budget, Transfers Adjustments, Revised Budget, YTD Actual, Encumbrances, Available Budget, Percent Used`

| Fixture Header        | Snake-Cased              | Canonical Target         | Alias Source                         | Required? | Verified? |
|-----------------------|--------------------------|--------------------------|--------------------------------------|-----------|-----------|
| Fund                  | fund                     | fund                     | exact match                          | YES       | PARTIAL CORROBORATION |
| Org                   | org                      | org                      | exact match                          | YES       | PARTIAL CORROBORATION |
| Object                | object                   | object                   | exact match                          | YES       | PARTIAL CORROBORATION |
| Account Description   | account_description      | account_description      | exact match (snake-case)             | NO (opt)  | PARTIAL CORROBORATION |
| Original Budget       | original_budget          | original_budget          | exact match (snake-case)             | YES       | UNVERIFIED -- real Munis may use "Adopted Budget" or "Appropriation" |
| Transfers Adjustments | transfers_adjustments    | transfers_adjustments    | exact match (snake-case)             | NO (opt)  | UNVERIFIED |
| Revised Budget        | revised_budget           | revised_budget           | exact match (snake-case)             | YES       | UNVERIFIED -- real Munis may call this "Amended Budget" |
| YTD Actual            | ytd_actual               | ytd_actual               | exact match (snake-case)             | YES       | UNVERIFIED -- real Munis may use "YTD Expended" or "Actuals YTD" |
| Encumbrances          | encumbrances             | encumbrances             | exact match                          | NO (opt)  | UNVERIFIED |
| Available Budget      | available_budget         | available_budget         | exact match (snake-case)             | NO (opt)  | UNVERIFIED |
| Percent Used          | percent_used             | percent_used             | exact match (snake-case)             | NO (opt)  | UNVERIFIED |

**Budget basis decision:** The code uses `revised_budget` in preference to
`original_budget` when both are present. Whether a given city prefers to run
variance analysis against original or revised budget is a city configuration
question. UNVERIFIED.

---

### 2.7 Chart of Accounts (`chart_of_accounts`)

**Synthetic fixture headers (actual CSV):**
`Account, Fund, Org, Object, Project, Account Description, Type, Status, Normal Balance`

| Fixture Header     | Snake-Cased         | Canonical Target    | Alias Source                    | Required? | Verified? |
|--------------------|---------------------|---------------------|---------------------------------|-----------|-----------|
| Account            | account             | account             | exact match                     | YES       | UNVERIFIED as exact Munis header; often "Account Number" or "Account Code" |
| Fund               | fund                | fund                | exact match                     | YES       | PARTIAL CORROBORATION |
| Org                | org                 | org                 | exact match                     | YES       | PARTIAL CORROBORATION |
| Object             | object              | object              | exact match                     | YES       | PARTIAL CORROBORATION |
| Project            | project             | project             | exact match                     | NO (opt)  | UNVERIFIED |
| Account Description| account_description | account_description | exact match (snake-case)        | YES       | PARTIAL CORROBORATION |
| Type               | type                | type                | exact match                     | NO (opt)  | UNVERIFIED |
| Status             | status              | status              | exact match                     | YES       | UNVERIFIED |
| Normal Balance     | normal_balance      | normal_balance      | exact match (snake-case)        | NO (opt)  | UNVERIFIED -- Munis may omit this column or call it "Dr/Cr" or "Balance Type" |

**Account field format assumed in fixture:** Compound key `<Fund>-<Org>-<Object>`
(e.g. `100-5100-5010`). The separate Fund/Org/Object columns repeat those
components. Whether real Munis COA exports use a compound key column is UNVERIFIED.

**Normal Balance vocabulary assumed:** Debit, Credit. Real Munis may use "D"/"C"
or "Dr"/"Cr" or similar abbreviations.

**Status vocabulary assumed:** Active, Inactive. Real Munis may include additional
states.

---

### 2.8 JE Upload Template (`je_upload`)

**Synthetic fixture headers (actual CSV/XLSX):**
`Journal, Line, Eff Date, Fund, Org, Object, Account Description, Debit, Credit, Line Description, Reference`

This is the most operationally critical dataset type because it produces a file
a human takes into Munis to execute a real journal entry import. Column order
and exact spelling are particularly important here.

| Fixture Header     | Snake-Cased         | Canonical Target    | Alias Source             | Required? | Verified? |
|--------------------|---------------------|---------------------|--------------------------|-----------|-----------|
| Journal            | journal             | journal             | exact match              | YES       | PARTIAL CORROBORATION -- Munis JE docs reference a Journal number |
| Line               | line                | line                | exact match              | YES       | PARTIAL CORROBORATION -- Munis v10.5 JE guide shows a Line field |
| Eff Date           | eff_date            | date                | alias: eff_date          | YES       | PARTIAL CORROBORATION -- "Effective Date" is the Munis v10.5 JE date field; "Eff Date" as header abbreviation appears in GL column listing snippet but is not explicitly confirmed for the JE import template header row |
| Fund               | fund                | fund                | exact match              | YES       | PARTIAL CORROBORATION |
| Org                | org                 | org                 | exact match              | YES       | PARTIAL CORROBORATION |
| Object             | object              | object              | exact match              | YES       | PARTIAL CORROBORATION |
| Account Description| account_description | account_description | exact match (snake-case) | NO (opt)  | PARTIAL CORROBORATION |
| Debit              | debit               | debit               | exact match              | YES       | PARTIAL CORROBORATION -- Munis v10.5 JE guide confirms separate Debit/Credit (not signed amount) |
| Credit             | credit              | credit              | exact match              | YES       | PARTIAL CORROBORATION -- as above |
| Line Description   | line_description    | line_description    | exact match (snake-case) | NO (opt)  | PARTIAL CORROBORATION -- Munis v10.5 JE guide names "Line Description" up to 30 chars |
| Reference          | reference           | reference           | exact match              | NO (opt)  | PARTIAL CORROBORATION -- Munis v10.5 JE guide names "Ref1" up to 6 chars; "Reference" as the export header spelling is UNVERIFIED |

**Assumed JE upload template column order (left to right):**
Journal, Line, Eff Date, Fund, Org, Object, Account Description, Debit, Credit, Line Description, Reference

This order matches the synthetic template but has NOT been confirmed against a
real city's Munis JE import specification. The Munis v10.5 guide implies Fund,
Org, Object are the core account dimensions; the column-order assumption for the
import template is UNVERIFIED.

**Critical JE import behavior noted in public docs (Munis v10.5 JE guide):**
- Amounts must be positive; D/C direction indicated by the Debit vs Credit column
  (not a sign on the amount). The code's derivation `amount = debit - credit` is
  INTERNAL only (for analysis); the upload template uses separate positive columns,
  which is consistent.
- The guide says "no header rows are allowed" when importing (must delete header
  before processing). The synthetic template file ships as headers-only (no data
  rows), so the user's workflow is: populate rows, delete the header row, import.
  This is documented behavior (PARTIAL CORROBORATION) but the exact Munis import
  UI steps depend on the version the city runs.

**Additional aliases registered in code (not used by fixture):**

| Alias (snake-cased) | Canonical Target    | Verified? |
|---------------------|---------------------|-----------|
| effective_date      | date                | PARTIAL CORROBORATION -- "Effective Date" is the Munis label |
| je_date             | date                | UNVERIFIED |
| line_no             | line                | UNVERIFIED |
| line_number         | line                | UNVERIFIED |
| jnl                 | journal             | UNVERIFIED |
| journal_number      | journal             | UNVERIFIED |
| account_desc        | account_description | UNVERIFIED |
| desc                | line_description    | UNVERIFIED |
| ref                 | reference           | UNVERIFIED |

---

## 3. Cross-Cutting Assumptions

### 3.1 Date Format

The normalizer calls `src.normalize.cleaning.parse_date` on all date columns.
The fixture dates use `YYYY-MM-DD` ISO format throughout.

**Assumed:** Munis exports dates in ISO YYYY-MM-DD or MM/DD/YYYY format
(both are handled by `parse_date`). The Munis v10.5 JE guide shows dates
entered via a calendar picker but does not specify the exported CSV date format.

**UNVERIFIED:** Whether real Munis CSV exports use ISO 8601 or US locale
MM/DD/YYYY, and whether locale varies by city configuration.

### 3.2 Amount/Number Format

The normalizer calls `src.normalize.cleaning.parse_amount` which handles:
- Dollar signs (`$1,250.00`)
- Thousands commas (`1,250.00`)
- Parentheses negatives (`(1,250.00)`)
- Plain decimals (`1250.00`)

**UNVERIFIED:** Whether real Munis exports include dollar signs, whether they use
parentheses for negative amounts, and whether locale separators differ from
US conventions (period as decimal, comma as thousands).

### 3.3 Sign Convention

`amount = debit - credit` (Decimal; blank side = 0; both blank = None).

The Munis v10.5 JE guide confirms amounts are entered as positive values with
a D/C indicator column. This is compatible with deriving a signed amount as
`debit - credit`, but whether Munis GL detail exports do or do not include a
separate signed amount column is UNVERIFIED.

**Critical for bank reconciliation:** The bank reconciliation workflow assumes
this sign convention. A city must confirm it before using the workflow on real
GL exports.

### 3.4 Dataset-Type Auto-Detection Scoring

The normalizer scores candidate dataset types using:

    score = 0.8 * (required_columns_matched / required_columns_total)
          + 0.2 * (optional_columns_matched / optional_columns_total)

Detection is accepted only when:
- best score >= 0.70 (MIN_DETECTION_SCORE)
- margin above second-best >= 0.05 (MIN_DETECTION_MARGIN)

Otherwise detection fails closed and the caller must pass `dataset_type`
explicitly.

This scoring is entirely deterministic and internal; it is not a Munis
specification. The thresholds are calibrated against the synthetic dataset only
and have not been tested against real export variation (extra columns, renamed
columns, version differences across Munis releases, city-specific customizations).

### 3.5 Excel Title-Block Detection

For XLSX files, the normalizer scans the first 10 rows (MAX_HEADER_SCAN_ROWS)
for a candidate header row by scoring each row as if it were the header. The
synthetic `gl_detail.xlsx` has a 3-row title block (city name, report title,
as-of date) before the real header on row 4 (0-based row 3).

**UNVERIFIED:** Whether real Munis XLSX exports consistently place the header
at row 4, or whether the title block structure (number of rows, content) varies
by report type, Munis version, or city customization.

### 3.6 Munis "Src" / Source Code Vocabulary

The fixture uses: `API` (AP-sourced), `GEN` (general journal), `POE`
(purchase order encumbrance), `CRP` (cash receipts/payments).

**UNVERIFIED:** Whether these source codes are standard across Munis installations
or whether they are city-configurable abbreviations.

### 3.7 Legacy XLS Support

The normalizer supports `.csv` and `.xlsx` only. Real Munis exports saved as
old-format `.xls` (BIFF8) must be re-saved as `.xlsx` before use. No `xlrd`
dependency is present in the codebase.

### 3.8 Header Row Requirement for JE Import

The Munis v10.5 Import Journals procedure (PARTIAL CORROBORATION via Horry
County SC document) indicates the import file should have no header row when
processing. The synthetic template ships headers-only; the intended workflow
is that a user populates data rows and removes the header before import.
Real Munis may also accept an Excel template via the "Standard Excel" format
option, which may generate a template with its own column structure.

---

## 4. TYLER_MUNIS_STYLE Preset vs TYLER_DATASET_TYPES (GW-28 status: DEFERRED)

`src/ingest/presets.py` defines a `TYLER_MUNIS_STYLE` preset (combined with
`GENERIC_ERP` as the `tyler_munis_style` entry in `PRESETS`). This preset is
a GENERIC alias layer used by the three original workflows (bank reconciliation,
budget variance, report review) before the Tyler-specific normalizer existed.

Key aliases in `TYLER_MUNIS_STYLE` not in `TYLER_DATASET_TYPES`:

| Alias (snake-cased) | Canonical Target | Notes |
|---------------------|-----------------|-------|
| jrnl_date           | date            | Duplicate of gl_detail alias |
| je_date             | date            | Duplicate of je_upload alias |
| jnl_date            | date            | Variant |
| gl_amount           | amount          | Not in any TYLER_DATASET_TYPES spec |
| journal_amount      | amount          | Not in any TYLER_DATASET_TYPES spec |
| je_amount           | amount          | Not in any TYLER_DATASET_TYPES spec |
| je_description      | description     | Not in any TYLER_DATASET_TYPES spec |
| comment             | description     | Generic |
| org_code            | department      | Maps "org" concept to "department" canonical -- differs from tyler.py which keeps "org" as the canonical |
| obj / object        | account_code    | Maps Munis object to "account_code" canonical -- differs from tyler.py which uses "object" as canonical |
| check_date          | date            | For check-register-as-generic-transaction use |
| check_amount        | amount          | For check-register-as-generic-transaction use |
| invoice            | invoice_number  | Not in any TYLER_DATASET_TYPES spec |
| po                  | po_number       | Not in any TYLER_DATASET_TYPES spec |

**GW-28 DEFERRED** -- Resolving the org/object canonical conflict requires renaming
all TYLER_DATASET_TYPES specs, synthetic fixtures, and downstream workflow column
references (a multi-file rename with no runtime benefit for the current synthetic-only
dataset). A `# ponytail: GW-28` comment in presets.py documents the intended fix path.

**Intended single canonical per dimension (for a future fix pass):**

| Munis dimension | Intended canonical  | Notes |
|----------------|---------------------|-------|
| Org (organization) | `org`           | Matches Munis terminology; remove preset's `org -> department` remapping |
| Object (expenditure object) | `object` | Matches Munis terminology; remove preset's `object -> account_code` remapping |

Observations:
1. `TYLER_MUNIS_STYLE` and `TYLER_DATASET_TYPES` use DIFFERENT canonical names
   for the same Munis "Org" dimension: the preset maps `org` -> `department`,
   while the tyler.py normalizer keeps `org` as the canonical column name. These
   are two separate pipeline paths (preset path for original workflows; tyler.py
   path for the four new Tyler workflows) so the inconsistency does not cause a
   runtime conflict, but it is a conceptual inconsistency that could confuse a
   developer trying to extend both.
2. `TYLER_MUNIS_STYLE` similarly maps `object` -> `account_code`, while
   `TYLER_DATASET_TYPES` keeps `object` as canonical. Same dual-path situation.
3. The `invoice` alias (bare "invoice" -> `invoice_number`) in `TYLER_MUNIS_STYLE`
   had no fixture coverage and no `TYLER_DATASET_TYPES` counterpart. **Removed by GW-30.**

---

## 5. City/Vendor Verification Checklist

The following questions must be confirmed with the city's Tyler Technologies
contact (or via a real Munis export file) before using this tool on live data.

### 5.1 GL Detail Export

- [ ] What are the exact column headers in your Munis GL Detail export (CSV or
      XLSX)? Specifically: is the date column labeled "Eff Date", "Effective Date",
      "GL Date", "Posting Date", or something else?
- [ ] Is there a "Description/Vendor" combined column, or separate "Description"
      and "Vendor" columns?
- [ ] What values appear in the "Src" (Source) column? Are they city-configurable
      or standard across Munis (e.g. API, GEN, POE, CRP)?
- [ ] Does the GL detail export include separate Debit and Credit columns, or a
      single signed Amount column?
- [ ] What date format does your Munis export use in CSV files (YYYY-MM-DD,
      MM/DD/YYYY, or other)?
- [ ] For XLSX exports: how many rows constitute the title block before the header
      row? Is it always 3 rows, or does it vary by report?

### 5.2 AP Invoice Detail Export

- [ ] What are the exact column headers in your AP invoice export? Specifically:
      is the amount column "Invoice Amount", "Invoice Net Amount", "Gross Amount",
      or something else?
- [ ] What Status values are possible (e.g. Paid, Open, Void, Hold, Partial)?
- [ ] Is "Invoice Amount" the total gross amount or the net (after discounts)?
- [ ] Are Qty and Unit Price columns always present, or only for PO-matched invoices?

### 5.3 Vendor List Export

- [ ] What are the exact column headers in your vendor master export?
- [ ] What Status values are possible (Active, Inactive, Suspended, Hold, Deleted)?
- [ ] Is the 1099 flag labeled "1099 Flag", "1099", or something else? What are
      the possible values (Y/N, Yes/No, True/False, 1/0)?
- [ ] Does the export include a DBA column?

### 5.4 Check Register Export

- [ ] What are the exact column headers in your check register export?
- [ ] Is it a "Check Register" or a "Warrant Register" (some states use "Warrant")?
- [ ] How are multiple invoices on a batch check represented in the export? Is it
      a semicolon-delimited list in a single column (e.g. "Invoice Number(s)"), or
      one row per invoice?
- [ ] What are the possible Check Status values (Cleared, Void, Outstanding, etc.)?
- [ ] What are the possible Check Type values (Printed, EFT, Wire, etc.)?

### 5.5 Purchase Orders Export

- [ ] What are the exact column headers in your PO export?
- [ ] Does the export produce one row per PO line, or one row per PO header?
- [ ] What are the possible PO Status values (Open, Closed, Cancelled, Approved)?
- [ ] Are Received Qty and Invoiced Qty always present, or only when receiving has
      been recorded?

### 5.6 Budget to Actual Export

- [ ] What are the exact column headers in your budget-to-actual export?
- [ ] Is the budget column labeled "Original Budget", "Adopted Budget",
      "Appropriation", or something else?
- [ ] Is the amended/revised budget column labeled "Revised Budget", "Amended
      Budget", or something else?
- [ ] Is the actuals column labeled "YTD Actual", "YTD Expended", "Actuals YTD",
      or something else?
- [ ] Do you want the variance workflow to compare against original or revised
      budget by default?
- [ ] Does the export include an Encumbrances column? Is "Available Budget"
      computed as Revised Budget minus YTD Actual minus Encumbrances?

### 5.7 Chart of Accounts Export

- [ ] What are the exact column headers in your COA export?
- [ ] Is the account identifier a compound key (e.g. "100-5100-5010") in a single
      column, or separate Fund/Org/Object columns only?
- [ ] What values appear in the "Normal Balance" column (Debit/Credit, D/C,
      Dr/Cr, or absent)?
- [ ] What Status values are possible (Active, Inactive, Deleted)?

### 5.8 JE Upload Template

- [ ] What is the EXACT column header row for your city's Munis JE import
      template? (This is the most operationally critical question -- a wrong header
      causes the import to reject all lines.)
- [ ] What is the expected column order for the JE import file?
- [ ] Does your Munis installation expect separate Debit and Credit amount columns
      (both positive), or a single signed Amount column with a D/C indicator?
- [ ] Is the date column labeled "Eff Date", "Effective Date", or something else?
- [ ] What is the maximum length of the Line Description field?
- [ ] What is the maximum length of the Reference/Ref1 field?
- [ ] Does the import file require a header row, or must it be removed before
      import?
- [ ] Which Munis import format does your city use -- the "Standard Excel" template
      generated by Munis, the "Budget" ASCII fixed-length format, or a
      "Standard Import Format" for exported Munis JE ASCII files?

### 5.9 Sign Convention and Amount Formats

- [ ] For the GL detail export: when a transaction is a pure debit (e.g. an
      expense posting), is the Debit column positive and Credit blank/zero, or does
      the export use a single signed Amount column?
- [ ] For GL cash accounts (fund balance, receivables): confirm that
      `amount = debit - credit` produces the correct signed amount for your GL
      postings (positive = net debit = expenditure direction).
- [ ] Do any CSV exports include dollar signs or thousands-separator commas in
      numeric columns, or are they always plain decimal numbers?
- [ ] Are negative amounts represented with a leading minus sign, or with
      parentheses?

### 5.10 General / Version

- [ ] What version of Munis (Tyler Enterprise ERP) is your city running? Column
      names and import template structures may differ between major versions.
- [ ] Are any of the column names above city-configured (i.e. your Munis admin
      renamed a standard column)? If so, what are the custom names?

---

## 6. Sources Consulted and Corroboration Notes

The following public sources were searched and/or fetched for this register.
PDFs marked "binary-encoded" could not be read as text by the fetch tool and
yielded no direct field-name confirmation.

1. **Munis v10.5 General Journal Entry guide** (via docslib.org, document
   ID 9186287). This was the most readable source. Confirmed: "Effective Date"
   as the JE date field; separate Debit/Credit columns; "Line Description" up
   to 30 chars; "Ref1" up to 6 chars; Line auto-assigned; Journal auto-assigned;
   Source = "GEN" for general entries; Amount must be positive.
   URL: https://docslib.org/doc/9186287/general-journal-entry-munis-version-10-5

2. **Horry County SC Munis Import Journals 2018** (PDF).
   Binary-encoded; not readable by fetch tool. Search snippet confirmed the
   document exists and covers Import Journals procedures with Standard Excel and
   Budget ASCII formats. No field names extractable.
   URL: https://www.horrycountysc.gov/media/4rhafcbj/munis-import-journals-2018.pdf

3. **Framingham MA General Ledger user guide** (PDF).
   Binary-encoded; not readable by fetch tool.
   URL: https://www.framinghamma.gov/DocumentCenter/View/44403/General-Ledger

4. **Franklin County OH Financial Reporting Training Manual 2016** (search
   snippet only). Snippet mentioned column names: LN, ORG, OBJECT, PROJ, REF1,
   REF2, REF3, LINE, DESCRIPTION, DEBIT, CREDIT, YEAR, PER, JOURNAL, SRC,
   EFF DATE, ENT DATE, JNL DESC, CLERK. This is the strongest corroboration
   for "Eff Date", "Src", "Org", "Object", "Year", "Per", "Journal" as Munis
   GL column labels, though it may refer to an on-screen report display, not
   an exported CSV header row.
   URL: https://www.franklincountyauditor.com/AUDR-website/media/Documents/Fiscally%20Speaking/MUNIS/Training%20Handouts/2016-Financial-Reporting-Training-Manual.pdf?ext=.pdf

5. **Muncie IN AP report examples** (search snippet only). Snippet mentioned
   columns: Invoice Number, Invoice Description, Status, Invoice Date, Due Date,
   G/L Date, Received Date, Payment Date, Invoice Net Amount. Suggests "Invoice
   Net Amount" rather than "Invoice Amount" as the amount column name; "Payment
   Date" rather than "Check Date".
   URL: https://www.muncie.in.gov/egov/documents/1610548945_42878.pdf

6. **Fairfield County OH Enterprise ERP GL Procedures 2021.6** (PDF).
   URL returned a 404; document not retrieved.
   URL: https://www.co.fairfield.oh.us/auditor/pdf/Enterprise-ERP-General-Ledger-Procedures-2021.6.pdf

7. **yumpu.com Munis Import Journals page** (page 1 of 3 only; remaining pages
   not accessible). Mentioned "Standard Long Account Format" and
   "Standard Import Format" as import source options. No complete field list.
   URL: https://www.yumpu.com/en/document/view/37227145/import-journals-objective-procedure-field-descriptions

8. **docslib.org Munis General Journal Entry v10.5** -- primary useful source,
   see item 1 above.

**What could NOT be corroborated from public sources:**
- Exact CSV export column header spellings for GL Detail, AP Invoice, Vendor
  List, Check Register, Purchase Orders, or Budget to Actual.
- The exact JE import template column order and header row requirement for the
  CSV/XLSX path.
- Date format in exported CSV files.
- Amount format (plain decimal vs currency-formatted) in exported CSV files.
- Source code vocabulary (API, GEN, POE, CRP) as standard vs city-configurable.
- The "Description/Vendor" combined column as a real Munis header.
- Invoice Number(s) as the check register multi-invoice column header.
- Normal Balance column presence and vocabulary in COA exports.

---

## 7. Internal Inconsistencies Noted (Not Fixed -- Files Are Frozen)

The following inconsistencies were observed between the code and fixtures.
Per the FROZEN-path constraint these files are not modified; they are recorded
here as discovered_followups for a future cleanup pass.

1. **Org-dimension canonical name conflict between the two pipeline paths.**
   `TYLER_MUNIS_STYLE` preset maps `org` -> `department` (canonical = "department"),
   while `TYLER_DATASET_TYPES` keeps `org` as the canonical column name. A developer
   feeding a Tyler file through the preset path (original workflows) gets a column
   named `department`; feeding it through `normalize_tyler_export` (Tyler workflows)
   gets a column named `org`. These two paths are not interchangeable.

2. **Object-dimension canonical name conflict.**
   Same issue: `TYLER_MUNIS_STYLE` maps `object` -> `account_code`; `TYLER_DATASET_TYPES`
   keeps `object` as canonical. A developer mixing the two paths will encounter
   different canonical names for the same Munis dimension.

3. **`invoice` alias in `TYLER_MUNIS_STYLE` has no fixture coverage.**
   `TYLER_MUNIS_STYLE` includes `"invoice": "invoice_number"` but no synthetic
   fixture has a bare "Invoice" header (fixtures use "Invoice Number" or
   "Invoice No"). This alias is untested against the synthetic dataset.

4. **`invoice_number_s` alias fragility.**
   The alias `invoice_number_s -> invoice_numbers` (for the check register column
   "Invoice Number(s)") depends on `to_snake_case` converting the parenthetical
   "(s)" to "_s". If the real Munis column is spelled differently (e.g.
   "Invoice Numbers", "Invoices", "Invoice No(s)") this alias will silently fail
   to resolve and the `invoice_numbers` optional column will simply be absent.
   **PARTIALLY FIXED by GW-29**: `normalize_tyler_export` now appends a warning
   to `TylerNormalizedExport.warnings` when an optional column that has registered
   aliases is absent after normalization (so callers/logs surface the gap). The
   alias fragility itself remains; see the checklist in Section 5.4.

5. **`TYLER_MUNIS_STYLE` registered `gl_amount`, `journal_amount`, `je_amount`
   aliases with no corresponding canonical in `TYLER_DATASET_TYPES`.**
   **FIXED by GW-30**: all three were deleted from `TYLER_MUNIS_STYLE`. They had
   no fixture coverage and no counterpart in `TYLER_DATASET_TYPES`. The bare
   `invoice -> invoice_number` alias was also deleted for the same reason.

6. **Budget basis preference is hardcoded, not city-configurable via the normalizer.**
   The normalizer derives `amount = debit - credit` unconditionally. The budget
   workflow's preference for `revised_budget` over `original_budget` is implemented
   in the workflow, not surfaced as a normalizer parameter. A city that always wants
   original budget as the basis must pass an explicit column mapping.
