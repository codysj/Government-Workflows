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
