"""Excel ingestion via pandas/openpyxl. Returns a ParsedTable."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.core.schemas import ParsedTable, make_id

# Re-export shared helpers so callers can use one import surface.
from src.ingest.csv_loader import file_sha256, to_input_file  # noqa: F401


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
