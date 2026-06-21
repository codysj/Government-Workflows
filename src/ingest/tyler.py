"""Tyler/Munis-style export normalizer (deterministic, local files only).

Munis-style ERP exports (GL detail, AP invoice detail, vendor list, check
register, purchase orders, budget-to-actual, chart of accounts, JE upload
templates) arrive as CSV/XLSX with vendor-flavored headers and, for Excel,
sometimes a title block above the real header row. This module turns such a
file into a canonical, snake_cased, traceable DataFrame that the Tyler-style
workflows (transaction search, AP duplicate review, JE upload prep, PO/invoice
mismatch review) consume.

Invariants (project hard rules):

  * Fully deterministic: same input file -> identical normalized output.
    No network, no credentials, no vendor SDKs - plain local CSV/XLSX only.
  * Fail closed: a missing required column, an unknown dataset type, or an
    unparseable file raises a clear ``ValueError`` the preflight layer can
    surface. Columns and rows are never silently dropped; footer-total rows
    and repeated header rows are flagged in ``warnings`` and kept.
  * Traceability: every normalized frame carries a ``source_row_index`` column
    (the 0-based position of the row in the parsed data region; for Excel
    files ``header_row_used`` gives the 0-based sheet row of the header, so
    the absolute file row is ``header_row_used + 1 + source_row_index``).
    The raw file is SHA-256 hashed into a fully populated ``InputFile``.
  * Money math: amount columns are parsed to ``Decimal`` via the shared
    helpers in ``src/normalize/cleaning.py`` ($, thousands commas, and
    parentheses-negative all handled). When separate Debit/Credit columns are
    present a signed ``amount`` column is derived (``amount = debit -
    credit``) and the original debit/credit columns are KEPT.

The column-alias maps below follow the conventions of
``src/ingest/presets.py`` (snake_cased source variant -> canonical name) but
are dataset-type specific, because the same header means different things in
different exports (e.g. "Vendor Name" is a join key in AP, not a description).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd
from pydantic import BaseModel, Field

from src.core.schemas import InputFile, SourceRowRef, make_id
from src.ingest.csv_loader import file_sha256
from src.ingest.presets import apply_aliases
from src.normalize.cleaning import (
    detect_footer_total_rows,
    detect_repeated_header_rows,
    parse_amount,
    parse_date,
    to_snake_case,
)

# --------------------------------------------------------------------------- #
# Dataset-type registry
# --------------------------------------------------------------------------- #
# Shared reference-field aliases (mirrors the tyler_munis_style preset).
_REF_ALIASES: dict[str, str] = {
    "vendor_no": "vendor_number",
    "vendor_id": "vendor_number",
    "vend_no": "vendor_number",
    "invoice_no": "invoice_number",
    "inv_no": "invoice_number",
    "po_no": "po_number",
    "purchase_order": "po_number",
    "check_no": "check_number",
    "chk_no": "check_number",
    "warrant_no": "check_number",
}


@dataclass(frozen=True)
class TylerDatasetSpec:
    """Declarative spec for one Munis-style export type.

    ``required_columns`` / ``optional_columns`` are CANONICAL snake_case
    names (post-aliasing). ``aliases`` maps snake_cased raw header variants
    onto those canonical names. ``date_columns`` / ``amount_columns`` are the
    canonical columns parsed to ``datetime.date`` / ``Decimal``.
    ``module`` is the workflow-facing tag for the dataset family.
    """

    dataset_type: str
    module: str
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    date_columns: tuple[str, ...] = ()
    amount_columns: tuple[str, ...] = ()


TYLER_DATASET_TYPES: dict[str, TylerDatasetSpec] = {
    "gl_detail": TylerDatasetSpec(
        dataset_type="gl_detail",
        module="gl",
        required_columns=(
            "fiscal_year", "period", "journal", "date", "fund", "org",
            "object", "description", "debit", "credit",
        ),
        optional_columns=(
            "source", "project", "account_description", "po_number",
            "check_number", "invoice_number", "amount",
        ),
        aliases={
            "year": "fiscal_year",
            "fy": "fiscal_year",
            "fiscal_yr": "fiscal_year",
            "per": "period",
            "prd": "period",
            "jnl": "journal",
            "journal_number": "journal",
            "eff_date": "date",
            "effective_date": "date",
            "jrnl_date": "date",
            "gl_date": "date",
            "post_date": "date",
            "posting_date": "date",
            "src": "source",
            "source_code": "source",
            "description_vendor": "description",
            "line_description": "description",
            "memo": "description",
            "desc": "description",
            "account_desc": "account_description",
            "acct_description": "account_description",
            **_REF_ALIASES,
        },
        date_columns=("date",),
        amount_columns=("debit", "credit", "amount"),
    ),
    "ap_invoice_detail": TylerDatasetSpec(
        dataset_type="ap_invoice_detail",
        module="ap",
        required_columns=(
            "vendor_number", "vendor_name", "invoice_number",
            "invoice_date", "invoice_amount",
        ),
        optional_columns=(
            "due_date", "po_number", "qty", "unit_price", "check_number",
            "check_date", "status", "fund", "org", "object", "description",
        ),
        aliases={
            "inv_date": "invoice_date",
            "invoice_dt": "invoice_date",
            "invoice_total": "invoice_amount",
            "inv_amount": "invoice_amount",
            "gross_amount": "invoice_amount",
            "quantity": "qty",
            "units": "qty",
            "price": "unit_price",
            "unit_cost": "unit_price",
            "chk_date": "check_date",
            "payment_date": "check_date",
            "invoice_status": "status",
            **_REF_ALIASES,
        },
        date_columns=("invoice_date", "due_date", "check_date"),
        amount_columns=("invoice_amount", "qty", "unit_price"),
    ),
    "vendor_list": TylerDatasetSpec(
        dataset_type="vendor_list",
        module="vendors",
        required_columns=("vendor_number", "vendor_name", "status"),
        optional_columns=(
            "dba", "address", "city", "state", "zip", "phone",
            "flag_1099", "terms",
        ),
        aliases={
            "1099_flag": "flag_1099",
            "1099": "flag_1099",
            "ten99_flag": "flag_1099",
            "vendor_status": "status",
            "doing_business_as": "dba",
            "addr": "address",
            "address_1": "address",
            "zip_code": "zip",
            "postal_code": "zip",
            "telephone": "phone",
            "phone_number": "phone",
            "payment_terms": "terms",
            **_REF_ALIASES,
        },
    ),
    "check_register": TylerDatasetSpec(
        dataset_type="check_register",
        module="checks",
        required_columns=(
            "check_number", "check_date", "vendor_number", "vendor_name",
            "check_amount", "status",
        ),
        optional_columns=("type", "clear_date", "invoice_numbers"),
        aliases={
            "chk_date": "check_date",
            "chk_amount": "check_amount",
            "payment_amount": "check_amount",
            "warrant_amount": "check_amount",
            # "Invoice Number(s)" snake-cases to "invoice_number_s".
            "invoice_number_s": "invoice_numbers",
            "invoices": "invoice_numbers",
            "invoice_list": "invoice_numbers",
            "check_status": "status",
            "check_type": "type",
            "cleared_date": "clear_date",
            "date_cleared": "clear_date",
            **_REF_ALIASES,
        },
        date_columns=("check_date", "clear_date"),
        amount_columns=("check_amount",),
    ),
    "purchase_orders": TylerDatasetSpec(
        dataset_type="purchase_orders",
        module="po",
        required_columns=(
            "po_number", "po_date", "vendor_number", "vendor_name",
            "status", "line", "qty", "unit_price", "line_amount",
        ),
        optional_columns=(
            "description", "received_qty", "invoiced_qty",
            "last_activity_date", "fund", "org", "object",
        ),
        aliases={
            "order_date": "po_date",
            "po_dt": "po_date",
            "line_no": "line",
            "line_number": "line",
            "quantity": "qty",
            "price": "unit_price",
            "unit_cost": "unit_price",
            "extended_amount": "line_amount",
            "ext_amount": "line_amount",
            "qty_received": "received_qty",
            "qty_invoiced": "invoiced_qty",
            "po_status": "status",
            "last_activity": "last_activity_date",
            **_REF_ALIASES,
        },
        date_columns=("po_date", "last_activity_date"),
        amount_columns=(
            "qty", "unit_price", "line_amount", "received_qty", "invoiced_qty",
        ),
    ),
    "budget_to_actual": TylerDatasetSpec(
        dataset_type="budget_to_actual",
        module="budget",
        required_columns=(
            "fund", "org", "object", "original_budget", "revised_budget",
            "ytd_actual",
        ),
        optional_columns=(
            "account_description", "transfers_adjustments", "encumbrances",
            "available_budget", "percent_used",
        ),
        aliases={
            "adopted_budget": "original_budget",
            "orig_budget": "original_budget",
            "amended_budget": "revised_budget",
            "rev_budget": "revised_budget",
            "ytd_expended": "ytd_actual",
            "ytd_expense": "ytd_actual",
            "actual_ytd": "ytd_actual",
            "transfers_adj": "transfers_adjustments",
            "adjustments": "transfers_adjustments",
            "encumbrance": "encumbrances",
            "available": "available_budget",
            "budget_available": "available_budget",
            "pct_used": "percent_used",
            "percent_of_budget": "percent_used",
            "account_desc": "account_description",
            "acct_description": "account_description",
        },
        amount_columns=(
            "original_budget", "transfers_adjustments", "revised_budget",
            "ytd_actual", "encumbrances", "available_budget", "percent_used",
        ),
    ),
    "chart_of_accounts": TylerDatasetSpec(
        dataset_type="chart_of_accounts",
        module="coa",
        required_columns=(
            "account", "fund", "org", "object", "account_description",
            "status",
        ),
        optional_columns=("project", "type", "normal_balance"),
        aliases={
            "account_number": "account",
            "account_no": "account",
            "acct": "account",
            "account_code": "account",
            "account_desc": "account_description",
            "account_title": "account_description",
            "account_name": "account_description",
            "normal_bal": "normal_balance",
            "dr_cr": "normal_balance",
            "balance_type": "normal_balance",
            "account_type": "type",
            "account_status": "status",
            "proj": "project",
        },
    ),
    "je_upload": TylerDatasetSpec(
        dataset_type="je_upload",
        module="je",
        required_columns=(
            "journal", "line", "date", "fund", "org", "object",
            "debit", "credit",
        ),
        optional_columns=(
            "account_description", "line_description", "reference", "amount",
        ),
        aliases={
            "eff_date": "date",
            "effective_date": "date",
            "je_date": "date",
            "line_no": "line",
            "line_number": "line",
            "jnl": "journal",
            "journal_number": "journal",
            "account_desc": "account_description",
            "desc": "line_description",
            "ref": "reference",
        },
        date_columns=("date",),
        amount_columns=("debit", "credit", "amount"),
    ),
}

# Detection thresholds (deterministic; exposed for tests/audit).
MIN_DETECTION_SCORE = 0.7
MIN_DETECTION_MARGIN = 0.05
# Minimum header-overlap score for a candidate Excel header row.
MIN_HEADER_ROW_SCORE = 0.5
# How many leading sheet rows are scanned for the real header row.
MAX_HEADER_SCAN_ROWS = 10


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
class TylerNormalizedExport(BaseModel):
    """A normalized Munis-style export (ParsedTable conventions).

    ``dataframe`` holds canonical snake_case columns plus ``source_row_index``
    (0-based position of each row in the parsed data region). Amount columns
    hold ``Decimal`` (or ``None`` for blanks); date columns hold
    ``datetime.date`` (or ``None``). All other cells are strings.
    """

    model_config = {"arbitrary_types_allowed": True}

    input_file: InputFile
    dataset_type: str
    module: str
    table_name: str
    dataframe: Any = None  # pandas.DataFrame
    header_row_used: int = 0
    applied_aliases: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    # dataset_type -> score breakdown (populated when auto-detected).
    detection_scores: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Header scoring / dataset-type detection
# --------------------------------------------------------------------------- #
def _canonical_header_set(columns: list[str], spec: TylerDatasetSpec) -> set[str]:
    """Snake-case ``columns`` and apply the spec aliases (first-wins).

    Mirrors :func:`src.ingest.presets.apply_aliases`: an alias only applies
    when its canonical target is not already a column.
    """
    snake = [to_snake_case(c) for c in columns]
    canon = set(snake)
    taken: set[str] = set()
    for src, dst in spec.aliases.items():
        if src in canon and dst not in canon and dst not in taken:
            canon.add(dst)
            taken.add(dst)
    return canon


def _score_headers(columns: list[str]) -> dict[str, dict[str, Any]]:
    """Deterministic header-overlap score breakdown for every dataset type."""
    scores: dict[str, dict[str, Any]] = {}
    for dtype in sorted(TYLER_DATASET_TYPES):
        spec = TYLER_DATASET_TYPES[dtype]
        canon = _canonical_header_set(columns, spec)
        req_total = len(spec.required_columns)
        opt_total = len(spec.optional_columns)
        req_hit = sum(1 for c in spec.required_columns if c in canon)
        opt_hit = sum(1 for c in spec.optional_columns if c in canon)
        req_score = (req_hit / req_total) if req_total else 0.0
        opt_score = (opt_hit / opt_total) if opt_total else 0.0
        scores[dtype] = {
            "score": round(0.8 * req_score + 0.2 * opt_score, 4),
            "required_matched": req_hit,
            "required_total": req_total,
            "optional_matched": opt_hit,
            "optional_total": opt_total,
        }
    return scores


def detect_dataset_type(
    df_or_path: Union[pd.DataFrame, str, Path],
) -> tuple[Optional[str], dict[str, dict[str, Any]]]:
    """Detect the Tyler dataset type of a DataFrame or CSV/XLSX file.

    Returns ``(dataset_type or None, score_breakdown)`` where
    ``score_breakdown`` maps every dataset type to its header-overlap scores
    (auditable). Returns ``None`` for the type on a low-confidence best score
    (< ``MIN_DETECTION_SCORE``) or a near-tie (margin <
    ``MIN_DETECTION_MARGIN``); callers must then pass an explicit type.
    """
    if isinstance(df_or_path, pd.DataFrame):
        columns = [str(c) for c in df_or_path.columns]
    else:
        raw, _header_row = _load_raw_frame(Path(df_or_path))
        columns = [str(c) for c in raw.columns]

    scores = _score_headers(columns)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    best_type, best = ranked[0]
    second_score = ranked[1][1]["score"] if len(ranked) > 1 else 0.0
    if best["score"] < MIN_DETECTION_SCORE:
        return (None, scores)
    if (best["score"] - second_score) < MIN_DETECTION_MARGIN:
        return (None, scores)
    return (best_type, scores)


# --------------------------------------------------------------------------- #
# Raw loading (CSV / XLSX) with deterministic Excel header detection
# --------------------------------------------------------------------------- #
def _detect_excel_header_row(
    raw: pd.DataFrame, *, dataset_type: Optional[str] = None
) -> int:
    """Find the real header row in an Excel sheet (0-based sheet row).

    Munis-style exports often carry a title block (city name, report title,
    as-of date) above the column headers. Each of the first
    ``MAX_HEADER_SCAN_ROWS`` rows is scored as if it were the header row
    (header-overlap against the dataset specs); the highest-scoring row wins,
    earliest row on ties. Fails closed when no row scores at least
    ``MIN_HEADER_ROW_SCORE``.
    """
    best_score = -1.0
    best_row = -1
    limit = min(MAX_HEADER_SCAN_ROWS, len(raw))
    for i in range(limit):
        values = [str(v).strip() for v in raw.iloc[i].tolist()]
        candidates = [v if v else f"unnamed_{j}" for j, v in enumerate(values)]
        if sum(1 for v in values if v) < 2:
            continue
        scores = _score_headers(candidates)
        if dataset_type is not None:
            score = scores[dataset_type]["score"]
        else:
            score = max(s["score"] for s in scores.values())
        if score > best_score:
            best_score = score
            best_row = i
    if best_row < 0 or best_score < MIN_HEADER_ROW_SCORE:
        raise ValueError(
            "tyler ingest failed (fail closed): could not locate a plausible "
            f"header row in the first {limit} rows of the Excel sheet "
            f"(best overlap score {max(best_score, 0.0):.2f} < "
            f"{MIN_HEADER_ROW_SCORE}). Check that the file is a Munis-style "
            "export with a header row, or convert it to CSV with headers on "
            "the first line."
        )
    return best_row


def _load_raw_frame(
    path: Path, *, dataset_type: Optional[str] = None
) -> tuple[pd.DataFrame, int]:
    """Load a CSV/XLSX file as an all-string DataFrame.

    Returns ``(frame, header_row_used)`` where ``header_row_used`` is the
    0-based row position of the header within the file/sheet. Never drops
    data rows. Raises ``ValueError`` (fail closed) for unsupported file types
    and unparseable files.
    """
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ValueError(
            f"tyler ingest failed (fail closed): file not found: {path}"
        )
    if suffix == ".csv":
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"tyler ingest failed (fail closed): could not parse CSV "
                f"'{path.name}': {exc}"
            ) from exc
        return df, 0
    if suffix in (".xlsx", ".xlsm"):
        try:
            raw = pd.read_excel(
                path, sheet_name=0, header=None, dtype=str, engine="openpyxl"
            )
        except Exception as exc:
            raise ValueError(
                f"tyler ingest failed (fail closed): could not parse Excel "
                f"'{path.name}': {exc}"
            ) from exc
        raw = raw.fillna("")
        if raw.empty:
            raise ValueError(
                f"tyler ingest failed (fail closed): Excel file "
                f"'{path.name}' contains no rows."
            )
        header_row = _detect_excel_header_row(raw, dataset_type=dataset_type)
        header_values = [str(v).strip() for v in raw.iloc[header_row].tolist()]
        columns = [
            v if v else f"unnamed_{j}" for j, v in enumerate(header_values)
        ]
        data = raw.iloc[header_row + 1:].reset_index(drop=True)
        data.columns = columns
        # Drop trailing all-blank padding rows only (never interior rows).
        while len(data) and all(
            str(v).strip() == "" for v in data.iloc[len(data) - 1].tolist()
        ):
            data = data.iloc[: len(data) - 1]
        return data.reset_index(drop=True), header_row
    raise ValueError(
        "tyler ingest failed (fail closed): unsupported file type "
        f"'{suffix}' for '{path.name}' (expected .csv or .xlsx)."
    )


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def _is_blank_cell(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip() == ""


def normalize_tyler_export(
    path: str | Path,
    dataset_type: Optional[str] = None,
    *,
    table_name: Optional[str] = None,
) -> TylerNormalizedExport:
    """Normalize a Munis-style CSV/XLSX export into canonical columns.

    When ``dataset_type`` is None the type is auto-detected (fail closed on
    low confidence). The returned export carries the normalized DataFrame
    (canonical snake_case columns + ``source_row_index``), a fully populated
    ``InputFile`` (sha256 of the raw file), the applied aliases, the header
    row used, and warnings (footer-total rows / repeated header rows are
    flagged, never deleted).
    """
    path = Path(path)
    explicit_type = dataset_type is not None
    if explicit_type and dataset_type not in TYLER_DATASET_TYPES:
        raise ValueError(
            "tyler ingest failed (fail closed): unknown dataset_type "
            f"'{dataset_type}'. Valid types: "
            f"{sorted(TYLER_DATASET_TYPES)}."
        )

    raw, header_row_used = _load_raw_frame(
        path, dataset_type=dataset_type if explicit_type else None
    )

    detection_scores: dict[str, Any] = {}
    if not explicit_type:
        detected, detection_scores = detect_dataset_type(raw)
        if detected is None:
            ranked = sorted(
                detection_scores.items(),
                key=lambda kv: (-kv[1]["score"], kv[0]),
            )
            top = ", ".join(
                f"{t}={s['score']:.2f}" for t, s in ranked[:3]
            )
            raise ValueError(
                "tyler ingest failed (fail closed): could not confidently "
                f"detect the dataset type of '{path.name}' (top scores: "
                f"{top}). Pass dataset_type explicitly; valid types: "
                f"{sorted(TYLER_DATASET_TYPES)}."
            )
        dataset_type = detected
    spec = TYLER_DATASET_TYPES[dataset_type]

    # Snake-case + alias (rename only; never drops or reorders data).
    snake_cols = [to_snake_case(c) for c in raw.columns]
    applied: dict[str, str] = {}
    for src, dst in spec.aliases.items():
        if src in snake_cols and dst not in snake_cols and dst not in applied.values():
            applied[src] = dst
    df = apply_aliases(raw, spec.aliases, normalize=True)

    # Fail closed on missing required canonical columns.
    missing = [c for c in spec.required_columns if c not in df.columns]
    if missing:
        raise ValueError(
            "tyler ingest failed (fail closed): dataset_type "
            f"'{dataset_type}' is missing required column(s) {missing} in "
            f"'{path.name}'. Columns found (normalized): "
            f"{list(df.columns)}. Fix the export's headers or pass the "
            "correct dataset_type."
        )

    warnings: list[str] = []

    # GW-29: warn when an optional alias targets a column not present after aliasing.
    # ponytail: only checks optional columns with at least one alias pointing at them;
    # upgrade path: promote to structured warning objects if callers need to filter by type.
    _alias_targets = {dst for dst in spec.aliases.values()}
    for opt_col in spec.optional_columns:
        if opt_col not in df.columns and opt_col in _alias_targets:
            src_aliases = [s for s, d in spec.aliases.items() if d == opt_col]
            warnings.append(
                f"optional_column_missing: '{opt_col}' not found after aliasing "
                f"(tried: {src_aliases}); column will be absent from the normalized frame."
            )

    # Flag (never delete) repeated header rows and footer/total rows.
    repeated = detect_repeated_header_rows(df)
    if repeated:
        warnings.append(
            f"repeated_header_rows: data rows {repeated} duplicate the "
            "header row; they were kept (review before analysis)."
        )
    footers = detect_footer_total_rows(df)
    if footers:
        warnings.append(
            f"footer_total_rows: data rows {footers} look like total/footer "
            "lines; they were kept (review before analysis)."
        )
    flagged_rows = set(repeated) | set(footers)

    # Traceability column (0-based position within the parsed data region).
    df = df.copy()
    df.insert(0, "source_row_index", range(len(df)))

    # Parse date columns -> datetime.date (None for blanks). Unparseable
    # non-blank cells outside flagged rows fail closed.
    errors: list[str] = []
    for col in spec.date_columns:
        if col not in df.columns:
            continue
        parsed_vals: list[Any] = []
        for pos, value in enumerate(df[col].tolist()):
            if _is_blank_cell(value):
                parsed_vals.append(None)
                continue
            parsed = parse_date(value)
            if parsed is None:
                if pos in flagged_rows:
                    parsed_vals.append(None)
                    continue
                errors.append(f"row {pos}, column '{col}': {value!r}")
                parsed_vals.append(None)
                continue
            parsed_vals.append(parsed)
        df[col] = pd.Series(parsed_vals, dtype=object)
    if errors:
        raise ValueError(
            "tyler ingest failed (fail closed): unparseable date value(s) "
            f"in '{path.name}' ({dataset_type}): "
            + "; ".join(errors[:5])
            + ("; ..." if len(errors) > 5 else "")
        )

    # Parse amount columns -> Decimal (None for blanks).
    for col in spec.amount_columns:
        if col not in df.columns:
            continue
        parsed_vals = []
        for pos, value in enumerate(df[col].tolist()):
            if _is_blank_cell(value):
                parsed_vals.append(None)
                continue
            parsed = parse_amount(value)
            if parsed is None:
                if pos in flagged_rows:
                    parsed_vals.append(None)
                    continue
                errors.append(f"row {pos}, column '{col}': {value!r}")
                parsed_vals.append(None)
                continue
            parsed_vals.append(parsed)
        df[col] = pd.Series(parsed_vals, dtype=object)
    if errors:
        raise ValueError(
            "tyler ingest failed (fail closed): unparseable amount value(s) "
            f"in '{path.name}' ({dataset_type}): "
            + "; ".join(errors[:5])
            + ("; ..." if len(errors) > 5 else "")
        )

    # Derive a signed amount from Debit/Credit (keep the originals).
    if (
        "debit" in spec.amount_columns
        and "credit" in spec.amount_columns
        and "debit" in df.columns
        and "credit" in df.columns
        and "amount" not in df.columns
    ):
        amounts: list[Any] = []
        for debit, credit in zip(df["debit"].tolist(), df["credit"].tolist()):
            if debit is None and credit is None:
                amounts.append(None)
            else:
                amounts.append(
                    (debit or Decimal("0")) - (credit or Decimal("0"))
                )
        df["amount"] = pd.Series(amounts, dtype=object)

    file_type = "csv" if path.suffix.lower() == ".csv" else "excel"
    input_file = InputFile(
        file_id=make_id(),
        file_name=path.name,
        file_type=file_type,
        file_hash=file_sha256(path),
        parser_used=f"tyler_normalizer:{dataset_type}",
        row_count=int(len(df)),
        column_names=[str(c) for c in df.columns],
    )

    return TylerNormalizedExport(
        input_file=input_file,
        dataset_type=dataset_type,
        module=spec.module,
        table_name=table_name or path.stem,
        dataframe=df,
        header_row_used=header_row_used,
        applied_aliases=applied,
        warnings=warnings,
        detection_scores=detection_scores,
    )


# --------------------------------------------------------------------------- #
# Traceability + audit-copy helpers
# --------------------------------------------------------------------------- #
def _serialize_cell(value: Any) -> str:
    """Deterministic string form for export/source-ref values."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def source_ref_for_row(
    export: TylerNormalizedExport,
    row_index: int,
    columns: Optional[list[str]] = None,
) -> SourceRowRef:
    """Build a SourceRowRef for one normalized row (by ``source_row_index``).

    ``columns`` limits the captured values (default: every column). Raises
    ``ValueError`` for an unknown row index or column name.
    """
    df = export.dataframe
    matches = df.index[df["source_row_index"] == row_index].tolist()
    if not matches:
        raise ValueError(
            f"source_ref_for_row: row_index {row_index} not present in "
            f"normalized '{export.table_name}' ({export.dataset_type})."
        )
    row = df.loc[matches[0]]
    selected = list(columns) if columns else [str(c) for c in df.columns]
    unknown = [c for c in selected if c not in df.columns]
    if unknown:
        raise ValueError(
            f"source_ref_for_row: unknown column(s) {unknown} in normalized "
            f"'{export.table_name}' ({export.dataset_type})."
        )
    return SourceRowRef(
        file_id=export.input_file.file_id,
        table_name=export.table_name,
        row_index=int(row_index),
        column_names=selected,
        source_values={c: _serialize_cell(row[c]) for c in selected},
    )


def write_normalized_csv(
    export: TylerNormalizedExport, out_dir: str | Path
) -> Path:
    """Write an audit copy of the normalized frame as CSV.

    Mirrors the ``_normalized_inputs/`` convention of the preset flow: the
    ORIGINAL upload stays the hashed source of record; this copy (named
    ``<table_name>__tyler_<dataset_type>.csv``) is what a workflow may read.
    Decimals and dates are serialized deterministically; ``None`` becomes an
    empty string. Returns the written path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{export.table_name}__tyler_{export.dataset_type}.csv"
    df = export.dataframe.copy()
    for col in df.columns:
        df[col] = [_serialize_cell(v) for v in df[col].tolist()]
    df.to_csv(dest, index=False)
    return dest


__all__ = [
    "TYLER_DATASET_TYPES",
    "TylerDatasetSpec",
    "TylerNormalizedExport",
    "MIN_DETECTION_SCORE",
    "MIN_DETECTION_MARGIN",
    "detect_dataset_type",
    "normalize_tyler_export",
    "source_ref_for_row",
    "write_normalized_csv",
]
