"""Core data contracts (Phase 2).

All 12 shared schemas as pydantic v2 models. These are the contracts every
workflow, the run ledger, the audit log, the LLM wrapper, and the validation
layer depend on. Field lists for WorkflowRun, InputFile, SourceRowRef,
DeterministicFinding, LLMResponse, and ValidationResult follow the spec exactly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def make_id() -> str:
    """Return a new opaque id (uuid4 hex)."""
    return uuid.uuid4().hex


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanReviewStatus(str, Enum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingType(str, Enum):
    MATCHED = "matched"
    UNMATCHED_BANK = "unmatched_bank"
    UNMATCHED_LEDGER = "unmatched_ledger"
    TIMING_DIFFERENCE = "timing_difference"
    DUPLICATE = "duplicate"
    SUMMARY = "summary"
    VARIANCE = "variance"
    MISSING_ACCOUNT = "missing_account"
    CONSISTENCY = "consistency"
    # Tyler/Munis-style workflow findings (transaction search, AP duplicate
    # review, JE upload prep, PO/invoice mismatch review). Purely additive.
    SEARCH_MATCH = "search_match"
    DUPLICATE_PAYMENT = "duplicate_payment"
    VENDOR_ANOMALY = "vendor_anomaly"
    PO_MISMATCH = "po_mismatch"
    JE_VALIDATION = "je_validation"
    MISSING_REFERENCE = "missing_reference"
    OTHER = "other"


class EventType(str, Enum):
    RUN_CREATED = "run_created"
    FILE_UPLOADED = "file_uploaded"
    FILE_PARSED = "file_parsed"
    DETERMINISTIC_ANALYSIS_COMPLETED = "deterministic_analysis_completed"
    LLM_REQUEST_SENT = "llm_request_sent"
    LLM_RESPONSE_RECEIVED = "llm_response_received"
    VALIDATION_COMPLETED = "validation_completed"
    HUMAN_REVIEW_ACTION = "human_review_action"
    EXPORT_GENERATED = "export_generated"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class ArtifactType(str, Enum):
    MARKDOWN = "markdown"
    CSV = "csv"
    JSON = "json"
    ZIP = "zip"
    OTHER = "other"


class RetentionCategory(str, Enum):
    """Records-retention classification for a run (Tier 1 extension).

    Lets staff tag each run for public-records / retention-schedule purposes.
    Deterministic metadata only; the AI never sets it.
    """

    DRAFT_WORKING = "draft_working"
    TRANSITORY = "transitory"
    ADMINISTRATIVE = "administrative_record"
    AUDIT_RECORD = "audit_record"
    PERMANENT = "permanent"


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class SourceRowRef(BaseModel):
    """Traceability anchor: where a normalized value came from."""

    file_id: str
    table_name: str
    row_index: int
    column_names: list[str] = Field(default_factory=list)
    source_values: dict[str, Any] = Field(default_factory=dict)


class InputFile(BaseModel):
    file_id: str = Field(default_factory=make_id)
    file_name: str
    file_type: str
    file_hash: str
    uploaded_at: datetime = Field(default_factory=utc_now)
    parser_used: str
    row_count: int
    column_names: list[str] = Field(default_factory=list)


class ParsedTable(BaseModel):
    """A parsed input file: metadata + the pandas DataFrame.

    The DataFrame is held under an arbitrary-types-allowed config. It is not
    serialized into the ledger directly; result tables are exported as CSV.
    """

    model_config = {"arbitrary_types_allowed": True}

    file_id: str
    table_name: str
    file_name: str
    file_type: str
    parser_used: str
    column_names: list[str] = Field(default_factory=list)
    row_count: int = 0
    dataframe: Any = None  # pandas.DataFrame


class NormalizedRecord(BaseModel):
    """A cleaned record that always carries its source-row reference."""

    record_id: str = Field(default_factory=make_id)
    values: dict[str, Any] = Field(default_factory=dict)
    source_row: SourceRowRef


class DeterministicFinding(BaseModel):
    finding_id: str = Field(default_factory=make_id)
    finding_type: FindingType
    severity: Severity = Severity.INFO
    description: str
    source_rows: list[SourceRowRef] = Field(default_factory=list)
    computed_values: dict[str, Any] = Field(default_factory=dict)
    rule_used: str
    requires_human_review: bool = False


class LLMRequest(BaseModel):
    request_id: str = Field(default_factory=make_id)
    run_id: Optional[str] = None
    prompt_template_version: str
    model_provider: str
    model_name: str
    prompt_text: str
    schema_name: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class LLMResponse(BaseModel):
    response_id: str = Field(default_factory=make_id)
    prompt_template_version: str
    model_provider: str
    model_name: str
    response_json: dict[str, Any] = Field(default_factory=dict)
    referenced_source_rows: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ValidationResult(BaseModel):
    validation_id: str = Field(default_factory=make_id)
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_source_refs: list[str] = Field(default_factory=list)
    invented_reference_detected: bool = False
    numeric_claims_checked: int = 0


class HumanReviewAction(BaseModel):
    action_id: str = Field(default_factory=make_id)
    run_id: Optional[str] = None
    finding_id: Optional[str] = None
    action: str  # e.g. "mark_reviewed", "needs_follow_up", "approve_draft"
    actor: str
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class ExportArtifact(BaseModel):
    artifact_id: str = Field(default_factory=make_id)
    run_id: Optional[str] = None
    artifact_type: ArtifactType
    file_name: str
    path: str
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=make_id)
    run_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: EventType
    actor: str
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowRun(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    run_id: str = Field(default_factory=make_id)
    workflow_type: str
    created_at: datetime = Field(default_factory=utc_now)
    created_by: str
    input_files: list[InputFile] = Field(default_factory=list)
    status: RunStatus = RunStatus.CREATED
    deterministic_results: list[DeterministicFinding] = Field(default_factory=list)
    llm_results: Optional[LLMResponse] = None
    validation_results: Optional[ValidationResult] = None
    human_review_status: HumanReviewStatus = HumanReviewStatus.PENDING
    retention_category: RetentionCategory = RetentionCategory.DRAFT_WORKING
    export_artifacts: list[ExportArtifact] = Field(default_factory=list)
    # In-memory only (not persisted to ledger): result tables + summary for UI.
    result_tables: dict[str, Any] = Field(default_factory=dict, exclude=True)
    summary: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Preflight / capability layer (shared across all workflows)
# --------------------------------------------------------------------------- #
class PreflightStatus(str, Enum):
    """Whether an uploaded input set may run a workflow.

    PASS    -> run normally.
    PARTIAL -> run only supported deterministic logic; flag unsupported
               conditions; the LLM may only explain deterministic findings.
    FAIL    -> do not run; do NOT call the LLM; return a structured report.
    """

    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


class PreflightConditionCode(str, Enum):
    """Stable condition codes (snake_case of the member name)."""

    MISSING_REQUIRED_FILE = "missing_required_file"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    MISSING_REQUIRED_COLUMN = "missing_required_column"
    AMBIGUOUS_COLUMN_MAPPING = "ambiguous_column_mapping"
    LOW_DATE_PARSE_CONFIDENCE = "low_date_parse_confidence"
    LOW_AMOUNT_PARSE_CONFIDENCE = "low_amount_parse_confidence"
    POSSIBLE_SIGN_CONVENTION_MISMATCH = "possible_sign_convention_mismatch"
    POSSIBLE_BATCH_MATCHING = "possible_batch_matching"
    POSSIBLE_PRIOR_PERIOD_ITEM = "possible_prior_period_item"
    POSSIBLE_ACCOUNT_ROLLUP = "possible_account_rollup"
    POSSIBLE_BUDGET_BASIS_MISMATCH = "possible_budget_basis_mismatch"
    POSSIBLE_UNKNOWN_REPORT_STRUCTURE = "possible_unknown_report_structure"
    UNSUPPORTED_PATTERN_DETECTED = "unsupported_pattern_detected"
    NEEDS_HUMAN_CONFIGURATION = "needs_human_configuration"


class PreflightFinding(BaseModel):
    """A single preflight condition.

    ``blocks_run=True`` forces FAIL (the workflow + LLM must not run).
    """

    code: PreflightConditionCode
    severity: Severity = Severity.INFO
    message: str
    affected_input: Optional[str] = None
    affected_column: Optional[str] = None
    suggested_action: str = ""
    blocks_run: bool = False


class ColumnMapping(BaseModel):
    """Resolution of one semantic column to a concrete input column."""

    semantic_name: str
    input_key: str
    mapped_column: Optional[str] = None
    confidence: float = 0.0
    source: Literal["auto", "human", "unmapped"] = "unmapped"
    candidates: list[str] = Field(default_factory=list)


class FileProfile(BaseModel):
    """Deterministic profile of one uploaded input file."""

    input_key: str
    file_name: str
    file_type: str
    present: bool = False
    row_count: int = 0
    detected_columns: list[str] = Field(default_factory=list)
    # column_name -> fraction (0..1) of non-blank cells that parse, for the
    # plausible date/amount columns only.
    parse_confidence: dict[str, float] = Field(default_factory=dict)
    repeated_header_rows: list[int] = Field(default_factory=list)
    footer_total_rows: list[int] = Field(default_factory=list)
    duplicate_row_groups: list[list[int]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CapabilitySpec(BaseModel):
    """What a workflow can handle. Each workflow exposes one as ``CAPABILITY``."""

    workflow_type: str
    required_inputs: list[str] = Field(default_factory=list)
    optional_inputs: list[str] = Field(default_factory=list)
    # input_key -> accepted extensions (e.g. ["csv"]); "*" key = default list.
    accepted_file_types: dict[str, list[str]] = Field(default_factory=dict)
    required_semantic_columns: dict[str, list[str]] = Field(default_factory=dict)
    optional_semantic_columns: dict[str, list[str]] = Field(default_factory=dict)
    min_date_confidence: float = 0.8
    min_amount_confidence: float = 0.8
    supported_patterns: list[str] = Field(default_factory=list)
    partially_supported_patterns: list[str] = Field(default_factory=list)
    unsupported_patterns: list[str] = Field(default_factory=list)
    notes: str = ""


class PreflightReport(BaseModel):
    """The structured result of running preflight for one input set."""

    workflow_type: str
    status: PreflightStatus
    file_profiles: list[FileProfile] = Field(default_factory=list)
    column_mappings: list[ColumnMapping] = Field(default_factory=list)
    findings: list[PreflightFinding] = Field(default_factory=list)
    supported_checks: list[str] = Field(default_factory=list)
    # The condition codes present (string values), for quick scanning.
    unsupported_conditions: list[str] = Field(default_factory=list)
    llm_allowed: bool = False
    partial: bool = False
    next_steps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    def to_summary(self) -> dict[str, Any]:
        """Compact, JSON-serializable summary for CLI/UI/exports."""
        return {
            "workflow_type": self.workflow_type,
            "status": self.status.value,
            "llm_allowed": self.llm_allowed,
            "partial": self.partial,
            "files": [
                {
                    "input_key": p.input_key,
                    "file_name": p.file_name,
                    "present": p.present,
                    "row_count": p.row_count,
                }
                for p in self.file_profiles
            ],
            "findings": [
                {
                    "code": f.code.value,
                    "severity": f.severity.value,
                    "message": f.message,
                    "affected_input": f.affected_input,
                    "affected_column": f.affected_column,
                    "blocks_run": f.blocks_run,
                }
                for f in self.findings
            ],
            "supported_checks": list(self.supported_checks),
            "unsupported_conditions": list(self.unsupported_conditions),
            "next_steps": list(self.next_steps),
        }
