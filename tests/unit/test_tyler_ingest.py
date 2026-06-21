"""Tests for the Tyler/Munis-style export normalizer (src/ingest/tyler.py)
and the synthetic City of Riverbend dataset (data/synthetic/tyler/)."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.core.schemas import FindingType, InputFile, SourceRowRef
from src.ingest import presets, tyler

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "tyler"

CSV_FILES = {
    "gl_detail.csv": "gl_detail",
    "ap_invoice_detail.csv": "ap_invoice_detail",
    "vendor_list.csv": "vendor_list",
    "check_register.csv": "check_register",
    "purchase_orders.csv": "purchase_orders",
    "budget_to_actual.csv": "budget_to_actual",
    "chart_of_accounts.csv": "chart_of_accounts",
    "je_upload_template.csv": "je_upload",
}
XLSX_FILES = {
    "gl_detail.xlsx": "gl_detail",
    "budget_to_actual.xlsx": "budget_to_actual",
    "je_upload_template.xlsx": "je_upload",
}


def _known() -> dict:
    return json.loads((DATA / "known_answers.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Dataset-type detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("file_name,expected", sorted(CSV_FILES.items()))
def test_detect_dataset_type_csv(file_name, expected):
    detected, scores = tyler.detect_dataset_type(DATA / file_name)
    assert detected == expected
    # Score breakdown is exposed for every type (auditable detection).
    assert set(scores) == set(tyler.TYLER_DATASET_TYPES)
    assert scores[expected]["score"] >= tyler.MIN_DETECTION_SCORE
    assert scores[expected]["required_matched"] == scores[expected]["required_total"]


@pytest.mark.parametrize("file_name,expected", sorted(XLSX_FILES.items()))
def test_detect_dataset_type_xlsx(file_name, expected):
    detected, _scores = tyler.detect_dataset_type(DATA / file_name)
    assert detected == expected


def test_detect_dataset_type_accepts_dataframe():
    df = pd.read_csv(DATA / "vendor_list.csv", dtype=str, keep_default_na=False)
    detected, _ = tyler.detect_dataset_type(df)
    assert detected == "vendor_list"


def test_detect_dataset_type_low_confidence_returns_none():
    df = pd.DataFrame({"alpha": ["1"], "beta": ["2"], "gamma": ["3"]})
    detected, scores = tyler.detect_dataset_type(df)
    assert detected is None
    assert all(s["score"] < tyler.MIN_DETECTION_SCORE for s in scores.values())


# --------------------------------------------------------------------------- #
# Normalization of every dataset file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "file_name,dtype", sorted({**CSV_FILES, **XLSX_FILES}.items())
)
def test_normalize_every_file(file_name, dtype):
    export = tyler.normalize_tyler_export(DATA / file_name)
    spec = tyler.TYLER_DATASET_TYPES[dtype]
    df = export.dataframe

    assert export.dataset_type == dtype
    assert export.module == spec.module
    # Canonical required columns present, plus the traceability column.
    for col in spec.required_columns:
        assert col in df.columns, (file_name, col)
    assert list(df.columns)[0] == "source_row_index"
    assert df["source_row_index"].tolist() == list(range(len(df)))

    # InputFile fully populated with the sha256 of the RAW file.
    inf = export.input_file
    assert isinstance(inf, InputFile)
    assert inf.file_hash == tyler.file_sha256(DATA / file_name)
    assert len(inf.file_hash) == 64
    assert inf.row_count == len(df)
    assert inf.file_name == file_name
    assert inf.parser_used == f"tyler_normalizer:{dtype}"
    assert inf.column_names == [str(c) for c in df.columns]

    # Row counts match the known-answers manifest.
    assert len(df) == _known()["file_row_counts"][file_name]


def test_normalized_dates_and_amounts_are_typed():
    export = tyler.normalize_tyler_export(DATA / "ap_invoice_detail.csv")
    df = export.dataframe
    assert all(isinstance(v, date) for v in df["invoice_date"])
    assert all(isinstance(v, Decimal) for v in df["invoice_amount"])
    # Blank unit prices stay None (never coerced).
    assert any(v is None for v in df["unit_price"])
    assert any(isinstance(v, Decimal) for v in df["unit_price"])


def test_applied_aliases_are_recorded():
    export = tyler.normalize_tyler_export(DATA / "gl_detail.csv")
    assert export.applied_aliases["eff_date"] == "date"
    assert export.applied_aliases["src"] == "source"
    assert export.applied_aliases["description_vendor"] == "description"
    assert export.applied_aliases["year"] == "fiscal_year"


# --------------------------------------------------------------------------- #
# Debit/Credit -> signed amount derivation
# --------------------------------------------------------------------------- #
def test_gl_signed_amount_derivation():
    export = tyler.normalize_tyler_export(DATA / "gl_detail.csv")
    df = export.dataframe
    # Originals kept; derived amount added.
    assert {"debit", "credit", "amount"} <= set(df.columns)
    for _, row in df.iterrows():
        debit = row["debit"] or Decimal("0")
        credit = row["credit"] or Decimal("0")
        assert row["amount"] == debit - credit
    # Credit-only rows are negative (cash receipts).
    crp = df[df["source"] == "CRP"]
    assert len(crp) > 0
    assert all(v < 0 for v in crp["amount"])


def test_signed_amount_handles_currency_strings(tmp_path):
    src = tmp_path / "je.csv"
    src.write_text(
        "Journal,Line,Eff Date,Fund,Org,Object,Account Description,"
        "Debit,Credit,Line Description,Reference\n"
        '1,1,2026-01-31,100,5100,5010,Office Supplies,"$1,234.50",,adj,REF-1\n'
        "1,2,2026-01-31,100,5200,5010,Office Supplies,,(234.50),adj,REF-1\n",
        encoding="utf-8",
    )
    export = tyler.normalize_tyler_export(src, "je_upload")
    df = export.dataframe
    assert df["amount"].tolist() == [Decimal("1234.50"), Decimal("234.50")]
    assert df["debit"].tolist()[0] == Decimal("1234.50")
    assert df["credit"].tolist()[1] == Decimal("-234.50")


# --------------------------------------------------------------------------- #
# XLSX title-block header detection
# --------------------------------------------------------------------------- #
def test_xlsx_title_block_header_detected():
    export = tyler.normalize_tyler_export(DATA / "gl_detail.xlsx")
    assert export.header_row_used == 3  # 3-row Munis-style title block
    csv_export = tyler.normalize_tyler_export(DATA / "gl_detail.csv")
    assert csv_export.header_row_used == 0
    # Same normalized content as the CSV variant.
    a = export.dataframe
    b = csv_export.dataframe
    assert list(a.columns) == list(b.columns)
    assert len(a) == len(b)
    assert a["journal"].tolist() == b["journal"].tolist()
    assert a["amount"].tolist() == b["amount"].tolist()
    assert a["date"].tolist() == b["date"].tolist()


def test_xlsx_headers_only_template():
    export = tyler.normalize_tyler_export(DATA / "je_upload_template.xlsx")
    assert export.dataset_type == "je_upload"
    assert len(export.dataframe) == 0
    for col in ("journal", "line", "date", "fund", "org", "object",
                "debit", "credit"):
        assert col in export.dataframe.columns


# --------------------------------------------------------------------------- #
# Fail-closed behavior
# --------------------------------------------------------------------------- #
def test_missing_required_column_fails_closed(tmp_path):
    df = pd.read_csv(DATA / "ap_invoice_detail.csv", dtype=str,
                     keep_default_na=False)
    crippled = df.drop(columns=["Invoice Amount"])
    src = tmp_path / "ap_missing.csv"
    crippled.to_csv(src, index=False)
    with pytest.raises(ValueError) as exc:
        tyler.normalize_tyler_export(src, "ap_invoice_detail")
    msg = str(exc.value)
    assert "fail closed" in msg
    assert "invoice_amount" in msg


def test_unknown_dataset_type_fails_closed():
    with pytest.raises(ValueError) as exc:
        tyler.normalize_tyler_export(DATA / "gl_detail.csv", "not_a_type")
    assert "unknown dataset_type" in str(exc.value)


def test_undetectable_file_fails_closed(tmp_path):
    src = tmp_path / "mystery.csv"
    src.write_text("alpha,beta\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        tyler.normalize_tyler_export(src)
    assert "could not confidently detect" in str(exc.value)


def test_unsupported_file_type_fails_closed(tmp_path):
    src = tmp_path / "data.txt"
    src.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        tyler.normalize_tyler_export(src, "gl_detail")
    assert "unsupported file type" in str(exc.value)


def test_optional_alias_missing_yields_warning(tmp_path):
    """GW-29: a file missing the invoice_numbers column (optional, alias-targeted)
    yields a warning instead of silently omitting it."""
    # Build a check_register CSV without any invoice_numbers alias column.
    src = tmp_path / "checks_no_invoices.csv"
    src.write_text(
        "Check Number,Check Date,Vendor Number,Vendor Name,Check Amount,Status\n"
        "CHK-1001,2026-01-15,V-1001,Acme Office Supply,1500.00,Cleared\n",
        encoding="utf-8",
    )
    export = tyler.normalize_tyler_export(src, "check_register")
    assert any("optional_column_missing" in w and "invoice_numbers" in w
               for w in export.warnings)
    assert "invoice_numbers" not in export.dataframe.columns


def test_footer_total_row_flagged_never_deleted(tmp_path):
    base = (DATA / "check_register.csv").read_text(encoding="utf-8")
    src = tmp_path / "checks_with_footer.csv"
    src.write_text(base + "TOTAL,,,,250000.00,,,,\n", encoding="utf-8")
    export = tyler.normalize_tyler_export(src, "check_register")
    n = _known()["file_row_counts"]["check_register.csv"]
    assert len(export.dataframe) == n + 1  # kept, not dropped
    assert any("footer_total_rows" in w for w in export.warnings)


# --------------------------------------------------------------------------- #
# Traceability helpers
# --------------------------------------------------------------------------- #
def test_source_ref_for_row():
    export = tyler.normalize_tyler_export(DATA / "ap_invoice_detail.csv")
    known = _known()
    row_index = known["ap_anomalies"]["D4"]["ap_row_indices"][0]
    ref = tyler.source_ref_for_row(
        export, row_index,
        columns=["vendor_number", "invoice_number", "invoice_amount",
                 "po_number"],
    )
    assert isinstance(ref, SourceRowRef)
    assert ref.file_id == export.input_file.file_id
    assert ref.table_name == export.table_name
    assert ref.row_index == row_index
    assert ref.source_values["invoice_number"] == "INV-44510"
    assert ref.source_values["invoice_amount"] == "12000.00"
    assert ref.source_values["po_number"] == ""  # the planted missing PO

    with pytest.raises(ValueError):
        tyler.source_ref_for_row(export, 10_000)
    with pytest.raises(ValueError):
        tyler.source_ref_for_row(export, 0, columns=["nope"])


def test_write_normalized_csv_audit_copy(tmp_path):
    export = tyler.normalize_tyler_export(DATA / "gl_detail.csv")
    dest = tyler.write_normalized_csv(export, tmp_path / "_normalized_inputs")
    assert dest.is_file()
    assert dest.name == "gl_detail__tyler_gl_detail.csv"
    out = pd.read_csv(dest, dtype=str, keep_default_na=False)
    assert len(out) == len(export.dataframe)
    assert list(out.columns) == [str(c) for c in export.dataframe.columns]
    # Deterministic serialization: dates ISO, Decimals plain strings.
    assert out.loc[0, "date"] == export.dataframe.loc[0, "date"].isoformat()
    # The ORIGINAL file is untouched (audit copy only).
    assert tyler.file_sha256(DATA / "gl_detail.csv") == export.input_file.file_hash


# --------------------------------------------------------------------------- #
# known_answers.json consistency against the normalized frames
# --------------------------------------------------------------------------- #
def test_known_answers_d1_duplicate_invoice():
    known = _known()
    export = tyler.normalize_tyler_export(DATA / "ap_invoice_detail.csv")
    df = export.dataframe
    d1 = known["ap_anomalies"]["D1"]
    rows = df[df["invoice_number"] == d1["invoice_number"]]
    assert len(rows) == 2  # appears EXACTLY twice
    assert rows["source_row_index"].tolist() == d1["ap_row_indices"]
    assert set(rows["vendor_number"]) == {d1["vendor_number"]}
    assert all(v == Decimal(d1["amount"]) for v in rows["invoice_amount"])
    assert rows["check_number"].tolist() == d1["check_numbers"]


def test_known_answers_p3_po_absent():
    known = _known()
    p3 = known["po_anomalies"]["P3"]
    po_export = tyler.normalize_tyler_export(DATA / "purchase_orders.csv")
    assert p3["po_number"] not in set(po_export.dataframe["po_number"])
    ap_export = tyler.normalize_tyler_export(DATA / "ap_invoice_detail.csv")
    ap_df = ap_export.dataframe
    bad = ap_df[ap_df["invoice_number"] == p3["invoice_number"]]
    assert bad["po_number"].tolist() == [p3["po_number"]]


def test_known_answers_q4_rows_in_gl():
    known = _known()
    q4 = known["transaction_search"]["Q4"]
    export = tyler.normalize_tyler_export(DATA / "gl_detail.csv")
    df = export.dataframe
    desc = df["description"].str.lower()
    mask = (df["fund"] == "300") & (
        desc.str.contains("pothole") | desc.str.contains("asphalt"))
    hits = df[mask]
    assert len(hits) == q4["expected_row_count"] == 3
    assert hits["source_row_index"].tolist() == q4["expected_row_indices"]
    assert hits["journal"].tolist() == q4["expected_journals"]


def test_known_answers_vendor_and_coa_plants():
    known = _known()
    ven = tyler.normalize_tyler_export(DATA / "vendor_list.csv").dataframe
    # D6 inactive vendor; D7 unknown vendor absent; D3 similar names present.
    assert ven.loc[ven["vendor_number"] == "V-1005", "status"].tolist() == ["Inactive"]
    assert "V-9999" not in set(ven["vendor_number"])
    d3 = known["ap_anomalies"]["D3"]
    assert set(d3["vendor_numbers"]) <= set(ven["vendor_number"])

    coa = tyler.normalize_tyler_export(DATA / "chart_of_accounts.csv").dataframe
    inactive = known["journal_entry"]["inactive_account"]
    row = coa[coa["account"] == inactive["account"]]
    assert row["status"].tolist() == ["Inactive"]
    assert row["source_row_index"].tolist() == inactive["coa_row_indices"]

    # COA covers every fund/org/object combination used elsewhere.
    combos = set()
    for name in ("gl_detail.csv", "ap_invoice_detail.csv",
                 "purchase_orders.csv", "budget_to_actual.csv"):
        df = tyler.normalize_tyler_export(DATA / name).dataframe
        combos |= set(zip(df["fund"], df["org"], df["object"]))
    coa_combos = set(zip(coa["fund"], coa["org"], coa["object"]))
    assert combos <= coa_combos


def test_known_answers_search_counts():
    known = _known()
    ap_df = tyler.normalize_tyler_export(DATA / "ap_invoice_detail.csv").dataframe
    chk_df = tyler.normalize_tyler_export(DATA / "check_register.csv").dataframe

    q1 = known["transaction_search"]["Q1"]
    mask = (
        ap_df["vendor_name"].str.contains("Cascade Paving")
        & ap_df["invoice_amount"].map(lambda v: v is not None and v > Decimal("5000"))
        & ap_df["invoice_date"].map(
            lambda d: d is not None and date(2026, 3, 1) <= d <= date(2026, 5, 31))
    )
    assert ap_df.index[mask].tolist() == q1["expected_row_indices"]
    assert int(mask.sum()) == q1["expected_row_count"] == 2

    q3 = known["transaction_search"]["Q3"]
    mask3 = (
        chk_df["vendor_name"].str.contains("Acme Office Supply")
        & chk_df["check_date"].map(
            lambda d: d is not None and date(2026, 2, 1) <= d <= date(2026, 2, 28))
    )
    hits = chk_df[mask3]
    assert len(hits) == q3["expected_row_count"] == 2
    assert sorted(hits["check_number"].tolist()) == q3["expected_check_numbers"]


def test_check_register_consistent_with_ap():
    """Every paid AP invoice's check exists with a matching amount sum."""
    ap_df = tyler.normalize_tyler_export(DATA / "ap_invoice_detail.csv").dataframe
    chk_df = tyler.normalize_tyler_export(DATA / "check_register.csv").dataframe
    paid = ap_df[(ap_df["status"] == "Paid") & (ap_df["check_number"] != "")]
    sums = paid.groupby("check_number")["invoice_amount"].apply(
        lambda s: sum(s, Decimal("0")))
    by_check = dict(zip(chk_df["check_number"], chk_df["check_amount"]))
    for check_no, total in sums.items():
        assert by_check[check_no] == total, check_no
    # One Void check exists as a true negative.
    assert (chk_df["status"] == "Void").sum() == 1


