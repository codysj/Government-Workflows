"""Workflow catalog, standalone preflight, and run creation routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.schemas.models import (
    PreflightResponse,
    RunDetail,
    WorkflowInfo,
    WorkflowList,
)
from api.services.catalog import get_workflow_info, list_workflow_infos
from api.services.inputs import (
    InputError,
    build_inputs,
    parse_config_field,
    validate_required_inputs,
)
from api.services.runs import (
    build_run_detail,
    execute_run,
    run_standalone_preflight,
)

router = APIRouter(tags=["workflows"])


def _require_workflow(workflow_type: str) -> WorkflowInfo:
    info = get_workflow_info(workflow_type)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown workflow '{workflow_type}'.",
        )
    return info


@router.get("/workflows", response_model=WorkflowList)
def list_workflows() -> WorkflowList:
    return WorkflowList(workflows=list_workflow_infos())


@router.get("/workflows/{workflow_type}", response_model=WorkflowInfo)
def get_workflow(workflow_type: str) -> WorkflowInfo:
    return _require_workflow(workflow_type)


@router.post(
    "/workflows/{workflow_type}/preflight",
    response_model=PreflightResponse,
)
async def preflight(workflow_type: str, request: Request) -> PreflightResponse:
    """Capability check only: no run recorded, no workflow, no LLM."""
    _require_workflow(workflow_type)
    settings = request.app.state.settings
    form = await request.form()
    try:
        config = parse_config_field(form)
        inputs, has_inputs = build_inputs(
            workflow_type, form, settings.upload_dir)
    except InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not has_inputs:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one input file or set use_sample=true.",
        )
    return run_standalone_preflight(workflow_type, inputs, config)


@router.post("/workflows/{workflow_type}/runs", response_model=RunDetail)
async def create_run(workflow_type: str, request: Request) -> RunDetail:
    """Run the workflow synchronously through the shared core pipeline.

    A preflight FAIL is a domain outcome: 200 with status='failed_preflight'
    and the preflight section populated. Only malformed requests are 4xx.
    """
    _require_workflow(workflow_type)
    settings = request.app.state.settings
    ledger = request.app.state.ledger
    audit = request.app.state.audit
    form = await request.form()
    try:
        config = parse_config_field(form)
        inputs, has_inputs = build_inputs(
            workflow_type, form, settings.upload_dir)
        validate_required_inputs(workflow_type, inputs)
    except InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not has_inputs and workflow_type != "freeform":
        raise HTTPException(
            status_code=422,
            detail="Provide at least one input file or set use_sample=true.",
        )

    actor_field = form.get("actor")
    actor = (str(actor_field).strip() if isinstance(actor_field, str) else "")
    actor = actor or settings.default_actor

    try:
        run_id = execute_run(
            workflow_type,
            inputs,
            ledger=ledger,
            audit=audit,
            actor=actor,
            export_dir=settings.export_dir,
            config=config,
            retention_category=settings.default_retention_category,
        )
    except Exception:
        # run_workflow already marked the run failed + audited it.
        raise HTTPException(
            status_code=500,
            detail=("The workflow run failed unexpectedly. The run was "
                    "marked failed; check the run history and audit log."),
        )

    detail = build_run_detail(ledger, run_id)
    if detail is None:  # pragma: no cover - the run was just created
        raise HTTPException(
            status_code=500,
            detail="The run completed but could not be read back.",
        )
    return detail
