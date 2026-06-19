"""GET /api/schedules (GW-11) - read-only listing of recurring runs."""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas.models import ScheduleList
from api.services.schedules import build_schedule_list

router = APIRouter(tags=["schedules"])


@router.get("/schedules", response_model=ScheduleList)
def list_schedules(request: Request) -> ScheduleList:
    """List configured recurring runs. Read-only this batch."""
    return ScheduleList(
        schedules=build_schedule_list(request.app.state.schedule_store))
