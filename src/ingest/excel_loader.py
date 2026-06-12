"""Excel ingestion via pandas/openpyxl. Returns a ParsedTable."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.core.schemas import ParsedTable, make_id

# Re-export shared helpers so callers can use one import surface.
from src.ingest.csv_loader import file_sha256, load_csv, to_input_file  # noqa: F401

# Extensions routed to the Excel reader by load_table.
EXCEL_EXTENSIONS = (".xlsx", ".xlsm")


def load_excel(
    path: str | Path, sheet_name: str | int = 0, table_name: str | None = None
) -> ParsedTable:
    path = Path(path)
    df = pd.read_excel(
        path, sheet_name=sheet_name, dtype=str, engine="openpyxl"
    )
    df = df.fillna("")
    table = table_name or path.stem
    return ParsedTable(
        file_id=make_id(),
        table_name=table,
        file_name=path.name,
        file_type="excel",
        parser_used="pandas.read_excel",
        column_names=list(df.columns),
        row_count=int(len(df)),
        dataframe=df,
    )


def load_table(
    path: str | Path,
    table_name: str | None = None,
    *,
    sheet_name: str | int = 0,
) -> ParsedTable:
    """Load a CSV or Excel file into a ParsedTable based on its extension.

    Deterministic dispatcher used by the workflow modules so every workflow
    that declares ``xlsx`` in its accepted file types actually parses it:
    ``.xlsx``/``.xlsm`` go through :func:`load_excel` (openpyxl, all cells as
    text, blanks preserved); everything else goes through
    :func:`src.ingest.csv_loader.load_csv` (unchanged existing behavior).
    """
    suffix = Path(path).suffix.lower()
    if suffix in EXCEL_EXTENSIONS:
        return load_excel(path, sheet_name=sheet_name, table_name=table_name)
    return load_csv(path, table_name=table_name)
