"""Deterministic cleaning utilities (Phase 3.2).

Column-name normalization to snake_case, date parsing, amount/currency parsing,
whitespace cleanup, duplicate-row detection, and source-row preservation.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import pandas as pd

from src.core.schemas import NormalizedRecord, ParsedTable, SourceRowRef


# --------------------------------------------------------------------------- #
# Column names
# --------------------------------------------------------------------------- #
def to_snake_case(name: str) -> str:
    s = str(name).strip()
    s = re.sub(r"[^\w\s]", " ", s)          # punctuation -> space
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)  # camelCase boundary
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    return s.lower().strip("_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [to_snake_case(c) for c in out.columns]
    return out


# --------------------------------------------------------------------------- #
# Whitespace
# --------------------------------------------------------------------------- #
def clean_whitespace(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


def parse_date(value: Any) -> Optional[date]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = clean_whitespace(str(value))
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last resort: pandas flexible parser.
    try:
        ts = pd.to_datetime(s, errors="raise")
        return ts.date()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Amounts / currency
# --------------------------------------------------------------------------- #
def parse_amount(value: Any) -> Optional[Decimal]:
    """Parse a currency/amount string into a Decimal.

    Handles $, thousands separators, and parentheses-as-negative.
    Returns None when the value is empty/unparseable.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = clean_whitespace(str(value))
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.endswith("-"):  # trailing-minus style
        negative = True
        s = s[:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    if not s:
        return None
    try:
        amount = Decimal(s)
    except InvalidOperation:
        return None
    return -amount if negative else amount


# --------------------------------------------------------------------------- #
# Duplicate detection
# --------------------------------------------------------------------------- #
def detect_duplicate_rows(
    df: pd.DataFrame, subset: Optional[list[str]] = None
) -> list[int]:
    """Return row_index positions (0-based) of rows duplicating an earlier row."""
    mask = df.duplicated(subset=subset, keep="first")
    return [int(i) for i in df.index[mask].tolist()]


# --------------------------------------------------------------------------- #
# Normalized records with source-row preservation
# --------------------------------------------------------------------------- #
def make_source_row_ref(
    parsed: ParsedTable, row_index: int, row: dict[str, Any]
) -> SourceRowRef:
    return SourceRowRef(
        file_id=parsed.file_id,
        table_name=parsed.table_name,
        row_index=int(row_index),
        column_names=list(row.keys()),
        source_values={k: ("" if pd.isna(v) else v) for k, v in row.items()},
    )


def normalize_table(
    parsed: ParsedTable,
    *,
    date_columns: Optional[list[str]] = None,
    amount_columns: Optional[list[str]] = None,
) -> list[NormalizedRecord]:
    """Clean a ParsedTable into NormalizedRecords, each carrying a SourceRowRef.

    row_index in the SourceRowRef is the ORIGINAL positional index of the row
    in the parsed dataframe, preserved across cleaning.
    """
    df = parsed.dataframe
    df = normalize_columns(df)
    date_columns = [to_snake_case(c) for c in (date_columns or [])]
    amount_columns = [to_snake_case(c) for c in (amount_columns or [])]

    records: list[NormalizedRecord] = []
    for pos, (_, raw_row) in enumerate(df.iterrows()):
        original = {to_snake_case(k): v for k, v in raw_row.to_dict().items()}
        cleaned: dict[str, Any] = {}
        for col, val in original.items():
            val = clean_whitespace(val)
            if col in date_columns:
                cleaned[col] = parse_date(val)
            elif col in amount_columns:
                cleaned[col] = parse_amount(val)
            else:
                cleaned[col] = val
        source = make_source_row_ref(parsed, pos, original)
        records.append(NormalizedRecord(values=cleaned, source_row=source))
    return records
