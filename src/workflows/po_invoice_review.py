"""Workflow - PO / Invoice Mismatch Review.

WORKFLOW_TYPE = "po_invoice_review"

Deterministic checks join AP invoice rows to purchase-order lines and flag
every known mismatch pattern (P1-P8). The LLM may ONLY explain flagged issues
and suggest human follow-up - it never recalculates, never declares an invoice
improper, and never invents identifiers.

Inputs dict keys
----------------
purchase_orders   (required) Tyler purchase_orders CSV/XLSX
ap_invoices       (required) Tyler ap_invoice_detail CSV/XLSX
vendor_list       (optional) Tyler vendor_list CSV/XLSX - enables P2 vendor-name
                  similarity note
check_register    (optional) Tyler check_register CSV/XLSX - reserved / pass-through
config            (optional) path to a JSON config file or a dict of overrides

Config keys (all optional; see POInvoiceReviewConfig)
-----------------------------------------------------
unit_price_tolerance_pct    float, default 1.0  (%)
qty_tolerance               int,   default 0    (units)
invoice_over_po_tolerance_pct float, default 0.0 (%)
closed_po_grace_days        int,   default 0
missing_po_min_amount       Decimal-compatible, default 5000.00

Export artifacts
----------------
po_invoice_exceptions.csv   - all flagged findings as a flat table
matched_po_invoices.csv     - clean joins (true negatives, for audit)
po_review_summary.md        - human-readable summary + LLM DRAFT
review_notes_draft.md       - LLM-drafted review notes for each finding category
validation_report.json
audit_log.json
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.core.schemas import (
    CapabilitySpec,
    DeterministicFinding,
    ExportArtifact,
    FileProfile,
    FindingType,
    LLMResponse,
    PreflightConditionCode,
    PreflightFinding,
    Severity,
    SourceRowRef,
    ValidationResult,
    make_id,
)
from src.core.exports import write_csv, write_json, write_markdown
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.llm.provider import MockLLMProvider as _CoreMockLLMProvider
from src.llm.provider import _extract_findings_from_prompt
from src.ingest.tyler import normalize_tyler_export, source_ref_for_row

WORKFLOW_TYPE = "po_invoice_review"
PROMPT_TEMPLATE_VERSION = "po_invoice_review.v1"

WORKFLOW_REGISTRY: dict[str, Any] = {}

EXPORT_FILE_NAMES = (
    "po_invoice_exceptions.csv",
    "matched_po_invoices.csv",
    "po_review_summary.md",
    "review_notes_draft.md",
    "validation_report.json",
    "audit_log.json",
)

# Repo-relative default sample paths for the wiring agent / demo UI.
SAMPLE_INPUTS: dict[str, str] = {
    "purchase_orders": "data/synthetic/tyler/purchase_orders.csv",
    "ap_invoices": "data/synthetic/tyler/ap_invoice_detail.csv",
    "vendor_list": "data/synthetic/tyler/vendor_list.csv",
    "check_register": "data/synthetic/tyler/check_register.csv",
    "config": "data/synthetic/po_invoice_review/match_config.json",
}

_DEFAULT_MISSING_PO_MIN_AMOUNT = Decimal("5000.00")
_VENDOR_SIMILARITY_THRESHOLD = 0.82  # difflib ratio


# --------------------------------------------------------------------------- #
# PREFLIGHT / CAPABILITY layer
# --------------------------------------------------------------------------- #
CAPABILITY = CapabilitySpec(
    workflow_type=WORKFLOW_TYPE,
    required_inputs=["purchase_orders", "ap_invoices"],
    optional_inputs=["vendor_list", "check_register"],
    accepted_file_types={"*": ["csv", "xlsx"]},
    required_semantic_columns={
        "purchase_orders": [
            "po_number", "vendor_number", "status", "line", "qty",
            "unit_price", "line_amount",
        ],
        "ap_invoices": [
            "vendor_number", "invoice_number", "invoice_amount",
        ],
    },
    optional_semantic_columns={
        "purchase_orders": [
            "last_activity_date", "po_date", "received_qty", "invoiced_qty",
        ],
        "ap_invoices": [
            "po_number", "qty", "unit_price", "invoice_date",
        ],
        "vendor_list": ["vendor_number", "vendor_name", "status"],
        "check_register": ["check_number", "check_date", "vendor_number"],
    },
    supported_patterns=[
        "invoice_exceeds_po",
        "wrong_vendor",
        "missing_po_number",
        "closed_po_usage",
        "unit_price_mismatch",
        "quantity_mismatch",
        "received_not_invoiced",
        "invoiced_not_received",
        "missing_po_over_threshold",
    ],
    partially_supported_patterns=[
        "partial_invoicing_across_periods",
    ],
    unsupported_patterns=[
        "multi_currency_po",
        "framework_agreement_releases",
    ],
    notes=(
        "Deterministic PO/invoice mismatch review joining Tyler purchase_orders "
        "and ap_invoice_detail exports. Flags eight mismatch patterns (P1-P8) with "
        "full source-row traceability. Multi-currency POs and framework-agreement "
        "release patterns are not supported."
    ),
)


def detect_conditions(
    profiles: dict[str, FileProfile],
    mappings: dict[str, Any],
    inputs: dict[str, Any],
    config: Optional[dict],
) -> list[PreflightFinding]:
    """Domain-specific unsupported-condition detection for PO/invoice review.

    Conservative: only emits advisory (non-blocking) findings.
    """
    findings: list[PreflightFinding] = []
    po_prof = profiles.get("purchase_orders")
    ap_prof = profiles.get("ap_invoices")
    if not (po_prof and po_prof.present and ap_prof and ap_prof.present):
        return findings
    return findings


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class POInvoiceReviewConfig:
    """Tolerance/threshold config. All deterministic, no LLM input."""

    unit_price_tolerance_pct: float = 1.0
    qty_tolerance: int = 0
    invoice_over_po_tolerance_pct: float = 0.0
    closed_po_grace_days: int = 0
    missing_po_min_amount: Decimal = _DEFAULT_MISSING_PO_MIN_AMOUNT

    @classmethod
    def from_config(cls, cfg: Any) -> "POInvoiceReviewConfig":
        if cfg is None:
            return cls()
        if isinstance(cfg, POInvoiceReviewConfig):
            return cfg
        if isinstance(cfg, (str, Path)):
            raw = Path(cfg).read_text(encoding="utf-8")
            cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            return cls()
        return cls(
            unit_price_tolerance_pct=float(
                cfg.get("unit_price_tolerance_pct", 1.0)
            ),
            qty_tolerance=int(cfg.get("qty_tolerance", 0)),
            invoice_over_po_tolerance_pct=float(
                cfg.get("invoice_over_po_tolerance_pct", 0.0)
            ),
            closed_po_grace_days=int(cfg.get("closed_po_grace_days", 0)),
            missing_po_min_amount=Decimal(
                str(cfg.get("missing_po_min_amount", "5000.00"))
            ),
        )


# --------------------------------------------------------------------------- #
# Deterministic output container
# --------------------------------------------------------------------------- #
@dataclass
class POInvoiceReviewOutput:
    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    result_tables: dict[str, pd.DataFrame] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _short_ref(ref: SourceRowRef) -> str:
    return f"{ref.table_name}:{ref.row_index}"


def _safe_decimal(value: Any) -> Optional[Decimal]:
    """Parse a value (str/int/float/Decimal/None) to Decimal, or None."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        s = str(value).strip().replace(",", "").replace("$", "").strip()
        if not s:
            return None
        return Decimal(s)
    except Exception:
        return None


