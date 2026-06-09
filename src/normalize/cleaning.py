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
# Messy-data + parse-confidence helpers (preflight layer)
# --------------------------------------------------------------------------- #
# Header synonyms used by best_semantic_column. Keys are semantic field names;
# values are header tokens (snake_case) that strongly indicate that field.
SEMANTIC_SYNONYMS: dict[str, list[str]] = {
    "date": [
        "date", "txn_date", "transaction_date", "posted_date", "post_date",
        "entry_date", "value_date", "effective_date",
    ],
    "amount": [
        "amount", "value", "txn_amount", "transaction_amount", "debit",
        "credit", "balance", "total", "actual", "amount_usd",
    ],
    "description": [
        "description", "memo", "payee", "vendor", "details", "narrative",
        "particulars", "name", "reference",
    ],
    "account_code": [
        "account_code", "account_number", "account_no", "acct", "acct_code",
        "gl_code", "gl_account", "code", "account",
    ],
    "account_name": [
        "account_name", "account_title", "account_description", "gl_name",
        "line_item",
    ],
    "section": ["section", "category", "group", "report_section", "heading"],
    "line_type": ["line_type", "type", "row_type", "level", "indent"],
    "fund": ["fund", "fund_code", "fund_name", "fund_no"],
    "department": ["department", "dept", "dept_code", "division", "unit"],
    "object": ["object", "object_code", "obj", "obj_code", "expenditure_object"],
}

_TOTAL_LABEL_RE = re.compile(
    r"\b(total|subtotal|grand\s*total|sum|totals)\b", re.IGNORECASE
)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip() == ""


def date_parse_confidence(series: pd.Series) -> float:
    """Fraction of non-blank cells in ``series`` that parse via ``parse_date``."""
    non_blank = [v for v in series.tolist() if not _is_blank(v)]
    if not non_blank:
        return 0.0
    parsed = sum(1 for v in non_blank if parse_date(v) is not None)
    return parsed / len(non_blank)


def amount_parse_confidence(series: pd.Series) -> float:
    """Fraction of non-blank cells in ``series`` that parse via ``parse_amount``."""
    non_blank = [v for v in series.tolist() if not _is_blank(v)]
    if not non_blank:
        return 0.0
    parsed = sum(1 for v in non_blank if parse_amount(v) is not None)
    return parsed / len(non_blank)


def clean_amount_series(series: pd.Series) -> pd.Series:
    """Return a cleaned string series for amount-like values (no rows dropped).

    Strips currency symbols and thousands commas, converts parentheses to a
    leading minus, and normalizes the unicode minus sign. Blanks pass through
    unchanged so source-row alignment is preserved.
    """

    def _clean(value: Any) -> Any:
        if _is_blank(value):
            return value
        s = clean_whitespace(str(value))
        s = s.replace("−", "-")  # unicode minus -> ascii hyphen
        negative = False
        if s.startswith("(") and s.endswith(")"):
            negative = True
            s = s[1:-1]
        s = s.replace("$", "").replace(",", "").replace(" ", "")
        if s.endswith("-"):
            negative = True
            s = s[:-1]
        if s.startswith("-"):
            negative = True
            s = s[1:]
        if not s:
            return value
        return f"-{s}" if negative else s

    return series.map(_clean)


def detect_repeated_header_rows(df: pd.DataFrame) -> list[int]:
    """Return positional indices of data rows that duplicate the header row.

    A row matches when its cell values equal the (snake_cased) column names,
    which happens when a header line is repeated inside the data (e.g. one
    header per page concatenated into a single export).
    """
    cols = [to_snake_case(c) for c in df.columns]
    matches: list[int] = []
    for pos, (_, row) in enumerate(df.iterrows()):
        values = [to_snake_case(str(v)) for v in row.tolist()]
        if values == cols:
            matches.append(pos)
    return matches


def detect_footer_total_rows(df: pd.DataFrame) -> list[int]:
    """Return positional indices of likely total/footer rows.

    A row qualifies when any cell label contains total/subtotal/grand total,
    OR when most cells are blank but at least one numeric value is present
    (a typical trailing total line).
    """
    matches: list[int] = []
    n_cols = max(len(df.columns), 1)
    for pos, (_, row) in enumerate(df.iterrows()):
        cells = row.tolist()
        if any(
            isinstance(v, str) and _TOTAL_LABEL_RE.search(v) for v in cells
        ) or any(
            (not _is_blank(v)) and _TOTAL_LABEL_RE.search(str(v)) for v in cells
        ):
            matches.append(pos)
            continue
        blanks = sum(1 for v in cells if _is_blank(v))
        has_amount = any(
            (not _is_blank(v)) and parse_amount(v) is not None for v in cells
        )
        if has_amount and blanks >= n_cols - 1:
            matches.append(pos)
    return matches


def normalize_description(value: Any) -> str:
    """Collapse whitespace and strip for fuzzy description comparison.

    Keeps the text human-readable (no case folding of the returned string);
    callers that want case-insensitive comparison can lower() the result.
    """
    if _is_blank(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def best_semantic_column(
    df: pd.DataFrame,
    semantic_name: str,
    *,
    synonyms: Optional[dict[str, list[str]]] = None,
) -> tuple[Optional[str], float, list[str]]:
    """Pick the best column for a semantic field.

    Returns ``(best_column, confidence, ranked_candidates)``.

    Scoring uses header-name synonyms (exact match > token-contains) and, for
    the ``date``/``amount`` semantics, parse-confidence as a tiebreaker. When two
    candidates score near-equally high the returned confidence is lowered so the
    preflight engine treats the mapping as ambiguous.
    """
    syn_map = synonyms or SEMANTIC_SYNONYMS
    wanted = syn_map.get(semantic_name, [semantic_name])
    cols = [str(c) for c in df.columns]

    scored: list[tuple[str, float]] = []
    for col in cols:
        snake = to_snake_case(col)
        name_score = 0.0
        if snake in wanted:
            name_score = 1.0
        elif any(w == snake for w in wanted):
            name_score = 1.0
        elif any(w in snake or snake in w for w in wanted):
            name_score = 0.6
        if name_score == 0.0:
            continue
        score = name_score
        if semantic_name == "date":
            score = 0.5 * name_score + 0.5 * date_parse_confidence(df[col])
        elif semantic_name == "amount":
            score = 0.5 * name_score + 0.5 * amount_parse_confidence(df[col])
        scored.append((col, score))

    if not scored:
        return (None, 0.0, [])

    scored.sort(key=lambda t: t[1], reverse=True)
    ranked = [c for c, _ in scored]
    best_col, best_score = scored[0]

    # Ambiguity: a near-tie between the top two strong candidates lowers
    # confidence so the engine flags AMBIGUOUS_COLUMN_MAPPING.
    if len(scored) >= 2:
        second_score = scored[1][1]
        if best_score >= 0.6 and (best_score - second_score) <= 0.1:
            best_score = min(best_score, 0.5)

    return (best_col, round(float(best_score), 4), ranked)


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
