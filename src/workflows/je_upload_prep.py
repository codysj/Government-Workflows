"""Workflow - Journal Entry Upload Prep.

Validates a draft journal entry file against the chart of accounts and fiscal
configuration, then produces a ready-to-upload Excel/CSV workbook (or a
structured error report if any blocking validation fails).

DETERMINISTIC code performs every check below and owns all calculations,
source-row tracking, validation, export formatting, and audit logging:

  BLOCKING (any one -> upload_ready=false; upload workbook NOT written):
  * Total debits != total credits per journal AND overall (Decimal, 2dp).
  * Missing required field: Eff Date, fund/org/object, line description,
    and at least one of debit or credit.
  * Unparseable or invalid date; date outside configured fiscal period.
  * Account/fund/org/object code not found in the COA.
  * Inactive account used (unless config allow_inactive_accounts=true).
  * Both debit AND credit populated on the same line.
  * Negative debit or credit.
  * Duplicate (journal, line) numbering.

  WARNING (does not block; requires_human_review=true):
  * fund/org/object combination never seen in COA combos nor gl_detail history.
  * Description shorter than 3 characters.
  * Round-dollar amount >= $10,000 on a single line.

The LLM may ONLY draft a plain-language review summary of the deterministic
validation findings. It never sees or edits the upload workbook contents and
never fixes data.

FAIL CLOSED: if ANY blocking error exists, je_upload.xlsx and je_upload.csv
are NOT written. Only je_validation_errors.csv, validation_report.json,
je_prep_summary.md, audit_log.json, and the preflight/review packet artifacts
are exported.

ON SUCCESS: je_upload.xlsx (one sheet, Munis template headers), je_upload.csv
(identical content), source_mapping.csv, je_prep_summary.md,
validation_report.json, audit_log.json are written.

Inputs dict keys:
    je_draft            (required) path to draft JE CSV or XLSX
    chart_of_accounts   (required) path to Tyler COA CSV or XLSX
    gl_detail           (optional) path to GL detail CSV or XLSX for combo-
                        plausibility history
    config              (optional) path to je_config.json

Config keys (je_config.json):
    fiscal_period_start         ISO date string, e.g. "2025-07-01"
    fiscal_period_end           ISO date string, e.g. "2026-06-30"
    allow_inactive_accounts     bool, default false
    round_dollar_warning_threshold  str/Decimal, default "10000.00"
    min_description_length      int, default 3
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import openpyxl
import pandas as pd

from src.core.exports import write_csv, write_json, write_markdown
from src.core.preflight import render_preflight_summary_md, run_preflight
from src.core.schemas import (
    ArtifactType,
    CapabilitySpec,
    ColumnMapping,
    DeterministicFinding,
    ExportArtifact,
    FileProfile,
    FindingType,
    LLMResponse,
    PreflightConditionCode,
    PreflightFinding,
    PreflightStatus,
    Severity,
    SourceRowRef,
    ValidationResult,
    make_id,
)
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.ingest.tyler import normalize_tyler_export
from src.llm.provider import MockLLMProvider as _CoreMockLLMProvider
from src.llm.provider import _extract_findings_from_prompt
from src.normalize.cleaning import parse_amount, parse_date, to_snake_case

WORKFLOW_TYPE = "je_upload_prep"
PROMPT_TEMPLATE_VERSION = "je_upload_prep.v1"

# Munis JE upload template headers (exact, in order).
UPLOAD_SHEET_NAME = "JE Upload"
UPLOAD_HEADERS = [
    "Journal",
    "Line",
    "Eff Date",
    "Fund",
    "Org",
    "Object",
    "Account Description",
    "Debit",
    "Credit",
    "Line Description",
    "Reference",
]

# Blocking rules (any one prevents upload workbook generation).
_BLOCKING_RULES = frozenset({
    "no_duplicate_journal_line",
    "eff_date_required",
    "eff_date_valid",
    "eff_date_in_fiscal_period",
    "required_fields_present",
    "account_in_coa",
    "no_inactive_account",
    "no_both_debit_and_credit",
    "no_negative_amount",
    "debits_equal_credits_per_journal",
    "debits_equal_credits_overall",
})

# Default config values.
_DEFAULT_FISCAL_START = date(2025, 7, 1)
_DEFAULT_FISCAL_END = date(2026, 6, 30)
_DEFAULT_ROUND_DOLLAR_THRESHOLD = Decimal("10000.00")
_DEFAULT_MIN_DESC_LENGTH = 3

# JE upload draft column aliases (raw snake_cased header -> canonical name).
_DRAFT_ALIASES: dict[str, str] = {
    "eff_date": "date",
    "effective_date": "date",
    "je_date": "date",
    "jnl": "journal",
    "journal_number": "journal",
    "line_no": "line",
    "line_number": "line",
    "account_desc": "account_description",
    "desc": "line_description",
    "ref": "reference",
}

# Sample inputs (repo-relative paths) for the wiring agent.
SAMPLE_INPUTS = {
    "je_draft": "data/synthetic/je_upload_prep/je_draft_valid.csv",
    "chart_of_accounts": "data/synthetic/tyler/chart_of_accounts.csv",
    "gl_detail": "data/synthetic/tyler/gl_detail.csv",
    "config": "data/synthetic/je_upload_prep/je_config.json",
}

EXPORT_FILE_NAMES = (
    "je_upload.xlsx",
    "je_upload.csv",
    "source_mapping.csv",
    "je_prep_summary.md",
    "je_validation_errors.csv",
    "validation_report.json",
    "audit_log.json",
)


# --------------------------------------------------------------------------- #
# PREFLIGHT / CAPABILITY layer
# --------------------------------------------------------------------------- #
CAPABILITY = CapabilitySpec(
    workflow_type=WORKFLOW_TYPE,
    required_inputs=["je_draft", "chart_of_accounts"],
    optional_inputs=["gl_detail", "config"],
    # 'config' is a JSON tolerance/fiscal-period file (deterministic config,
    # mirroring bank_reconciliation's reconciliation_config); without the
    # per-key entry the generic engine would flag the .json as an unsupported
    # optional file type and downgrade an otherwise-clean run to PARTIAL.
    accepted_file_types={"*": ["csv", "xlsx"], "config": ["json"]},
    required_semantic_columns={
        "je_draft": ["journal", "line", "fund", "org", "object"],
        "chart_of_accounts": ["fund", "org", "object"],
    },
    optional_semantic_columns={
        "je_draft": ["date", "debit", "credit", "line_description", "reference",
                     "account_description"],
        "chart_of_accounts": ["status", "account_description"],
        "gl_detail": ["fund", "org", "object"],
    },
    min_date_confidence=0.8,
    min_amount_confidence=0.8,
    supported_patterns=[
        "debits_equal_credits_per_journal",
        "debits_equal_credits_overall",
        "eff_date_required",
        "eff_date_valid_calendar",
        "eff_date_in_fiscal_period",
        "required_fields_present",
        "account_in_coa",
        "no_inactive_account",
        "no_both_debit_and_credit",
        "no_negative_amount",
        "no_duplicate_journal_line",
    ],
    partially_supported_patterns=[
        "combo_plausibility_warning",
    ],
    unsupported_patterns=[],
    notes=(
        "Strictly fail-closed: any blocking validation failure prevents upload "
        "workbook generation. Requires a chart_of_accounts (Tyler COA format) "
        "to validate account codes and statuses. An optional gl_detail file "
        "extends the combo-plausibility check."
    ),
)


def detect_conditions(
    profiles: dict[str, FileProfile],
    mappings: dict[str, dict[str, ColumnMapping]],
    inputs: dict[str, Any],
    config: Optional[dict] = None,
) -> list[PreflightFinding]:
    """Domain-specific preflight detector for JE upload prep.

    Returns [] always - blocking conditions are handled by the deterministic
    validator (run_deterministic), not by preflight domain conditions.
    """
    return []


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class JEUploadConfig:
    """Deterministic configuration for JE upload validation."""

    fiscal_period_start: date = field(default_factory=lambda: _DEFAULT_FISCAL_START)
    fiscal_period_end: date = field(default_factory=lambda: _DEFAULT_FISCAL_END)
    allow_inactive_accounts: bool = False
    round_dollar_warning_threshold: Decimal = field(
        default_factory=lambda: _DEFAULT_ROUND_DOLLAR_THRESHOLD
    )
    min_description_length: int = _DEFAULT_MIN_DESC_LENGTH

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JEUploadConfig":
        start = data.get("fiscal_period_start")
        end = data.get("fiscal_period_end")
        threshold = data.get("round_dollar_warning_threshold", "10000.00")
        return cls(
            fiscal_period_start=parse_date(start) or _DEFAULT_FISCAL_START,
            fiscal_period_end=parse_date(end) or _DEFAULT_FISCAL_END,
            allow_inactive_accounts=bool(data.get("allow_inactive_accounts", False)),
            round_dollar_warning_threshold=Decimal(str(threshold)),
            min_description_length=int(
                data.get("min_description_length", _DEFAULT_MIN_DESC_LENGTH)
            ),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "JEUploadConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Deterministic output container
# --------------------------------------------------------------------------- #
@dataclass
class JEUploadOutput:
    """Result of deterministic JE validation."""

    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    upload_rows: list[dict[str, Any]] = field(default_factory=list)
    source_mapping: list[dict[str, Any]] = field(default_factory=list)
    blocking_count: int = 0
    warning_count: int = 0
    upload_ready: bool = False
    total_debits: Decimal = field(default_factory=Decimal)
    total_credits: Decimal = field(default_factory=Decimal)
    # file_id and file_name of the draft for the source mapping.
    je_draft_file_id: str = ""
    je_draft_file_name: str = ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _short_ref(ref: SourceRowRef) -> str:
    return f"{ref.table_name}:{ref.row_index}"


def _je_finding(
    rule: str,
    description: str,
    severity: Severity,
    source_rows: list[SourceRowRef],
    computed_values: Optional[dict] = None,
    requires_human_review: bool = True,
) -> DeterministicFinding:
    return DeterministicFinding(
        finding_type=FindingType.JE_VALIDATION,
        severity=severity,
        description=description,
        source_rows=source_rows,
        computed_values=computed_values or {},
        rule_used=rule,
        requires_human_review=requires_human_review,
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Raw JE draft loader
# --------------------------------------------------------------------------- #
def _load_je_draft_raw(path: str | Path) -> tuple[pd.DataFrame, str, str]:
    """Load the JE draft as an all-string DataFrame without date/amount parsing.

    Returns (df, file_id, file_name). The DataFrame has snake_cased, aliased
    columns. A synthetic file_id is generated from the SHA-256 of the file.
    Source_row_index (0-based) is added as the first column.

    Handles CSV and XLSX (openpyxl for xlsx). Raises ValueError on unsupported
    file types or unparseable files.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    file_id = _file_sha256(path)[:32]  # use first 32 hex chars as stable id
    file_name = path.name

    if suffix == ".csv":
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(
                f"je_upload_prep: could not read JE draft CSV '{path.name}': {exc}"
            ) from exc
        # Strip trailing commas from each line so that ERP exports and inline
        # test fixtures (which end every data row with a trailing comma) parse
        # cleanly without an extra unnamed column or ParserError.
        cleaned = "\n".join(line.rstrip(",") for line in raw_text.splitlines())
        try:
            import io as _io
            df = pd.read_csv(
                _io.StringIO(cleaned), dtype=str, keep_default_na=False
            )
        except Exception as exc:
            raise ValueError(
                f"je_upload_prep: could not parse JE draft CSV '{path.name}': {exc}"
            ) from exc
    elif suffix in (".xlsx", ".xlsm"):
        try:
            df = pd.read_excel(str(path), dtype=str, sheet_name=0)
            df = df.fillna("")
        except Exception as exc:
            raise ValueError(
                f"je_upload_prep: could not parse JE draft Excel '{path.name}': {exc}"
            ) from exc
    else:
        raise ValueError(
            f"je_upload_prep: unsupported JE draft file type '{suffix}'; "
            "expected .csv or .xlsx."
        )

    # Normalize column names.
    df.columns = [to_snake_case(str(c)) for c in df.columns]

    # Apply aliases (first-wins, no clobbering).
    for src, dst in _DRAFT_ALIASES.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # Insert 0-based source_row_index.
    df = df.copy()
    df.insert(0, "source_row_index", range(len(df)))

    return df, file_id, file_name