def _safe_date(value: Any) -> Optional[Any]:
    """Return a date-like value from datetime.date / pd.Timestamp / str, or None."""
    import datetime
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value
    if hasattr(value, "date"):
        try:
            return value.date()
        except Exception:
            pass
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except Exception:
        return None


def _strip_legal_suffix(name: str) -> str:
    """Remove common legal suffixes for fuzzy vendor-name comparison."""
    for suffix in (
        " llc", " inc", " inc.", " co", " co.", " corp", " corp.",
        " company", " ltd", " ltd.", " lp", " llp", " pllc",
    ):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)].strip()
    return name.strip()


def _vendor_similarity(name_a: str, name_b: str) -> float:
    """difflib ratio on stripped, lowercased vendor names."""
    a = _strip_legal_suffix(name_a.lower())
    b = _strip_legal_suffix(name_b.lower())
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# Core deterministic checks
# --------------------------------------------------------------------------- #
def run_deterministic(
    po_path: Any,
    ap_path: Any,
    *,
    vendor_list_path: Optional[Any] = None,
    check_register_path: Optional[Any] = None,
    config: Optional[POInvoiceReviewConfig] = None,
) -> POInvoiceReviewOutput:
    """Run all P1-P8 deterministic PO/invoice mismatch checks.

    Uses normalize_tyler_export for both the PO and AP invoice files so that
    file SHA-256 hashing and source_row_index traceability come for free.
    """
    cfg = config or POInvoiceReviewConfig()

    # --- Load / normalize inputs ---------------------------------------- #
    po_export = normalize_tyler_export(po_path, dataset_type="purchase_orders")
    ap_export = normalize_tyler_export(ap_path, dataset_type="ap_invoice_detail")
    po_df = po_export.dataframe.copy()
    ap_df = ap_export.dataframe.copy()

    vendor_export = None
    vendor_map: dict[str, str] = {}   # vendor_number -> vendor_name
    vendor_status: dict[str, str] = {}  # vendor_number -> status
    if vendor_list_path is not None:
        vendor_export = normalize_tyler_export(
            vendor_list_path, dataset_type="vendor_list"
        )
        vdf = vendor_export.dataframe
        for _, vrow in vdf.iterrows():
            vnum = str(vrow.get("vendor_number", "")).strip()
            if vnum:
                vendor_map[vnum] = str(vrow.get("vendor_name", "")).strip()
                vendor_status[vnum] = str(vrow.get("status", "")).strip()

    findings: list[DeterministicFinding] = []
    exception_rows: list[dict] = []
    matched_rows: list[dict] = []

    # Build PO lookup: po_number -> list of row dicts (lines)
    po_lines_by_number: dict[str, list[dict]] = {}
    for _, po_row in po_df.iterrows():
        po_num = str(po_row.get("po_number", "")).strip()
        if po_num:
            po_lines_by_number.setdefault(po_num, []).append(po_row.to_dict())

    # Build PO total per PO number (sum of line_amount across all lines)
    po_total_by_number: dict[str, Decimal] = {}
    for po_num, lines in po_lines_by_number.items():
        total = Decimal(0)
        for ln in lines:
            amt = _safe_decimal(ln.get("line_amount"))
            if amt is not None:
                total += amt
        po_total_by_number[po_num] = total

    # Build running total of AP invoiced per PO number (for P1)
    ap_invoiced_by_po: dict[str, Decimal] = {}
    for _, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if po_num:
            amt = _safe_decimal(ap_row.get("invoice_amount"))
            if amt is not None:
                ap_invoiced_by_po[po_num] = (
                    ap_invoiced_by_po.get(po_num, Decimal(0)) + amt
                )

    # Track which AP rows are flagged (for matched vs exception split)
    flagged_ap_indices: set[int] = set()
    # Track which PO lines are flagged (for P7)
    flagged_po_indices: set[int] = set()

    # -------------------------------------------------------------------- #
    # P1: invoice_exceeds_po
    # -------------------------------------------------------------------- #
    tolerance_pct = Decimal(str(cfg.invoice_over_po_tolerance_pct)) / Decimal("100")
    po_nums_processed_p1: set[str] = set()
    for _, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if not po_num or po_num in po_nums_processed_p1:
            continue
        if po_num not in po_lines_by_number:
            continue  # P3 covers missing PO
        po_nums_processed_p1.add(po_num)
        po_total = po_total_by_number.get(po_num, Decimal(0))
        invoiced = ap_invoiced_by_po.get(po_num, Decimal(0))
        allowed = po_total * (Decimal(1) + tolerance_pct)
        if invoiced > allowed:
            # Collect all AP rows for this PO
            ap_refs: list[SourceRowRef] = []
            for ap_idx, ap_r in ap_df.iterrows():
                if str(ap_r.get("po_number", "")).strip() == po_num:
                    ap_refs.append(source_ref_for_row(ap_export, int(ap_r["source_row_index"])))
                    flagged_ap_indices.add(int(ap_idx))
            # Collect all PO rows for this PO number
            po_refs: list[SourceRowRef] = []
            for po_idx, po_r in po_df.iterrows():
                if str(po_r.get("po_number", "")).strip() == po_num:
                    po_refs.append(source_ref_for_row(po_export, int(po_r["source_row_index"])))
                    flagged_po_indices.add(int(po_idx))
            diff = invoiced - po_total
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.PO_MISMATCH,
                    severity=Severity.HIGH,
                    description=(
                        f"Total invoiced against {po_num} is {invoiced} "
                        f"which exceeds the PO total {po_total} "
                        f"(overage {diff}; tolerance {cfg.invoice_over_po_tolerance_pct}%)."
                    ),
                    source_rows=ap_refs + po_refs,
                    computed_values={
                        "po_number": po_num,
                        "po_total": str(po_total),
                        "total_invoiced": str(invoiced),
                        "overage": str(diff),
                        "tolerance_pct": str(cfg.invoice_over_po_tolerance_pct),
                        "comparison_level": "po_total",
                    },
                    rule_used="invoice_exceeds_po",
                    requires_human_review=True,
                )
            )
            for r in ap_refs + po_refs:
                exception_rows.append(_finding_row("P1", "invoice_exceeds_po", findings[-1]))

    # -------------------------------------------------------------------- #
    # P2: wrong_vendor
    # -------------------------------------------------------------------- #
    for ap_idx, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if not po_num or po_num not in po_lines_by_number:
            continue
        ap_vendor = str(ap_row.get("vendor_number", "")).strip()
        po_vendors = {
            str(ln.get("vendor_number", "")).strip()
            for ln in po_lines_by_number[po_num]
        }
        if ap_vendor and po_vendors and ap_vendor not in po_vendors:
            po_vendor_num = next(iter(po_vendors))
            # Optional: note if names are similar (possible mis-keyed vendor)
            sim_note = ""
            if vendor_map and ap_vendor in vendor_map and po_vendor_num in vendor_map:
                sim = _vendor_similarity(
                    vendor_map[ap_vendor], vendor_map[po_vendor_num]
                )
                if sim >= _VENDOR_SIMILARITY_THRESHOLD:
                    sim_note = (
                        f" (Note: vendor names are similar - "
                        f"'{vendor_map[ap_vendor]}' vs '{vendor_map[po_vendor_num]}'"
                        f", similarity {sim:.2f} - possible mis-keyed vendor.)"
                    )
            ap_ref = source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
            po_refs = [
                source_ref_for_row(po_export, int(po_df.loc[po_idx, "source_row_index"]))
                for po_idx in po_df.index
                if str(po_df.loc[po_idx, "po_number"]).strip() == po_num
            ]
            flagged_ap_indices.add(int(ap_idx))
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.PO_MISMATCH,
                    severity=Severity.HIGH,
                    description=(
                        f"Invoice {ap_row.get('invoice_number', '')} "
                        f"(vendor {ap_vendor}) references PO {po_num} "
                        f"which was issued to vendor {po_vendor_num}.{sim_note}"
                    ),
                    source_rows=[ap_ref] + po_refs,
                    computed_values={
                        "po_number": po_num,
                        "invoice_number": str(ap_row.get("invoice_number", "")),
                        "invoice_vendor_number": ap_vendor,
                        "po_vendor_number": po_vendor_num,
                        "similarity_note": sim_note,
                    },
                    rule_used="wrong_vendor",
                    requires_human_review=True,
                )
            )
            exception_rows.append(_finding_row("P2", "wrong_vendor", findings[-1]))

    # -------------------------------------------------------------------- #
    # P3a: missing_po (invoice references a PO that does not exist in PO file)
    # -------------------------------------------------------------------- #
    for ap_idx, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if not po_num:
            continue
        if po_num not in po_lines_by_number:
            ap_ref = source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
            flagged_ap_indices.add(int(ap_idx))
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.MISSING_REFERENCE,
                    severity=Severity.HIGH,
                    description=(
                        f"Invoice {ap_row.get('invoice_number', '')} "
                        f"references PO {po_num} which does not exist "
                        f"in the purchase orders file."
                    ),
                    source_rows=[ap_ref],
                    computed_values={
                        "invoice_number": str(ap_row.get("invoice_number", "")),
                        "po_number": po_num,
                        "vendor_number": str(ap_row.get("vendor_number", "")),
                        "invoice_amount": str(ap_row.get("invoice_amount", "")),
                    },
                    rule_used="missing_po",
                    requires_human_review=True,
                )
            )
            exception_rows.append(_finding_row("P3", "missing_po", findings[-1]))

    # -------------------------------------------------------------------- #
    # P3b: missing_po_over_threshold (blank PO with amount >= threshold)
    # -------------------------------------------------------------------- #
    for ap_idx, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if po_num:
            continue  # only blank PO rows
        amt = _safe_decimal(ap_row.get("invoice_amount"))
        if amt is None or amt < cfg.missing_po_min_amount:
            continue
        ap_ref = source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
        flagged_ap_indices.add(int(ap_idx))
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.MISSING_REFERENCE,
                severity=Severity.MEDIUM,
                description=(
                    f"Invoice {ap_row.get('invoice_number', '')} "
                    f"(amount {amt}) has no PO number and exceeds the "
                    f"required-PO threshold of {cfg.missing_po_min_amount}."
                ),
                source_rows=[ap_ref],
                computed_values={
                    "invoice_number": str(ap_row.get("invoice_number", "")),
                    "vendor_number": str(ap_row.get("vendor_number", "")),
                    "invoice_amount": str(amt),
                    "threshold": str(cfg.missing_po_min_amount),
                    "po_number": "",
                },
                rule_used="missing_po_over_threshold",
                requires_human_review=True,
            )
        )
        exception_rows.append(
            _finding_row("P3b", "missing_po_over_threshold", findings[-1])
        )

    # -------------------------------------------------------------------- #
    # P4: closed_po_usage
    # -------------------------------------------------------------------- #
    import datetime
    for ap_idx, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if not po_num or po_num not in po_lines_by_number:
            continue
        po_lines = po_lines_by_number[po_num]
        po_status = str(po_lines[0].get("status", "")).strip()
        if po_status.lower() != "closed":
            continue
        last_activity_raw = po_lines[0].get("last_activity_date")
        last_activity = _safe_date(last_activity_raw)
        invoice_date = _safe_date(ap_row.get("invoice_date"))
        if last_activity is None or invoice_date is None:
            continue
        grace = datetime.timedelta(days=cfg.closed_po_grace_days)
        cutoff = last_activity + grace
        if invoice_date > cutoff:
            ap_ref = source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
            po_refs = [
                source_ref_for_row(po_export, int(po_df.loc[po_idx, "source_row_index"]))
                for po_idx in po_df.index
                if str(po_df.loc[po_idx, "po_number"]).strip() == po_num
            ]
            flagged_ap_indices.add(int(ap_idx))
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.PO_MISMATCH,
                    severity=Severity.MEDIUM,
                    description=(
                        f"Invoice {ap_row.get('invoice_number', '')} dated "
                        f"{invoice_date} bills against PO {po_num} which has "
                        f"Status=Closed (last activity {last_activity}). "
                        f"Invoice date exceeds last activity + grace period "
                        f"({cfg.closed_po_grace_days} days)."
                    ),
                    source_rows=[ap_ref] + po_refs,
                    computed_values={
                        "po_number": po_num,
                        "invoice_number": str(ap_row.get("invoice_number", "")),
                        "invoice_date": str(invoice_date),
                        "po_status": po_status,
                        "last_activity_date": str(last_activity),
                        "grace_days": str(cfg.closed_po_grace_days),
                    },
                    rule_used="closed_po_usage",
                    requires_human_review=True,
                )
            )
            exception_rows.append(_finding_row("P4", "closed_po_usage", findings[-1]))

    # -------------------------------------------------------------------- #
    # P5 and P6: line-level unit_price_mismatch and quantity_mismatch
    # -------------------------------------------------------------------- #
    # For invoices that carry qty and unit_price, compare against PO line.
    # Match by PO number; when the invoice line count matches PO we do line-by-
    # line; otherwise we use the first matching-qty or first-line heuristic.
    up_tol = Decimal(str(cfg.unit_price_tolerance_pct)) / Decimal("100")

    for ap_idx, ap_row in ap_df.iterrows():
        po_num = str(ap_row.get("po_number", "")).strip()
        if not po_num or po_num not in po_lines_by_number:
            continue
        inv_qty = _safe_decimal(ap_row.get("qty"))
        inv_up = _safe_decimal(ap_row.get("unit_price"))
        if inv_qty is None or inv_up is None:
            continue  # no line-detail; PO-total comparison applies

        po_lines = po_lines_by_number[po_num]
        # Find the best-matching PO line for this invoice row
        # (prefer matching qty, else use line 1)
        best_po_line = None
        best_po_df_idx: Optional[int] = None
        for po_df_idx, po_r in po_df.iterrows():
            if str(po_r.get("po_number", "")).strip() != po_num:
                continue
            po_line_qty = _safe_decimal(po_r.get("qty"))
            if po_line_qty is not None and po_line_qty == inv_qty:
                best_po_line = po_r.to_dict()
                best_po_df_idx = int(po_df_idx)
                break
        if best_po_line is None:
            # Fall back to line 1
            for po_df_idx, po_r in po_df.iterrows():
                if str(po_r.get("po_number", "")).strip() != po_num:
                    continue
                best_po_line = po_r.to_dict()
                best_po_df_idx = int(po_df_idx)
                break

        if best_po_line is None:
            continue

        po_up = _safe_decimal(best_po_line.get("unit_price"))
        po_qty = _safe_decimal(best_po_line.get("qty"))

        if po_up is not None and inv_up is not None and po_up != Decimal(0):
            diff_pct = abs(inv_up - po_up) / po_up
            if diff_pct > up_tol:
                # P5: unit_price_mismatch (qty must be equal or we can't isolate)
                ap_ref = source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
                po_ref = source_ref_for_row(
                    po_export,
                    int(po_df.loc[best_po_df_idx, "source_row_index"]),
                )
                flagged_ap_indices.add(int(ap_idx))
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.PO_MISMATCH,
                        severity=Severity.MEDIUM,
                        description=(
                            f"Invoice {ap_row.get('invoice_number', '')} unit "
                            f"price {inv_up} differs from PO {po_num} line "
                            f"{best_po_line.get('line', '?')} unit price {po_up} "
                            f"(deviation {diff_pct * 100:.2f}%, tolerance "
                            f"{cfg.unit_price_tolerance_pct}%)."
                        ),
                        source_rows=[ap_ref, po_ref],
                        computed_values={
                            "po_number": po_num,
                            "invoice_number": str(ap_row.get("invoice_number", "")),
                            "po_line": str(best_po_line.get("line", "")),
                            "invoice_unit_price": str(inv_up),
                            "po_unit_price": str(po_up),
                            "deviation_pct": f"{float(diff_pct * 100):.2f}",
                            "tolerance_pct": str(cfg.unit_price_tolerance_pct),
                            "comparison_level": "line_level",
                        },
                        rule_used="unit_price_mismatch",
                        requires_human_review=True,
                    )
                )
                exception_rows.append(
                    _finding_row("P5", "unit_price_mismatch", findings[-1])
                )

        if po_qty is not None and inv_qty is not None:
            qty_over = inv_qty - po_qty
            if qty_over > cfg.qty_tolerance:
                ap_ref = source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
                po_ref = source_ref_for_row(
                    po_export,
                    int(po_df.loc[best_po_df_idx, "source_row_index"]),
                )
                flagged_ap_indices.add(int(ap_idx))
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.PO_MISMATCH,
                        severity=Severity.MEDIUM,
                        description=(
                            f"Invoice {ap_row.get('invoice_number', '')} qty "
                            f"{inv_qty} exceeds PO {po_num} line "
                            f"{best_po_line.get('line', '?')} ordered qty {po_qty} "
                            f"(overage {qty_over}, tolerance {cfg.qty_tolerance})."
                        ),
                        source_rows=[ap_ref, po_ref],
                        computed_values={
                            "po_number": po_num,
                            "invoice_number": str(ap_row.get("invoice_number", "")),
                            "po_line": str(best_po_line.get("line", "")),
                            "invoice_qty": str(inv_qty),
                            "po_qty": str(po_qty),
                            "overage": str(qty_over),
                            "qty_tolerance": str(cfg.qty_tolerance),
                            "comparison_level": "line_level",
                        },
                        rule_used="quantity_mismatch",
                        requires_human_review=True,
                    )
                )
                exception_rows.append(
                    _finding_row("P6", "quantity_mismatch", findings[-1])
                )

    # -------------------------------------------------------------------- #
    # P7: received_not_invoiced
    # -------------------------------------------------------------------- #
    # PO line received_qty > 0 and invoiced_qty == 0, with no matching AP invoice
    po_invoiced_invoice_numbers: set[str] = set()
    for _, ap_row in ap_df.iterrows():
        po_invoiced_invoice_numbers.add(str(ap_row.get("invoice_number", "")).strip())

    for po_idx, po_row in po_df.iterrows():
        received = _safe_decimal(po_row.get("received_qty"))
        invoiced = _safe_decimal(po_row.get("invoiced_qty"))
        if received is None or received <= 0:
            continue
        if invoiced is not None and invoiced > 0:
            continue  # already has invoice qty
        # Check no AP invoice row references this PO number + vendor
        po_num = str(po_row.get("po_number", "")).strip()
        po_vendor = str(po_row.get("vendor_number", "")).strip()
        has_ap_invoice = any(
            str(ap_row.get("po_number", "")).strip() == po_num
            and str(ap_row.get("vendor_number", "")).strip() == po_vendor
            for _, ap_row in ap_df.iterrows()
        )
        if not has_ap_invoice:
            po_ref = source_ref_for_row(po_export, int(po_row["source_row_index"]))
            flagged_po_indices.add(int(po_idx))
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.PO_MISMATCH,
                    severity=Severity.LOW,
                    description=(
                        f"PO {po_num} line {po_row.get('line', '?')} "
                        f"(vendor {po_vendor}) has received_qty {received} "
                        f"but invoiced_qty {invoiced or 0} and no matching "
                        f"AP invoice. This may be an accrual candidate."
                    ),
                    source_rows=[po_ref],
                    computed_values={
                        "po_number": po_num,
                        "po_line": str(po_row.get("line", "")),
                        "vendor_number": po_vendor,
                        "received_qty": str(received),
                        "invoiced_qty": str(invoiced or 0),
                        "accrual_candidate": "true",
                        "comparison_level": "line_level",
                    },
                    rule_used="received_not_invoiced",
                    requires_human_review=False,
                )
            )
            exception_rows.append(
                _finding_row("P7", "received_not_invoiced", findings[-1])
            )

    # -------------------------------------------------------------------- #
    # P8: invoiced_not_received
    # -------------------------------------------------------------------- #
    # Either: PO line invoiced_qty > received_qty, or AP invoice qty > received_qty
    for po_idx, po_row in po_df.iterrows():
        po_num = str(po_row.get("po_number", "")).strip()
        po_vendor = str(po_row.get("vendor_number", "")).strip()
        received = _safe_decimal(po_row.get("received_qty"))
        po_invoiced = _safe_decimal(po_row.get("invoiced_qty"))
        if received is None or po_invoiced is None:
            continue
        if po_invoiced <= received:
            continue
        # Find the relevant AP rows
        ap_refs: list[SourceRowRef] = []
        for ap_idx_inner, ap_row in ap_df.iterrows():
            if str(ap_row.get("po_number", "")).strip() == po_num:
                ap_refs.append(
                    source_ref_for_row(ap_export, int(ap_row["source_row_index"]))
                )
                flagged_ap_indices.add(int(ap_idx_inner))
        po_ref = source_ref_for_row(po_export, int(po_row["source_row_index"]))
        flagged_po_indices.add(int(po_idx))
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.PO_MISMATCH,
                severity=Severity.MEDIUM,
                description=(
                    f"PO {po_num} line {po_row.get('line', '?')} "
                    f"(vendor {po_vendor}): invoiced_qty {po_invoiced} "
                    f"exceeds received_qty {received} "
                    f"(difference {po_invoiced - received})."
                ),
                source_rows=[po_ref] + ap_refs,
                computed_values={
                    "po_number": po_num,
                    "po_line": str(po_row.get("line", "")),
                    "vendor_number": po_vendor,
                    "received_qty": str(received),
                    "invoiced_qty": str(po_invoiced),
                    "overage": str(po_invoiced - received),
                    "comparison_level": "line_level",
                },
                rule_used="invoiced_not_received",
                requires_human_review=True,
            )
        )
        exception_rows.append(
            _finding_row("P8", "invoiced_not_received", findings[-1])
        )

    # -------------------------------------------------------------------- #
    # Skipped-check INFO findings (vendor_list absent)
    # -------------------------------------------------------------------- #
    if vendor_export is None:
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.OTHER,
                severity=Severity.INFO,
                description=(
                    "vendor_list not provided; P2 vendor-name similarity notes "
                    "and vendor status checks are skipped."
                ),
                source_rows=[],
                computed_values={"skipped_check": "vendor_similarity_note"},
                rule_used="vendor_list_skipped",
                requires_human_review=False,
            )
        )
    if check_register_path is None:
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.OTHER,
                severity=Severity.INFO,
                description=(
                    "check_register not provided; payment-date cross-checks "
                    "against the PO/invoice review are skipped."
                ),
                source_rows=[],
                computed_values={"skipped_check": "check_register"},
                rule_used="check_register_skipped",
                requires_human_review=False,
            )
        )

    # -------------------------------------------------------------------- #
    # Build matched (clean join) table
    # -------------------------------------------------------------------- #
    for ap_idx, ap_row in ap_df.iterrows():
        if int(ap_idx) in flagged_ap_indices:
            continue
        po_num = str(ap_row.get("po_number", "")).strip()
        if not po_num or po_num not in po_lines_by_number:
            continue
        matched_rows.append(
            {
                "po_number": po_num,
                "invoice_number": str(ap_row.get("invoice_number", "")),
                "vendor_number": str(ap_row.get("vendor_number", "")),
                "invoice_amount": str(ap_row.get("invoice_amount", "")),
                "ap_source_row": int(ap_row.get("source_row_index", ap_idx)),
            }
        )

    summary = _build_summary(findings, po_df, ap_df)
    result_tables = {
        "exceptions": pd.DataFrame(exception_rows),
        "matched": pd.DataFrame(matched_rows),
    }
    return POInvoiceReviewOutput(
        findings=findings, summary=summary, result_tables=result_tables
    )


