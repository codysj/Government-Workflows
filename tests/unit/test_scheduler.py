"""Tests for the local scheduled-runs store (src/core/scheduler.py)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.core.scheduler import (
    DEFAULT_INTERVAL_DAYS,
    DEFAULT_STORE_PATH,
    CadenceType,
    Schedule,
    ScheduleStore,
    advance_due,
    make_schedule,
)


# --------------------------------------------------------------------------- #
# advance_due — pure date math
# --------------------------------------------------------------------------- #
def test_advance_monthly():
    assert advance_due(date(2026, 1, 15), CadenceType.MONTHLY) == date(2026, 2, 15)


def test_advance_monthly_dec_to_jan_rollover():
    assert advance_due(date(2026, 12, 10), CadenceType.MONTHLY) == date(2027, 1, 10)


def test_advance_monthly_day_clamped():
    # Jan 31 + 1 month -> last valid day of Feb (2026 is not a leap year).
    assert advance_due(date(2026, 1, 31), CadenceType.MONTHLY) == date(2026, 2, 28)


def test_advance_quarterly():
    assert advance_due(date(2026, 1, 15), CadenceType.QUARTERLY) == date(2026, 4, 15)


def test_advance_quarterly_year_rollover():
    assert advance_due(date(2026, 11, 30), CadenceType.QUARTERLY) == date(2027, 2, 28)


def test_advance_before_agenda_default():
    assert advance_due(date(2026, 1, 1), CadenceType.BEFORE_AGENDA) == date(2026, 1, 15)


def test_advance_before_agenda_custom_interval():
    got = advance_due(date(2026, 1, 1), CadenceType.BEFORE_AGENDA, interval_days=7)
    assert got == date(2026, 1, 8)


def test_advance_custom_days_across_month():
    got = advance_due(date(2026, 1, 25), CadenceType.CUSTOM, interval_days=10)
    assert got == date(2026, 2, 4)


# --------------------------------------------------------------------------- #
# make_schedule
# --------------------------------------------------------------------------- #
def test_make_schedule_sets_next_due_to_start():
    s = make_schedule("bank_reconciliation", CadenceType.MONTHLY, "Monthly recon",
                      start=date(2026, 3, 1))
    assert s.next_due == date(2026, 3, 1)
    assert s.workflow_type == "bank_reconciliation"
    assert s.cadence is CadenceType.MONTHLY
    assert s.active is True
    assert s.interval_days == DEFAULT_INTERVAL_DAYS


def test_make_schedule_custom_interval():
    s = make_schedule("report_review", CadenceType.CUSTOM, "Every 5 days",
                      start=date(2026, 1, 1), interval_days=5)
    assert s.interval_days == 5


# --------------------------------------------------------------------------- #
# ScheduleStore — CRUD + JSON round-trip
# --------------------------------------------------------------------------- #
def test_default_store_path():
    assert str(DEFAULT_STORE_PATH).replace("\\", "/") == "runs/schedules.json"


def test_crud_roundtrip_via_json_file(tmp_path):
    path = tmp_path / "schedules.json"
    store = ScheduleStore(path)

    s = make_schedule("bank_reconciliation", CadenceType.MONTHLY, "Monthly recon",
                      start=date(2026, 6, 1))
    sid = store.add(s)
    assert path.exists()
    assert sid == s.schedule_id
    assert len(store.list()) == 1

    # Reload from disk: dates/datetimes round-trip as ISO strings.
    reloaded = ScheduleStore(path)
    got = reloaded.get(sid)
    assert got is not None
    assert got.next_due == date(2026, 6, 1)
    assert got.cadence is CadenceType.MONTHLY
    assert isinstance(got.created_at, datetime)

    # update
    got.label = "Renamed"
    reloaded.update(got)
    assert ScheduleStore(path).get(sid).label == "Renamed"

    # remove
    reloaded.remove(sid)
    assert reloaded.get(sid) is None
    assert ScheduleStore(path).list() == []


def test_update_unknown_raises(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    orphan = make_schedule("x", CadenceType.MONTHLY, "x", start=date(2026, 1, 1))
    with pytest.raises(KeyError):
        store.update(orphan)


def test_remove_absent_is_noop(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    store.remove("does-not-exist")  # should not raise
    assert store.list() == []


# --------------------------------------------------------------------------- #
# due() filtering
# --------------------------------------------------------------------------- #
def test_due_filters_by_as_of_and_sorts(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    early = make_schedule("a", CadenceType.MONTHLY, "early", start=date(2026, 1, 5))
    mid = make_schedule("b", CadenceType.MONTHLY, "mid", start=date(2026, 1, 10))
    future = make_schedule("c", CadenceType.MONTHLY, "future", start=date(2026, 2, 1))
    store.add(mid)
    store.add(early)
    store.add(future)

    due = store.due(as_of=date(2026, 1, 15))
    # future excluded; results sorted by next_due ascending.
    assert [s.label for s in due] == ["early", "mid"]


def test_due_excludes_inactive(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    s = make_schedule("a", CadenceType.MONTHLY, "inactive", start=date(2026, 1, 1))
    s.active = False
    store.add(s)
    assert store.due(as_of=date(2026, 12, 31)) == []


def test_due_includes_boundary_equal_date(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    s = make_schedule("a", CadenceType.MONTHLY, "boundary", start=date(2026, 1, 15))
    store.add(s)
    assert len(store.due(as_of=date(2026, 1, 15))) == 1


# --------------------------------------------------------------------------- #
# mark_run
# --------------------------------------------------------------------------- #
def test_mark_run_advances_and_sets_last_run(tmp_path):
    path = tmp_path / "s.json"
    store = ScheduleStore(path)
    s = make_schedule("a", CadenceType.MONTHLY, "recon", start=date(2026, 1, 1))
    sid = store.add(s)

    when = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    updated = store.mark_run(sid, when)
    assert updated.last_run_at == when
    assert updated.next_due == date(2026, 2, 1)  # advanced one month from run date

    # persisted across reload
    reloaded = ScheduleStore(path).get(sid)
    assert reloaded.last_run_at == when
    assert reloaded.next_due == date(2026, 2, 1)


def test_mark_run_no_advance(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    s = make_schedule("a", CadenceType.CUSTOM, "c", start=date(2026, 1, 1),
                      interval_days=7)
    sid = store.add(s)
    when = datetime(2026, 1, 3, tzinfo=timezone.utc)
    updated = store.mark_run(sid, when, advance=False)
    assert updated.last_run_at == when
    assert updated.next_due == date(2026, 1, 1)  # unchanged


def test_mark_run_custom_uses_interval(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    s = make_schedule("a", CadenceType.CUSTOM, "c", start=date(2026, 1, 1),
                      interval_days=7)
    sid = store.add(s)
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    updated = store.mark_run(sid, when)
    assert updated.next_due == date(2026, 1, 8)


def test_mark_run_unknown_raises(tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    with pytest.raises(KeyError):
        store.mark_run("nope", datetime.now(timezone.utc))
