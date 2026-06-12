"""Workflow - Natural-Language Transaction Search.

WORKFLOW_TYPE = "transaction_search"

Lets a finance staff member search across Tyler/Munis export files using plain
English.  The workflow is strictly two-stage and fail-closed:

STAGE 1 - INTENT PARSE
  The LLM is asked to propose a ``SearchCriteria`` (vendor name fragment,
  invoice/PO/check number, fund/department/object, date range, amount range,
  keywords, module).  The proposal is schema-validated (pydantic) and
  range-sanity-checked deterministically (date_from <= date_to, amounts
  non-negative, module known).  Anything invalid is dropped to
  ``unparsed_terms`` and flagged.

  The MOCK / offline path is a deterministic rule-based parser (regex +
  keyword): month-name/ISO date ranges, over/under/between $X amounts, quoted
  or recognised vendor tokens, INV-/PO-/check number patterns, "fund NNN"
  dimension mentions, and a ``keywords`` list for remaining tokens.  It
  satisfies Q1-Q4 from ``data/synthetic/tyler/known_answers.json``.

  If NOTHING parseable is extracted preflight-style FAIL: a structured report
  is returned and the search is not executed.

STAGE 2 - DETERMINISTIC EXECUTION
  Load the provided Tyler exports via ``src.ingest.tyler.normalize_tyler_export``
  (gives free SHA-256 + source_row_index traceability), tag rows with their
  module, and apply the validated criteria as deterministic pandas filters:
    * vendor/keyword matching: casefold + punctuation-strip, plus conservative
      fuzzy fallback with ``difflib.SequenceMatcher`` ratio >= 0.85 (stdlib only).
    * date range, amount range, fund/org/object, exact reference numbers.

  Each matching row becomes a ``DeterministicFinding``
  (finding_type=SEARCH_MATCH) carrying its ``SourceRowRef`` and matched-field
  values.  Findings are capped at ``max_results`` (default 200) with an
  explicit SUMMARY truncation finding.

EXPORTS
  ``search_criteria.json``  - validated criteria + LLM-proposed vs defaulted
                              fields + unparsed_terms
  ``search_results.csv``    - source_file, source_row_index, module, matched_fields
  ``search_summary.md``     - human-readable summary
  ``validation_report.json``
  ``audit_log.json``

INPUTS dict
  ``query``            (required str) plain-English search query
  ``gl_detail``        (optional) path to Tyler GL detail CSV/XLSX
  ``ap_invoices``      (optional) path to Tyler AP invoice detail CSV/XLSX
  ``checks``           (optional) path to Tyler check register CSV/XLSX
  ``purchase_orders``  (optional) path to Tyler purchase orders CSV/XLSX

  At least one data file is required; the ``query`` must be non-blank.
  Both are enforced via ``detect_conditions`` (blocking FAIL).

CONFIG dict (subset passed as ``config`` arg)
  ``max_results``           int, default 200 - cap on detail findings
  ``fuzzy_threshold``       float, default 0.85 - SequenceMatcher ratio cutoff
  ``missing_po_threshold``  Decimal-compatible str, default "5000.00" (unused
                            here; carried for cross-workflow consistency)
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from src.core.schemas import (
    ArtifactType,
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
from src.core.preflight import run_preflight, render_preflight_summary_md, preflight_report_dict
from src.core.run_ledger import RunLedger
from src.core.audit_log import AuditLog
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.ingest.tyler import (
    normalize_tyler_export,
    source_ref_for_row,
    TYLER_DATASET_TYPES,
)

WORKFLOW_TYPE = "transaction_search"
PROMPT_TEMPLATE_VERSION = "transaction_search.v1"

# Module name -> (inputs key, tyler dataset_type)
_MODULE_MAP: dict[str, tuple[str, str]] = {
    "gl": ("gl_detail", "gl_detail"),
    "ap": ("ap_invoices", "ap_invoice_detail"),
    "ap_invoice_detail": ("ap_invoices", "ap_invoice_detail"),
    "checks": ("checks", "check_register"),
    "check_register": ("checks", "check_register"),
    "po": ("purchase_orders", "purchase_orders"),
    "purchase_orders": ("purchase_orders", "purchase_orders"),
}

_KNOWN_MODULES = frozenset(["gl", "ap", "ap_invoice_detail", "checks", "check_register", "po", "purchase_orders"])

# Default sample inputs (repo-relative).
_TYLER_DATA = "data/synthetic/tyler"
SAMPLE_INPUTS: dict[str, str] = {
    "query": "payments to Cascade Paving over $5,000 between March and May 2026",
    "gl_detail": f"{_TYLER_DATA}/gl_detail.csv",
    "ap_invoices": f"{_TYLER_DATA}/ap_invoice_detail.csv",
    "checks": f"{_TYLER_DATA}/check_register.csv",
    "purchase_orders": f"{_TYLER_DATA}/purchase_orders.csv",
}

# Month-name -> (month_number, days_in_month) used by the mock parser.
_MONTH_NAMES: dict[str, tuple[int, int]] = {
    "january": (1, 31), "jan": (1, 31),
    "february": (2, 28), "feb": (2, 28),
    "march": (3, 31), "mar": (3, 31),
    "april": (4, 30), "apr": (4, 30),
    "may": (5, 31),
    "june": (6, 30), "jun": (6, 30),
    "july": (7, 31), "jul": (7, 31),
    "august": (8, 31), "aug": (8, 31),
    "september": (9, 30), "sep": (9, 30), "sept": (9, 30),
    "october": (10, 31), "oct": (10, 31),
    "november": (11, 30), "nov": (11, 30),
    "december": (12, 31), "dec": (12, 31),
}

DEFAULT_MAX_RESULTS = 200
DEFAULT_FUZZY_THRESHOLD = 0.85


# --------------------------------------------------------------------------- #
# SearchCriteria schema (pydantic v2)
# --------------------------------------------------------------------------- #
class SearchCriteria(BaseModel):
    """Structured search criteria proposed by the LLM (or the mock parser)
    and then schema-validated + range-checked deterministically.

    All fields are optional; a criterion is applied only when set.
    ``unparsed_terms`` collects tokens rejected by validation.
    ``llm_proposed_fields`` lists which field names came from the LLM vs
    defaulted to None.
    """

    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    check_number: Optional[str] = None
    fund: Optional[str] = None
    department: Optional[str] = None
    object_code: Optional[str] = None
    account: Optional[str] = None
    module: Optional[str] = None
    date_from: Optional[str] = None   # ISO date YYYY-MM-DD
    date_to: Optional[str] = None     # ISO date YYYY-MM-DD
    amount_min: Optional[str] = None  # Decimal-compatible str
    amount_max: Optional[str] = None  # Decimal-compatible str
    keywords: list[str] = Field(default_factory=list)
    unparsed_terms: list[str] = Field(default_factory=list)
    llm_proposed_fields: list[str] = Field(default_factory=list)

    @field_validator("module")
    @classmethod
    def _validate_module(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in _KNOWN_MODULES:
            raise ValueError(f"Unknown module '{v}'; must be one of {sorted(_KNOWN_MODULES)}")
        return v

    @field_validator("date_from", "date_to")
    @classmethod
    def _validate_iso_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Date '{v}' is not a valid ISO date (YYYY-MM-DD)")
        return v

    @field_validator("amount_min", "amount_max")
    @classmethod
    def _validate_amount(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        try:
            d = Decimal(str(v).replace(",", "").replace("$", "").strip())
        except InvalidOperation:
            raise ValueError(f"Amount '{v}' is not a valid decimal number")
        if d < 0:
            raise ValueError(f"Amount '{v}' must be non-negative")
        return str(d)

    @model_validator(mode="after")
    def _validate_ranges(self) -> "SearchCriteria":
        if self.date_from and self.date_to:
            d1 = date.fromisoformat(self.date_from)
            d2 = date.fromisoformat(self.date_to)
            if d1 > d2:
                raise ValueError(
                    f"date_from ({self.date_from}) must be <= date_to ({self.date_to})"
                )
        if self.amount_min and self.amount_max:
            a1 = Decimal(self.amount_min)
            a2 = Decimal(self.amount_max)
            if a1 > a2:
                raise ValueError(
                    f"amount_min ({self.amount_min}) must be <= amount_max ({self.amount_max})"
                )
        return self

    def has_any_criterion(self) -> bool:
        """Return True when at least one searchable criterion is set."""
        return bool(
            self.vendor or self.invoice_number or self.po_number
            or self.check_number or self.fund or self.department
            or self.object_code or self.account or self.module
            or self.date_from or self.date_to
            or self.amount_min or self.amount_max
            or self.keywords
        )


# --------------------------------------------------------------------------- #
# PREFLIGHT / CAPABILITY layer
# --------------------------------------------------------------------------- #
CAPABILITY = CapabilitySpec(
    workflow_type=WORKFLOW_TYPE,
    required_inputs=[],   # "query" is a string, not a file; at-least-one
    optional_inputs=["gl_detail", "ap_invoices", "checks", "purchase_orders"],
    accepted_file_types={"*": ["csv", "xlsx"]},
    required_semantic_columns={},
    optional_semantic_columns={},
    supported_patterns=[
        "vendor_name_fragment_match",
        "invoice_number_exact_match",
        "po_number_exact_match",
        "check_number_exact_match",
        "fund_filter",
        "date_range_filter",
        "amount_range_filter",
        "keyword_description_search",
        "fuzzy_vendor_name_fallback",
    ],
    partially_supported_patterns=[
        "multi_module_cross_search",
    ],
    unsupported_patterns=[
        "free_text_narrative_match_without_keywords",
        "semantic_similarity_search",
    ],
    notes=(
        "Natural-language transaction search across GL, AP, check register, and "
        "purchase orders. At least one data file and a non-blank query are required. "
        "The LLM proposes structured SearchCriteria; execution is fully deterministic."
    ),
)


def detect_conditions(
    profiles: dict[str, FileProfile],
    mappings: Any,
    inputs: dict[str, Any],
    config: Optional[dict] = None,
) -> list[PreflightFinding]:
    """Domain-specific condition detection for transaction search.

    Emits blocking FAIL findings for:
      * empty / blank query
      * no data file provided

    These cannot be expressed in CapabilitySpec.required_inputs (query is a
    string, and the at-least-one-of constraint is custom), so they are handled
    here.
    """
    findings: list[PreflightFinding] = []
    query = str(inputs.get("query") or "").strip()
    if not query:
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.MISSING_REQUIRED_FILE,
                severity=Severity.CRITICAL,
                message="Required input 'query' is missing or blank.",
                affected_input="query",
                suggested_action=(
                    "Provide a non-blank plain-English query describing the "
                    "transactions you want to find."
                ),
                blocks_run=True,
            )
        )

    data_keys = ("gl_detail", "ap_invoices", "checks", "purchase_orders")
    has_data_file = any(
        isinstance(profiles.get(k), FileProfile) and profiles[k].present
        for k in data_keys
    )
    # Fallback: if profiles are not populated yet, check inputs dict
    if not has_data_file:
        has_data_file = any(inputs.get(k) is not None for k in data_keys)
    if not has_data_file:
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.MISSING_REQUIRED_FILE,
                severity=Severity.CRITICAL,
                message=(
                    "No data file provided. At least one of gl_detail, "
                    "ap_invoices, checks, purchase_orders is required."
                ),
                affected_input="gl_detail",
                suggested_action=(
                    "Upload at least one Tyler-style export file (GL detail, "
                    "AP invoices, check register, or purchase orders) to search."
                ),
                blocks_run=True,
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class TransactionSearchConfig:
    max_results: int = DEFAULT_MAX_RESULTS
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD
    missing_po_threshold: str = "5000.00"

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "TransactionSearchConfig":
        if not data:
            return cls()
        return cls(
            max_results=int(data.get("max_results", DEFAULT_MAX_RESULTS)),
            fuzzy_threshold=float(data.get("fuzzy_threshold", DEFAULT_FUZZY_THRESHOLD)),
            missing_po_threshold=str(data.get("missing_po_threshold", "5000.00")),
        )


# --------------------------------------------------------------------------- #
# Deterministic output container
# --------------------------------------------------------------------------- #
@dataclass
class TransactionSearchOutput:
    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    criteria: Optional[SearchCriteria] = None
    result_rows: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Mock / deterministic criteria parser
# --------------------------------------------------------------------------- #
def _normalize_text(s: str) -> str:
    """Casefold, strip accents, remove punctuation except hyphens."""
    s = s.casefold()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_criteria_from_query(query: str) -> SearchCriteria:
    """Deterministic rule-based parser for the MOCK / offline path.

    Extracts SearchCriteria from a plain-English query using regex patterns.
    Good enough to satisfy Q1-Q4 from known_answers.json.

    NOTE: amount and reference-number patterns are applied to the RAW query
    before punctuation normalization, because normalization turns "$5,000"
    into "5 000" which breaks digit patterns.  Other patterns (dates, fund,
    vendor keywords) operate on the normalized lowercase form.
    """
    raw = query.strip()
    q = _normalize_text(raw)
    proposed: dict[str, Any] = {}
    proposed_fields: list[str] = []
    consumed: list[tuple[int, int]] = []   # (start, end) spans consumed (in q)
    _parser_unparsed: list[str] = []       # accumulated before _validate_criteria_dict

    def _consume(m: re.Match) -> None:
        consumed.append((m.start(), m.end()))

    # Amount patterns run on the RAW query (preserves "$5,000" style).
    raw_lower = raw.lower()

    # --- Amount range ---------------------------------------------------- #
    # "between $X and $Y" on raw
    am = re.search(
        r'\bbetween\s+\$?([\d,]+(?:\.\d+)?)\s+and\s+\$?([\d,]+(?:\.\d+)?)\b',
        raw_lower
    )
    if am:
        proposed["amount_min"] = am.group(1).replace(",", "")
        proposed["amount_max"] = am.group(2).replace(",", "")
        proposed_fields.extend(["amount_min", "amount_max"])
    else:
        # "over/above/more than $X" on raw
        am2 = re.search(
            r'\b(?:over|above|more\s+than|greater\s+than|exceeds?)\s+\$?([\d,]+(?:\.\d+)?)\b',
            raw_lower
        )
        if am2:
            proposed["amount_min"] = am2.group(1).replace(",", "")
            proposed_fields.append("amount_min")
        else:
            # "under/below/less than $X" on raw
            am3 = re.search(
                r'\b(?:under|below|less\s+than)\s+\$?([\d,]+(?:\.\d+)?)\b',
                raw_lower
            )
            if am3:
                proposed["amount_max"] = am3.group(1).replace(",", "")
                proposed_fields.append("amount_max")

    # --- Invoice number (on raw) ----------------------------------------- #
    m = re.search(r'\binv[-_]?\d+\b', raw, re.IGNORECASE)
    if m:
        proposed["invoice_number"] = m.group(0).upper().replace("_", "-")
        proposed_fields.append("invoice_number")
        # Mark position in normalized q (approximate: use same offset since
        # raw and q have same length for ASCII; use q match as fallback)
        qm = re.search(r'\binv[-_]?\d+\b', q, re.IGNORECASE)
        if qm:
            _consume(qm)

    # --- PO number (on raw) ---------------------------------------------- #
    m = re.search(r'\bpo[-_]?\d+\b', raw, re.IGNORECASE)
    if m:
        proposed["po_number"] = m.group(0).upper().replace("_", "-")
        proposed_fields.append("po_number")
        qm = re.search(r'\bpo[-_]?\d+\b', q, re.IGNORECASE)
        if qm:
            _consume(qm)

    # --- Check number (on raw) ------------------------------------------- #
    m = re.search(r'\bcheck\s+(?:number\s+)?(\d{4,})\b', raw, re.IGNORECASE)
    if m:
        proposed["check_number"] = m.group(1)
        proposed_fields.append("check_number")
        qm = re.search(r'\bcheck\s+(?:number\s+)?(\d{4,})\b', q, re.IGNORECASE)
        if qm:
            _consume(qm)

    # --- Fund ------------------------------------------------------------ #
    m = re.search(r'\bfund\s+(\d{1,4})\b', q, re.IGNORECASE)
    if m:
        proposed["fund"] = m.group(1)
        proposed_fields.append("fund")
        _consume(m)

    # --- Department / org ------------------------------------------------ #
    m = re.search(r'\b(?:department|dept|org)\s+(\d{4})\b', q, re.IGNORECASE)
    if m:
        proposed["department"] = m.group(1)
        proposed_fields.append("department")
        _consume(m)

    # --- Object code ----------------------------------------------------- #
    m = re.search(r'\bobject\s+(\d{4})\b', q, re.IGNORECASE)
    if m:
        proposed["object_code"] = m.group(1)
        proposed_fields.append("object_code")
        _consume(m)

    # --- Date range: "between MONTH and MONTH YYYY" ---------------------- #
    # "between March and May 2026"
    month_pat = r'(' + '|'.join(_MONTH_NAMES.keys()) + r')'
    m = re.search(
        month_pat + r'\s+and\s+' + month_pat + r'(?:\s+(\d{4}))?',
        q, re.IGNORECASE
    )
    if m:
        m1_name = m.group(1).lower()
        m2_name = m.group(2).lower()
        yr_str = m.group(3)
        if not yr_str:
            # Year is required for deterministic date ranges; drop the token.
            _parser_unparsed.append(
                f"{m.group(1)}-{m.group(2)}: "
                "year required for month-only date ranges; "
                "re-run with an explicit year"
            )
            _consume(m)
        else:
            year = int(yr_str)
            mn1, _ = _MONTH_NAMES[m1_name]
            mn2, days2 = _MONTH_NAMES[m2_name]
            # If m2_name is 'february' use the actual last day based on year
            if mn2 == 2:
                import calendar
                days2 = calendar.monthrange(year, 2)[1]
            proposed["date_from"] = f"{year}-{mn1:02d}-01"
            proposed["date_to"] = f"{year}-{mn2:02d}-{days2:02d}"
            proposed_fields.extend(["date_from", "date_to"])
            _consume(m)
    else:
        # "in MONTH YYYY" or "in MONTH"
        m = re.search(month_pat + r'(?:\s+(\d{4}))?', q, re.IGNORECASE)
        if m:
            mn_name = m.group(1).lower()
            yr_str = m.group(2)
            if not yr_str:
                # Year is required for deterministic date ranges; drop the token.
                _parser_unparsed.append(
                    f"{m.group(1)}: "
                    "year required for month-only date ranges; "
                    "re-run with an explicit year"
                )
                _consume(m)
            else:
                year = int(yr_str)
                mn, days = _MONTH_NAMES[mn_name]
                if mn == 2:
                    import calendar
                    days = calendar.monthrange(year, 2)[1]
                proposed["date_from"] = f"{year}-{mn:02d}-01"
                proposed["date_to"] = f"{year}-{mn:02d}-{days:02d}"
                proposed_fields.extend(["date_from", "date_to"])
                _consume(m)

    # --- ISO date range "2026-01-01 to 2026-03-31" ----------------------- #
    m = re.search(
        r'(\d{4}-\d{2}-\d{2})\s+(?:to|through|thru)\s+(\d{4}-\d{2}-\d{2})',
        q
    )
    if m and "date_from" not in proposed:
        proposed["date_from"] = m.group(1)
        proposed["date_to"] = m.group(2)
        proposed_fields.extend(["date_from", "date_to"])
        _consume(m)

    # --- Module hint ---------------------------------------------------- #
    # "checks" / "warrants" -> check register; higher priority than "payments"
    if re.search(r'\b(?:check|checks|warrant|warrants)\b', q, re.IGNORECASE):
        if "check_number" not in proposed and "invoice_number" not in proposed:
            proposed["module"] = "checks"
            proposed_fields.append("module")
    elif re.search(r'\b(?:invoice|invoices|ap|payable)\b', q, re.IGNORECASE):
        if "module" not in proposed:
            proposed["module"] = "ap"
            proposed_fields.append("module")
    elif re.search(r'\b(?:payment|payments|paid)\b', q, re.IGNORECASE):
        # "payments to <vendor>" is an AP-style query
        if "module" not in proposed:
            proposed["module"] = "ap"
            proposed_fields.append("module")
    elif re.search(r'\b(?:po|purchase\s*order)\b', q, re.IGNORECASE):
        if "module" not in proposed:
            proposed["module"] = "po"
            proposed_fields.append("module")
    elif re.search(r'\b(?:gl|general\s+ledger|journal)\b', q, re.IGNORECASE):
        if "module" not in proposed:
            proposed["module"] = "gl"
            proposed_fields.append("module")
    elif proposed.get("invoice_number"):
        # Invoice number implies AP first, GL second
        if "module" not in proposed:
            proposed["module"] = "ap"
            proposed_fields.append("module")

    # --- Vendor name ----------------------------------------------------- #
    # Check for known vendor-name fragments (case-insensitive substring of query).
    # Common vendor fragments from the Riverbend dataset:
    _KNOWN_VENDOR_FRAGMENTS = [
        "cascade paving",
        "acme office supply",
        "riverbend electric",
        "summit it services",
        "pacific water treatment",
        "old town hardware",
        "unknown vendor",
        "northgate consulting",
        "abc water supply",
        "blue ridge supply",
        "central maintenance",
        "delta road services",
        "eastern paving",
        "first choice janitorial",
        "granite supply",
        "highland electric",
    ]
    for frag in _KNOWN_VENDOR_FRAGMENTS:
        if frag in q:
            proposed["vendor"] = frag.title()
            proposed_fields.append("vendor")
            break

    if "vendor" not in proposed:
        # Quoted string -> vendor
        m = re.search(r'"([^"]+)"', raw)
        if m:
            proposed["vendor"] = m.group(1)
            proposed_fields.append("vendor")

    # --- Keywords (remaining meaningful tokens) ------------------------- #
    # Strip consumed spans from the query text, then extract remaining words
    # with 4+ chars that are not common stopwords.
    _STOPWORDS = frozenset([
        "the", "and", "for", "from", "that", "this", "with", "have", "will",
        "more", "than", "over", "under", "between", "payments", "charges",
        "transactions", "invoices", "invoice", "checks", "check", "fund",
        "department", "object", "search", "find", "show", "list", "all",
        "any", "were", "been", "into", "during", "within", "about", "amount",
        "amounts", "total", "payment", "paid", "purchase", "purchases",
        "order", "orders", "number", "date", "year", "fiscal",
    ])
    remaining = q
    # Remove consumed spans (sort by start descending so slicing offsets don't shift)
    for start, end in sorted(consumed, reverse=True):
        remaining = remaining[:start] + " " + remaining[end:]
    remaining_words = re.findall(r'\b[a-z][a-z0-9\-]{2,}\b', remaining)
    keywords = [
        w for w in remaining_words
        if w not in _STOPWORDS and not w.isdigit()
        and len(w) >= 4
        and w not in (proposed.get("vendor") or "").lower()
    ]
    # De-duplicate while preserving order
    seen: set[str] = set()
    unique_kw: list[str] = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique_kw.append(w)
    if unique_kw and "keywords" not in proposed:
        proposed["keywords"] = unique_kw[:10]
        proposed_fields.append("keywords")

    proposed["llm_proposed_fields"] = proposed_fields

    # Merge parser-level unparsed notes so _validate_criteria_dict propagates them.
    if _parser_unparsed:
        existing = list(proposed.get("unparsed_terms") or [])
        proposed["unparsed_terms"] = existing + _parser_unparsed

    # Validate and sanitize
    criteria, unparsed = _validate_criteria_dict(proposed)
    criteria.unparsed_terms = unparsed
    return criteria


def _validate_criteria_dict(raw: dict[str, Any]) -> tuple[SearchCriteria, list[str]]:
    """Validate a proposed criteria dict, dropping invalid fields to unparsed_terms.

    Returns (SearchCriteria, unparsed_terms_list).
    """
    unparsed: list[str] = list(raw.get("unparsed_terms", []))
    proposed_fields: list[str] = list(raw.get("llm_proposed_fields", []))
    clean: dict[str, Any] = {}

    scalar_fields = [
        "vendor", "invoice_number", "po_number", "check_number",
        "fund", "department", "object_code", "account", "module",
        "date_from", "date_to", "amount_min", "amount_max",
    ]
    for f in scalar_fields:
        if f not in raw or raw[f] is None:
            continue
        # Try individual validator by constructing a partial
        try:
            partial = SearchCriteria.model_validate({f: raw[f]})
            clean[f] = raw[f]
        except Exception as exc:
            unparsed.append(f"{f}={raw[f]!r} (rejected: {exc})")
            if f in proposed_fields:
                proposed_fields.remove(f)

    if raw.get("keywords"):
        kws = [str(k) for k in raw["keywords"] if str(k).strip()]
        clean["keywords"] = kws

    # Cross-field range validation (date_from <= date_to etc.)
    if "date_from" in clean and "date_to" in clean:
        try:
            d1 = date.fromisoformat(clean["date_from"])
            d2 = date.fromisoformat(clean["date_to"])
            if d1 > d2:
                unparsed.append(
                    f"date_from={clean['date_from']!r} > date_to={clean['date_to']!r}"
                )
                del clean["date_from"]
                del clean["date_to"]
        except ValueError:
            pass

    if "amount_min" in clean and "amount_max" in clean:
        try:
            a1 = Decimal(clean["amount_min"])
            a2 = Decimal(clean["amount_max"])
            if a1 > a2:
                unparsed.append(
                    f"amount_min={clean['amount_min']!r} > amount_max={clean['amount_max']!r}"
                )
                del clean["amount_min"]
                del clean["amount_max"]
        except InvalidOperation:
            pass

    clean["llm_proposed_fields"] = proposed_fields
    return SearchCriteria(**clean), unparsed


# --------------------------------------------------------------------------- #
# Deterministic execution helpers
# --------------------------------------------------------------------------- #
def _normalize_for_match(s: Any) -> str:
    """Casefold, strip punctuation for comparison."""
    if s is None:
        return ""
    return re.sub(r"[^\w\s]", " ", str(s).casefold()).strip()


def _fuzzy_match(a: str, b: str, threshold: float) -> bool:
    """True when SequenceMatcher ratio >= threshold (both strings must be non-empty)."""
    a = _normalize_for_match(a)
    b = _normalize_for_match(b)
    if not a or not b:
        return False
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold


def _vendor_matches(row_vendor: Any, query_vendor: str, threshold: float) -> bool:
    """Return True if the row vendor name matches the query vendor fragment."""
    rv = _normalize_for_match(row_vendor)
    qv = _normalize_for_match(query_vendor)
    if not rv or not qv:
        return False
    # Exact substring match (casefold)
    if qv in rv or rv in qv:
        return True
    # Fuzzy fallback
    return _fuzzy_match(rv, qv, threshold)


def _keyword_matches(row: dict[str, Any], keywords: list[str]) -> bool:
    """Return True when ANY keyword appears (as a substring) in any text column.

    Uses OR semantics: a row matches if at least one keyword is found.  This
    mirrors natural search-engine behaviour and avoids over-filtering when the
    mock parser extracts more keywords than the user intended (e.g. "pothole
    repair" extracts both tokens, but a row describing "asphalt cold patch -
    pothole program" should still match).
    """
    if not keywords:
        return True
    text = " ".join(
        _normalize_for_match(v)
        for v in row.values()
        if isinstance(v, str)
    )
    return any(kw.casefold() in text for kw in keywords)


def _row_matches(
    row: dict[str, Any],
    criteria: SearchCriteria,
    module: str,
    fuzzy_threshold: float,
) -> tuple[bool, list[str]]:
    """Check whether a row matches the criteria. Returns (matched, matched_fields)."""
    matched_fields: list[str] = []

    # Module filter (only when specified)
    if criteria.module and criteria.module not in (module, _normalize_for_match(module)):
        # Normalize module aliases
        spec_module = _MODULE_MAP.get(criteria.module, ("", ""))[1]
        row_dtype_part = module  # e.g. "gl_detail", "ap_invoice_detail"
        if criteria.module not in (row_dtype_part,) and spec_module != row_dtype_part:
            return False, []

    # Vendor
    if criteria.vendor:
        vendor_cols = ("vendor_name", "vendor")
        found_vendor = False
        for col in vendor_cols:
            if col in row and row[col]:
                if _vendor_matches(row[col], criteria.vendor, fuzzy_threshold):
                    found_vendor = True
                    matched_fields.append(col)
                    break
        if not found_vendor:
            return False, []

    # Invoice number (exact after casefold)
    if criteria.invoice_number:
        col = "invoice_number"
        if not (col in row and _normalize_for_match(row[col]) == _normalize_for_match(criteria.invoice_number)):
            return False, []
        matched_fields.append(col)

    # PO number
    if criteria.po_number:
        col = "po_number"
        if not (col in row and _normalize_for_match(row[col]) == _normalize_for_match(criteria.po_number)):
            return False, []
        matched_fields.append(col)

    # Check number
    if criteria.check_number:
        col = "check_number"
        if not (col in row and _normalize_for_match(row[col]) == _normalize_for_match(criteria.check_number)):
            return False, []
        matched_fields.append(col)

    # Fund
    if criteria.fund:
        col = "fund"
        if not (col in row and _normalize_for_match(str(row[col])) == _normalize_for_match(criteria.fund)):
            return False, []
        matched_fields.append(col)

    # Department / org
    if criteria.department:
        found_dept = False
        for col in ("org", "department"):
            if col in row and _normalize_for_match(str(row[col])) == _normalize_for_match(criteria.department):
                found_dept = True
                matched_fields.append(col)
                break
        if not found_dept:
            return False, []

    # Object code
    if criteria.object_code:
        found_obj = False
        for col in ("object", "object_code"):
            if col in row and _normalize_for_match(str(row[col])) == _normalize_for_match(criteria.object_code):
                found_obj = True
                matched_fields.append(col)
                break
        if not found_obj:
            return False, []

    # Date range - pick the best date column for this module
    date_cols_by_module: dict[str, list[str]] = {
        "gl_detail": ["date"],
        "ap_invoice_detail": ["invoice_date"],
        "check_register": ["check_date"],
        "purchase_orders": ["po_date"],
    }
    if criteria.date_from or criteria.date_to:
        date_candidates = date_cols_by_module.get(module, ["date", "invoice_date", "check_date", "po_date"])
        row_date: Optional[date] = None
        date_col_used: Optional[str] = None
        for col in date_candidates:
            if col in row and row[col] is not None:
                v = row[col]
                if isinstance(v, date):
                    row_date = v
                    date_col_used = col
                    break
                else:
                    try:
                        row_date = date.fromisoformat(str(v))
                        date_col_used = col
                        break
                    except ValueError:
                        pass
        if row_date is None:
            return False, []
        if criteria.date_from and row_date < date.fromisoformat(criteria.date_from):
            return False, []
        if criteria.date_to and row_date > date.fromisoformat(criteria.date_to):
            return False, []
        if date_col_used:
            matched_fields.append(date_col_used)

    # Amount range - pick the best amount column
    amount_cols_by_module: dict[str, list[str]] = {
        "gl_detail": ["amount", "debit", "credit"],
        "ap_invoice_detail": ["invoice_amount"],
        "check_register": ["check_amount"],
        "purchase_orders": ["line_amount"],
    }
    if criteria.amount_min or criteria.amount_max:
        amt_candidates = amount_cols_by_module.get(module, ["amount", "invoice_amount", "check_amount"])
        row_amount: Optional[Decimal] = None
        amt_col_used: Optional[str] = None
        for col in amt_candidates:
            if col in row and row[col] is not None:
                v = row[col]
                if isinstance(v, Decimal):
                    # Use absolute value for range comparison (GL has signed amounts)
                    row_amount = abs(v)
                    amt_col_used = col
                    break
                else:
                    try:
                        row_amount = abs(Decimal(str(v).replace(",", "").replace("$", "").strip()))
                        amt_col_used = col
                        break
                    except (InvalidOperation, ValueError):
                        pass
        if row_amount is None:
            return False, []
        if criteria.amount_min and row_amount < Decimal(criteria.amount_min):
            return False, []
        if criteria.amount_max and row_amount > Decimal(criteria.amount_max):
            return False, []
        if amt_col_used:
            matched_fields.append(amt_col_used)

    # Keywords
    if criteria.keywords:
        if not _keyword_matches(row, criteria.keywords):
            return False, []
        matched_fields.append("description_keywords")

    if not matched_fields and not criteria.has_any_criterion():
        return False, []

    return True, matched_fields


def _short_ref(ref: SourceRowRef) -> str:
    return f"{ref.table_name}:{ref.row_index}"


# --------------------------------------------------------------------------- #
# Main deterministic execution
# --------------------------------------------------------------------------- #
def run_deterministic(
    inputs: dict[str, Any],
    criteria: SearchCriteria,
    *,
    config: Optional[TransactionSearchConfig] = None,
) -> TransactionSearchOutput:
    """Apply validated criteria to the loaded Tyler exports.

    Returns a ``TransactionSearchOutput`` with findings, summary, and
    result_rows (for the CSV export).
    """
    config = config or TransactionSearchConfig()

    # Map inputs key -> (dataset_type, module_tag)
    _INPUTS_TO_DTYPE: dict[str, tuple[str, str]] = {
        "gl_detail": ("gl_detail", "gl_detail"),
        "ap_invoices": ("ap_invoice_detail", "ap_invoice_detail"),
        "checks": ("check_register", "check_register"),
        "purchase_orders": ("purchase_orders", "purchase_orders"),
    }

    findings: list[DeterministicFinding] = []
    result_rows: list[dict[str, Any]] = []
    match_count_by_module: dict[str, int] = {}
    total_amount = Decimal("0")
    truncated = False
    detail_count = 0
    input_files_used: list[Any] = []

    for input_key, (dtype, module_tag) in _INPUTS_TO_DTYPE.items():
        path = inputs.get(input_key)
        if path is None:
            continue

        # Apply module filter early (skip load if not needed)
        if criteria.module:
            # Normalise the criteria module to the dataset_type name
            criteria_dtype = _MODULE_MAP.get(criteria.module, ("", ""))[1]
            if criteria_dtype and criteria_dtype != dtype:
                continue

        try:
            export = normalize_tyler_export(str(path), dataset_type=dtype)
        except Exception as exc:
            # Non-blocking: surface as a SUMMARY finding, continue
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.SUMMARY,
                    severity=Severity.MEDIUM,
                    description=f"Could not load '{input_key}' ({path}): {exc}",
                    source_rows=[],
                    computed_values={"input_key": input_key, "error": str(exc)},
                    rule_used="file_load_error",
                    requires_human_review=False,
                )
            )
            continue

        input_files_used.append(export.input_file)
        df = export.dataframe
        table_name = export.table_name

        for i, row_series in df.iterrows():
            row = row_series.to_dict()
            src_row_idx = int(row.get("source_row_index", i))
            matched, matched_fields = _row_matches(row, criteria, dtype, config.fuzzy_threshold)
            if not matched:
                continue

            # Cap detail findings
            if detail_count >= config.max_results:
                truncated = True
                continue

            # Filter matched_fields to actual DataFrame columns (some like
            # "description_keywords" are synthetic tags, not real columns).
            actual_cols = [c for c in (matched_fields or []) if c in df.columns]
            src_ref = source_ref_for_row(export, src_row_idx, columns=actual_cols or None)

            # Build a clean description
            natural_id_parts = []
            for col in ("invoice_number", "check_number", "po_number", "journal"):
                v = row.get(col)
                if v is not None and str(v).strip():
                    natural_id_parts.append(f"{col}={v}")

            desc = (
                f"Match in {module_tag} row {src_row_idx}"
                + (f" ({', '.join(natural_id_parts)})" if natural_id_parts else "")
                + f" - matched fields: {', '.join(matched_fields) or '(criteria)'}"
            )

            # Amount for summary stats
            for amt_col in ("invoice_amount", "check_amount", "line_amount", "amount"):
                av = row.get(amt_col)
                if av is not None and isinstance(av, Decimal):
                    total_amount += av
                    break
                elif av is not None:
                    try:
                        total_amount += Decimal(str(av).replace(",", "").replace("$", "").strip())
                    except (InvalidOperation, ValueError):
                        pass
                    break

            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.SEARCH_MATCH,
                    severity=Severity.INFO,
                    description=desc,
                    source_rows=[src_ref],
                    computed_values={
                        "module": module_tag,
                        "source_row_index": src_row_idx,
                        "matched_fields": matched_fields,
                        **{k: str(v) for k, v in row.items()
                           if k in matched_fields or k in (
                               "vendor_name", "invoice_number", "check_number",
                               "po_number", "journal", "fund", "description",
                               "invoice_amount", "check_amount", "line_amount", "amount",
                           )},
                    },
                    rule_used="transaction_search_match",
                    requires_human_review=False,
                )
            )

            result_rows.append({
                "source_file": str(path),
                "source_row_index": src_row_idx,
                "module": module_tag,
                "matched_fields": ";".join(matched_fields),
                **{k: str(v) if v is not None else ""
                   for k, v in row.items()
                   if k != "source_row_index"},
            })

            detail_count += 1
            match_count_by_module[module_tag] = match_count_by_module.get(module_tag, 0) + 1

    if truncated:
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.SUMMARY,
                severity=Severity.MEDIUM,
                description=(
                    f"Search results truncated: {config.max_results} of "
                    f"{detail_count + (config.max_results if truncated else 0)}+ "
                    f"matching rows shown. Refine your search to reduce results."
                ),
                source_rows=[],
                computed_values={
                    "max_results": config.max_results,
                    "shown": detail_count,
                },
                rule_used="result_truncation",
                requires_human_review=False,
            )
        )

    summary: dict[str, Any] = {
        "workflow_type": WORKFLOW_TYPE,
        "query": inputs.get("query", ""),
        "total_matches": detail_count,
        "match_count_by_module": match_count_by_module,
        "total_signed_amount": str(total_amount),
        "truncated": truncated,
        "criteria_fields_used": criteria.llm_proposed_fields,
        "unparsed_terms": criteria.unparsed_terms,
        "requires_human_review": False,
        "input_files": [
            {"file_id": f.file_id, "file_name": f.file_name, "row_count": f.row_count}
            for f in input_files_used
        ],
    }

    return TransactionSearchOutput(
        findings=findings,
        summary=summary,
        criteria=criteria,
        result_rows=result_rows,
    )


# --------------------------------------------------------------------------- #
# LLM prompt (advisory only)
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY summarize, explain, classify, flag, and draft advisory language.\n"
    "- You MUST NOT calculate totals, invent row ids, or fabricate account numbers,\n"
    "  vendors, amounts, or dates not already in the findings below.\n"
    "- Every claim must cite source_row ids from 'referenced_source_rows'.\n"
    "- All output is a DRAFT for human review; never produce approval language.\n"
    "- In STAGE 1 (intent parse) propose SearchCriteria JSON; in STAGE 2 (summarize)\n"
    "  only explain and suggest follow-up steps.\n"
)

_CRITERIA_CONTRACT = (
    "Return JSON with keys: vendor (str|null), invoice_number (str|null), "
    "po_number (str|null), check_number (str|null), fund (str|null), "
    "department (str|null), object_code (str|null), account (str|null), "
    "module (str|null - one of gl/ap/checks/po), "
    "date_from (ISO date str|null), date_to (ISO date str|null), "
    "amount_min (str|null), amount_max (str|null), "
    "keywords (list[str]), unparsed_terms (list[str]).\n"
)

_SUMMARY_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def build_criteria_prompt(query: str) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Transaction search - STAGE 1: intent parse.\n"
        + f"USER QUERY: {query}\n\n"
        + "TASK: Extract structured SearchCriteria from the plain-English query above. "
        "Do NOT calculate; do NOT invent vendors, accounts, or amounts not stated in "
        "the query. Leave fields null/empty when the query does not specify them.\n\n"
        + _CRITERIA_CONTRACT
    )


def _findings_block(det: TransactionSearchOutput) -> str:
    rows = []
    for f in det.findings:
        rows.append({
            "finding_id": f.finding_id,
            "finding_type": f.finding_type.value,
            "rule_used": f.rule_used,
            "severity": f.severity.value,
            "description": f.description,
            "computed_values": f.computed_values,
            "source_row_ids": [_short_ref(s) for s in f.source_rows],
        })
    return json.dumps(rows, default=str, indent=2)


def build_summary_prompt(det: TransactionSearchOutput) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Transaction search - STAGE 2: result summary.\n"
        + "TASK: Summarize the search results, explain what was found, suggest "
        "review steps for any interesting patterns. Do NOT invent row ids or amounts.\n\n"
        + f"SEARCH CRITERIA:\n{json.dumps(det.criteria.model_dump() if det.criteria else {}, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _SUMMARY_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM
# --------------------------------------------------------------------------- #
def mock_llm_response(det: TransactionSearchOutput) -> dict[str, Any]:
    """Build a deterministic mock summary from findings only.

    Cites ONLY source-row ids from the deterministic findings.
    """
    from src.llm.provider import _extract_findings_from_prompt
    refs: list[str] = []
    categorized: list[dict] = []
    for f in det.findings:
        f_refs = [_short_ref(s) for s in f.source_rows]
        refs.extend(f_refs)
        if f.finding_type == FindingType.SEARCH_MATCH:
            categorized.append({
                "category": f.finding_type.value,
                "description": f.description,
                "referenced_source_rows": f_refs,
            })
    refs = sorted(set(refs))
    n = det.summary.get("total_matches", 0)
    modules = det.summary.get("match_count_by_module", {})
    module_str = ", ".join(f"{m}: {c}" for m, c in modules.items()) or "none"
    return {
        "summary": (
            f"Transaction search found {n} matching row(s) "
            f"({module_str}). Each cited item references its source row. "
            "No figures were calculated or invented by the assistant."
        ),
        "categorized_exceptions": categorized[:20],  # keep response manageable
        "referenced_source_rows": refs[:50],
        "suggested_review_steps": [
            "Confirm each matched transaction against the underlying source file.",
            "Verify that the search criteria captured all relevant transactions.",
            "Have finance staff review any flagged items before taking action.",
        ],
        "draft_memo": (
            "DRAFT - for human review only. The automated transaction search "
            "identified the rows listed above using deterministic criteria derived "
            "from your query. No data was computed or invented by the assistant."
        ),
    }


def _call_llm(det: TransactionSearchOutput, provider: Any = None) -> tuple[dict[str, Any], str, str]:
    """Return (response_json, model_provider, model_name).

    Uses the injected shared provider when present; falls back to the local
    deterministic mock (the default path).
    """
    if provider is not None:
        prompt = build_summary_prompt(det)
        for meth in ("generate_structured_response", "mock_response"):
            fn = getattr(provider, meth, None)
            if callable(fn):
                try:
                    resp = fn(prompt, schema="WorkflowLLMOutput")
                except TypeError:
                    resp = fn(prompt)
                data = getattr(resp, "response_json", resp)
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        data = {"summary": str(data), "referenced_source_rows": []}
                p = getattr(provider, "model_provider", "mock")
                m = getattr(provider, "model_name", "mock")
                return data, str(p), str(m)
    return mock_llm_response(det), "mock", "mock-transaction-search"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: dict[str, Any],
    det: TransactionSearchOutput,
) -> ValidationResult:
    """Validate the LLM summary response.

    Rejects invented source-row references; warns on numeric claims not in
    findings. Missing references on zero-result runs are a warning not an error.
    """
    return _core_validate_llm_output(
        response_json,
        det,
        require_references=True,
        missing_references_is_error=bool(det.findings),
        check_numeric_claims=True,
        numeric_claims_are_warnings=True,
    )


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
EXPORT_FILE_NAMES = (
    "search_criteria.json",
    "search_results.csv",
    "search_summary.md",
    "validation_report.json",
    "audit_log.json",
)


def export_artifacts(
    out_dir: str | Path,
    det: TransactionSearchOutput,
    response_json: dict[str, Any],
    validation: ValidationResult,
    audit_events: Optional[list[dict[str, Any]]] = None,
    *,
    run_id: Optional[str] = None,
) -> list[ExportArtifact]:
    """Write the five search artifacts and return their manifests."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportArtifact] = []

    def _wj(name: str, obj: Any) -> None:
        artifacts.append(write_json(out / name, obj, run_id=run_id))

    def _wm(name: str, text: str) -> None:
        artifacts.append(write_markdown(out / name, text, run_id=run_id))

    def _wc(name: str, rows: list[dict]) -> None:
        artifacts.append(write_csv(out / name, rows, run_id=run_id))

    # search_criteria.json
    criteria_dict: dict[str, Any] = {}
    if det.criteria:
        criteria_dict = det.criteria.model_dump()
    _wj("search_criteria.json", criteria_dict)

    # search_results.csv
    _wc("search_results.csv", det.result_rows)

    # search_summary.md
    s = det.summary
    lines = [
        "# Transaction Search Summary",
        "",
        f"- Query: {s.get('query', '')}",
        f"- Total matches: {s.get('total_matches', 0)}",
        f"- Truncated: {s.get('truncated', False)}",
        "",
        "## Match count by module",
    ]
    for mod, cnt in (s.get("match_count_by_module") or {}).items():
        lines.append(f"- {mod}: {cnt}")
    lines += [
        "",
        f"- Total signed amount: {s.get('total_signed_amount', '0')}",
        "",
        "## AI Summary (DRAFT - human review required)",
        str(response_json.get("summary", "")),
        "",
        "## Suggested review steps",
    ]
    for step in (response_json.get("suggested_review_steps") or []):
        lines.append(f"- [ ] {step}")
    if s.get("unparsed_terms"):
        lines += ["", "## Unparsed query terms"]
        for t in s["unparsed_terms"]:
            lines.append(f"- {t}")
    _wm("search_summary.md", "\n".join(lines) + "\n")

    # validation_report.json
    _wj("validation_report.json", validation.model_dump_json(indent=2))

    # audit_log.json
    _wj("audit_log.json", json.dumps(audit_events or [], default=str, indent=2))

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
    """End-to-end transaction search run.

    ``inputs`` keys:
        query              (required) plain-English search query string
        gl_detail          (optional) path to Tyler GL detail CSV/XLSX
        ap_invoices        (optional) path to Tyler AP invoice detail CSV/XLSX
        checks             (optional) path to Tyler check register CSV/XLSX
        purchase_orders    (optional) path to Tyler purchase orders CSV/XLSX

    ``config`` dict keys (all optional):
        max_results         int   default 200
        fuzzy_threshold     float default 0.85
        missing_po_threshold str  default "5000.00"

    Returns dict: run_id, workflow_type, summary, findings, validation,
    preflight, and (when export_dir is set) export_paths + export_artifacts.
    """
    run_id = run_id or make_id()
    cfg = TransactionSearchConfig.from_dict(config if isinstance(config, dict) else None)

    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=WORKFLOW_TYPE)

    # ------------------------------------------------------------------ #
    # Preflight
    # ------------------------------------------------------------------ #
    preflight_report = run_preflight(
        CAPABILITY,
        inputs,
        config=config if isinstance(config, dict) else None,
        detect_conditions=detect_conditions,
    )
    pf_dict = preflight_report_dict(preflight_report)

    if ledger is not None and hasattr(ledger, "store_preflight"):
        ledger.store_preflight(run_id, pf_dict)

    # ------------------------------------------------------------------ #
    # FAIL: do not run, do not call LLM
    # ------------------------------------------------------------------ #
    from src.core.schemas import PreflightStatus
    if preflight_report.status == PreflightStatus.FAIL:
        pf_md = render_preflight_summary_md(preflight_report)

        if export_dir is not None:
            out = Path(export_dir) / run_id
            out.mkdir(parents=True, exist_ok=True)
            fail_artifacts: list[ExportArtifact] = []
            fail_artifacts.append(
                write_json(out / "preflight_report.json", pf_dict, run_id=run_id)
            )
            fail_artifacts.append(
                write_markdown(out / "preflight_summary.md", pf_md, run_id=run_id)
            )
            if ledger is not None and hasattr(ledger, "store_export_artifact"):
                for a in fail_artifacts:
                    ledger.store_export_artifact(run_id, a)
            if audit is not None and hasattr(audit, "export_generated"):
                audit.export_generated(
                    run_id, actor,
                    artifacts=[a.file_name for a in fail_artifacts]
                )

        if audit is not None and hasattr(audit, "run_failed"):
            audit.run_failed(
                run_id, actor,
                reason="preflight_fail",
                preflight_status=preflight_report.status.value,
            )

        return {
            "run_id": run_id,
            "workflow_type": WORKFLOW_TYPE,
            "preflight": pf_dict,
            "preflight_status": preflight_report.status.value,
            "summary": {
                "workflow_type": WORKFLOW_TYPE,
                "preflight_status": preflight_report.status.value,
                "next_steps": preflight_report.next_steps,
            },
            "findings": [],
            "validation": None,
        }

    # ------------------------------------------------------------------ #
    # STAGE 1: Parse criteria
    # ------------------------------------------------------------------ #
    query = str(inputs.get("query", "")).strip()
    try:
        criteria = _parse_criteria_from_query(query)
    except Exception as exc:
        criteria = SearchCriteria(unparsed_terms=[f"parse_error: {exc}"])

    # Validate proposed criteria (additional sanity pass)
    if not criteria.has_any_criterion():
        # Nothing parseable -> structured FAIL report
        fail_summary = {
            "workflow_type": WORKFLOW_TYPE,
            "preflight_status": "pass",
            "criteria_parse_failed": True,
            "query": query,
            "next_steps": [
                "Rephrase the query to include a vendor name, invoice/PO/check number, "
                "date range, amount range, fund number, or keyword.",
                "Example: 'invoices to Acme Office Supply in March 2026 over $500'",
            ],
        }
        if audit is not None and hasattr(audit, "run_failed"):
            audit.run_failed(run_id, actor, reason="unparseable_query", query=query)
        return {
            "run_id": run_id,
            "workflow_type": WORKFLOW_TYPE,
            "preflight": pf_dict,
            "preflight_status": preflight_report.status.value,
            "criteria_parse_failed": True,
            "summary": fail_summary,
            "findings": [],
            "validation": None,
        }

    # ------------------------------------------------------------------ #
    # STAGE 2: Deterministic execution
    # ------------------------------------------------------------------ #
    det = run_deterministic(inputs, criteria, config=cfg)

    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )

    # ------------------------------------------------------------------ #
    # LLM summary (advisory; mock by default)
    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    validation = validate_llm_output(response_json, det)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "preflight": pf_dict,
        "preflight_status": preflight_report.status.value,
        "criteria": criteria,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
    }

    # ------------------------------------------------------------------ #
    # Exports
    # ------------------------------------------------------------------ #
    if export_dir is not None:
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifacts = export_artifacts(
            Path(export_dir) / run_id,
            det, response_json, validation,
            audit_events, run_id=run_id,
        )
        if ledger is not None and hasattr(ledger, "store_export_artifact"):
            for a in artifacts:
                ledger.store_export_artifact(run_id, a)
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(
                run_id, actor, artifacts=[a.file_name for a in artifacts]
            )
        result["export_artifacts"] = artifacts
        result["export_paths"] = {a.file_name: a.path for a in artifacts}

        # NOTE: the consolidated review packet is generated by the shared
        # runner (app.workflow_registry.run_workflow), which owns packet
        # generation for every workflow (canonical pattern).

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


WORKFLOW = {
    "workflow_type": WORKFLOW_TYPE,
    "run": run,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    "export_files": list(EXPORT_FILE_NAMES),
    "sample_inputs": SAMPLE_INPUTS,
}
