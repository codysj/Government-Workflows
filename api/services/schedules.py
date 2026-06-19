"""Builder for GET /api/schedules (GW-11 scheduled runs - listing only).

Reads a src.core.scheduler.ScheduleStore at the schedules path wired onto
app.state and maps each Schedule record to the read-only ScheduleInfo model.
This batch only LISTS schedules; creating or triggering scheduled runs is
deferred (surfaced as a discovered follow-up).
"""
from __future__ import annotations

from src.core.scheduler import Schedule, ScheduleStore

from api.schemas.models import ScheduleInfo


def _iso(value: object) -> str:
    """Render a date/datetime (or already-string) as an ISO-8601 string."""
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _to_info(sched: Schedule) -> ScheduleInfo:
    return ScheduleInfo(
        schedule_id=sched.schedule_id,
        workflow_type=sched.workflow_type,
        cadence=sched.cadence.value,
        label=sched.label,
        interval_days=sched.interval_days,
        next_due=_iso(sched.next_due),
        active=sched.active,
        created_at=_iso(sched.created_at),
        last_run_at=_iso(sched.last_run_at) if sched.last_run_at else None,
    )


def build_schedule_list(store: ScheduleStore) -> list[ScheduleInfo]:
    """Return ScheduleInfo models for every schedule in ``store``."""
    return [_to_info(s) for s in store.list()]
