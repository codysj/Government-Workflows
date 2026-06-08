"""Chart-of-accounts context loader (Phase 0 ``src/context``).

Loads a chart-of-accounts CSV into a small, dependency-light structure that
exposes the set of valid account codes + names. This is reference data — NOT a
RAG corpus (spec Tier 2 explicitly says do NOT use retrieval for small
structured reference data like chart-of-accounts files).

The synthetic chart-of-accounts files in ``data/synthetic`` use two different
schemas:

  * ``budget_variance/chart_of_accounts.csv``: fund, account, department,
    object, account_name  (the account *code* lives in the ``account`` column).
  * ``report_review/chart_of_accounts.csv``: account_code, account_name,
    normal_balance.

So the loader auto-detects the code column (``account_code`` then ``account``)
and the name column (``account_name`` then ``name``) after snake_case
normalization, and never assumes a fixed schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from src.normalize.cleaning import normalize_columns, to_snake_case

# Candidate columns (after snake_case normalization), most-specific first.
_CODE_COLUMNS = ("account_code", "account", "code", "account_number", "acct")
_NAME_COLUMNS = ("account_name", "name", "description", "account_description")


@dataclass
class ChartOfAccounts:
    """Valid account codes + names parsed from a chart-of-accounts file.

    Attributes
    ----------
    codes:
        Ordered set of valid account codes (as stripped strings).
    names:
        Mapping ``code -> account_name`` (empty string when no name column).
    code_column / name_column:
        The detected source columns (after normalization), for diagnostics.
    """

    codes: set[str] = field(default_factory=set)
    names: dict[str, str] = field(default_factory=dict)
    code_column: Optional[str] = None
    name_column: Optional[str] = None

    def is_valid_code(self, code: object) -> bool:
        """Return True when ``code`` is a known account code (stripped match)."""
        return str(code).strip() in self.codes

    def __len__(self) -> int:
        return len(self.codes)

    def __contains__(self, code: object) -> bool:
        return self.is_valid_code(code)


def _first_col(columns: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    for c in candidates:
        if c in columns:
            return c
    return None


def load_chart_of_accounts(path: str | Path) -> ChartOfAccounts:
    """Load a chart-of-accounts CSV into a :class:`ChartOfAccounts`.

    Returns an empty (but valid) ChartOfAccounts when ``path`` is None/missing
    so callers can treat "no chart of accounts" the same as "empty".
    """
    if path is None:
        return ChartOfAccounts()
    p = Path(path)
    if not p.is_file():
        return ChartOfAccounts()

    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df = normalize_columns(df)
    columns = list(df.columns)

    code_col = _first_col(columns, _CODE_COLUMNS)
    name_col = _first_col(columns, _NAME_COLUMNS)
    if code_col is None:
        # No recognizable code column; nothing to validate against.
        return ChartOfAccounts(code_column=None, name_column=name_col)

    codes: set[str] = set()
    names: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row[code_col]).strip()
        if not code:
            continue
        codes.add(code)
        if name_col is not None:
            names[code] = str(row[name_col]).strip()
    return ChartOfAccounts(
        codes=codes,
        names=names,
        code_column=code_col,
        name_column=name_col,
    )


def chart_of_accounts_from_codes(
    codes: list[str], names: Optional[dict[str, str]] = None
) -> ChartOfAccounts:
    """Build a ChartOfAccounts directly from an iterable of codes (for tests /
    in-memory reference data)."""
    clean = {str(c).strip() for c in codes if str(c).strip()}
    return ChartOfAccounts(codes=clean, names=dict(names or {}))


# Snake-case helper re-export so callers do not import cleaning directly just for
# column-name handling.
__all__ = [
    "ChartOfAccounts",
    "load_chart_of_accounts",
    "chart_of_accounts_from_codes",
    "to_snake_case",
]
