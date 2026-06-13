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


class PreflightResponse(BaseModel):
    status: Literal["pass", "partial", "fail"]
    llm_allowed: bool
    files: list[PreflightFileInfo] = Field(default_factory=list)
    findings: list[PreflightFindingInfo] = Field(default_factory=list)
    supported_checks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


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
    runs: list[RunListItem] = Field(default_factory=list)


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