def _finding_row(code: str, rule: str, f: DeterministicFinding) -> dict:
    """Flatten a finding to one row for po_invoice_exceptions.csv."""
    return {
        "finding_code": code,
        "finding_id": f.finding_id,
        "rule_used": rule,
        "finding_type": f.finding_type.value,
        "severity": f.severity.value,
        "description": f.description,
        "source_rows": ";".join(_short_ref(s) for s in f.source_rows),
        "computed_values": json.dumps(f.computed_values, default=str),
        "requires_human_review": f.requires_human_review,
    }


def _build_summary(
    findings: list[DeterministicFinding],
    po_df: pd.DataFrame,
    ap_df: pd.DataFrame,
) -> dict[str, Any]:
    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule_used] = by_rule.get(f.rule_used, 0) + 1
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1
    return {
        "workflow_type": WORKFLOW_TYPE,
        "total_findings": len(findings),
        "findings_by_rule": by_rule,
        "findings_by_severity": by_severity,
        "po_rows": len(po_df),
        "ap_rows": len(ap_df),
        "requires_human_review": any(f.requires_human_review for f in findings),
    }


# --------------------------------------------------------------------------- #
# LLM prompt (advisory only - references deterministic findings + source rows)
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY explain flagged PO/invoice issues, suggest human follow-up "
    "steps (e.g. 'confirm receipt with department'), and draft review notes.\n"
    "- You MUST NOT recalculate, decide that an invoice is improper, or invent "
    "identifiers, amounts, dates, or PO numbers.\n"
    "- Use ONLY numbers and source-row ids that appear in the findings below.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- All output is a DRAFT for human review. Never produce approval language.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def _findings_block(det: POInvoiceReviewOutput) -> str:
    rows = []
    for f in det.findings:
        rows.append(
            {
                "finding_id": f.finding_id,
                "finding_type": f.finding_type.value,
                "rule_used": f.rule_used,
                "severity": f.severity.value,
                "description": f.description,
                "computed_values": f.computed_values,
                "source_row_ids": [_short_ref(s) for s in f.source_rows],
            }
        )
    return json.dumps(rows, default=str, indent=2)