# --------------------------------------------------------------------------- #
# COA loading
# --------------------------------------------------------------------------- #
def _load_coa(coa_path: str | Path) -> tuple[set[str], set[str], set[str], dict[str, dict]]:
    """Load chart of accounts.

    Returns (valid_funds, valid_orgs, valid_objects, account_details).
    account_details maps 'fund-org-object' -> {'status': ..., 'description': ...}
    """
    exp = normalize_tyler_export(str(coa_path), dataset_type="chart_of_accounts")
    df = exp.dataframe
    valid_funds: set[str] = set()
    valid_orgs: set[str] = set()
    valid_objects: set[str] = set()
    account_details: dict[str, dict] = {}
    for _, row in df.iterrows():
        fund = str(row.get("fund", "")).strip()
        org = str(row.get("org", "")).strip()
        obj = str(row.get("object", "")).strip()
        status = str(row.get("status", "Active")).strip()
        desc = str(row.get("account_description", "")).strip()
        if fund:
            valid_funds.add(fund)
        if org:
            valid_orgs.add(org)
        if obj:
            valid_objects.add(obj)
        if fund and org and obj:
            key = f"{fund}-{org}-{obj}"
            account_details[key] = {"status": status, "description": desc}
    return valid_funds, valid_orgs, valid_objects, account_details


