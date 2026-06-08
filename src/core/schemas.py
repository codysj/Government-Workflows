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
from typing import Any, Optional

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