def build_prompt(det: POInvoiceReviewOutput) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: PO / invoice mismatch review.\n"
        + "TASK: Summarize the flagged PO/invoice mismatches, draft plain-language "
        "review notes for each exception category, suggest human follow-up steps "
        "(e.g. 'confirm receipt with department'), and draft a review memo. "
        "Do NOT recalculate, decide an invoice is improper, or produce approval "
        "language.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM (DEFAULT path; no API key, no internet)
# --------------------------------------------------------------------------- #
class MockLLMProvider(_CoreMockLLMProvider):
    """Deterministic mock for PO/invoice review.

    Derives output ONLY from the deterministic findings; cites real source-row
    ids; never invents data.
    """

    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        exception_types = {
            FindingType.PO_MISMATCH.value,
            FindingType.MISSING_REFERENCE.value,
        }
        exceptions = [f for f in findings if f.get("finding_type") in exception_types]
        ref_ids: list[str] = []
        categorized = []
        for f in exceptions:
            refs = f.get("source_row_ids", [])
            ref_ids.extend(refs)
            rule = f.get("rule_used", "other")
            categorized.append(
                {
                    "category": rule,
                    "description": f.get("description", ""),
                    "referenced_source_rows": refs,
                }
            )
        return {
            "summary": (
                f"Deterministic PO/invoice review flagged {len(exceptions)} "
                "exception(s) for human review. See the categorized list; each "
                "item cites the source rows that triggered it."
            ),
            "categorized_exceptions": categorized,
            "referenced_source_rows": sorted(set(ref_ids)),
            "suggested_review_steps": [
                "Confirm each flagged invoice against the purchase order on file.",
                "Contact the relevant department to verify goods/services receipt.",
                "Verify closed PO invoices with the department that issued the PO.",
                "Obtain missing PO numbers from the originating department for "
                "invoices over threshold.",
            ],
            "draft_memo": (
                "DRAFT -- for human review only. The automated PO/invoice "
                "mismatch review identified items requiring finance staff "
                "confirmation before payment can be finalized. No invoice was "
                "declared improper by the assistant."
            ),
        }


