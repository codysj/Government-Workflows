"""Local, manual-trigger scheduled recurring runs (Tier 1 roadmap #3).

The roadmap asks for configured workflows to run on a schedule (monthly
reconciliation reminders, quarterly variance reviews, report review before
agenda-packet deadlines). For the MVP this is LOCAL-ONLY and MANUAL-TRIGGERED:
there is NO background daemon, NO cron, and NO threads. Instead this module
provides a deterministic store of :class:`Schedule` records and pure "what is
due" logic, so the UI/CLI can list due schedules whenever the user opens the
app and let them launch the corresponding run by hand.

Everything here is deterministic and side-effect free apart from
:class:`ScheduleStore`'s JSON persistence. The date math (:func:`advance_due`)
takes the reference date as a parameter and never calls ``date.today()`` /
``datetime.now()`` internally, so cadence rollover is fully testable.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from src.core.schemas import make_id, utc_now

# Default on-disk location for the schedule store.
DEFAULT_STORE_PATH = Path("runs") / "schedules.json"

# Fallback interval (days) for cadences driven by interval_days.
DEFAULT_INTERVAL_DAYS = 14


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class CadenceType(str, Enum):
    """How often a scheduled workflow recurs."""

    MONTHLY = "monthly"          # monthly reconciliation reminder (+1 month)
    QUARTERLY = "quarterly"      # quarterly variance review (+3 months)
    BEFORE_AGENDA = "before_agenda"  # report review before agenda deadlines
    CUSTOM = "custom"            # every interval_days


# --------------------------------------------------------------------------- #
# Pure date math
# --------------------------------------------------------------------------- #
def _add_months(d: date, months: int) -> date:
    """Return ``d`` shifted by ``months``, clamping the day to month length.

    Stdlib only (no dateutil). Day-of-month is clamped so that e.g. Jan 31 +1
    month lands on the last valid day of February rather than overflowing.
    """
    # Convert to a zero-based month index, shift, then split back out.
    month_index = (d.year * 12 + (d.month - 1)) + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    # Last day of the target month: day 1 of the following month minus one day.
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


def advance_due(
    current: date,
    cadence: CadenceType,
    interval_days: Optional[int] = None,
) -> date:
    """Compute the next due date from ``current`` for ``cadence``.

    Pure function: the reference date is the ``current`` parameter, never the
    system clock. ``interval_days`` is used for CUSTOM and BEFORE_AGENDA (it
    defaults to :data:`DEFAULT_INTERVAL_DAYS` when not supplied); it is ignored
    for MONTHLY / QUARTERLY which step by calendar month.
    """
    if cadence is CadenceType.MONTHLY:
        return _add_months(current, 1)
    if cadence is CadenceType.QUARTERLY:
        return _add_months(current, 3)
    # BEFORE_AGENDA and CUSTOM both advance by a fixed number of days.
    days = DEFAULT_INTERVAL_DAYS if interval_days is None else interval_days
    return current + timedelta(days=days)


# --------------------------------------------------------------------------- #
# Schedule model
# --------------------------------------------------------------------------- #
class Schedule(BaseModel):
    """A configured recurring run.

    ``next_due`` is the date the workflow is next expected to be run; the store
    surfaces it via :meth:`ScheduleStore.due`. ``interval_days`` drives CUSTOM
    and BEFORE_AGENDA cadences. Dates/datetimes serialize to ISO strings in JSON.
    """

    schedule_id: str = Field(default_factory=make_id)
    workflow_type: str
    cadence: CadenceType
    label: str
    interval_days: int = DEFAULT_INTERVAL_DAYS
    next_due: date
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    last_run_at: Optional[datetime] = None


def make_schedule(
    workflow_type: str,
    cadence: CadenceType,
    label: str,
    start: date,
    interval_days: Optional[int] = None,
) -> Schedule:
    """Build a :class:`Schedule` whose first ``next_due`` is ``start``.

    ``start`` is the first date the run is due (the caller decides the kickoff
    date). For CUSTOM / BEFORE_AGENDA cadences ``interval_days`` sets the step
    used by :func:`advance_due`; when omitted it defaults to
    :data:`DEFAULT_INTERVAL_DAYS`.
    """
    step = DEFAULT_INTERVAL_DAYS if interval_days is None else interval_days
    return Schedule(
        workflow_type=workflow_type,
        cadence=cadence,
        label=label,
        interval_days=step,
        next_due=start,
    )


# --------------------------------------------------------------------------- #
# Persistent store
# --------------------------------------------------------------------------- #
class ScheduleStore:
    """A JSON-file-backed collection of :class:`Schedule` records.

    The store loads on construction and rewrites the whole file on every
    mutation (the schedule set is tiny). Pass a tmp path in tests; the default
    is :data:`DEFAULT_STORE_PATH` (``runs/schedules.json``).
    """

    def __init__(self, path: str | Path = DEFAULT_STORE_PATH) -> None:
        self.path = Path(path)
        self._schedules: dict[str, Schedule] = {}
        self._load()

    # -- persistence -------------------------------------------------------- #
    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw:
            sched = Schedule.model_validate(item)
            self._schedules[sched.schedule_id] = sched

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [s.model_dump(mode="json") for s in self._schedules.values()]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- CRUD --------------------------------------------------------------- #
    def add(self, schedule: Schedule) -> str:
        """Store ``schedule`` and return its id."""
        self._schedules[schedule.schedule_id] = schedule
        self._save()
        return schedule.schedule_id

    def list(self) -> list[Schedule]:
        """Return all schedules (insertion order)."""
        return list(self._schedules.values())

    def get(self, schedule_id: str) -> Optional[Schedule]:
        """Return the schedule with ``schedule_id`` or ``None``."""
        return self._schedules.get(schedule_id)

    def update(self, schedule: Schedule) -> None:
        """Replace the stored schedule with matching ``schedule_id``.

        Raises ``KeyError`` if the id is not present.
        """
        if schedule.schedule_id not in self._schedules:
            raise KeyError(schedule.schedule_id)
        self._schedules[schedule.schedule_id] = schedule
        self._save()

    def remove(self, schedule_id: str) -> None:
        """Delete the schedule with ``schedule_id`` (no-op if absent)."""
        if self._schedules.pop(schedule_id, None) is not None:
            self._save()

    # -- run lifecycle ------------------------------------------------------ #
    def mark_run(
        self, schedule_id: str, when: datetime, advance: bool = True
    ) -> Schedule:
        """Record that the schedule ran at ``when``.

        Sets ``last_run_at`` and, when ``advance`` is True, recomputes
        ``next_due`` via :func:`advance_due` from the run date. Returns the
        updated schedule. Raises ``KeyError`` for an unknown id.
        """
        sched = self._schedules[schedule_id]
        sched.last_run_at = when
        if advance:
            sched.next_due = advance_due(
                when.date(), sched.cadence, sched.interval_days
            )
        self._save()
        return sched

    def due(self, as_of: date) -> list[Schedule]:
        """Return active schedules due on or before ``as_of``, by ``next_due``."""
        ready = [
            s
            for s in self._schedules.values()
            if s.active and s.next_due <= as_of
        ]
        return sorted(ready, key=lambda s: s.next_due)


__all__ = [
    "CadenceType",
    "Schedule",
    "ScheduleStore",
    "advance_due",
    "make_schedule",
    "DEFAULT_STORE_PATH",
    "DEFAULT_INTERVAL_DAYS",
]
