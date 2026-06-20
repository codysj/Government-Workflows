"""Run execution + ledger->contract mapping.

All execution goes through ``app.workflow_registry.run_workflow`` (the same
pipeline Streamlit and the CLI use). Run details are ALWAYS rehydrated from
the ledger, so live responses and post-restart responses are built by one
code path and never drift apart. ``result_tables`` (in-memory DataFrames) are
deliberately never serialized.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app import workflow_registry as wfr
from src.core import preflight as preflight_engine
from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger

from api.schemas.models import (
    AiSection,
    ArtifactInfo,
    AuditEventInfo,
    FindingInfo,
    PreflightFileInfo,
    PreflightFindingInfo,
    PreflightResponse,
    ReviewActionInfo,
    RunDetail,
    RunListItem,
    SourceRowInfo,
    SuggestedMapping,
    ValidationSection,
)

ALLOWED_REVIEW_ACTIONS = [key for key, _ in wfr.HUMAN_REVIEW_ACTIONS]

# The run-level review status transition policy lives in app.workflow_registry
# (the single shared home used by BOTH this API and Streamlit). Re-exported here
# so existing callers (api/routes/runs.py imports it from api.services.runs)
# keep working without change. It is now atomic + concurrency-safe.
apply_review_status_transition = wfr.apply_review_status_transition

_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".xlsx": ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
    ".txt": "text/plain",
    ".zip": "application/zip",
}


def _title_for(workflow_type: str) -> str:
    try:
        return wfr.get_descriptor(workflow_type).title
    except KeyError:
        return workflow_type


# --------------------------------------------------------------------------- #
# Preflight (standalone, no run recorded, no LLM)
# --------------------------------------------------------------------------- #
def run_standalone_preflight(
    workflow_type: str,
    inputs: dict[str, Any],
    config: Optional[dict] = None,
) -> PreflightResponse:
    """Run ONLY the capability check via the core engine (no workflow, no
    LLM, nothing written to the ledger). Mirrors cli._run_preflight_only."""
    module = wfr.WORKFLOW_MODULES[workflow_type]  # KeyError -> 404 upstream
    report = preflight_engine.run_preflight(
        module.CAPABILITY,
        inputs,
        approved_mappings=None,
        config=config,
        detect_conditions=getattr(module, "detect_conditions", None),
    )
    return preflight_response_from_dict(
        preflight_engine.preflight_report_dict(report))


def preflight_response_from_dict(d: dict[str, Any]) -> PreflightResponse:
    """Map a stored/serialized PreflightReport dict to the contract shape."""
    return PreflightResponse(
        status=d.get("status", "fail"),
        llm_allowed=bool(d.get("llm_allowed", False)),
        files=[
            PreflightFileInfo(
                input_key=p.get("input_key", ""),
                file_name=p.get("file_name", ""),
                present=bool(p.get("present", False)),
                row_count=int(p.get("row_count", 0) or 0),
            )
            for p in d.get("file_profiles", [])
        ],
        findings=[
            PreflightFindingInfo(
                code=f.get("code", ""),
                severity=f.get("severity", "info"),
                message=f.get("message", ""),
                affected_input=f.get("affected_input"),
                blocks_run=bool(f.get("blocks_run", False)),
            )
            for f in d.get("findings", [])
        ],
        supported_checks=list(d.get("supported_checks", [])),
        next_steps=list(d.get("next_steps", [])),
        suggested_mappings=[
            SuggestedMapping(
                input_key=m.get("input_key", ""),
                semantic_name=m.get("semantic_name", ""),
                mapped_column=m.get("mapped_column"),
                confidence=float(m.get("confidence", 0.0) or 0.0),
                source=str(m.get("source", "unmapped")),
                candidates=list(m.get("candidates", []) or []),
            )
            for m in d.get("column_mappings", [])
        ],
    )


# --------------------------------------------------------------------------- #
# Run execution (synchronous; reuses the shared pipeline)
# --------------------------------------------------------------------------- #
def execute_run(
    workflow_type: str,
    inputs: dict[str, Any],
    *,
    ledger: RunLedger,
    audit: AuditLog,
    actor: str,
    export_dir: Path,
    config: Optional[dict] = None,
    retention_category: str = "draft_working",
) -> str:
    """Run the workflow through the shared pipeline and return the run_id.

    provider=None keeps the offline deterministic mock as the LLM (default).
    Preflight FAIL and freeform refusals are domain outcomes carried in the
    persisted run, not exceptions. Unexpected exceptions propagate (the
    pipeline has already marked the run failed); callers map them to 500.
    """
    result = wfr.run_workflow(
        workflow_type,
        inputs,
        ledger=ledger,
        audit=audit,
        provider=None,
        actor=actor,
        export_dir=export_dir,
        config=config,
        retention_category=retention_category,
    )
    return result.run_id


# --------------------------------------------------------------------------- #
# Ledger -> contract mapping (single source for live + rehydrated responses)
# --------------------------------------------------------------------------- #
def _map_status(raw_status: str, summary: dict, preflight: Optional[dict]) -> str:
    if raw_status == "completed":
        return "completed"
    if raw_status == "failed":
        blocked = bool(summary.get("blocked"))
        pf_failed = bool(preflight) and preflight.get("status") == "fail"
        return "failed_preflight" if (blocked or pf_failed) else "failed"
    # created/running only appear if a process died mid-run; surface as failed
    # so the contract's status enum holds (documented in api_contract.md).
    return "failed"


def _validation_passed(summary: dict) -> Optional[bool]:
    status = summary.get("validation_status")
    if status == "passed":
        return True
    if status in ("warnings", "failed"):
        return False
    return None


def build_run_list(
    ledger: RunLedger, limit: int, offset: int = 0
) -> tuple[list[RunListItem], int]:
    """Build one page of run-list items plus the TOTAL run count.

    Returns ``(items, total)``. ``total`` is the full number of runs in the
    ledger; ``items`` is the ``[offset:offset+limit]`` slice (newest-first, as
    the ledger orders them). All counting/slicing happens here; no core change.
    """
    all_rows = ledger.list_runs()
    total = len(all_rows)
    start = max(offset, 0)
    page = all_rows[start:start + max(limit, 0)]
    items: list[RunListItem] = []
    for row in page:
        run_id = row["run_id"]
        summary = row.get("summary") or {}
        preflight = ledger.get_preflight(run_id)
        items.append(RunListItem(
            run_id=run_id,
            workflow_type=row.get("workflow_type", ""),
            workflow_title=_title_for(row.get("workflow_type", "")),
            created_at=str(row.get("created_at", "")),
            status=_map_status(row.get("status", ""), summary, preflight),
            human_review_status=row.get("human_review_status", "pending"),
            validation_passed=_validation_passed(summary),
            finding_count=len(ledger.list_findings(run_id)),
            artifact_count=len(ledger.list_export_artifacts(run_id)),
        ))
    return items, total


def build_run_detail(ledger: RunLedger, run_id: str) -> Optional[RunDetail]:
    """Rehydrate a full RunDetail from the ledger (works after restart).

    Notes:
    - findings/llm/validation come back as the JSON dicts pydantic wrote, so
      Decimals are already strings and datetimes are ISO strings.
    - the live ``result_tables`` DataFrames are never persisted and are not
      part of the API surface.
    """
    run = ledger.get_run(run_id)
    if run is None:
        return None

    summary = run.get("summary") or {}
    preflight_dict = run.get("preflight")

    findings = [
        FindingInfo(
            finding_id=f.get("finding_id", ""),
            finding_type=str(f.get("finding_type", "other")),
            severity=str(f.get("severity", "info")),
            description=f.get("description", ""),
            rule_used=f.get("rule_used", ""),
            requires_human_review=bool(f.get("requires_human_review", False)),
            computed_values=f.get("computed_values") or {},
            source_rows=[
                SourceRowInfo(
                    file_id=s.get("file_id", ""),
                    table_name=s.get("table_name", ""),
                    row_index=int(s.get("row_index", 0) or 0),
                    column_names=s.get("column_names") or [],
                    source_values=s.get("source_values") or {},
                )
                for s in (f.get("source_rows") or [])
            ],
        )
        for f in run.get("findings", [])
    ]

    # The ledger stores a LIST of LLM responses; the last one is the active
    # draft. ai is null when the LLM was never called (fail-closed paths).
    ai: Optional[AiSection] = None
    llm_responses = run.get("llm_responses") or []
    if llm_responses:
        last = llm_responses[-1]
        ai = AiSection(
            available=True,
            model_provider=last.get("model_provider"),
            model_name=last.get("model_name"),
            response=last.get("response_json") or {},
            referenced_source_rows=last.get("referenced_source_rows") or [],
        )

    validation: Optional[ValidationSection] = None
    validation_results = run.get("validation_results") or []
    if validation_results:
        last_v = validation_results[-1]
        validation = ValidationSection(
            passed=bool(last_v.get("passed", False)),
            errors=last_v.get("errors") or [],
            warnings=last_v.get("warnings") or [],
            invented_reference_detected=bool(
                last_v.get("invented_reference_detected", False)),
            numeric_claims_checked=int(
                last_v.get("numeric_claims_checked", 0) or 0),
        )

    return RunDetail(
        run_id=run_id,
        workflow_type=run.get("workflow_type", ""),
        workflow_title=_title_for(run.get("workflow_type", "")),
        created_at=str(run.get("created_at", "")),
        created_by=run.get("created_by", ""),
        status=_map_status(run.get("status", ""), summary, preflight_dict),
        human_review_status=run.get("human_review_status", "pending"),
        retention_category=run.get("retention_category", "draft_working"),
        summary=summary,
        preflight=(preflight_response_from_dict(preflight_dict)
                   if preflight_dict else None),
        findings=findings,
        ai=ai,
        validation=validation,
        artifacts=build_artifacts(ledger, run_id),
        review_actions=build_review_actions(ledger, run_id),
        allowed_review_actions=list(ALLOWED_REVIEW_ACTIONS),
    )


def build_artifacts(ledger: RunLedger, run_id: str) -> list[ArtifactInfo]:
    return [
        ArtifactInfo(
            file_name=a.get("file_name", ""),
            artifact_type=str(a.get("artifact_type", "other")),
            sha256=a.get("sha256", ""),
            download_url=f"/api/runs/{run_id}/artifacts/{a.get('file_name', '')}",
        )
        for a in ledger.list_export_artifacts(run_id)
    ]


def build_review_actions(ledger: RunLedger, run_id: str) -> list[ReviewActionInfo]:
    actions = [
        ReviewActionInfo(
            action=a.get("action", ""),
            actor=a.get("actor", ""),
            note=a.get("note"),
            finding_id=a.get("finding_id"),
            created_at=str(a.get("created_at", "")),
        )
        for a in ledger.list_human_review_actions(run_id)
    ]
    actions.sort(key=lambda a: a.created_at)
    return actions


def build_audit_events(ledger: RunLedger, run_id: str) -> list[AuditEventInfo]:
    return [
        AuditEventInfo(
            event_type=e.get("event_type", ""),
            actor=e.get("actor", ""),
            timestamp=str(e.get("timestamp", "")),
            details=e.get("details") or {},
        )
        for e in ledger.list_audit_events(run_id)
    ]


# --------------------------------------------------------------------------- #
# Artifact download (path-traversal-safe)
# --------------------------------------------------------------------------- #
def resolve_artifact(
    ledger: RunLedger,
    run_id: str,
    file_name: str,
    export_dir: Path,
) -> Optional[tuple[Path, str]]:
    """Resolve a recorded artifact to (path, media_type), or None.

    Safety: the path is NEVER constructed from the request; we look the
    artifact up by exact recorded file_name, then verify the recorded path
    resolves INSIDE the configured export directory before serving it.
    """
    record = next(
        (a for a in ledger.list_export_artifacts(run_id)
         if a.get("file_name") == file_name),
        None,
    )
    if record is None:
        return None
    path = Path(record.get("path", "")).resolve()
    root = Path(export_dir).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None  # outside the export tree -> treat as not found
    if not path.is_file():
        return None
    media = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return path, media