def mock_llm_response(det: POInvoiceReviewOutput) -> dict[str, Any]:
    """Build a mock response directly from findings (no prompt round-trip)."""
    exception_types = {FindingType.PO_MISMATCH.value, FindingType.MISSING_REFERENCE.value}
    refs: list[str] = []
    categorized = []
    for f in det.findings:
        f_refs = [_short_ref(s) for s in f.source_rows]
        refs.extend(f_refs)
        if f.finding_type.value in exception_types:
            categorized.append(
                {
                    "category": f.rule_used,
                    "description": f.description,
                    "referenced_source_rows": f_refs,
                }
            )
    refs = sorted(set(refs))
    n_exc = sum(
        1 for f in det.findings if f.finding_type.value in exception_types
    )
    return {
        "summary": (
            f"Deterministic PO/invoice review flagged {n_exc} exception(s) "
            f"for human review. See the categorized list; each item references "
            f"the source rows that triggered it."
        ),
        "categorized_exceptions": categorized,
        "referenced_source_rows": refs,
        "suggested_review_steps": [
            "Confirm each flagged invoice against the purchase order on file.",
            "Contact the relevant department to verify goods/services receipt.",
            "Verify closed PO invoices with the department that issued the PO.",
            "Obtain missing PO numbers from the originating department for "
            "invoices over threshold.",
        ],
        "draft_memo": (
            "DRAFT -- for human review only. The automated PO/invoice mismatch "
            "review identified items requiring finance staff confirmation before "
            "payment can be finalized. No invoice was declared improper by the "
            "assistant."
        ),
    }


