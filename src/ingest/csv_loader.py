"""CSV ingestion. Returns a ParsedTable and an InputFile with a sha256 hash."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from src.core.schemas import InputFile, ParsedTable, make_id


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_csv(path: str | Path, table_name: str | None = None) -> ParsedTable:
    path = Path(path)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
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
