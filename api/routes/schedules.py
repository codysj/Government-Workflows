"""Schedules: list (GW-11), create + manual trigger (GW-17), due (GW-18).

All read endpoints build a FRESH ScheduleStore per request (GW-19) so they
reflect schedules written after the API started (by Streamlit or by the create
endpoint) without an API restart. The store JSON path lives on
``app.state.schedules_path``.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app import workflow_registry as wfr
from src.core.schemas import utc_now

from api.schemas.models import (
    RunDetail,
    ScheduleCreateRequest,
    ScheduleInfo,
    ScheduleList,
)
from api.services.inputs import InputError, build_sample_inputs
from api.services.runs import build_run_detail, execute_run
from api.services.schedules import (
    ScheduleInputError,
    build_schedule_list,
    create_schedule,
    load_store,
    to_info,
)

router = APIRouter(tags=["schedules"])


@router.get("/schedules", response_model=ScheduleList)
def list_schedules(request: Request) -> ScheduleList:
    """List configured recurring runs (reflects post-startup writes)."""
    store = load_store(request.app.state.schedules_path)
    return ScheduleList(schedules=build_schedule_list(store))


@router.get("/schedules/due", response_model=ScheduleList)
def due_schedules(
    request: Request,
    as_of: str | None = Query(default=None),
) -> ScheduleList:
    """Active schedules due on or before ``as_of`` (defaults to today).

    This is a reminder query, not a recorded finding, so defaulting to the
    server's ``date.today()`` is intentional.
    """
    if as_of is None or not as_of.strip():
        as_of_date = date.today()
    else:
        try:
            as_of_date = date.fromisoformat(as_of.strip())
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid as_of date '{as_of}'. Use YYYY-MM-DD.")
    store = load_store(request.app.state.schedules_path)
    return ScheduleList(
        schedules=[to_info(s) for s in store.due(as_of_date)])


@router.post(
    "/schedules", response_model=ScheduleInfo, status_code=201)
def create_schedule_endpoint(
    body: ScheduleCreateRequest,
    request: Request,
    response: Response,
) -> ScheduleInfo:
    """Create a recurring run. Returns the new ScheduleInfo with HTTP 201."""
    store = load_store(request.app.state.schedules_path)
    try:
        sched = create_schedule(
            store,
            workflow_type=body.workflow_type,
            label=body.label,
            cadence=body.cadence,
            start=body.start,
            interval_days=body.interval_days,
            known_workflow_types=set(wfr.WORKFLOW_MODULES),
        )
    except ScheduleInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return to_info(sched)


@router.post("/schedules/{schedule_id}/run", response_model=RunDetail)
def trigger_schedule(schedule_id: str, request: Request) -> RunDetail:
    """Manually trigger a scheduled run.

    Resolves the workflow's bundled SAMPLE inputs and runs them through the
    SAME shared pipeline the run endpoint uses, then records the run against the
    schedule (mark_run advances next_due) and returns the resulting RunDetail.
    """
    settings = request.app.state.settings
    ledger = request.app.state.ledger
    audit = request.app.state.audit
    store = load_store(settings.schedules_path)

    sched = store.get(schedule_id)
    if sched is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown schedule '{schedule_id}'.")

    try:
        inputs = build_sample_inputs(sched.workflow_type)
    except KeyError:
        raise HTTPException(
            status_code=422,
            detail=(f"Schedule references unknown workflow "
                    f"'{sched.workflow_type}'."))
    except InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        run_id = execute_run(
            sched.workflow_type,
            inputs,
            ledger=ledger,
            audit=audit,
            actor=settings.default_actor,
            export_dir=settings.export_dir,
            retention_category=settings.default_retention_category,
        )
    except Exception:
        # run_workflow already marked the run failed + audited it.
        raise HTTPException(
            status_code=500,
            detail=("The scheduled run failed unexpectedly. The run was "
                    "marked failed; check the run history and audit log."),
        )

    # Record the run against the schedule and advance its next_due. mark_run
    # mutates the in-memory store and rewrites the JSON file.
    store.mark_run(schedule_id, utc_now(), advance=True)

    detail = build_run_detail(ledger, run_id)
    if detail is None:  # pragma: no cover - run was just created
        raise HTTPException(
            status_code=500,
            detail="The scheduled run completed but could not be loaded.")
    return detail