def _call_llm(
    det: POInvoiceReviewOutput, provider: Any = None
) -> tuple[dict[str, Any], str, str]:
    """Return (response_json, model_provider, model_name).

    Falls back to the local mock when provider is None (default path).
    """
    if provider is not None:
        prompt = build_prompt(det)
        for meth in ("generate_structured_response", "mock_response"):
            fn = getattr(provider, meth, None)
            if callable(fn):
                try:
                    resp = fn(prompt, schema=None)
                except TypeError:
                    resp = fn(prompt)
                data = getattr(resp, "response_json", resp)
                if isinstance(data, str):
                    data = json.loads(data)
                p = getattr(provider, "model_provider", "mock")
                m = getattr(provider, "model_name", "mock")
                return data, str(p), str(m)
    return mock_llm_response(det), "mock", "mock-po-invoice-review"


# --------------------------------------------------------------------------- #
# Validation (deterministic guardrail check)
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: dict, det: POInvoiceReviewOutput
) -> ValidationResult:
    """Thin wrapper: delegates to the canonical validator.

    Missing source references are treated as warnings (not errors) because
    INFO/skip findings carry no source rows.
    """
    return _core_validate_llm_output(
        response_json,
        det,
        require_references=True,
        missing_references_is_error=False,
        check_numeric_claims=False,
    )


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def export_artifacts(
    out_dir: str | Path,
    det: POInvoiceReviewOutput,
    response_json: dict,
    validation: ValidationResult,
    audit_events: Optional[list[dict]] = None,
    *,
    run_id: Optional[str] = None,
) -> list[ExportArtifact]:
    """Write the six PO/invoice review artifacts. Returns artifact manifests."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportArtifact] = []

    def _write_md(name: str, text: str) -> None:
        artifacts.append(write_markdown(out / name, text, run_id=run_id))

    def _write_json(name: str, text: str) -> None:
        artifacts.append(write_json(out / name, text, run_id=run_id))

    def _write_csv(name: str, df: pd.DataFrame) -> None:
        artifacts.append(write_csv(out / name, df, run_id=run_id))

    # po_invoice_exceptions.csv
    exc_df = det.result_tables.get("exceptions", pd.DataFrame())
    _write_csv("po_invoice_exceptions.csv", exc_df)

    # matched_po_invoices.csv
    matched_df = det.result_tables.get("matched", pd.DataFrame())
    _write_csv("matched_po_invoices.csv", matched_df)

    # po_review_summary.md
    s = det.summary
    exception_findings = [
        f for f in det.findings
        if f.finding_type in (FindingType.PO_MISMATCH, FindingType.MISSING_REFERENCE)
    ]
    summary_lines = [
        "# PO / Invoice Mismatch Review Summary",
        "",
        f"- PO rows: {s.get('po_rows', 0)}",
        f"- AP invoice rows: {s.get('ap_rows', 0)}",
        f"- Total findings: {s.get('total_findings', 0)}",
        f"- Exception findings (high/medium/low): {len(exception_findings)}",
        "",
        "## Findings by rule",
        "",
    ]
    for rule, cnt in sorted(s.get("findings_by_rule", {}).items()):
        summary_lines.append(f"- {rule}: {cnt}")
    summary_lines += [
        "",
        "## AI Summary (DRAFT -- human review required)",
        str(response_json.get("summary", "")),
        "",
        "## Flagged exceptions",
        "",
    ]
    for f in exception_findings:
        refs = ", ".join(_short_ref(s_ref) for s_ref in f.source_rows) or "(none)"
        summary_lines.append(
            f"- **[{f.severity.value}] {f.rule_used}** -- {f.description} "
            f"(source rows: {refs})"
        )
    summary_lines += [
        "",
        "## Review memo (DRAFT)",
        str(response_json.get("draft_memo", "")),
        "",
        "_All matching and figures computed deterministically. AI commentary is "
        "a draft for human review._",
    ]
    _write_md("po_review_summary.md", "\n".join(summary_lines))

    # review_notes_draft.md
    notes_lines = ["# PO / Invoice Review Notes (DRAFT)", ""]
    steps = response_json.get("suggested_review_steps") or []
    for step in steps:
        notes_lines.append(f"- [ ] {step}")
    notes_lines.append("")
    notes_lines.append("## Exception-specific notes")
    for exc in response_json.get("categorized_exceptions") or []:
        notes_lines.append(
            f"- [ ] **{exc.get('category', '')}**: {exc.get('description', '')}"
        )
    _write_md("review_notes_draft.md", "\n".join(notes_lines))

    # validation_report.json
    _write_json(
        "validation_report.json",
        json.dumps(validation.model_dump(), default=str, indent=2),
    )

    # audit_log.json
    _write_json(
        "audit_log.json",
        json.dumps(audit_events or [], default=str, indent=2),
    )

    return artifacts


# --------------------------------------------------------------------------- #
# Preflight integration helper
# --------------------------------------------------------------------------- #
def _run_preflight(inputs: dict[str, Any], config_dict: Optional[dict]) -> Any:
    """Run the shared preflight engine for this workflow.

    Returns the PreflightReport (never raises).
    """
    from src.core.preflight import run_preflight as _engine_preflight
    return _engine_preflight(
        CAPABILITY,
        inputs,
        config=config_dict,
        detect_conditions=detect_conditions,
    )


# --------------------------------------------------------------------------- #
# Workflow entry point
# --------------------------------------------------------------------------- #
def run(
    inputs: dict[str, Any],
    *,
    provider: Any = None,
    ledger: Any = None,
    audit: Any = None,
    run_id: Optional[str] = None,
    actor: str = "system",
    export_dir: str | Path | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """End-to-end PO/invoice mismatch review run.

    inputs keys:
        purchase_orders     (required) path to Tyler purchase_orders CSV/XLSX
        ap_invoices         (required) path to Tyler ap_invoice_detail CSV/XLSX
        vendor_list         (optional) path to Tyler vendor_list CSV/XLSX
        check_register      (optional) path to Tyler check_register CSV/XLSX
        config              (optional) path to a JSON config file or a dict

    When provider is None the local mock LLM is used (default offline path).
    When ledger / audit are provided their findings/responses/validation/events
    are persisted via the shared method names. Returns a dict:
        run_id, workflow_type, summary, findings, validation, preflight,
        export_paths (when export_dir is set).
    """
    from src.core.schemas import PreflightStatus
    from src.core.review_packet import generate_failed_preflight_packet

    run_id = run_id or make_id()

    # Resolve config
    if config is None:
        config = inputs.get("config")
    cfg = POInvoiceReviewConfig.from_config(config)
    config_dict: Optional[dict] = None
    if isinstance(config, dict):
        config_dict = config

    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=WORKFLOW_TYPE)

    # Preflight
    preflight_report = _run_preflight(inputs, config_dict)
    preflight_dict = preflight_report.model_dump(mode="json")

    if ledger is not None and hasattr(ledger, "store_preflight"):
        ledger.store_preflight(run_id, preflight_dict)

    # FAIL closed
    if preflight_report.status == PreflightStatus.FAIL:
        result: dict[str, Any] = {
            "run_id": run_id,
            "workflow_type": WORKFLOW_TYPE,
            "preflight": preflight_dict,
            "summary": {
                "workflow_type": WORKFLOW_TYPE,
                "preflight_status": "fail",
                "next_steps": preflight_report.next_steps,
            },
            "findings": [],
            "validation": None,
        }
        if export_dir is not None:
            out = Path(export_dir)
            out.mkdir(parents=True, exist_ok=True)
            if ledger is not None and audit is not None:
                arts = generate_failed_preflight_packet(
                    ledger, audit, run_id, export_dir, actor=actor
                )
            else:
                from src.core.exports import write_json as _wj, write_markdown as _wm
                from src.core.preflight import render_preflight_summary_md
                run_out = out / run_id
                run_out.mkdir(parents=True, exist_ok=True)
                arts = [
                    _wj(run_out / "preflight_report.json", preflight_dict, run_id=run_id),
                    _wm(
                        run_out / "preflight_summary.md",
                        render_preflight_summary_md(preflight_report),
                        run_id=run_id,
                    ),
                ]
            result["export_artifacts"] = arts
            result["export_paths"] = {a.file_name: a.path for a in arts}
        if audit is not None and hasattr(audit, "run_failed"):
            audit.run_failed(run_id, actor, reason="preflight_fail")
        return result

    # Deterministic analysis
    det = run_deterministic(
        inputs["purchase_orders"],
        inputs["ap_invoices"],
        vendor_list_path=inputs.get("vendor_list"),
        check_register_path=inputs.get("check_register"),
        config=cfg,
    )
    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )

    # LLM assist (advisory; mock by default)
    if audit is not None and hasattr(audit, "llm_request_sent"):
        audit.llm_request_sent(run_id, actor, template=PROMPT_TEMPLATE_VERSION)
    response_json, model_provider, model_name = _call_llm(det, provider)
    llm_response = LLMResponse(
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        model_provider=model_provider,
        model_name=model_name,
        response_json=response_json,
        referenced_source_rows=list(
            response_json.get("referenced_source_rows", []) or []
        ),
    )
    if ledger is not None and hasattr(ledger, "store_llm_response"):
        ledger.store_llm_response(run_id, llm_response)
    if audit is not None and hasattr(audit, "llm_response_received"):
        audit.llm_response_received(run_id, actor, model_name=model_name)

    # Validation
    validation = validate_llm_output(response_json, det)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    result = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
        "preflight": preflight_dict,
    }

    # Exports
    if export_dir is not None:
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifacts = export_artifacts(
            export_dir,
            det,
            response_json,
            validation,
            audit_events,
            run_id=run_id,
        )
        if ledger is not None and hasattr(ledger, "store_export_artifact"):
            for a in artifacts:
                ledger.store_export_artifact(run_id, a)
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(
                run_id, actor, artifacts=[a.file_name for a in artifacts]
            )
        # NOTE: the consolidated review packet is generated by the shared
        # runner (app.workflow_registry.run_workflow), which owns packet
        # generation for every workflow (canonical pattern). Generating it
        # here too produced a nested duplicate under <export_dir>/<run_id>.
        result["export_artifacts"] = artifacts
        result["export_paths"] = {a.file_name: a.path for a in artifacts}

    if audit is not None and hasattr(audit, "run_completed"):
        audit.run_completed(run_id, actor, passed=validation.passed)

    return result


# --------------------------------------------------------------------------- #
# Registry hooks
# --------------------------------------------------------------------------- #
def register(registry: Any) -> None:
    """Register this workflow with a dict-like or callable registry."""
    if hasattr(registry, "register") and callable(registry.register):
        registry.register(WORKFLOW_TYPE, run)
    else:
        registry[WORKFLOW_TYPE] = run


WORKFLOW_REGISTRY[WORKFLOW_TYPE] = run

WORKFLOW = {
    "workflow_type": WORKFLOW_TYPE,
    "run": run,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    "export_files": list(EXPORT_FILE_NAMES),
    "sample_inputs": SAMPLE_INPUTS,
}