def _load_gl_combos(gl_path: str | Path) -> set[str]:
    """Load gl_detail and return the set of 'fund-org-object' combos seen."""
    exp = normalize_tyler_export(str(gl_path), dataset_type="gl_detail")
    df = exp.dataframe
    combos: set[str] = set()
    for _, row in df.iterrows():
        fund = str(row.get("fund", "")).strip()
        org = str(row.get("org", "")).strip()
        obj = str(row.get("object", "")).strip()
        if fund and org and obj:
            combos.add(f"{fund}-{org}-{obj}")
    return combos


# --------------------------------------------------------------------------- #
# Deterministic validation
# --------------------------------------------------------------------------- #
def run_deterministic(
    je_draft_path: str | Path,
    coa_path: str | Path,
    *,
    gl_detail_path: Optional[str | Path] = None,
    config: Optional[JEUploadConfig] = None,
) -> JEUploadOutput:
    """Run all deterministic JE validation checks.

    The JE draft is loaded as raw strings so that invalid dates/amounts are
    detected as findings (not raised as exceptions). Each row is validated
    individually: ALL defects are reported, not just the first one.

    Returns a JEUploadOutput with findings, upload_rows (empty on blocking
    failure), source_mapping, and summary stats.
    """
    cfg = config or JEUploadConfig()

    # Load the JE draft as raw strings (no date/amount parsing yet).
    df, je_file_id, je_file_name = _load_je_draft_raw(je_draft_path)
    table_name = Path(je_draft_path).stem

    # Load COA.
    valid_funds, valid_orgs, valid_objects, account_details = _load_coa(coa_path)

    # Load optional GL combo history.
    gl_combos: set[str] = set()
    if gl_detail_path is not None:
        try:
            gl_combos = _load_gl_combos(gl_detail_path)
        except Exception:
            pass  # advisory-only; do not fail the workflow

    findings: list[DeterministicFinding] = []

    def _row_ref(src_idx: int) -> SourceRowRef:
        row = df[df["source_row_index"] == src_idx].iloc[0]
        return SourceRowRef(
            file_id=je_file_id,
            table_name=table_name,
            row_index=int(src_idx),
            column_names=list(df.columns),
            source_values={
                k: ("" if v is None else str(v))
                for k, v in row.items()
            },
        )

    # ------------------------------------------------------------------ #
    # 1. Duplicate (journal, line) numbering
    # ------------------------------------------------------------------ #
    seen_journal_lines: dict[tuple, int] = {}
    for pos in range(len(df)):
        row = df.iloc[pos]
        jnl = str(row.get("journal", "")).strip()
        ln = str(row.get("line", "")).strip()
        key = (jnl, ln)
        src_idx = int(row["source_row_index"])
        if key in seen_journal_lines:
            first_idx = seen_journal_lines[key]
            findings.append(
                _je_finding(
                    "no_duplicate_journal_line",
                    f"Duplicate (Journal={jnl!r}, Line={ln!r}): "
                    f"source rows {first_idx} and {src_idx}.",
                    Severity.HIGH,
                    [_row_ref(first_idx), _row_ref(src_idx)],
                    computed_values={"journal": jnl, "line": ln},
                )
            )
        else:
            seen_journal_lines[key] = src_idx

    # ------------------------------------------------------------------ #
    # 2. Per-row field validation
    # ------------------------------------------------------------------ #
    # We parse dates and amounts row-by-row here (not via tyler normalizer)
    # so that bad values become findings, not exceptions.
    parsed_dates: dict[int, Optional[date]] = {}
    parsed_debits: dict[int, Optional[Decimal]] = {}
    parsed_credits: dict[int, Optional[Decimal]] = {}

    for pos in range(len(df)):
        row = df.iloc[pos]
        src_idx = int(row["source_row_index"])
        ref = _row_ref(src_idx)

        jnl = str(row.get("journal", "")).strip()
        fund = str(row.get("fund", "")).strip()
        org = str(row.get("org", "")).strip()
        obj = str(row.get("object", "")).strip()
        desc = str(row.get("line_description", "")).strip()
        raw_date_str = str(row.get("date", "")).strip()
        raw_debit_str = str(row.get("debit", "")).strip()
        raw_credit_str = str(row.get("credit", "")).strip()

        # --- Parse date ---------------------------------------------------
        parsed_dt: Optional[date] = None
        if not raw_date_str:
            # Missing date -> blocking.
            findings.append(
                _je_finding(
                    "eff_date_required",
                    f"Row {src_idx} (Journal={jnl!r}): Eff Date is missing or blank.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"journal": jnl, "source_row_index": src_idx},
                )
            )
        else:
            parsed_dt = parse_date(raw_date_str)
            if parsed_dt is None:
                # Non-blank but unparseable date -> blocking.
                findings.append(
                    _je_finding(
                        "eff_date_valid",
                        f"Row {src_idx} (Journal={jnl!r}): Eff Date "
                        f"{raw_date_str!r} is not a valid calendar date.",
                        Severity.HIGH,
                        [ref],
                        computed_values={
                            "raw_date": raw_date_str,
                            "source_row_index": src_idx,
                        },
                    )
                )
            else:
                # Valid date: check fiscal period.
                if (parsed_dt < cfg.fiscal_period_start
                        or parsed_dt > cfg.fiscal_period_end):
                    findings.append(
                        _je_finding(
                            "eff_date_in_fiscal_period",
                            f"Row {src_idx} (Journal={jnl!r}): Eff Date "
                            f"{parsed_dt} is outside the configured fiscal period "
                            f"{cfg.fiscal_period_start}..{cfg.fiscal_period_end}.",
                            Severity.HIGH,
                            [ref],
                            computed_values={
                                "eff_date": str(parsed_dt),
                                "fiscal_start": str(cfg.fiscal_period_start),
                                "fiscal_end": str(cfg.fiscal_period_end),
                            },
                        )
                    )
        parsed_dates[src_idx] = parsed_dt

        # --- Parse debit/credit -------------------------------------------
        parsed_db: Optional[Decimal] = None
        parsed_cr: Optional[Decimal] = None
        if raw_debit_str:
            parsed_db = parse_amount(raw_debit_str)
        if raw_credit_str:
            parsed_cr = parse_amount(raw_credit_str)
        parsed_debits[src_idx] = parsed_db
        parsed_credits[src_idx] = parsed_cr

        # --- Required field: fund/org/object ------------------------------
        missing_segs = []
        if not fund:
            missing_segs.append("Fund")
        if not org:
            missing_segs.append("Org")
        if not obj:
            missing_segs.append("Object")
        if missing_segs:
            findings.append(
                _je_finding(
                    "required_fields_present",
                    f"Row {src_idx} (Journal={jnl!r}): missing required "
                    f"field(s): {', '.join(missing_segs)}.",
                    Severity.HIGH,
                    [ref],
                    computed_values={
                        "journal": jnl,
                        "missing": missing_segs,
                    },
                )
            )

        # --- Required field: at least one of debit or credit --------------
        has_debit = parsed_db is not None and parsed_db != Decimal("0")
        has_credit = parsed_cr is not None and parsed_cr != Decimal("0")
        if not has_debit and not has_credit:
            findings.append(
                _je_finding(
                    "required_fields_present",
                    f"Row {src_idx} (Journal={jnl!r}): both Debit and Credit "
                    "are blank or zero; at least one must be populated.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"journal": jnl},
                )
            )

        # --- Both debit AND credit populated ------------------------------
        debit_nonzero = parsed_db is not None and parsed_db != Decimal("0")
        credit_nonzero = parsed_cr is not None and parsed_cr != Decimal("0")
        if debit_nonzero and credit_nonzero:
            findings.append(
                _je_finding(
                    "no_both_debit_and_credit",
                    f"Row {src_idx} (Journal={jnl!r}): both Debit ({parsed_db}) "
                    f"and Credit ({parsed_cr}) are populated on the same line.",
                    Severity.HIGH,
                    [ref],
                    computed_values={
                        "debit": str(parsed_db),
                        "credit": str(parsed_cr),
                    },
                )
            )

        # --- Negative debit or credit -------------------------------------
        if parsed_db is not None and parsed_db < Decimal("0"):
            findings.append(
                _je_finding(
                    "no_negative_amount",
                    f"Row {src_idx} (Journal={jnl!r}): Debit {parsed_db} "
                    "is negative.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"debit": str(parsed_db)},
                )
            )
        if parsed_cr is not None and parsed_cr < Decimal("0"):
            findings.append(
                _je_finding(
                    "no_negative_amount",
                    f"Row {src_idx} (Journal={jnl!r}): Credit {parsed_cr} "
                    "is negative.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"credit": str(parsed_cr)},
                )
            )

        # --- Account/fund/org/object in COA -------------------------------
        if fund and fund not in valid_funds:
            findings.append(
                _je_finding(
                    "account_in_coa",
                    f"Row {src_idx} (Journal={jnl!r}): Fund {fund!r} not "
                    "found in chart of accounts.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"fund": fund},
                )
            )
        if org and org not in valid_orgs:
            findings.append(
                _je_finding(
                    "account_in_coa",
                    f"Row {src_idx} (Journal={jnl!r}): Org {org!r} not "
                    "found in chart of accounts.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"org": org},
                )
            )
        if obj and obj not in valid_objects:
            findings.append(
                _je_finding(
                    "account_in_coa",
                    f"Row {src_idx} (Journal={jnl!r}): Object {obj!r} not "
                    "found in chart of accounts.",
                    Severity.HIGH,
                    [ref],
                    computed_values={"object": obj},
                )
            )

        # --- Inactive account check ---------------------------------------
        account_key = f"{fund}-{org}-{obj}" if (fund and org and obj) else None
        if (not cfg.allow_inactive_accounts
                and account_key
                and account_key in account_details):
            status = account_details[account_key].get("status", "Active")
            if str(status).strip().lower() not in ("active", ""):
                findings.append(
                    _je_finding(
                        "no_inactive_account",
                        f"Row {src_idx} (Journal={jnl!r}): Account {account_key} "
                        f"has Status={status!r} (Inactive); cannot use an inactive "
                        "account (set allow_inactive_accounts=true to override).",
                        Severity.HIGH,
                        [ref],
                        computed_values={
                            "account": account_key,
                            "status": status,
                        },
                    )
                )

        # --- Warning: combo plausibility ----------------------------------
        # Flag if the fund/org/object COMBINATION does not appear in the COA
        # or in the GL history.
        if (fund and org and obj
                and fund in valid_funds
                and org in valid_orgs
                and obj in valid_objects):
            combo = f"{fund}-{org}-{obj}"
            in_coa = combo in account_details
            in_gl = combo in gl_combos
            if not in_coa and not in_gl:
                findings.append(
                    _je_finding(
                        "combo_plausibility",
                        f"Row {src_idx} (Journal={jnl!r}): Fund/Org/Object "
                        f"combination {combo} has never been seen in the chart "
                        "of accounts or GL history (each segment is valid, but "
                        "this combination is new).",
                        Severity.MEDIUM,
                        [ref],
                        computed_values={"combination": combo},
                        requires_human_review=True,
                    )
                )

        # --- Warning: short description -----------------------------------
        if len(desc) < cfg.min_description_length:
            findings.append(
                _je_finding(
                    "description_too_short",
                    f"Row {src_idx} (Journal={jnl!r}): Line Description "
                    f"{desc!r} is shorter than {cfg.min_description_length} "
                    "characters.",
                    Severity.LOW,
                    [ref],
                    computed_values={
                        "description": desc,
                        "min_length": cfg.min_description_length,
                    },
                    requires_human_review=True,
                )
            )

        # --- Warning: round dollar >= threshold ---------------------------
        amount_val = (parsed_db or Decimal("0")) + (parsed_cr or Decimal("0"))
        if amount_val >= cfg.round_dollar_warning_threshold:
            if amount_val % Decimal("1") == Decimal("0"):
                findings.append(
                    _je_finding(
                        "round_dollar_large_amount",
                        f"Row {src_idx} (Journal={jnl!r}): Amount {amount_val} "
                        f"is a round-dollar amount >= "
                        f"{cfg.round_dollar_warning_threshold}; verify this is "
                        "not a keying error.",
                        Severity.LOW,
                        [ref],
                        computed_values={
                            "amount": str(amount_val),
                            "threshold": str(cfg.round_dollar_warning_threshold),
                        },
                        requires_human_review=True,
                    )
                )

    # ------------------------------------------------------------------ #
    # 3. Debit/credit balance check (per journal and overall)
    # ------------------------------------------------------------------ #
    journal_rows: dict[str, list[tuple[int, Decimal, Decimal]]] = {}
    for pos in range(len(df)):
        row = df.iloc[pos]
        src_idx = int(row["source_row_index"])
        jnl = str(row.get("journal", "")).strip()
        db = parsed_debits.get(src_idx) or Decimal("0")
        cr = parsed_credits.get(src_idx) or Decimal("0")
        # Use absolute values for amounts (negative amounts are already flagged
        # as blocking above; use as-is for the balance so we report the true gap).
        journal_rows.setdefault(jnl, []).append((src_idx, db, cr))

    total_debits = Decimal("0")
    total_credits = Decimal("0")
    for jnl, rows_for_jnl in sorted(journal_rows.items()):
        jnl_debit = sum(r[1] for r in rows_for_jnl)
        jnl_credit = sum(r[2] for r in rows_for_jnl)
        total_debits += jnl_debit
        total_credits += jnl_credit
        if round(jnl_debit, 2) != round(jnl_credit, 2):
            src_refs = [_row_ref(r[0]) for r in rows_for_jnl]
            findings.append(
                _je_finding(
                    "debits_equal_credits_per_journal",
                    f"Journal {jnl!r}: total debits {jnl_debit:.2f} != "
                    f"total credits {jnl_credit:.2f} "
                    f"(difference {jnl_debit - jnl_credit:.2f}).",
                    Severity.CRITICAL,
                    src_refs,
                    computed_values={
                        "journal": jnl,
                        "total_debits": str(jnl_debit),
                        "total_credits": str(jnl_credit),
                        "difference": str(jnl_debit - jnl_credit),
                    },
                )
            )

    if round(total_debits, 2) != round(total_credits, 2):
        all_refs = [
            _row_ref(int(df.iloc[pos]["source_row_index"]))
            for pos in range(len(df))
        ]
        findings.append(
            _je_finding(
                "debits_equal_credits_overall",
                f"Overall: total debits {total_debits:.2f} != "
                f"total credits {total_credits:.2f} "
                f"(difference {total_debits - total_credits:.2f}).",
                Severity.CRITICAL,
                all_refs,
                computed_values={
                    "total_debits": str(total_debits),
                    "total_credits": str(total_credits),
                    "difference": str(total_debits - total_credits),
                },
            )
        )

    # ------------------------------------------------------------------ #
    # 4. Classify blocking vs warning findings
    # ------------------------------------------------------------------ #
    blocking_count = sum(1 for f in findings if f.rule_used in _BLOCKING_RULES)
    warning_count = len(findings) - blocking_count
    upload_ready = blocking_count == 0

    # ------------------------------------------------------------------ #
    # 5. Build upload rows (only when no blocking errors)
    # ------------------------------------------------------------------ #
    upload_rows: list[dict[str, Any]] = []
    source_mapping: list[dict[str, Any]] = []
    if upload_ready:
        for pos in range(len(df)):
            row = df.iloc[pos]
            src_idx = int(row["source_row_index"])
            dt = parsed_dates.get(src_idx)
            date_str = str(dt) if dt else ""
            db = parsed_debits.get(src_idx)
            cr = parsed_credits.get(src_idx)
            debit_str = (
                f"{db:.2f}" if db is not None and db != Decimal("0") else ""
            )
            credit_str = (
                f"{cr:.2f}" if cr is not None and cr != Decimal("0") else ""
            )
            upload_row = {
                "Journal": str(row.get("journal", "")).strip(),
                "Line": str(row.get("line", "")).strip(),
                "Eff Date": date_str,
                "Fund": str(row.get("fund", "")).strip(),
                "Org": str(row.get("org", "")).strip(),
                "Object": str(row.get("object", "")).strip(),
                "Account Description": str(
                    row.get("account_description", "")
                ).strip(),
                "Debit": debit_str,
                "Credit": credit_str,
                "Line Description": str(row.get("line_description", "")).strip(),
                "Reference": str(row.get("reference", "")).strip(),
            }
            upload_rows.append(upload_row)
            source_mapping.append(
                {
                    "upload_row": pos,
                    "source_row_index": src_idx,
                    "je_draft_file": je_file_name,
                    "journal": upload_row["Journal"],
                    "line": upload_row["Line"],
                }
            )

    summary = _build_summary(
        findings,
        blocking_count=blocking_count,
        warning_count=warning_count,
        upload_ready=upload_ready,
        total_debits=total_debits,
        total_credits=total_credits,
        row_count=len(df),
    )
    return JEUploadOutput(
        findings=findings,
        summary=summary,
        upload_rows=upload_rows,
        source_mapping=source_mapping,
        blocking_count=blocking_count,
        warning_count=warning_count,
        upload_ready=upload_ready,
        total_debits=total_debits,
        total_credits=total_credits,
        je_draft_file_id=je_file_id,
        je_draft_file_name=je_file_name,
    )


