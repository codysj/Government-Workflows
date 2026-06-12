"""Workflow — AP Duplicate / Suspicious Payment Review.

MODULE DOCSTRING
================
WORKFLOW_TYPE = "ap_duplicate_review"

Deterministic review of Tyler/Munis-style Accounts Payable exports for
duplicate, fraudulent-pattern, and policy-violating payments.  Every check is
plain Python/Decimal; the LLM is advisory only (summarise + draft).

INPUTS dict keys
----------------
ap_invoices     (required) path or TylerNormalizedExport for ap_invoice_detail
                (csv or xlsx)
vendor_list     (optional) path or TylerNormalizedExport for vendor_list
check_register  (optional) path or TylerNormalizedExport for check_register
purchase_orders (optional) path or TylerNormalizedExport for purchase_orders
config          (optional) path to a JSON config file, or a dict of overrides

CONFIG keys (all optional; defaults shown)
------------------------------------------
near_date_window_days      int     7       window (days) for D2 same-vendor/amount
amount_tolerance           Decimal 0.01    tolerance for "same amount" comparisons
split_threshold            Decimal 5000.00 per-invoice threshold for D4/D8
split_window_days          int     3       window (days) for D8 split-payment group
missing_po_min_amount      Decimal 5000.00 minimum amount to trigger D4 missing-PO
vendor_similarity_threshold float   0.88    difflib ratio for D3 name similarity

DETERMINISTIC CHECKS
---------------------
D1  exact duplicate vendor+invoice_number across AP rows            (high)
D1b one invoice paid by multiple checks (requires check_register)  (high)
D2  same vendor + same amount + different invoice within window     (medium)
D3  similar vendor names (difflib >= threshold) with same amount    (medium)
D4  invoice >= missing_po_min_amount with blank/missing PO          (medium)
D5  check date earlier than invoice date (requires check_register)  (medium)
D6  invoice for a vendor with Status=Inactive in vendor_list        (high)
D7  vendor number absent from vendor_list entirely                  (high)
D8  split-payment: 2+ invoices, same vendor, each < split_threshold,
    sum >= split_threshold, within split_window_days                (high)

Optional-file checks (D1b, D5) are SKIPPED with an explicit INFO finding when
the needed file is absent.

EXPORTS
-------
flagged_payments.csv, duplicate_groups.csv, ap_review_summary.md,
review_notes_draft.md, validation_report.json, audit_log.json
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.core.exports import write_csv, write_json, write_markdown
from src.core.preflight import render_preflight_summary_md, run_preflight
from src.core.schemas import (
    ArtifactType,
    CapabilitySpec,
    DeterministicFinding,
    ExportArtifact,
    FileProfile,
    FindingType,
    LLMResponse,
    ColumnMapping,
    PreflightConditionCode,
    PreflightFinding,
    PreflightStatus,
    Severity,
    SourceRowRef,
    ValidationResult,
    make_id,
)
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.ingest.tyler import (
    TylerNormalizedExport,
    normalize_tyler_export,
    source_ref_for_row,
)
from src.llm.provider import MockLLMProvider as _CoreMockLLMProvider
from src.llm.provider import _extract_findings_from_prompt

WORKFLOW_TYPE = "ap_duplicate_review"
PROMPT_TEMPLATE_VERSION = "ap_duplicate_review.v1"

# Repo-relative default sample input paths (for the wiring agent / demo UI).
SAMPLE_INPUTS: dict[str, str] = {
    "ap_invoices": "data/synthetic/tyler/ap_invoice_detail.csv",
    "vendor_list": "data/synthetic/tyler/vendor_list.csv",
    "check_register": "data/synthetic/tyler/check_register.csv",
    "purchase_orders": "data/synthetic/tyler/purchase_orders.csv",
}

EXPORT_FILE_NAMES = (
    "flagged_payments.csv",
    "duplicate_groups.csv",
    "ap_review_summary.md",
    "review_notes_draft.md",
    "validation_report.json",
    "audit_log.json",
)

# --------------------------------------------------------------------------- #
# PREFLIGHT / CAPABILITY layer
# --------------------------------------------------------------------------- #
CAPABILITY = CapabilitySpec(
    workflow_type=WORKFLOW_TYPE,
    required_inputs=["ap_invoices"],
    optional_inputs=["vendor_list", "check_register", "purchase_orders"],
    accepted_file_types={"*": ["csv", "xlsx"]},
    required_semantic_columns={
        "ap_invoices": ["vendor_number", "vendor_name", "invoice_number", "invoice_amount"],
    },
    optional_semantic_columns={
        "ap_invoices": ["invoice_date", "po_number", "check_number", "check_date"],
        "vendor_list": ["vendor_number", "status"],
        "check_register": ["check_number", "check_date", "vendor_number"],
        "purchase_orders": ["po_number", "vendor_number"],
    },
    supported_patterns=[
        "duplicate_invoice_number",
        "same_vendor_same_amount_near_date",
        "similar_vendor_names_same_amount",
        "missing_po_over_threshold",
        "payment_before_invoice_date",
        "inactive_vendor_payment",
        "unknown_vendor_payment",
        "split_payment_pattern",
    ],
    partially_supported_patterns=[
        "duplicate_check_number",
        "invoice_paid_by_multiple_checks",
    ],
    unsupported_patterns=[
        "multi_currency_payments",
        "inter_fund_transfer_detection",
    ],
    notes=(
        "Deterministic AP duplicate/suspicious-payment review. "
        "Checks D1-D8 require ap_invoices (required). "
        "D1b (multi-check) and D5 (check-date < invoice-date) require check_register (optional). "
        "D6 (inactive vendor) and D7 (unknown vendor) require vendor_list (optional). "
        "D4 (missing PO) uses po_number from ap_invoices; no purchase_orders needed. "
        "When an optional file is absent the check is skipped with an explicit INFO finding."
    ),
)


def detect_conditions(
    profiles: dict[str, FileProfile],
    mappings: dict[str, dict[str, ColumnMapping]],
    inputs: dict[str, Any],
    config: Optional[dict] = None,
) -> list[PreflightFinding]:
    """Domain-specific unsupported-condition detection for AP duplicate review.

    Conservative: only emits findings on genuine signal. All non-blocking.
    """
    findings: list[PreflightFinding] = []
    ap_profile = profiles.get("ap_invoices")
    if ap_profile is None or not ap_profile.present:
        return findings
    # Nothing domain-specific to check beyond what the generic engine handles;
    # optional-file skips are emitted at runtime as INFO findings, not here.
    return findings


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class APDuplicateConfig:
    """All thresholds that control the deterministic checks."""

    near_date_window_days: int = 7
    amount_tolerance: Decimal = Decimal("0.01")
    split_threshold: Decimal = Decimal("5000.00")
    split_window_days: int = 3
    missing_po_min_amount: Decimal = Decimal("5000.00")
    vendor_similarity_threshold: float = 0.88

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "APDuplicateConfig":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        kwargs: dict[str, Any] = {}
        for k, v in data.items():
            if k not in known:
                continue
            if k in ("amount_tolerance", "split_threshold", "missing_po_min_amount"):
                kwargs[k] = Decimal(str(v))
            elif k in ("near_date_window_days", "split_window_days"):
                kwargs[k] = int(v)
            elif k == "vendor_similarity_threshold":
                kwargs[k] = float(v)
        return cls(**kwargs)

    @classmethod
    def from_json(cls, path: str | Path) -> "APDuplicateConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Deterministic output container
# --------------------------------------------------------------------------- #
@dataclass
class APDuplicateOutput:
    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _short_ref(ref: SourceRowRef) -> str:
    return f"{ref.table_name}:{ref.row_index}"


def _load_tyler(
    val: Any, dataset_type: str, table_name: Optional[str] = None
) -> Optional[TylerNormalizedExport]:
    """Normalise a path or an already-normalised TylerNormalizedExport.

    Returns None if val is None (optional file absent).
    Raises ValueError (fail-closed) if the file cannot be parsed.
    """
    if val is None:
        return None
    if isinstance(val, TylerNormalizedExport):
        return val
    return normalize_tyler_export(
        Path(str(val)), dataset_type=dataset_type, table_name=table_name
    )


def _strip_suffix(name: str) -> str:
    """Remove common legal suffixes for fuzzy vendor-name comparison."""
    name = name.casefold().strip()
    name = re.sub(r"\bllc\b|\binc\b|\bco\b|\bcompany\b|\bcorp\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _amounts_equal(a: Optional[Decimal], b: Optional[Decimal], tol: Decimal) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _date_within(a: Optional[date], b: Optional[date], window: int) -> bool:
    if a is None or b is None:
        return False
    return abs((a - b).days) <= window


def _info_finding(rule: str, description: str) -> DeterministicFinding:
    return DeterministicFinding(
        finding_type=FindingType.OTHER,
        severity=Severity.INFO,
        description=description,
        source_rows=[],
        computed_values={},
        rule_used=rule,
        requires_human_review=False,
    )


def _group_id(prefix: str, key: str) -> str:
    """Stable group id for related findings (deterministic, no UUIDs)."""
    import hashlib
    return prefix + "_" + hashlib.md5((prefix + key).encode()).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Deterministic checks
# --------------------------------------------------------------------------- #
def run_deterministic(
    ap_export: TylerNormalizedExport,
    vendor_export: Optional[TylerNormalizedExport] = None,
    check_export: Optional[TylerNormalizedExport] = None,
    po_export: Optional[TylerNormalizedExport] = None,
    *,
    config: Optional[APDuplicateConfig] = None,
) -> APDuplicateOutput:
    """Run all deterministic AP duplicate / suspicious-payment checks.

    Parameters
    ----------
    ap_export:
        Normalised ap_invoice_detail export (required).
    vendor_export:
        Normalised vendor_list export (optional; D6/D7 skipped if absent).
    check_export:
        Normalised check_register export (optional; D1b/D5 skipped if absent).
    po_export:
        Normalised purchase_orders export (optional; reserved for future PO checks).
    config:
        Threshold overrides; defaults used when None.
    """
    cfg = config or APDuplicateConfig()
    findings: list[DeterministicFinding] = []

    ap_df = ap_export.dataframe
    ap_file_id = ap_export.input_file.file_id
    ap_table = ap_export.table_name

    def ap_ref(row_index: int, columns: Optional[list[str]] = None) -> SourceRowRef:
        return source_ref_for_row(ap_export, row_index, columns)

    # ------------------------------------------------------------------ #
    # Build vendor and check lookup structures (when optional files present)
    # ------------------------------------------------------------------ #
    # vendor_map: vendor_number -> {status, vendor_name}
    vendor_map: dict[str, dict[str, str]] = {}
    if vendor_export is not None:
        vdf = vendor_export.dataframe
        for _, vrow in vdf.iterrows():
            vnum = str(vrow.get("vendor_number", "")).strip()
            if vnum:
                vendor_map[vnum] = {
                    "status": str(vrow.get("status", "")).strip(),
                    "vendor_name": str(vrow.get("vendor_name", "")).strip(),
                }

    # check_map: check_number -> check_date (date | None)
    check_map: dict[str, Optional[date]] = {}
    # invoice_to_checks: invoice_number -> list[check_number]
    # NOTE: Void checks are excluded from multi-check detection (a Void + its
    # reissue is a normal workflow, not a suspicious multi-payment).
    invoice_to_checks: dict[str, list[str]] = {}
    if check_export is not None:
        cdf = check_export.dataframe
        for _, crow in cdf.iterrows():
            cnum = str(crow.get("check_number", "")).strip()
            cstatus = str(crow.get("status", "")).strip().lower()
            cdate = crow.get("check_date")
            check_map[cnum] = cdate if isinstance(cdate, date) else None
            if cstatus == "void":
                continue  # skip void checks - void + reissue is not a dup
            # Parse invoice_numbers (semicolon-joined)
            raw_inv = str(crow.get("invoice_numbers", "") or "").strip()
            for inv in raw_inv.split(";"):
                inv = inv.strip()
                if inv:
                    invoice_to_checks.setdefault(inv, []).append(cnum)

    # ------------------------------------------------------------------ #
    # Optional-file skip notices
    # ------------------------------------------------------------------ #
    if check_export is None:
        findings.append(
            _info_finding(
                "skip_d1b_d5_no_check_register",
                "Check D1b (multi-check payment) and D5 (check date before invoice date) "
                "require check_register; file not provided - these checks are skipped.",
            )
        )
    if vendor_export is None:
        findings.append(
            _info_finding(
                "skip_d6_d7_no_vendor_list",
                "Check D6 (inactive vendor) and D7 (unknown vendor) require vendor_list; "
                "file not provided - these checks are skipped.",
            )
        )

    # Helpers
    def _dec(val: Any) -> Optional[Decimal]:
        if isinstance(val, Decimal):
            return val
        if val is None or str(val).strip() == "":
            return None
        try:
            return Decimal(str(val).replace(",", "").replace("$", "").strip())
        except Exception:
            return None

    def _date(val: Any) -> Optional[date]:
        if isinstance(val, date):
            return val
        return None

    # ------------------------------------------------------------------ #
    # D1  Exact duplicate vendor_number + invoice_number
    # ------------------------------------------------------------------ #
    seen_inv: dict[tuple[str, str], int] = {}  # (vendor_number, invoice_number) -> first row_idx
    dup_groups_d1: dict[tuple[str, str], list[int]] = {}
    for _, row in ap_df.iterrows():
        vnum = str(row.get("vendor_number", "")).strip()
        inv = str(row.get("invoice_number", "")).strip()
        ridx = int(row["source_row_index"])
        if not vnum or not inv:
            continue
        key = (vnum, inv)
        if key in seen_inv:
            dup_groups_d1.setdefault(key, [seen_inv[key]]).append(ridx)
        else:
            seen_inv[key] = ridx

    for key, indices in dup_groups_d1.items():
        vnum, inv = key
        grp_id = _group_id("D1", f"{vnum}|{inv}")
        refs = [ap_ref(i) for i in indices]
        amounts = []
        for i in indices:
            row = ap_df[ap_df["source_row_index"] == i].iloc[0]
            a = _dec(row.get("invoice_amount"))
            amounts.append(str(a) if a is not None else "")
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.DUPLICATE_PAYMENT,
                severity=Severity.HIGH,
                description=(
                    f"Duplicate invoice number: vendor {vnum} invoice {inv} "
                    f"appears on {len(indices)} AP rows (rows {indices})."
                ),
                source_rows=refs,
                computed_values={
                    "group_id": grp_id,
                    "vendor_number": vnum,
                    "invoice_number": inv,
                    "row_indices": indices,
                    "amounts": amounts,
                },
                rule_used="duplicate_invoice_number",
                requires_human_review=True,
            )
        )

    # ------------------------------------------------------------------ #
    # D1b  Invoice paid by multiple checks (requires check_register)
    # ------------------------------------------------------------------ #
    if check_export is not None:
        for inv, check_list in invoice_to_checks.items():
            if len(check_list) <= 1:
                continue
            grp_id = _group_id("D1b", inv)
            # Find AP rows matching this invoice number
            ap_rows = ap_df[
                ap_df["invoice_number"].apply(lambda x: str(x).strip()) == inv
            ]
            refs = [ap_ref(int(r["source_row_index"])) for _, r in ap_rows.iterrows()]
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.DUPLICATE_PAYMENT,
                    severity=Severity.HIGH,
                    description=(
                        f"Invoice {inv} was paid by {len(check_list)} different checks: "
                        f"{sorted(check_list)}."
                    ),
                    source_rows=refs,
                    computed_values={
                        "group_id": grp_id,
                        "invoice_number": inv,
                        "check_numbers": sorted(check_list),
                    },
                    rule_used="invoice_paid_by_multiple_checks",
                    requires_human_review=True,
                )
            )

    # ------------------------------------------------------------------ #
    # D2  Same vendor + same amount + different invoice within window
    # ------------------------------------------------------------------ #
    ap_rows_list = list(ap_df.iterrows())
    for i in range(len(ap_rows_list)):
        _, row_i = ap_rows_list[i]
        vnum_i = str(row_i.get("vendor_number", "")).strip()
        inv_i = str(row_i.get("invoice_number", "")).strip()
        amt_i = _dec(row_i.get("invoice_amount"))
        d_i = _date(row_i.get("invoice_date"))
        ridx_i = int(row_i["source_row_index"])
        if not vnum_i or amt_i is None:
            continue
        for j in range(i + 1, len(ap_rows_list)):
            _, row_j = ap_rows_list[j]
            vnum_j = str(row_j.get("vendor_number", "")).strip()
            inv_j = str(row_j.get("invoice_number", "")).strip()
            amt_j = _dec(row_j.get("invoice_amount"))
            d_j = _date(row_j.get("invoice_date"))
            ridx_j = int(row_j["source_row_index"])
            if vnum_i != vnum_j:
                continue
            if inv_i == inv_j:
                continue  # already caught by D1
            if not _amounts_equal(amt_i, amt_j, cfg.amount_tolerance):
                continue
            if not _date_within(d_i, d_j, cfg.near_date_window_days):
                continue
            grp_id = _group_id("D2", f"{vnum_i}|{amt_i}|{min(ridx_i, ridx_j)}")
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.DUPLICATE_PAYMENT,
                    severity=Severity.MEDIUM,
                    description=(
                        f"Same vendor {vnum_i}, same amount {amt_i}, different invoice "
                        f"numbers ({inv_i} and {inv_j}) within "
                        f"{cfg.near_date_window_days} days "
                        f"(dates {d_i} and {d_j})."
                    ),
                    source_rows=[ap_ref(ridx_i), ap_ref(ridx_j)],
                    computed_values={
                        "group_id": grp_id,
                        "vendor_number": vnum_i,
                        "amount": str(amt_i),
                        "invoice_numbers": [inv_i, inv_j],
                        "invoice_dates": [str(d_i), str(d_j)],
                        "days_apart": abs((d_i - d_j).days) if d_i and d_j else "",
                    },
                    rule_used="same_vendor_same_amount_near_date",
                    requires_human_review=True,
                )
            )

    # ------------------------------------------------------------------ #
    # D3  Similar vendor names with same-amount invoices within window
    # ------------------------------------------------------------------ #
    # Build unique (vendor_number, vendor_name) pairs with their invoice rows.
    vendor_inv_rows: dict[str, list[dict[str, Any]]] = {}
    for _, row in ap_df.iterrows():
        vnum = str(row.get("vendor_number", "")).strip()
        vname = str(row.get("vendor_name", "")).strip()
        if not vnum:
            continue
        vendor_inv_rows.setdefault(vnum, []).append(
            {
                "vendor_name": vname,
                "amount": _dec(row.get("invoice_amount")),
                "invoice_date": _date(row.get("invoice_date")),
                "invoice_number": str(row.get("invoice_number", "")).strip(),
                "source_row_index": int(row["source_row_index"]),
            }
        )

    vendor_numbers = sorted(vendor_inv_rows)
    checked_pairs: set[frozenset[str]] = set()
    for vi in range(len(vendor_numbers)):
        vnum_a = vendor_numbers[vi]
        vname_a = vendor_inv_rows[vnum_a][0]["vendor_name"] if vendor_inv_rows[vnum_a] else ""
        stripped_a = _strip_suffix(vname_a)
        for vj in range(vi + 1, len(vendor_numbers)):
            vnum_b = vendor_numbers[vj]
            if frozenset([vnum_a, vnum_b]) in checked_pairs:
                continue
            vname_b = vendor_inv_rows[vnum_b][0]["vendor_name"] if vendor_inv_rows[vnum_b] else ""
            stripped_b = _strip_suffix(vname_b)
            if not stripped_a or not stripped_b:
                continue
            ratio = difflib.SequenceMatcher(None, stripped_a, stripped_b).ratio()
            if ratio < cfg.vendor_similarity_threshold:
                continue
            checked_pairs.add(frozenset([vnum_a, vnum_b]))
            # Find invoice pairs from both vendors where amount matches within window
            for row_a in vendor_inv_rows[vnum_a]:
                for row_b in vendor_inv_rows[vnum_b]:
                    amt_a = row_a["amount"]
                    amt_b = row_b["amount"]
                    d_a = row_a["invoice_date"]
                    d_b = row_b["invoice_date"]
                    if not _amounts_equal(amt_a, amt_b, cfg.amount_tolerance):
                        continue
                    if not _date_within(d_a, d_b, cfg.near_date_window_days):
                        continue
                    grp_id = _group_id(
                        "D3",
                        f"{vnum_a}|{vnum_b}|{amt_a}|{row_a['source_row_index']}",
                    )
                    findings.append(
                        DeterministicFinding(
                            finding_type=FindingType.VENDOR_ANOMALY,
                            severity=Severity.MEDIUM,
                            description=(
                                f"Similar vendor names: '{vname_a}' ({vnum_a}) and "
                                f"'{vname_b}' ({vnum_b}) have same-amount invoices "
                                f"({amt_a}) within {cfg.near_date_window_days} days "
                                f"(name similarity {ratio:.2f})."
                            ),
                            source_rows=[
                                ap_ref(row_a["source_row_index"]),
                                ap_ref(row_b["source_row_index"]),
                            ],
                            computed_values={
                                "group_id": grp_id,
                                "vendor_a": vnum_a,
                                "vendor_name_a": vname_a,
                                "vendor_b": vnum_b,
                                "vendor_name_b": vname_b,
                                "amount": str(amt_a),
                                "similarity_ratio": f"{ratio:.4f}",
                                "invoice_number_a": row_a["invoice_number"],
                                "invoice_number_b": row_b["invoice_number"],
                                "invoice_date_a": str(d_a),
                                "invoice_date_b": str(d_b),
                            },
                            rule_used="similar_vendor_names_same_amount",
                            requires_human_review=True,
                        )
                    )

    # ------------------------------------------------------------------ #
    # D4  Missing/blank PO number on invoices >= missing_po_min_amount
    # ------------------------------------------------------------------ #
    for _, row in ap_df.iterrows():
        po_num = str(row.get("po_number", "") or "").strip()
        amt = _dec(row.get("invoice_amount"))
        ridx = int(row["source_row_index"])
        if po_num:
            continue
        if amt is None or amt < cfg.missing_po_min_amount:
            continue
        vnum = str(row.get("vendor_number", "")).strip()
        inv = str(row.get("invoice_number", "")).strip()
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.MISSING_REFERENCE,
                severity=Severity.MEDIUM,
                description=(
                    f"Invoice {inv} for vendor {vnum} amount {amt} has no PO number "
                    f"(threshold {cfg.missing_po_min_amount})."
                ),
                source_rows=[ap_ref(ridx)],
                computed_values={
                    "vendor_number": vnum,
                    "invoice_number": inv,
                    "amount": str(amt),
                    "threshold": str(cfg.missing_po_min_amount),
                },
                rule_used="missing_po_over_threshold",
                requires_human_review=True,
            )
        )

    # ------------------------------------------------------------------ #
    # D5  Check date earlier than invoice date (requires check_register)
    # ------------------------------------------------------------------ #
    if check_export is not None:
        for _, row in ap_df.iterrows():
            inv_date = _date(row.get("invoice_date"))
            cnum = str(row.get("check_number", "") or "").strip()
            ridx = int(row["source_row_index"])
            if not cnum or inv_date is None:
                continue
            chk_date = check_map.get(cnum)
            if chk_date is None:
                continue
            if chk_date < inv_date:
                vnum = str(row.get("vendor_number", "")).strip()
                inv = str(row.get("invoice_number", "")).strip()
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.DUPLICATE_PAYMENT,
                        severity=Severity.MEDIUM,
                        description=(
                            f"Check {cnum} date {chk_date} is before invoice {inv} date "
                            f"{inv_date} for vendor {vnum}."
                        ),
                        source_rows=[ap_ref(ridx)],
                        computed_values={
                            "vendor_number": vnum,
                            "invoice_number": inv,
                            "invoice_date": str(inv_date),
                            "check_number": cnum,
                            "check_date": str(chk_date),
                            "days_early": str((inv_date - chk_date).days),
                        },
                        rule_used="payment_before_invoice_date",
                        requires_human_review=True,
                    )
                )

    # ------------------------------------------------------------------ #
    # D6  Invoice for an Inactive vendor (requires vendor_list)
    # ------------------------------------------------------------------ #
    if vendor_export is not None:
        for _, row in ap_df.iterrows():
            vnum = str(row.get("vendor_number", "")).strip()
            ridx = int(row["source_row_index"])
            if not vnum or vnum not in vendor_map:
                continue
            status = vendor_map[vnum].get("status", "").strip()
            if status.lower() == "inactive":
                inv = str(row.get("invoice_number", "")).strip()
                amt = _dec(row.get("invoice_amount"))
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.VENDOR_ANOMALY,
                        severity=Severity.HIGH,
                        description=(
                            f"Invoice {inv} from vendor {vnum} "
                            f"({vendor_map[vnum].get('vendor_name', '')}) "
                            f"whose status is Inactive."
                        ),
                        source_rows=[ap_ref(ridx)],
                        computed_values={
                            "vendor_number": vnum,
                            "vendor_name": vendor_map[vnum].get("vendor_name", ""),
                            "vendor_status": status,
                            "invoice_number": inv,
                            "amount": str(amt) if amt is not None else "",
                        },
                        rule_used="inactive_vendor_payment",
                        requires_human_review=True,
                    )
                )

    # ------------------------------------------------------------------ #
    # D7  Vendor number absent from vendor_list entirely
    # ------------------------------------------------------------------ #
    if vendor_export is not None:
        for _, row in ap_df.iterrows():
            vnum = str(row.get("vendor_number", "")).strip()
            ridx = int(row["source_row_index"])
            if not vnum or vnum in vendor_map:
                continue
            inv = str(row.get("invoice_number", "")).strip()
            vname = str(row.get("vendor_name", "")).strip()
            amt = _dec(row.get("invoice_amount"))
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.MISSING_REFERENCE,
                    severity=Severity.HIGH,
                    description=(
                        f"Vendor {vnum} ('{vname}') on invoice {inv} is absent "
                        f"from the vendor list."
                    ),
                    source_rows=[ap_ref(ridx)],
                    computed_values={
                        "vendor_number": vnum,
                        "vendor_name": vname,
                        "invoice_number": inv,
                        "amount": str(amt) if amt is not None else "",
                    },
                    rule_used="unknown_vendor_payment",
                    requires_human_review=True,
                )
            )

    # ------------------------------------------------------------------ #
    # D8  Split-payment pattern: 2+ invoices, same vendor, each <
    #     split_threshold, sum >= split_threshold, within split_window_days
    # ------------------------------------------------------------------ #
    # Group AP rows by vendor_number.
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for _, row in ap_df.iterrows():
        vnum = str(row.get("vendor_number", "")).strip()
        if not vnum:
            continue
        by_vendor.setdefault(vnum, []).append(
            {
                "invoice_number": str(row.get("invoice_number", "")).strip(),
                "amount": _dec(row.get("invoice_amount")),
                "invoice_date": _date(row.get("invoice_date")),
                "source_row_index": int(row["source_row_index"]),
            }
        )

    for vnum, rows in by_vendor.items():
        # Collect rows where each invoice is below the threshold.
        under_rows = [r for r in rows if r["amount"] is not None and r["amount"] < cfg.split_threshold]
        if len(under_rows) < 2:
            continue
        # Find subsets of 2+ rows where:
        #   - all dates are within split_window_days of each other
        #   - total >= split_threshold
        # Use a sliding window over date-sorted rows.
        dated = [r for r in under_rows if r["invoice_date"] is not None]
        if len(dated) < 2:
            continue
        dated_sorted = sorted(dated, key=lambda r: r["invoice_date"])
        n = len(dated_sorted)
        for start in range(n):
            window_rows = [dated_sorted[start]]
            for end in range(start + 1, n):
                d_start = dated_sorted[start]["invoice_date"]
                d_end = dated_sorted[end]["invoice_date"]
                if (d_end - d_start).days > cfg.split_window_days:
                    break
                window_rows.append(dated_sorted[end])
                if len(window_rows) < 2:
                    continue
                total = sum(r["amount"] for r in window_rows)
                if total >= cfg.split_threshold:
                    grp_id = _group_id(
                        "D8",
                        vnum + "|" + "|".join(str(r["source_row_index"]) for r in window_rows),
                    )
                    refs = [ap_ref(r["source_row_index"]) for r in window_rows]
                    inv_nums = [r["invoice_number"] for r in window_rows]
                    amounts = [str(r["amount"]) for r in window_rows]
                    dates = [str(r["invoice_date"]) for r in window_rows]
                    findings.append(
                        DeterministicFinding(
                            finding_type=FindingType.DUPLICATE_PAYMENT,
                            severity=Severity.HIGH,
                            description=(
                                f"Possible split-payment: vendor {vnum} has "
                                f"{len(window_rows)} invoices each below "
                                f"{cfg.split_threshold} totaling {total} within "
                                f"{cfg.split_window_days} days "
                                f"(invoices {inv_nums})."
                            ),
                            source_rows=refs,
                            computed_values={
                                "group_id": grp_id,
                                "vendor_number": vnum,
                                "invoice_numbers": inv_nums,
                                "amounts": amounts,
                                "total": str(total),
                                "split_threshold": str(cfg.split_threshold),
                                "invoice_dates": dates,
                            },
                            rule_used="split_payment_pattern",
                            requires_human_review=True,
                        )
                    )

    summary = _build_summary(findings, cfg)
    return APDuplicateOutput(findings=findings, summary=summary)


def _build_summary(findings: list[DeterministicFinding], cfg: APDuplicateConfig) -> dict[str, Any]:
    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    skip_rules = {
        "skip_d1b_d5_no_check_register",
        "skip_d6_d7_no_vendor_list",
    }
    flagged = [f for f in findings if f.rule_used not in skip_rules]
    for f in findings:
        by_rule[f.rule_used] = by_rule.get(f.rule_used, 0) + 1
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1
    return {
        "workflow_type": WORKFLOW_TYPE,
        "total_findings": len(flagged),
        "total_with_info": len(findings),
        "findings_by_rule": by_rule,
        "findings_by_severity": by_severity,
        "requires_human_review": any(f.requires_human_review for f in findings),
        "config": {
            "near_date_window_days": cfg.near_date_window_days,
            "amount_tolerance": str(cfg.amount_tolerance),
            "split_threshold": str(cfg.split_threshold),
            "split_window_days": cfg.split_window_days,
            "missing_po_min_amount": str(cfg.missing_po_min_amount),
            "vendor_similarity_threshold": cfg.vendor_similarity_threshold,
        },
    }


# --------------------------------------------------------------------------- #
# LLM prompt (advisory only)
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY explain, summarize, draft review notes, classify, and flag.\n"
    "- You MUST NOT determine whether fraud occurred, calculate new figures, or invent "
    "vendor names, invoice numbers, amounts, or dates.\n"
    "- Use ONLY numbers and source-row ids that appear in the DETERMINISTIC FINDINGS below.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- All output is a DRAFT for human review; use 'requires human review' language.\n"
    "- Do NOT produce final-approval or fraud-conclusion language.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def _findings_block(det: APDuplicateOutput) -> str:
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


def build_prompt(det: APDuplicateOutput) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: AP duplicate / suspicious payment review.\n"
        + "TASK: Summarize the flagged issues, draft plain-language review notes for each "
        "category of exception, suggest human follow-up steps, and draft a review memo. "
        "Do NOT conclude fraud or produce approval language.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM (DEFAULT path)
# --------------------------------------------------------------------------- #
class _APMockLLMProvider(_CoreMockLLMProvider):
    """AP-review mock. Overrides _build to produce AP-specific commentary."""

    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        skip_rules = {
            "skip_d1b_d5_no_check_register",
            "skip_d6_d7_no_vendor_list",
        }
        flagged = [f for f in findings if f.get("rule_used") not in skip_rules]
        ref_ids: list[str] = []
        categorized = []
        for f in flagged:
            refs = f.get("source_row_ids", [])
            ref_ids.extend(refs)
            categorized.append(
                {
                    "category": f.get("rule_used", "other"),
                    "description": f.get("description", ""),
                    "referenced_source_rows": refs,
                }
            )
        return {
            "summary": (
                f"Deterministic AP review flagged {len(flagged)} potential issue(s) "
                "for human review. See the categorized list; each item cites the source "
                "rows that triggered it. No fraud conclusions are made."
            ),
            "categorized_exceptions": categorized,
            "referenced_source_rows": sorted(set(ref_ids)),
            "suggested_review_steps": [
                "Confirm each duplicate-invoice pair is not a legitimate re-billing.",
                "Verify same-vendor/same-amount invoices are for distinct deliverables.",
                "Check similar vendor names against the approved vendor list.",
                "Obtain PO documentation for any invoice above the threshold.",
                "Confirm check date vs invoice date timing with the issuing department.",
                "Reactivate or remove inactive vendors per procurement policy.",
                "Validate any unknown vendor against procurement records.",
                "Review split-payment groups for approval-threshold circumvention.",
            ],
            "draft_memo": (
                "DRAFT -- for human review only. The automated AP review identified "
                "items that require finance staff confirmation before any payment is "
                "voided, re-issued, or approved. This memo does not certify any payment "
                "as correct or fraudulent."
            ),
        }


def mock_llm_response(det: APDuplicateOutput) -> dict[str, Any]:
    """Build a source-cited mock response from findings only."""
    skip_rules = {
        "skip_d1b_d5_no_check_register",
        "skip_d6_d7_no_vendor_list",
    }
    refs: list[str] = []
    categorized = []
    for f in det.findings:
        if f.rule_used in skip_rules:
            continue
        f_refs = [_short_ref(s) for s in f.source_rows]
        refs.extend(f_refs)
        categorized.append(
            {
                "category": f.rule_used,
                "description": f.description,
                "referenced_source_rows": f_refs,
            }
        )
    refs = sorted(set(refs))
    n = det.summary.get("total_findings", 0)
    return {
        "summary": (
            f"Deterministic AP review flagged {n} potential issue(s) for human review. "
            "See the categorized list; each item cites the source rows that triggered it."
        ),
        "categorized_exceptions": categorized,
        "referenced_source_rows": refs,
        "suggested_review_steps": [
            "Confirm each duplicate-invoice pair is not a legitimate re-billing.",
            "Verify same-vendor/same-amount invoices are for distinct deliverables.",
            "Obtain PO documentation for any invoice above the threshold.",
            "Review split-payment groups for approval-threshold circumvention.",
        ],
        "draft_memo": (
            "DRAFT -- for human review only. The automated AP review identified items "
            "requiring finance staff confirmation. No fraud determination is made here."
        ),
    }


def _call_llm(det: APDuplicateOutput, provider: Any = None) -> tuple[dict, str, str]:
    """Return (response_json, model_provider, model_name)."""
    provider = provider or _APMockLLMProvider()
    prompt = build_prompt(det)
    raw = provider.generate_structured_response(prompt, schema=None)
    if not isinstance(raw, dict):
        raw = {"summary": str(raw), "referenced_source_rows": []}
    return (
        raw,
        getattr(provider, "model_provider", "mock"),
        getattr(provider, "model_name", "mock-llm"),
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: dict[str, Any], det: APDuplicateOutput
) -> ValidationResult:
    """Thin wrapper: delegates to the canonical validator."""
    return _core_validate_llm_output(
        response_json,
        det,
        require_references=True,
        missing_references_is_error=False,  # skip/info findings have no refs
        check_numeric_claims=False,
    )


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def export_artifacts(
    out_dir: str | Path,
    det: APDuplicateOutput,
    response_json: dict[str, Any],
    validation: ValidationResult,
    audit_events: Optional[list[dict[str, Any]]] = None,
    *,
    run_id: Optional[str] = None,
    preflight_report: Any = None,
) -> list[ExportArtifact]:
    """Write the six AP-review artifacts. Returns ExportArtifact list."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportArtifact] = []

    def _write_md(name: str, text: str) -> None:
        artifacts.append(write_markdown(out / name, text, run_id=run_id))

    def _write_json_file(name: str, obj: Any) -> None:
        artifacts.append(write_json(out / name, obj, run_id=run_id))

    def _write_csv_file(name: str, df: pd.DataFrame) -> None:
        artifacts.append(write_csv(out / name, df, run_id=run_id))

    skip_rules = {
        "skip_d1b_d5_no_check_register",
        "skip_d6_d7_no_vendor_list",
    }

    # flagged_payments.csv
    fp_rows = []
    for f in det.findings:
        if f.rule_used in skip_rules:
            continue
        fp_rows.append(
            {
                "finding_id": f.finding_id,
                "rule_used": f.rule_used,
                "finding_type": f.finding_type.value,
                "severity": f.severity.value,
                "group_id": f.computed_values.get("group_id", ""),
                "description": f.description,
                "source_refs": ";".join(_short_ref(s) for s in f.source_rows),
                "computed_values": json.dumps(f.computed_values, default=str),
                "requires_human_review": f.requires_human_review,
            }
        )
    flagged_cols = [
        "finding_id", "rule_used", "finding_type", "severity", "group_id",
        "description", "source_refs", "computed_values", "requires_human_review",
    ]
    _write_csv_file("flagged_payments.csv", pd.DataFrame(fp_rows, columns=flagged_cols))

    # duplicate_groups.csv  (D1 / D2 / D8 grouped rows)
    group_rules = {
        "duplicate_invoice_number",
        "same_vendor_same_amount_near_date",
        "split_payment_pattern",
    }
    grp_rows = []
    for f in det.findings:
        if f.rule_used not in group_rules:
            continue
        grp_id = f.computed_values.get("group_id", "")
        for ref in f.source_rows:
            grp_rows.append(
                {
                    "group_id": grp_id,
                    "rule_used": f.rule_used,
                    "severity": f.severity.value,
                    "source_ref": _short_ref(ref),
                    "row_index": ref.row_index,
                    "vendor_number": f.computed_values.get("vendor_number", ""),
                }
            )
    grp_cols = ["group_id", "rule_used", "severity", "source_ref", "row_index", "vendor_number"]
    _write_csv_file("duplicate_groups.csv", pd.DataFrame(grp_rows, columns=grp_cols))

    # ap_review_summary.md
    s = det.summary
    lines = [
        "# AP Duplicate / Suspicious Payment Review Summary",
        "",
        f"Total flagged findings: {s.get('total_findings', 0)}",
        "",
        "## Findings by rule",
    ]
    for rule, cnt in sorted(s.get("findings_by_rule", {}).items()):
        if rule in skip_rules:
            lines.append(f"- {rule}: {cnt} (INFO/skipped)")
        else:
            lines.append(f"- {rule}: {cnt}")
    lines += [
        "",
        "## Findings by severity",
    ]
    for sev, cnt in sorted(s.get("findings_by_severity", {}).items()):
        lines.append(f"- {sev}: {cnt}")
    lines += [
        "",
        "## AI Summary (DRAFT -- human review required)",
        str(response_json.get("summary", "")),
        "",
        "## Flagged items",
    ]
    for f in det.findings:
        if f.rule_used in skip_rules:
            continue
        refs = ", ".join(_short_ref(r) for r in f.source_rows) or "(none)"
        lines.append(
            f"- **[{f.severity.value}] {f.rule_used}** -- {f.description} "
            f"(source rows: {refs})"
        )
    lines += [
        "",
        "_All checks are deterministic. AI commentary is a DRAFT for human review._",
    ]
    _write_md("ap_review_summary.md", "\n".join(lines))

    # review_notes_draft.md
    draft_lines = [
        "# AP Review Notes (DRAFT -- for human review only)",
        "",
        str(response_json.get("draft_memo", "")),
        "",
        "## Suggested review steps",
        "",
    ]
    for step in response_json.get("suggested_review_steps") or []:
        draft_lines.append(f"- [ ] {step}")
    draft_lines += [
        "",
        "## Exception details",
        "",
    ]
    for exc in response_json.get("categorized_exceptions") or []:
        if isinstance(exc, dict):
            cat = exc.get("category", "")
            desc = exc.get("description", "")
            exc_refs = ", ".join(exc.get("referenced_source_rows") or []) or "(none)"
            draft_lines.append(f"### {cat}")
            draft_lines.append(desc)
            draft_lines.append(f"_Source rows: {exc_refs}_")
            draft_lines.append("")
    _write_md("review_notes_draft.md", "\n".join(draft_lines))

    # validation_report.json
    _write_json_file(
        "validation_report.json",
        json.dumps(validation.model_dump(), default=str, indent=2),
    )

    # audit_log.json
    _write_json_file(
        "audit_log.json",
        json.dumps(audit_events or [], default=str, indent=2),
    )

    return artifacts


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
    """End-to-end AP duplicate / suspicious payment review run.

    inputs keys
    -----------
    ap_invoices     (required) path / TylerNormalizedExport  ap_invoice_detail
    vendor_list     (optional) path / TylerNormalizedExport  vendor_list
    check_register  (optional) path / TylerNormalizedExport  check_register
    purchase_orders (optional) path / TylerNormalizedExport  purchase_orders (reserved)
    config          (optional) path to JSON or dict (overrides threshold defaults)

    The ``config`` keyword argument or ``inputs['config']`` are both accepted;
    the keyword argument takes precedence.

    Returns dict: run_id, workflow_type, summary, findings, validation,
    preflight, and (when export_dir is set) export_paths + export_artifacts.
    """
    run_id = run_id or make_id()

    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=WORKFLOW_TYPE)

    # ------------------------------------------------------------------
    # Config resolution
    # ------------------------------------------------------------------
    if config is None:
        config = inputs.get("config")
    if isinstance(config, (str, Path)):
        cfg = APDuplicateConfig.from_json(config)
    elif isinstance(config, dict):
        cfg = APDuplicateConfig.from_dict(config)
    elif isinstance(config, APDuplicateConfig):
        cfg = config
    else:
        cfg = APDuplicateConfig()

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------
    preflight_inputs: dict[str, Any] = {
        "ap_invoices": inputs.get("ap_invoices"),
        "vendor_list": inputs.get("vendor_list"),
        "check_register": inputs.get("check_register"),
        "purchase_orders": inputs.get("purchase_orders"),
    }
    preflight = run_preflight(
        CAPABILITY,
        preflight_inputs,
        detect_conditions=detect_conditions,
        config=config if isinstance(config, dict) else None,
    )

    preflight_dict = preflight.to_summary()

    # Write preflight reports when export_dir is provided.
    _preflight_artifacts: list[ExportArtifact] = []
    if export_dir is not None:
        out = Path(export_dir)
        out.mkdir(parents=True, exist_ok=True)
        _preflight_artifacts.append(
            write_json(out / "preflight_report.json", preflight_dict, run_id=run_id)
        )
        _preflight_artifacts.append(
            write_markdown(
                out / "preflight_summary.md",
                render_preflight_summary_md(preflight),
                run_id=run_id,
            )
        )

    if preflight.status == PreflightStatus.FAIL:
        if audit is not None and hasattr(audit, "run_failed"):
            audit.run_failed(run_id, actor, reason="preflight_fail")
        fail_result: dict[str, Any] = {
            "run_id": run_id,
            "workflow_type": WORKFLOW_TYPE,
            "preflight": preflight_dict,
            "preflight_status": preflight.status.value,
            "summary": {"workflow_type": WORKFLOW_TYPE, "preflight_status": "fail"},
            "findings": [],
            "validation": None,
        }
        if _preflight_artifacts:
            fail_result["export_artifacts"] = _preflight_artifacts
            fail_result["export_paths"] = {a.file_name: a.path for a in _preflight_artifacts}
        return fail_result

    # ------------------------------------------------------------------
    # Ingest / normalise optional files
    # ------------------------------------------------------------------
    try:
        ap_export = _load_tyler(inputs["ap_invoices"], "ap_invoice_detail", "ap_invoices")
    except Exception as exc:
        raise ValueError(f"ap_invoices: fail closed - {exc}") from exc

    vendor_export: Optional[TylerNormalizedExport] = None
    if inputs.get("vendor_list") is not None:
        try:
            vendor_export = _load_tyler(inputs["vendor_list"], "vendor_list", "vendor_list")
        except Exception as exc:
            raise ValueError(f"vendor_list: fail closed - {exc}") from exc

    check_export: Optional[TylerNormalizedExport] = None
    if inputs.get("check_register") is not None:
        try:
            check_export = _load_tyler(inputs["check_register"], "check_register", "check_register")
        except Exception as exc:
            raise ValueError(f"check_register: fail closed - {exc}") from exc

    po_export: Optional[TylerNormalizedExport] = None
    if inputs.get("purchase_orders") is not None:
        try:
            po_export = _load_tyler(inputs["purchase_orders"], "purchase_orders", "purchase_orders")
        except Exception as exc:
            raise ValueError(f"purchase_orders: fail closed - {exc}") from exc

    # ------------------------------------------------------------------
    # Deterministic analysis
    # ------------------------------------------------------------------
    det = run_deterministic(
        ap_export,
        vendor_export=vendor_export,
        check_export=check_export,
        po_export=po_export,
        config=cfg,
    )

    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )
    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)

    # ------------------------------------------------------------------
    # LLM assist (advisory; mock by default)
    # ------------------------------------------------------------------
    if audit is not None and hasattr(audit, "llm_request_sent"):
        audit.llm_request_sent(run_id, actor, template=PROMPT_TEMPLATE_VERSION)

    response_json, model_provider, model_name = _call_llm(det, provider)
    llm_response = LLMResponse(
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        model_provider=model_provider,
        model_name=model_name,
        response_json=response_json,
        referenced_source_rows=list(response_json.get("referenced_source_rows", []) or []),
    )
    if ledger is not None and hasattr(ledger, "store_llm_response"):
        ledger.store_llm_response(run_id, llm_response)
    if audit is not None and hasattr(audit, "llm_response_received"):
        audit.llm_response_received(run_id, actor, model_name=model_name)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    validation = validate_llm_output(response_json, det)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "preflight": preflight_dict,
        "preflight_status": preflight.status.value,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
    }

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    if export_dir is not None:
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifact_list = export_artifacts(
            export_dir, det, response_json, validation, audit_events,
            run_id=run_id, preflight_report=preflight,
        )
        if ledger is not None and hasattr(ledger, "store_export_artifact"):
            for a in artifact_list:
                ledger.store_export_artifact(run_id, a)
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(
                run_id, actor, artifacts=[a.file_name for a in artifact_list]
            )
        result["export_artifacts"] = artifact_list
        result["export_paths"] = {a.file_name: a.path for a in artifact_list}

    if audit is not None and hasattr(audit, "run_completed"):
        audit.run_completed(run_id, actor, passed=validation.passed)

    return result


# --------------------------------------------------------------------------- #
# Registry hook
# --------------------------------------------------------------------------- #
def register(registry: Any) -> None:
    """Register this workflow with a dict-like or callable registry."""
    if hasattr(registry, "register") and callable(registry.register):
        registry.register(WORKFLOW_TYPE, run)
    else:
        registry[WORKFLOW_TYPE] = run


WORKFLOW = {
    "workflow_type": WORKFLOW_TYPE,
    "run": run,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    "export_files": list(EXPORT_FILE_NAMES),
    "sample_inputs": SAMPLE_INPUTS,
}
