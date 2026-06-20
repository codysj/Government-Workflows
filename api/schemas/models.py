"""Response models for API contract v1.

Conventions (documented in docs/frontend/api_contract.md):
- Datetimes are ISO-8601 strings exactly as persisted by the core (the ledger
  stores them as strings; the API never re-parses them).
- Decimal values produced by the core arrive here already serialized as
  strings (pydantic v2 JSON mode / the ledger's model_dump_json). The API
  passes them through unchanged; the frontend formats but never recalculates.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    status: str = "ok"
    app: str = "municipal-finance-ai"
    version: str
    llm_mode: Literal["mock", "real"]


# --------------------------------------------------------------------------- #
# Workflow catalog
# --------------------------------------------------------------------------- #
class UploadFieldInfo(BaseModel):
    key: str
    label: str
    required: bool
    file_types: list[str] = Field(default_factory=list)
    help: str = ""


class TextInputInfo(BaseModel):
    key: str
    label: str
    required: bool
    help: str = ""
    example: Optional[str] = None


class WorkflowInfo(BaseModel):
    workflow_type: str
    title: str
    description: str
    note: Optional[str] = None
    category: Literal["review", "search", "prep", "other"]
    uploads: list[UploadFieldInfo] = Field(default_factory=list)
    text_inputs: list[TextInputInfo] = Field(default_factory=list)
    has_sample: bool = False
    sample_description: Optional[str] = None


class WorkflowList(BaseModel):
    workflows: list[WorkflowInfo] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
class PreflightFileInfo(BaseModel):
    input_key: str
    file_name: str
    present: bool
    row_count: int = 0


class PreflightFindingInfo(BaseModel):
    code: str
    severity: str
    message: str
    affected_input: Optional[str] = None
    blocks_run: bool = False


class SuggestedMapping(BaseModel):
    """One column-mapping suggestion mirroring src.core.schemas.ColumnMapping.

    Advisory only: it surfaces what the deterministic preflight engine resolved
    (or could not resolve) for a required/optional semantic column so the UI can
    show suggested column matches. ``mapped_column`` is the chosen column (or
    null when unmapped); ``confidence`` is the engine's score; ``source`` is one
    of auto/human/unmapped; ``candidates`` are the ranked column names.
    """

    input_key: str
    semantic_name: str
    mapped_column: Optional[str] = None
    confidence: float = 0.0
    source: str = "unmapped"
    candidates: list[str] = Field(default_factory=list)


class PreflightResponse(BaseModel):
    status: Literal["pass", "partial", "fail"]
    llm_allowed: bool
    files: list[PreflightFileInfo] = Field(default_factory=list)
    findings: list[PreflightFindingInfo] = Field(default_factory=list)
    supported_checks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    suggested_mappings: list[SuggestedMapping] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class RunListItem(BaseModel):
    run_id: str
    workflow_type: str
    workflow_title: str
    created_at: str
    status: str
    human_review_status: str
    validation_passed: Optional[bool] = None
    finding_count: int = 0
    artifact_count: int = 0


class RunList(BaseModel):
    """A page of run history.

    ``runs`` is the page (backward-compatible: the only field prior callers
    relied on). ``total`` is the full count of runs in the ledger; ``limit`` and
    ``offset`` echo the page window actually applied.
    """

    runs: list[RunListItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class SourceRowInfo(BaseModel):
    file_id: str
    table_name: str
    row_index: int
    column_names: list[str] = Field(default_factory=list)
    source_values: dict[str, Any] = Field(default_factory=dict)


class FindingInfo(BaseModel):
    finding_id: str
    finding_type: str
    severity: str
    description: str
    rule_used: str
    requires_human_review: bool = False
    computed_values: dict[str, Any] = Field(default_factory=dict)
    source_rows: list[SourceRowInfo] = Field(default_factory=list)


class AiSection(BaseModel):
    """AI-drafted content. ALWAYS rendered separately from deterministic
    findings in any UI; everything in ``response`` is a draft requiring human
    review."""

    available: bool
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    response: Optional[dict[str, Any]] = None
    referenced_source_rows: list[str] = Field(default_factory=list)


class ValidationSection(BaseModel):
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    invented_reference_detected: bool = False
    numeric_claims_checked: int = 0


class ArtifactInfo(BaseModel):
    file_name: str
    artifact_type: str
    sha256: str
    download_url: str


class ArtifactList(BaseModel):
    artifacts: list[ArtifactInfo] = Field(default_factory=list)


class ReviewActionInfo(BaseModel):
    action: str
    actor: str
    note: Optional[str] = None
    finding_id: Optional[str] = None
    created_at: str = ""


class RunDetail(BaseModel):
    run_id: str
    workflow_type: str
    workflow_title: str
    created_at: str
    created_by: str
    status: Literal["completed", "failed_preflight", "failed"]
    human_review_status: str
    retention_category: str
    summary: dict[str, Any] = Field(default_factory=dict)
    preflight: Optional[PreflightResponse] = None
    findings: list[FindingInfo] = Field(default_factory=list)
    ai: Optional[AiSection] = None
    validation: Optional[ValidationSection] = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    review_actions: list[ReviewActionInfo] = Field(default_factory=list)
    allowed_review_actions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Review actions
# --------------------------------------------------------------------------- #
class ReviewActionRequest(BaseModel):
    action: str
    actor: str
    note: Optional[str] = None
    finding_id: Optional[str] = None


class ReviewActionResult(BaseModel):
    human_review_status: str
    review_actions: list[ReviewActionInfo] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditEventInfo(BaseModel):
    event_type: str
    actor: str
    timestamp: str
    details: dict[str, Any] = Field(default_factory=dict)


class AuditEventList(BaseModel):
    events: list[AuditEventInfo] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Settings (GW-8) - READ-ONLY mirror of app_settings.json
# --------------------------------------------------------------------------- #
class SettingsInfo(BaseModel):
    """Read-only view of the app settings the deterministic core uses.

    Decimal-like numeric tolerances are serialized as strings so the frontend
    displays them verbatim and never does arithmetic on amounts. ``editable``
    is always False this batch (the API never writes app_settings.json).
    """

    city_name: str
    default_actor: str
    llm_provider: str
    mock_mode: bool
    date_tolerance_days: int
    amount_tolerance: str
    variance_threshold_pct: str
    variance_dollar_threshold: str
    export_dir: str
    role: str
    default_retention_category: str
    editable: bool = False


# --------------------------------------------------------------------------- #
# AI usage log (GW-9) - cross-run record of every LLM interaction
# --------------------------------------------------------------------------- #
class AiUsageRow(BaseModel):
    """One LLM interaction row from src.core.ai_usage_log.ai_usage_log_rows.

    Fields mirror the dict keys that function returns exactly. ``created_at`` is
    an ISO-8601 string as persisted by the ledger.
    """

    run_id: Optional[str] = None
    workflow_type: Optional[str] = None
    created_at: Optional[str] = None
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    prompt_template_version: Optional[str] = None
    validation_status: Optional[str] = None
    ai_draft_status: Optional[str] = None
    referenced_source_row_count: int = 0


class AiUsageList(BaseModel):
    rows: list[AiUsageRow] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Redaction assist (GW-11) - stateless PII scan
# --------------------------------------------------------------------------- #
class RedactionScanRequest(BaseModel):
    text: str
    extra_patterns: Optional[dict[str, str]] = None


class RedactionFindingInfo(BaseModel):
    """A masked PII finding. Only the masked preview is ever returned; raw
    sensitive text is never echoed back to the caller."""

    category: str
    masked_preview: str
    start: int
    end: int


class RedactionScanResult(BaseModel):
    findings: list[RedactionFindingInfo] = Field(default_factory=list)
    redacted_text: str
    finding_count: int


# --------------------------------------------------------------------------- #
# Schedules - configured recurring runs (list, create, due, manual trigger)
# --------------------------------------------------------------------------- #
class ScheduleInfo(BaseModel):
    """Mirror of a src.core.scheduler.Schedule record.

    Dates/datetimes are ISO-8601 strings. ``next_due`` is a plain ``YYYY-MM-DD``
    date; ``created_at`` / ``last_run_at`` are full ISO datetimes.
    """

    schedule_id: str
    workflow_type: str
    cadence: str
    label: str
    interval_days: int
    next_due: str
    active: bool
    created_at: str
    last_run_at: Optional[str] = None


class ScheduleList(BaseModel):
    schedules: list[ScheduleInfo] = Field(default_factory=list)


class ScheduleCreateRequest(BaseModel):
    """Create a recurring run.

    ``cadence`` is one of monthly/quarterly/before_agenda/custom. ``start`` is
    the first due date (``YYYY-MM-DD``); when omitted it defaults to today on the
    server. ``interval_days`` drives the custom/before_agenda step (ignored for
    monthly/quarterly which step by calendar month).
    """

    workflow_type: str
    label: str
    cadence: str
    start: Optional[str] = None
    interval_days: Optional[int] = None