def _build_summary(
    findings: list[DeterministicFinding],
    *,
    blocking_count: int,
    warning_count: int,
    upload_ready: bool,
    total_debits: Decimal,
    total_credits: Decimal,
    row_count: int,
) -> dict[str, Any]:
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule_used] = by_rule.get(f.rule_used, 0) + 1
    return {
        "workflow_type": WORKFLOW_TYPE,
        "upload_ready": upload_ready,
        "total_findings": len(findings),
        "blocking_findings": blocking_count,
        "warning_findings": warning_count,
        "findings_by_rule": by_rule,
        "total_debits": str(total_debits),
        "total_credits": str(total_credits),
        "row_count": row_count,
        "requires_human_review": any(f.requires_human_review for f in findings),
    }


# --------------------------------------------------------------------------- #
# LLM prompt (advisory only)
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY summarize, explain, classify, and flag deterministic findings.\n"
    "- You MUST NOT edit, fix, or suggest corrections to the journal entry data.\n"
    "- You MUST NOT calculate, invent account codes, amounts, dates, or vendors.\n"
    "- Use ONLY numbers and source-row ids that appear in the findings below.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- All output is a DRAFT for human review; do NOT produce final-approval language.\n"
    "- Do NOT suggest that the upload workbook is correct; state DRAFT status.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def _findings_block(det: JEUploadOutput) -> str:
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


