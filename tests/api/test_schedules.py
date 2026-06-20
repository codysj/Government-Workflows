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


def test_schedules_reflect_post_startup_writes(tmp_path_factory):
    """GW-19: a schedule written to the JSON store AFTER startup shows up with
    the SAME app instance (no recreate)."""
    root = tmp_path_factory.mktemp("sched_live")
    settings = _settings(root)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/schedules").json() == {"schedules": []}
        # Write directly to the JSON path, mimicking Streamlit/another process.
        store = ScheduleStore(settings.schedules_path)
        sched = make_schedule(
            workflow_type="report_review",
            cadence=CadenceType.QUARTERLY,
            label="Quarterly variance review",
            start=date(2026, 9, 1),
        )
        store.add(sched)
        # Same app instance now reflects it (store re-read per request).
        schedules = c.get("/api/schedules").json()["schedules"]
        assert [s["schedule_id"] for s in schedules] == [sched.schedule_id]


def test_create_schedule(tmp_path_factory):
    """GW-17: POST /api/schedules creates a schedule, returns 201 + ScheduleInfo,
    and the new schedule is then listable on the same app."""
    root = tmp_path_factory.mktemp("sched_create")
    with TestClient(create_app(_settings(root))) as c:
        r = c.post("/api/schedules", json={
            "workflow_type": "ap_duplicate_review",
            "label": "Monthly AP",
            "cadence": "monthly",
            "start": "2026-08-01",
        })
        assert r.status_code == 201, r.text
        row = r.json()
        assert SCHEDULE_KEYS == set(row)
        assert row["workflow_type"] == "ap_duplicate_review"
        assert row["cadence"] == "monthly"
        assert row["next_due"] == "2026-08-01"
        assert row["active"] is True
        # Listable afterwards on the same instance.
        listed = c.get("/api/schedules").json()["schedules"]
        assert any(s["schedule_id"] == row["schedule_id"] for s in listed)


def test_create_schedule_defaults_start_to_today(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_create_default")
    with TestClient(create_app(_settings(root))) as c:
        r = c.post("/api/schedules", json={
            "workflow_type": "report_review",
            "label": "Custom cadence",
            "cadence": "custom",
            "interval_days": 21,
        })
        assert r.status_code == 201, r.text
        assert r.json()["interval_days"] == 21
        assert r.json()["next_due"] == date.today().isoformat()


def test_create_schedule_bad_workflow_422(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_bad_wf")
    with TestClient(create_app(_settings(root))) as c:
        r = c.post("/api/schedules", json={
            "workflow_type": "nope",
            "label": "x",
            "cadence": "monthly",
        })
        assert r.status_code == 422
        assert "detail" in r.json()


def test_create_schedule_bad_cadence_422(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_bad_cad")
    with TestClient(create_app(_settings(root))) as c:
        r = c.post("/api/schedules", json={
            "workflow_type": "ap_duplicate_review",
            "label": "x",
            "cadence": "fortnightly",
        })
        assert r.status_code == 422
        assert "detail" in r.json()


def test_schedules_due(tmp_path_factory):
    """GW-18: GET /api/schedules/due?as_of=... returns only due, active ones."""
    root = tmp_path_factory.mktemp("sched_due")
    settings = _settings(root)
    store = ScheduleStore(settings.schedules_path)
    due_sched = make_schedule(
        workflow_type="ap_duplicate_review",
        cadence=CadenceType.MONTHLY,
        label="Due now",
        start=date(2026, 1, 1),
    )
    future_sched = make_schedule(
        workflow_type="report_review",
        cadence=CadenceType.MONTHLY,
        label="Future",
        start=date(2027, 1, 1),
    )
    store.add(due_sched)
    store.add(future_sched)
    with TestClient(create_app(settings)) as c:
        r = c.get("/api/schedules/due", params={"as_of": "2026-06-15"})
        assert r.status_code == 200, r.text
        ids = [s["schedule_id"] for s in r.json()["schedules"]]
        assert ids == [due_sched.schedule_id]


def test_schedules_due_bad_date_422(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_due_bad")
    with TestClient(create_app(_settings(root))) as c:
        r = c.get("/api/schedules/due", params={"as_of": "not-a-date"})
        assert r.status_code == 422


def test_trigger_schedule_runs_and_advances(tmp_path_factory):
    """GW-17: POST /api/schedules/{id}/run yields a completed run AND advances
    the schedule (next_due moves forward, last_run_at set)."""
    root = tmp_path_factory.mktemp("sched_trigger")
    settings = _settings(root)
    store = ScheduleStore(settings.schedules_path)
    sched = make_schedule(
        workflow_type="ap_duplicate_review",
        cadence=CadenceType.MONTHLY,
        label="Monthly AP",
        start=date(2026, 6, 1),
    )
    store.add(sched)

    with TestClient(create_app(settings)) as c:
        r = c.post(f"/api/schedules/{sched.schedule_id}/run")
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail["status"] == "completed"
        assert detail["workflow_type"] == "ap_duplicate_review"
        assert detail["run_id"]
        # The run is visible in run history.
        runs = c.get("/api/runs", params={"limit": 100}).json()["runs"]
        assert any(x["run_id"] == detail["run_id"] for x in runs)
        # The schedule advanced: next_due moved off the start and last_run_at set.
        listed = c.get("/api/schedules").json()["schedules"]
        updated = next(
            s for s in listed if s["schedule_id"] == sched.schedule_id)
        assert updated["next_due"] != "2026-06-01"
        assert updated["last_run_at"] is not None


def test_trigger_unknown_schedule_404(tmp_path_factory):
    root = tmp_path_factory.mktemp("sched_trigger_404")
    with TestClient(create_app(_settings(root))) as c:
        r = c.post("/api/schedules/not_a_schedule/run")
        assert r.status_code == 404