# --------------------------------------------------------------------------- #
# Additive schema / preset changes stay backward compatible
# --------------------------------------------------------------------------- #
def test_new_finding_types_added():
    assert FindingType.SEARCH_MATCH.value == "search_match"
    assert FindingType.DUPLICATE_PAYMENT.value == "duplicate_payment"
    assert FindingType.VENDOR_ANOMALY.value == "vendor_anomaly"
    assert FindingType.PO_MISMATCH.value == "po_mismatch"
    assert FindingType.JE_VALIDATION.value == "je_validation"
    assert FindingType.MISSING_REFERENCE.value == "missing_reference"
    # Pre-existing members untouched.
    assert FindingType.MATCHED.value == "matched"
    assert FindingType.OTHER.value == "other"


def test_tyler_munis_preset_extended_backward_compatible():
    aliases = presets.PRESETS["tyler_munis_style"]
    # Original mappings preserved (gl_amount/journal_amount/je_amount/invoice
    # were dead aliases with no fixture or TYLER_DATASET_TYPES counterpart --
    # removed by GW-30).
    assert aliases["jrnl_date"] == "date"
    assert "gl_amount" not in aliases  # GW-30: deleted dead alias
    assert "journal_amount" not in aliases  # GW-30: deleted dead alias
    assert "je_amount" not in aliases  # GW-30: deleted dead alias
    assert "invoice" not in aliases  # GW-30: deleted dead alias (no fixture)
    assert aliases["org"] == "department"
    assert aliases["object"] == "account_code"
    # New Munis-style variants present.
    assert aliases["eff_date"] == "date"
    assert aliases["vendor_no"] == "vendor_number"
    assert aliases["invoice_no"] == "invoice_number"
    assert aliases["po_no"] == "po_number"
    assert aliases["check_no"] == "check_number"