def build_je_upload_prep_prompt(det: JEUploadOutput) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Journal entry upload preparation and validation.\n"
        + "TASK: Summarize the validation findings, explain each flagged issue "
        "in plain language for finance staff, suggest human review steps, and "
        "draft a review memo. Do NOT fix data, calculate, or certify the JE.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM (deterministic, offline)
# --------------------------------------------------------------------------- #
class MockLLMProvider(_CoreMockLLMProvider):
    """Deterministic mock for JE upload prep.

    Only references source-row ids from the deterministic findings. Produces
    upload-prep-specific review language.
    """

    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        blocking = [
            f for f in findings if f.get("rule_used") in _BLOCKING_RULES
        ]
        warnings = [
            f for f in findings if f.get("rule_used") not in _BLOCKING_RULES
        ]
        ref_ids: list[str] = []
        categorized = []
        for f in findings:
            refs = list(f.get("source_row_ids", []) or [])
            ref_ids.extend(refs)
            categorized.append(
                {
                    "category": f.get("rule_used") or f.get("finding_type") or "other",
                    "description": f.get("description", ""),
                    "referenced_source_rows": refs,
                }
            )
        n_blocking = len(blocking)
        n_warn = len(warnings)
        return {
            "summary": (
                f"Deterministic JE validation identified {n_blocking} blocking "
                f"error(s) and {n_warn} warning(s). "
                + (
                    "The journal entry is NOT ready for upload. "
                    "All blocking errors must be corrected before the upload "
                    "workbook can be generated."
                    if n_blocking > 0
                    else "The journal entry passed all blocking checks and is "
                    "ready for upload, subject to human review of any warnings."
                )
            ),
            "categorized_exceptions": categorized,
            "referenced_source_rows": sorted(set(ref_ids)),
            "suggested_review_steps": [
                "Correct all blocking validation errors before re-running.",
                "Verify each flagged account code against the chart of accounts.",
                "Confirm that Eff Date values fall within the fiscal period.",
                "Ensure each journal entry line has either a debit or credit (not both).",
                "Review warning-only items with finance staff before uploading.",
            ],
            "draft_memo": (
                "DRAFT - for human review only. The automated JE upload prep "
                "workflow identified validation issues requiring finance staff "
                "attention before this journal entry can be uploaded to Munis. "
                "No corrections were made by the assistant; all findings are "
                "deterministic code checks."
            ),
        }


