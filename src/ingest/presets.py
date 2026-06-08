"""Lightweight local-file import presets (Tier 1 roadmap: import adapters).

Municipal ERP exports (Tyler/Munis, OpenGov, generic GL dumps) use a zoo of
column names for the same concepts: "Posting Date" / "GL Date" / "Effective
Date" all mean the transaction date; "Transaction Amount" / "Debit" / "Net
Amount" mean the amount. The deterministic workflows already snake-case headers
and auto-detect a handful of common names, but they cannot know every vendor's
spelling.

This module adds a small, GENERIC column-alias layer that maps those vendor-ish
header variants onto the canonical names the workflows expect (``date``,
``amount``, ``description``, ``account``/``account_code``, ``fund``,
``department``). It is:

  * Local-file only — NO API integration, NO authentication, NO vendor SDKs.
  * Deterministic and side-effect free — it only renames columns.
  * Opt-in — workflows are not changed; callers apply a preset to a DataFrame
    before handing it to a workflow. This keeps the core pipeline untouched.

The presets are placeholders modelled on common export shapes, not real vendor
schemas. Use :func:`apply_preset` to normalize a DataFrame's columns.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from src.normalize.cleaning import normalize_columns

# --------------------------------------------------------------------------- #
# Canonical alias maps (snake_cased variant -> canonical column name)
# --------------------------------------------------------------------------- #
# A broad, generic set covering common GL/bank export header spellings.
GENERIC_ERP: dict[str, str] = {
    # date
    "posting_date": "date",
    "post_date": "date",
    "gl_date": "date",
    "effective_date": "date",
    "value_date": "date",
    "trans_date": "date",
    "transaction_date": "date",
    "txn_date": "date",
    # amount
    "transaction_amount": "amount",
    "txn_amount": "amount",
    "net_amount": "amount",
    "amt": "amount",
    "line_amount": "amount",
    # description
    "memo": "description",
    "details": "description",
    "narrative": "description",
    "reference": "description",
    "payee": "description",
    "vendor_name": "description",
    # account / fund / department
    "account_number": "account_code",
    "account_no": "account_code",
    "gl_account": "account_code",
    "object_code": "account_code",
    "fund_code": "fund",
    "fund_no": "fund",
    "dept": "department",
    "department_name": "department",
    "division": "department",
}

# Vendor-flavored placeholders (NOT real schemas — illustrative for the demo).
TYLER_MUNIS_STYLE: dict[str, str] = {
    "jrnl_date": "date",
    "gl_amount": "amount",
    "org": "department",
    "object": "account_code",
}

OPENGOV_STYLE: dict[str, str] = {
    "transaction_post_date": "date",
    "transaction_total": "amount",
    "account_label": "description",
}

PRESETS: dict[str, dict[str, str]] = {
    "generic_erp": GENERIC_ERP,
    "tyler_munis_style": {**GENERIC_ERP, **TYLER_MUNIS_STYLE},
    "opengov_style": {**GENERIC_ERP, **OPENGOV_STYLE},
}


def list_presets() -> list[str]:
    """Return the available preset names."""
    return sorted(PRESETS)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def apply_aliases(
    df: pd.DataFrame, aliases: dict[str, str], *, normalize: bool = True
) -> pd.DataFrame:
    """Return a copy of ``df`` with columns renamed via ``aliases``.

    Columns are first snake-cased (so "Posting Date" matches ``posting_date``)
    when ``normalize`` is True. Aliasing never drops or reorders data — it only
    renames. A canonical target that already exists is not overwritten (the
    existing canonical column wins, and the alias source is left as-is).
    """
    out = normalize_columns(df) if normalize else df.copy()
    existing = set(out.columns)
    rename: dict[str, str] = {}
    for src, dst in aliases.items():
        if src in existing and dst not in existing and dst not in rename.values():
            rename[src] = dst
    return out.rename(columns=rename)


def apply_preset(
    df: pd.DataFrame, preset: str = "generic_erp", *, extra: Optional[dict] = None
) -> pd.DataFrame:
    """Normalize ``df`` columns using a named preset (+ optional extra aliases).

    Raises ``KeyError`` for an unknown preset name.
    """
    aliases = dict(PRESETS[preset])
    if extra:
        aliases.update(extra)
    return apply_aliases(df, aliases)


def normalize_csv(
    src_path: str | Path,
    preset: str,
    out_dir: str | Path,
    *,
    extra: Optional[dict] = None,
) -> Path:
    """Read a CSV, apply a preset's column aliases, and write an aliased copy.

    Returns the path to the written copy (``<out_dir>/<src stem>__<preset>.csv``).
    This is the import-adapter step a caller runs BEFORE handing a file to a
    workflow: the workflow still reads a plain CSV path, so its deterministic
    logic is unchanged. Raises ``KeyError`` for an unknown preset name.
    """
    src = Path(src_path)
    df = pd.read_csv(src)
    aliased = apply_preset(df, preset, extra=extra)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{src.stem}__{preset}.csv"
    aliased.to_csv(dest, index=False)
    return dest


__all__ = [
    "GENERIC_ERP",
    "TYLER_MUNIS_STYLE",
    "OPENGOV_STYLE",
    "PRESETS",
    "list_presets",
    "apply_aliases",
    "apply_preset",
    "normalize_csv",
]
