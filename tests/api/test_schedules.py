"""GET /api/schedules (GW-11) - read-only listing of recurring runs."""
from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from src.core.scheduler import CadenceType, ScheduleStore, make_schedule

from api.main import create_app
from api.services.settings import ApiSettings

SCHEDULE_KEYS = {
    "schedule_id", "workflow_type", "cadence", "label", "interval_days",
    "next_due", "active", "created_at", "last_run_at",
}


def _settings(root) -> ApiSettings:
    return ApiSettings(
        ledger_db_path=root / "ledger.db",
        audit_dir=root / "audit",
        export_dir=root / "exports",
        upload_dir=root / "uploads",
        schedules_path=root / "schedules.json",
        frontend_dist=root / "no_dist",
        llm_mode="mock",
    )


def test_schedules_empty_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_empty")
    with TestClient(create_app(_settings(root))) as c:
        r = c.get("/api/schedules")
        assert r.status_code == 200, r.text
        assert r.json() == {"schedules": []}


def test_schedules_lists_added_schedule(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_one")
    settings = _settings(root)
    # Seed the store at the tmp path BEFORE building the app (the app loads the
    # store once on startup).
    store = ScheduleStore(settings.schedules_path)
    sched = make_schedule(
        workflow_type="ap_duplicate_review",
        cadence=CadenceType.MONTHLY,
        label="Monthly AP reconciliation",
        start=date(2026, 7, 1),
    )
    store.add(sched)

    with TestClient(create_app(settings)) as c:
        r = c.get("/api/schedules")
        assert r.status_code == 200, r.text
        schedules = r.json()["schedules"]
        assert len(schedules) == 1
        row = schedules[0]
        assert SCHEDULE_KEYS == set(row)
        assert row["schedule_id"] == sched.schedule_id
        assert row["workflow_type"] == "ap_duplicate_review"
        assert row["cadence"] == "monthly"
        assert row["label"] == "Monthly AP reconciliation"
        assert row["next_due"] == "2026-07-01"
        assert row["active"] is True
        assert row["last_run_at"] is None
