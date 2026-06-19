"""GET /api/ai-usage (GW-9) - cross-run AI usage log."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.services.settings import ApiSettings

AI_USAGE_KEYS = {
    "run_id", "workflow_type", "created_at", "model_provider", "model_name",
    "prompt_template_version", "validation_status", "ai_draft_status",
    "referenced_source_row_count",
}


def test_ai_usage_empty_on_fresh_ledger(tmp_path_factory):
    """A brand-new ledger with no runs yields an empty rows list."""
    root = tmp_path_factory.mktemp("ai_usage_fresh")
    settings = ApiSettings(
        ledger_db_path=root / "ledger.db",
        audit_dir=root / "audit",
        export_dir=root / "exports",
        upload_dir=root / "uploads",
        schedules_path=root / "schedules.json",
        frontend_dist=root / "no_dist",
        llm_mode="mock",
    )
    with TestClient(create_app(settings)) as c:
        r = c.get("/api/ai-usage")
        assert r.status_code == 200, r.text
        assert r.json() == {"rows": []}


def test_ai_usage_populated_after_run(client):
    """A real sample run that records AI usage shows up in ai-usage."""
    run = client.post(
        "/api/workflows/ap_duplicate_review/runs",
        data={"use_sample": "true", "actor": "tester"},
    ).json()
    assert run["ai"]["available"] is True

    r = client.get("/api/ai-usage")
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows, "expected at least one AI usage row after a sample run"
    assert AI_USAGE_KEYS == set(rows[0])
    # The run we just created is represented.
    assert any(row["run_id"] == run["run_id"] for row in rows)
    row = next(row for row in rows if row["run_id"] == run["run_id"])
    assert row["workflow_type"] == "ap_duplicate_review"
    assert row["model_provider"] is not None
    assert isinstance(row["referenced_source_row_count"], int)


def test_ai_usage_newest_first(client):
    """Rows are returned newest-first by created_at (contract: newest leads)."""
    # Two sample runs create at least two AI usage rows in sequence.
    for _ in range(2):
        client.post(
            "/api/workflows/ap_duplicate_review/runs",
            data={"use_sample": "true", "actor": "tester"},
        )
    rows = client.get("/api/ai-usage").json()["rows"]
    stamps = [r["created_at"] for r in rows if r["created_at"]]
    assert stamps == sorted(stamps, reverse=True), (
        "ai-usage rows must be sorted newest-first by created_at"
    )
