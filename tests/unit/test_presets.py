"""Tests for the import-preset / column-alias helper (src/ingest/presets.py)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.ingest import presets
from src.workflows.bank_reconciliation import reconcile

ERP_BANK = (
    Path(__file__).resolve().parents[2]
    / "data" / "synthetic" / "bank_reconciliation" / "erp_style_bank.csv"
)
LEDGER = (
    Path(__file__).resolve().parents[2]
    / "data" / "synthetic" / "bank_reconciliation" / "ledger.csv"
)


def test_list_presets():
    names = presets.list_presets()
    assert "generic_erp" in names
    assert "tyler_munis_style" in names
    assert "opengov_style" in names


def test_apply_preset_maps_erp_headers():
    df = pd.DataFrame({
        "Posting Date": ["2026-01-03"],
        "Memo": ["Payroll"],
        "Transaction Amount": [12500.0],
    })
    out = presets.apply_preset(df, "generic_erp")
    assert "date" in out.columns
    assert "amount" in out.columns
    assert "description" in out.columns
    # Data preserved, just renamed.
    assert out.loc[0, "amount"] == 12500.0


def test_apply_aliases_does_not_overwrite_existing_canonical():
    # If a canonical column already exists, the alias source is left intact.
    df = pd.DataFrame({"date": ["2026-01-01"], "posting_date": ["2026-02-02"]})
    out = presets.apply_aliases(df, {"posting_date": "date"})
    assert "date" in out.columns
    assert "posting_date" in out.columns  # not overwritten


def test_apply_preset_unknown_raises():
    with pytest.raises(KeyError):
        presets.apply_preset(pd.DataFrame(), "no_such_preset")


def test_normalize_csv_writes_aliased_copy(tmp_path):
    dest = presets.normalize_csv(ERP_BANK, "generic_erp", tmp_path)
    assert dest.is_file()
    assert dest.parent == tmp_path
    out = pd.read_csv(dest)
    assert {"date", "amount", "description"} <= set(out.columns)
    # Same number of rows as the source (no data dropped).
    assert len(out) == len(pd.read_csv(ERP_BANK))
    # The ORIGINAL file is untouched (a copy was written).
    assert list(pd.read_csv(ERP_BANK).columns) == [
        "Posting Date", "Memo", "Transaction Amount"]


def test_normalize_csv_unknown_preset_raises(tmp_path):
    with pytest.raises(KeyError):
        presets.normalize_csv(ERP_BANK, "nope", tmp_path)


def test_generic_erp_covers_budget_dimensions():
    df = pd.DataFrame({
        "Fund Code": ["General"], "Account Key": ["5001"],
        "Cost Center": ["Police"], "Object Class": ["Salaries"],
        "Budget Amount": ["50000"],
    })
    out = presets.apply_preset(df, "generic_erp")
    assert {"fund", "account", "department", "object", "budget_amount"} <= set(out.columns)


def test_generic_erp_covers_report_dimensions():
    df = pd.DataFrame({
        "Statement Section": ["Revenues"], "Account No": ["4010"],
        "Account Description": ["Property Tax"], "Row Type": ["line_item"],
        "Reported Amount": ["500000"],
    })
    out = presets.apply_preset(df, "generic_erp")
    assert {"section", "account_code", "account_name", "line_type", "amount"} <= set(out.columns)


def test_normalize_csv_preserves_integer_codes_and_blanks(tmp_path):
    """Aliasing must rename columns only — integer-like account codes must not
    become floats ('5010' not '5010.0') and blank subtotal cells stay blank."""
    erp_report = (
        Path(__file__).resolve().parents[2]
        / "data" / "synthetic" / "report_review" / "erp_style_report.csv"
    )
    dest = presets.normalize_csv(erp_report, "generic_erp", tmp_path)
    out = pd.read_csv(dest, dtype=str, keep_default_na=False)
    codes = list(out["account_code"])
    assert "5010" in codes and "5010.0" not in codes
    # Subtotal rows keep a blank account code.
    assert "" in codes


def test_erp_style_file_reconciles_after_aliasing():
    """An ERP-style export (Posting Date / Memo / Transaction Amount) only
    reconciles once its columns are aliased onto the canonical names."""
    raw = pd.read_csv(ERP_BANK)
    # 'Posting Date' snake-cases to 'posting_date', which the matcher does NOT
    # auto-detect, so without aliasing the date column is missing.
    normalized = presets.apply_preset(raw, "generic_erp")
    assert {"date", "amount"} <= set(normalized.columns)

    # Build a ParsedTable-like input via a temp CSV the workflow can read.
    out = reconcile(_as_temp_csv(normalized), str(LEDGER))
    assert out.summary["bank_rows"] == len(raw)
    # The duplicate Acme payment (same amount+date class) and the matches show
    # the deterministic engine ran on the aliased ERP data.
    assert out.summary["matched"] >= 1


ERP_COA = (
    Path(__file__).resolve().parents[2]
    / "data" / "synthetic" / "report_review" / "erp_style_chart_of_accounts.csv"
)


def test_chart_of_accounts_in_list_presets():
    assert "chart_of_accounts" in presets.list_presets()


def test_apply_preset_maps_coa_headers():
    df = pd.DataFrame({
        "GL Code": ["5010"],
        "Account Title": ["Salaries and Wages"],
        "Balance Type": ["debit"],
    })
    out = presets.apply_preset(df, "chart_of_accounts")
    assert {"account_code", "account_name", "normal_balance"} <= set(out.columns)
    assert out.loc[0, "account_code"] == "5010"
    assert out.loc[0, "account_name"] == "Salaries and Wages"
    assert out.loc[0, "normal_balance"] == "debit"


def test_apply_preset_coa_covers_header_variants():
    df = pd.DataFrame({
        "Acct No": ["4010"], "Account Desc": ["Property Tax"],
        "Normal Bal": ["credit"], "Fund Code": ["General"],
    })
    out = presets.apply_preset(df, "chart_of_accounts")
    assert {"account_code", "account_name", "normal_balance", "fund"} <= set(out.columns)


def test_normalize_csv_coa_yields_canonical_columns(tmp_path):
    """The ERP-style COA file, after preset + dtype=str normalize, matches the
    report_review chart_of_accounts contract with codes preserved as strings."""
    dest = presets.normalize_csv(ERP_COA, "chart_of_accounts", tmp_path)
    out = pd.read_csv(dest, dtype=str, keep_default_na=False)
    assert list(out.columns) == ["account_code", "account_name", "normal_balance"]
    codes = list(out["account_code"])
    # Integer-like codes preserved as strings (no float coercion).
    assert "5010" in codes and "5010.0" not in codes
    assert "4010" in codes
    # Could substitute for the canonical chart_of_accounts.csv.
    canonical = pd.read_csv(
        Path(__file__).resolve().parents[2]
        / "data" / "synthetic" / "report_review" / "chart_of_accounts.csv",
        dtype=str, keep_default_na=False,
    )
    assert list(out.columns) == list(canonical.columns)
    assert out.equals(canonical)


_TMP: list = []


def _as_temp_csv(df: pd.DataFrame) -> str:
    import tempfile

    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="")
    df.to_csv(fd, index=False)
    fd.flush()
    fd.close()
    _TMP.append(fd.name)
    return fd.name