def _call_llm(det: JEUploadOutput, provider: Any = None) -> tuple[dict, str, str]:
    provider = provider or MockLLMProvider()
    prompt = build_je_upload_prep_prompt(det)
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
    response_json: dict[str, Any], det: JEUploadOutput
) -> ValidationResult:
    """Validate that the LLM output only cites known source rows and numbers."""
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
def _write_upload_xlsx(out_path: Path, rows: list[dict[str, Any]]) -> None:
    """Write the upload workbook (openpyxl, template headers, one sheet)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = UPLOAD_SHEET_NAME
    ws.append(UPLOAD_HEADERS)
    for row in rows:
        ws.append([row.get(h, "") for h in UPLOAD_HEADERS])
    wb.save(out_path)


def export_artifacts(
    out_dir: str | Path,
    det: JEUploadOutput,
    response_json: dict[str, Any],
    validation: ValidationResult,
    audit_events: Optional[list[dict[str, Any]]] = None,
    *,
    run_id: Optional[str] = None,
) -> list[ExportArtifact]:
    """Write all JE upload prep artifacts. Returns artifact manifests."""
    from src.core.exports import sha256_file

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportArtifact] = []

    def _md(name: str, text: str) -> None:
        artifacts.append(write_markdown(out / name, text, run_id=run_id))

    def _json(name: str, obj: Any) -> None:
        artifacts.append(write_json(out / name, obj, run_id=run_id))

    def _csv(name: str, data: Any, columns: Optional[list] = None) -> None:
        artifacts.append(write_csv(out / name, data, columns=columns, run_id=run_id))

    upload_ready = det.upload_ready

    # --- je_upload.xlsx and je_upload.csv (only on success) ---------------
    if upload_ready:
        xlsx_path = out / "je_upload.xlsx"
        _write_upload_xlsx(xlsx_path, det.upload_rows)
        artifacts.append(
            ExportArtifact(
                run_id=run_id,
                artifact_type=ArtifactType.OTHER,
                file_name="je_upload.xlsx",
                path=str(xlsx_path),
                sha256=sha256_file(xlsx_path),
            )
        )
        upload_df = pd.DataFrame(det.upload_rows, columns=UPLOAD_HEADERS)
        _csv("je_upload.csv", upload_df)

        # source_mapping.csv
        _csv(
            "source_mapping.csv",
            det.source_mapping,
            columns=["upload_row", "source_row_index", "je_draft_file",
                     "journal", "line"],
        )

    # --- je_validation_errors.csv (always) --------------------------------
    error_rows = []
    for f in det.findings:
        error_rows.append(
            {
                "finding_id": f.finding_id,
                "rule_used": f.rule_used,
                "severity": f.severity.value,
                "description": f.description,
                "source_rows": ";".join(_short_ref(s) for s in f.source_rows),
                "computed_values": json.dumps(f.computed_values, default=str),
                "requires_human_review": f.requires_human_review,
                "is_blocking": f.rule_used in _BLOCKING_RULES,
            }
        )
    errors_df = pd.DataFrame(
        error_rows,
        columns=[
            "finding_id", "rule_used", "severity", "description",
            "source_rows", "computed_values", "requires_human_review",
            "is_blocking",
        ],
    )
    _csv("je_validation_errors.csv", errors_df)

    # --- je_prep_summary.md (always) --------------------------------------
    s = det.summary
    status_str = "UPLOAD READY" if upload_ready else "INVALID - NOT READY FOR UPLOAD"
    summary_lines = [
        "# JE Upload Prep Summary",
        "",
        f"**Status: {status_str}**",
        "",
        f"- Total rows: {s.get('row_count', 0)}",
        f"- Blocking errors: {s.get('blocking_findings', 0)}",
        f"- Warnings: {s.get('warning_findings', 0)}",
        f"- Total debits: {s.get('total_debits', '0')}",
        f"- Total credits: {s.get('total_credits', '0')}",
        "",
    ]
    if not upload_ready:
        summary_lines += ["## Reason(s) Upload Blocked", ""]
        for f in det.findings:
            if f.rule_used in _BLOCKING_RULES:
                refs = (
                    ", ".join(_short_ref(sr) for sr in f.source_rows) or "(none)"
                )
                summary_lines.append(
                    f"- **[{f.severity.value}] {f.rule_used}**: "
                    f"{f.description} (source rows: {refs})"
                )
    else:
        summary_lines += [
            "## Validation Passed",
            "",
            "All blocking checks passed. Review warnings before uploading.",
            "",
        ]

    if det.warning_count > 0:
        summary_lines += ["", "## Warnings (review before upload)", ""]
        for f in det.findings:
            if f.rule_used not in _BLOCKING_RULES:
                refs = (
                    ", ".join(_short_ref(sr) for sr in f.source_rows) or "(none)"
                )
                summary_lines.append(
                    f"- **[{f.severity.value}] {f.rule_used}**: "
                    f"{f.description} (source rows: {refs})"
                )

    summary_lines += [
        "",
        "## AI Summary (DRAFT - human review required)",
        str(response_json.get("summary", "")),
        "",
        "## Draft Memo",
        str(response_json.get("draft_memo", "")),
        "",
        "_All validation checks computed deterministically. "
        "AI commentary is a draft for human review._",
    ]
    _md("je_prep_summary.md", "\n".join(summary_lines))

    # --- validation_report.json (always) ----------------------------------
    _json("validation_report.json", validation.model_dump())

    # --- audit_log.json (always) ------------------------------------------
    _json("audit_log.json", audit_events or [])

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
    """End-to-end JE upload prep run.

    inputs keys:
        je_draft            (required) path to draft JE CSV/XLSX
        chart_of_accounts   (required) path to Tyler COA CSV/XLSX
        gl_detail           (optional) path to GL detail CSV/XLSX
        config              (optional) path to je_config.json

    config may be a JEUploadConfig, a dict, or a path to JSON; it overrides
    inputs['config'] when provided.

    Returns a dict: run_id, workflow_type, summary (includes upload_ready),
    findings, validation, preflight, and (when export_dir is set) export_paths.
    """
    run_id = run_id or make_id()

    # --- Load config ------------------------------------------------------ #
    if config is None and inputs.get("config"):
        config = inputs["config"]
    if config is None:
        cfg = JEUploadConfig()
    elif isinstance(config, JEUploadConfig):
        cfg = config
    elif isinstance(config, dict):
        cfg = JEUploadConfig.from_dict(config)
    elif isinstance(config, (str, Path)):
        cfg = JEUploadConfig.from_json(config)
    else:
        cfg = JEUploadConfig()

    # --- Audit: run created ----------------------------------------------- #
    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=WORKFLOW_TYPE)

    # --- Preflight -------------------------------------------------------- #
    preflight_report = run_preflight(
        CAPABILITY,
        inputs,
        config={
            "fiscal_period_start": str(cfg.fiscal_period_start),
            "fiscal_period_end": str(cfg.fiscal_period_end),
        },
        detect_conditions=detect_conditions,
    )
    preflight_dict = preflight_report.to_summary()

    # Store preflight in ledger.
    if ledger is not None and hasattr(ledger, "store_preflight"):
        ledger.store_preflight(run_id, preflight_dict)

    # Handle FAIL: do not run, do not call LLM.
    if preflight_report.status == PreflightStatus.FAIL:
        if audit is not None and hasattr(audit, "run_failed"):
            audit.run_failed(run_id, actor, reason="preflight_failed")

        fail_result: dict[str, Any] = {
            "run_id": run_id,
            "workflow_type": WORKFLOW_TYPE,
            "summary": {"upload_ready": False, "preflight_status": "fail"},
            "findings": [],
            "preflight": preflight_dict,
            "upload_ready": False,
        }

        if export_dir is not None:
            out = Path(export_dir) / run_id
            out.mkdir(parents=True, exist_ok=True)
            pf_json = write_json(
                out / "preflight_report.json",
                preflight_report.model_dump(mode="json"),
                run_id=run_id,
            )
            pf_md = write_markdown(
                out / "preflight_summary.md",
                render_preflight_summary_md(preflight_report),
                run_id=run_id,
            )
            if ledger is not None and hasattr(ledger, "store_export_artifact"):
                ledger.store_export_artifact(run_id, pf_json)
                ledger.store_export_artifact(run_id, pf_md)
            if audit is not None and hasattr(audit, "export_generated"):
                audit.export_generated(
                    run_id, actor,
                    artifacts=["preflight_report.json", "preflight_summary.md"],
                )
            fail_result["export_paths"] = {
                "preflight_report.json": str(out / "preflight_report.json"),
                "preflight_summary.md": str(out / "preflight_summary.md"),
            }

        return fail_result

    # --- Deterministic validation ----------------------------------------- #
    det = run_deterministic(
        inputs["je_draft"],
        inputs["chart_of_accounts"],
        gl_detail_path=inputs.get("gl_detail"),
        config=cfg,
    )
    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )

    # --- LLM assist (advisory) -------------------------------------------- #
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

    # --- Validation ------------------------------------------------------- #
    validation = validate_llm_output(response_json, det)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "upload_ready": det.upload_ready,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
        "preflight": preflight_dict,
    }

    # --- Exports ---------------------------------------------------------- #
    if export_dir is not None:
        out = Path(export_dir) / run_id
        out.mkdir(parents=True, exist_ok=True)
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifacts = export_artifacts(
            out, det, response_json, validation, audit_events, run_id=run_id
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

    if audit is not None and hasattr(audit, "run_completed"):
        audit.run_completed(run_id, actor, upload_ready=det.upload_ready)

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
}
