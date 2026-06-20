"""Service helpers for the schedules endpoints.

Maps src.core.scheduler.Schedule records to the ScheduleInfo contract model and
provides create/validation helpers. A FRESH ScheduleStore is built per request
(:func:`load_store`) so the read endpoints reflect schedules written after the
API started (e.g. by Streamlit or by the create endpoint) WITHOUT a restart
(GW-19). The store file is tiny, so re-reading it per request is cheap.

No financial logic lives here; the scheduler core owns all date math.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from src.core.scheduler import (
    CadenceType,
    Schedule,
    ScheduleStore,
    make_schedule,
)

from api.schemas.models import ScheduleInfo


class ScheduleInputError(Exception):
    """A user-correctable schedule input problem -> HTTP 422 plain message."""


def load_store(path: str | Path) -> ScheduleStore:
    """Build a fresh ScheduleStore at ``path`` (re-reads the JSON each call)."""
    return ScheduleStore(path)


def _iso(value: object) -> str:
    """Render a date/datetime (or already-string) as an ISO-8601 string."""
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def to_info(sched: Schedule) -> ScheduleInfo:
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


# Backwards-compatible alias (the old private name used by build_schedule_list).
_to_info = to_info


def build_schedule_list(store: ScheduleStore) -> list[ScheduleInfo]:
    """Return ScheduleInfo models for every schedule in ``store``."""
    return [to_info(s) for s in store.list()]


def _parse_cadence(raw: str) -> CadenceType:
    try:
        return CadenceType(str(raw).strip().lower())
    except ValueError:
        allowed = ", ".join(c.value for c in CadenceType)
        raise ScheduleInputError(
            f"Unknown cadence '{raw}'. Allowed cadences: {allowed}.")


def _parse_start(raw: str | None) -> date:
    if raw is None or not str(raw).strip():
        return date.today()
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise ScheduleInputError(
            f"Invalid start date '{raw}'. Use the YYYY-MM-DD format.")


def create_schedule(
    store: ScheduleStore,
    *,
    workflow_type: str,
    label: str,
    cadence: str,
    start: str | None,
    interval_days: int | None,
    known_workflow_types: set[str],
) -> Schedule:
    """Validate inputs, build a Schedule via make_schedule, persist it.

    Raises :class:`ScheduleInputError` (-> 422) for an unknown workflow type,
    an unknown cadence, a bad start date, or a non-positive interval. Returns
    the stored Schedule.
    """
    if workflow_type not in known_workflow_types:
        allowed = ", ".join(sorted(known_workflow_types))
        raise ScheduleInputError(
            f"Unknown workflow type '{workflow_type}'. Known types: {allowed}.")
    if not str(label).strip():
        raise ScheduleInputError("A non-empty 'label' is required.")
    cadence_value = _parse_cadence(cadence)
    start_date = _parse_start(start)
    if interval_days is not None and interval_days <= 0:
        raise ScheduleInputError(
            "'interval_days' must be a positive number of days.")

    sched = make_schedule(
        workflow_type=workflow_type,
        cadence=cadence_value,
        label=str(label).strip(),
        start=start_date,
        interval_days=interval_days,
    )
    store.add(sched)
    return sched
