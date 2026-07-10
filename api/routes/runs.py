"""Run history, run detail, review actions, artifacts, audit routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.workflow_registry import record_human_review_action

from api.schemas.models import (
    ArtifactList,
    AuditEventList,
    QaTurn,
    QuestionRequest,
    ReviewActionRequest,
    ReviewActionResult,
    RunDetail,
    RunList,
)
from api.services.runs import (
    ALLOWED_REVIEW_ACTIONS,
    answer_run_question,
    apply_review_status_transition,
    build_artifacts,
    build_audit_events,
    build_review_actions,
    build_run_detail,
    build_run_list,
    resolve_artifact,
)

router = APIRouter(tags=["runs"])


def _require_run(ledger, run_id: str) -> dict:
    run = ledger.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown run '{run_id}'.")
    return run


@router.get("/runs", response_model=RunList)
def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    workflow_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    human_review_status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> RunList:
    items, total = build_run_list(
        request.app.state.ledger, limit, offset,
        workflow_type=workflow_type,
        status=status,
        human_review_status=human_review_status,
        search=search,
    )
    return RunList(runs=items, total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, request: Request) -> RunDetail:
    detail = build_run_detail(request.app.state.ledger, run_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown run '{run_id}'.")
    return detail


@router.post("/runs/{run_id}/review-actions", response_model=ReviewActionResult)
def post_review_action(
    run_id: str,
    body: ReviewActionRequest,
    request: Request,
) -> ReviewActionResult:
    ledger = request.app.state.ledger
    audit = request.app.state.audit
    _require_run(ledger, run_id)
    if body.action not in ALLOWED_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=("Unknown review action '" + body.action + "'. Allowed: "
                    + ", ".join(ALLOWED_REVIEW_ACTIONS) + "."),
        )
    record_human_review_action(
        ledger,
        audit,
        run_id=run_id,
        action=body.action,
        actor=body.actor,
        finding_id=body.finding_id,
        note=body.note or None,
    )
    new_status = apply_review_status_transition(ledger, run_id, body.action)
    return ReviewActionResult(
        human_review_status=new_status,
        review_actions=build_review_actions(ledger, run_id),
    )


@router.post("/runs/{run_id}/questions", response_model=QaTurn)
def post_question(run_id: str, body: QuestionRequest, request: Request) -> QaTurn:
    """Ask a question about a completed run. The answer is an advisory DRAFT
    grounded only in this run's findings, source rows, reference data, and
    metadata; it is validated, audited, and added to the run's review packet."""
    ledger = request.app.state.ledger
    audit = request.app.state.audit
    settings = request.app.state.settings
    _require_run(ledger, run_id)
    if not body.question.strip():
        raise HTTPException(status_code=422, detail="Question must not be empty.")
    turn = answer_run_question(
        ledger, audit, run_id, body.question,
        export_dir=settings.export_dir,
        actor=(body.actor or "finance_staff"),
    )
    if turn is None:
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'.")
    return turn


@router.get("/runs/{run_id}/artifacts", response_model=ArtifactList)
def list_artifacts(run_id: str, request: Request) -> ArtifactList:
    ledger = request.app.state.ledger
    _require_run(ledger, run_id)
    return ArtifactList(artifacts=build_artifacts(ledger, run_id))


@router.get("/runs/{run_id}/artifacts/{file_name}")
def download_artifact(
    run_id: str, file_name: str, request: Request
) -> FileResponse:
    ledger = request.app.state.ledger
    settings = request.app.state.settings
    _require_run(ledger, run_id)
    resolved = resolve_artifact(
        ledger, run_id, file_name, settings.export_dir)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Artifact '{file_name}' was not found for run "
                    f"'{run_id}'."),
        )
    path, media_type = resolved
    return FileResponse(
        path=str(path), media_type=media_type, filename=path.name)


@router.get("/runs/{run_id}/audit", response_model=AuditEventList)
def get_audit(run_id: str, request: Request) -> AuditEventList:
    ledger = request.app.state.ledger
    _require_run(ledger, run_id)
    return AuditEventList(events=build_audit_events(ledger, run_id))
