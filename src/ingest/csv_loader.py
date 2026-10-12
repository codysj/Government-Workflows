"""CSV ingestion. Returns a ParsedTable and an InputFile with a sha256 hash."""
from __future__ import annotations

import csv
import hashlib
import itertools
from pathlib import Path

import pandas as pd

from src.core.schemas import InputFile, ParsedTable, make_id


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_MAX_TITLE_ROWS = 10


def title_block_length(rows: list[list]) -> int:
    """How many rows sit above the real header row.

    Report exports (Munis "YEAR-TO-DATE BUDGET REPORT", city name, "FOR 2026 10")
    put a title block above the column headers: rows with at most one filled
    cell, holding text. The header is the first row with two or more filled
    cells. Skipped only when that row reads as headers (no amounts or dates) and
    every row above it reads as a title, so real data is never dropped; 0 otherwise.
    """
    from src.normalize.cleaning import parse_amount, parse_date

    def filled(row):
        return [str(c).strip() for c in row if str(c).strip()]

    def is_value(text):
        return parse_amount(text) is not None or parse_date(text) is not None

    for i, row in enumerate(rows[:_MAX_TITLE_ROWS + 1]):
        cells = filled(row)
        if len(cells) >= 2:
            titles_ok = all(not any(is_value(c) for c in filled(r)) for r in rows[:i])
            return i if i and titles_ok and not any(is_value(c) for c in cells) else 0
    return 0


def read_csv_table(path: str | Path) -> pd.DataFrame:
    """Read a CSV as all-text, skipping a report title block above the header."""
    with Path(path).open(newline="", encoding="utf-8-sig", errors="replace") as fh:
        head = list(itertools.islice(csv.reader(fh), _MAX_TITLE_ROWS + 1))
    skip = title_block_length(head)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, skiprows=skip)
    df.attrs["title_rows"] = skip
    return df


def read_excel_table(path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    """Read an Excel sheet as all-text (blanks as ""), skipping a title block."""
    head = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=str,
                         engine="openpyxl", nrows=_MAX_TITLE_ROWS + 1).fillna("")
    skip = title_block_length(head.values.tolist())
    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, engine="openpyxl",
                       header=skip).fillna("")
    df.attrs["title_rows"] = skip
    return df


def load_csv(path: str | Path, table_name: str | None = None) -> ParsedTable:
    path = Path(path)
    df = read_csv_table(path)
    table = table_name or path.stem
    return ParsedTable(
        file_id=make_id(),
        table_name=table,
        file_name=path.name,
        file_type="csv",
        parser_used="pandas.read_csv",
        column_names=list(df.columns),
        row_count=int(len(df)),
        dataframe=df,
    )


def to_input_file(path: str | Path, parsed: ParsedTable) -> InputFile:
    path = Path(path)
    return InputFile(
        file_id=parsed.file_id,
        file_name=path.name,
        file_type=parsed.file_type,
        file_hash=file_sha256(path),
        parser_used=parsed.parser_used,
        row_count=parsed.row_count,
        column_names=parsed.column_names,
        input_key=parsed.table_name,
    )
