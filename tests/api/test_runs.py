"""Run creation, run detail, artifacts, review actions, audit, restart."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SYNTHETIC = REPO_ROOT / "data" / "synthetic"


@pytest.fixture(scope="module")
def ap_run(client) -> dict:
    """One ap_duplicate_review sample run shared by the tests below."""
    r = client.post(
        "/api/workflows/ap_duplicate_review/runs",
        data={"use_sample": "true", "actor": "tester"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_ap_run_completed_with_findings(ap_run):
    assert ap_run["status"] == "completed"
    assert ap_run["workflow_type"] == "ap_duplicate_review"
    assert ap_run["created_by"] == "tester"
    assert ap_run["run_id"]
    assert len(ap_run["findings"]) > 0
    # Source-row traceability is first-class on findings.
    rows = [s for f in ap_run["findings"] for s in f["source_rows"]]
    assert rows, "expected at least one finding with source rows"
    assert {"file_id", "table_name", "row_index", "column_names",
            "source_values"} <= set(rows[0])
    # Preflight provenance is populated on every run.
    assert ap_run["preflight"] is not None
    assert ap_run["preflight"]["status"] in ("pass", "partial")
    assert ap_run["allowed_review_actions"] == [
        "mark_reviewed", "mark_resolved", "needs_follow_up", "add_note",
        "reject_ai_explanation", "approve_draft",
    ]


def test_ap_run_ai_and_validation(ap_run):
    ai = ap_run["ai"]
    assert ai is not None and ai["available"] is True
    assert ai["model_provider"] is not None
    assert isinstance(ai["response"], dict)
    validation = ap_run["validation"]
    assert validation is not None
    assert validation["passed"] is True
    assert validation["errors"] == []


def test_ap_run_artifacts_and_download(client, ap_run):
    names = {a["file_name"] for a in ap_run["artifacts"]}
    assert "validation_report.json" in names
    artifact = next(a for a in ap_run["artifacts"]
                    if a["file_name"] == "validation_report.json")
    assert artifact["download_url"].startswith(
        f"/api/runs/{ap_run['run_id']}/artifacts/")
    r = client.get(artifact["download_url"])
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == artifact["sha256"]


def test_artifact_path_traversal_404(client, ap_run):
    run_id = ap_run["run_id"]
    for name in ("..%2F..%2Fapp_settings.json",
                 "..\\..\\app_settings.json",
                 "no_such_artifact.csv"):
        r = client.get(f"/api/runs/{run_id}/artifacts/{name}")
        assert r.status_code == 404, name


def test_transaction_search_sample_run(client):
    r = client.post(
        "/api/workflows/transaction_search/runs",
        data={"use_sample": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    # The example query is auto-filled when use_sample=true and no query sent.
    assert body["summary"].get("query") or body["findings"] is not None


def test_je_upload_prep_invalid_draft_fails_closed(client):
    je_draft = (SYNTHETIC / "je_upload_prep" / "je_draft_invalid.csv")
    coa = SYNTHETIC / "tyler" / "chart_of_accounts.csv"
    r = client.post(
        "/api/workflows/je_upload_prep/runs",
        files={
            "je_draft": ("je_draft_invalid.csv",
                         je_draft.read_bytes(), "text/csv"),
            "chart_of_accounts": ("chart_of_accounts.csv",
                                  coa.read_bytes(), "text/csv"),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["summary"].get("upload_ready") is False
    names = {a["file_name"] for a in body["artifacts"]}
    assert "je_upload.xlsx" not in names
    assert "je_upload.csv" not in names


def test_run_failed_preflight_is_domain_outcome(client):
    """A blocking preflight failure returns 200 with status=failed_preflight."""
    bad_csv = b"foo,bar\n1,2\n"
    r = client.post(
        "/api/workflows/report_review/runs",
        files={"report_table": ("broken.csv", bad_csv, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed_preflight"
    assert body["preflight"] is not None
    assert body["preflight"]["status"] == "fail"
    assert any(f["blocks_run"] for f in body["preflight"]["findings"])
    assert body["preflight"]["next_steps"]
    assert body["findings"] == []
    assert body["ai"] is None
    assert body["validation"] is None
    # The failed run is recorded and visible in the run list.
    runs = client.get("/api/runs", params={"limit": 100}).json()["runs"]
    row = next(x for x in runs if x["run_id"] == body["run_id"])
    assert row["status"] == "failed_preflight"
    assert row["validation_passed"] is None


def test_run_missing_required_inputs_422(client):
    r = client.post("/api/workflows/je_upload_prep/runs", data={})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_run_unknown_workflow_404(client):
    r = client.post("/api/workflows/nope/runs", data={"use_sample": "true"})
    assert r.status_code == 404


def test_review_action_happy_path(client, ap_run):
    run_id = ap_run["run_id"]
    finding_id = ap_run["findings"][0]["finding_id"]
    r = client.post(
        f"/api/runs/{run_id}/review-actions",
        json={"action": "mark_reviewed", "actor": "tester",
              "note": "looks right", "finding_id": finding_id},
    )
    assert r.status_code == 200
    body = r.json()
    # mark_reviewed moves a pending run into in_review.
    assert body["human_review_status"] == "in_review"
    actions = body["review_actions"]
    assert any(
        a["action"] == "mark_reviewed" and a["finding_id"] == finding_id
        and a["note"] == "looks right" for a in actions)
    # The action shows up on the run detail and in the audit trail.
    detail = client.get(f"/api/runs/{run_id}").json()
    assert any(a["action"] == "mark_reviewed"
               for a in detail["review_actions"])
    audit = client.get(f"/api/runs/{run_id}/audit").json()["events"]
    assert any(e["event_type"] == "human_review_action" for e in audit)


def test_review_status_transitions(client, ap_run):
    """Decisions set terminal status; add_note never changes it."""
    run_id = ap_run["run_id"]
    r = client.post(
        f"/api/runs/{run_id}/review-actions",
        json={"action": "approve_draft", "actor": "tester"},
    )
    assert r.json()["human_review_status"] == "approved"
    # Engagement actions never downgrade an approved run.
    r = client.post(
        f"/api/runs/{run_id}/review-actions",
        json={"action": "mark_reviewed", "actor": "tester"},
    )
    assert r.json()["human_review_status"] == "approved"
    # add_note is status-neutral.
    r = client.post(
        f"/api/runs/{run_id}/review-actions",
        json={"action": "add_note", "actor": "tester", "note": "context"},
    )
    assert r.json()["human_review_status"] == "approved"
    # A later explicit rejection wins (latest decision).
    r = client.post(
        f"/api/runs/{run_id}/review-actions",
        json={"action": "reject_ai_explanation", "actor": "tester"},
    )
    assert r.json()["human_review_status"] == "rejected"
    # The run detail and run list reflect the updated status.
    assert (client.get(f"/api/runs/{run_id}").json()["human_review_status"]
            == "rejected")


def test_review_status_transition_all_rules(client):
    """Full transition matrix on a FRESH run (independent of the shared run).

    approve->approved; engagement does NOT downgrade an approved run;
    add_note is status-neutral; reject->rejected (latest decision wins);
    mark_reviewed on a pending run -> in_review.
    """
    # A dedicated run so this test owns the run's status lifecycle.
    run = client.post(
        "/api/workflows/ap_duplicate_review/runs",
        data={"use_sample": "true", "actor": "tester"},
    ).json()
    run_id = run["run_id"]

    def act(action, **kw):
        return client.post(
            f"/api/runs/{run_id}/review-actions",
            json={"action": action, "actor": "tester", **kw},
        ).json()["human_review_status"]

    # mark_reviewed moves pending -> in_review.
    assert act("mark_reviewed") == "in_review"
    # approve_draft is a terminal decision.
    assert act("approve_draft") == "approved"
    # Engagement actions never downgrade an approved run.
    assert act("mark_reviewed") == "approved"
    assert act("mark_resolved") == "approved"
    assert act("needs_follow_up") == "approved"
    # add_note is status-neutral.
    assert act("add_note", note="context") == "approved"
    # A later explicit decision wins (latest decision).
    assert act("reject_ai_explanation") == "rejected"
    # Engagement still cannot downgrade the terminal rejected status.
    assert act("mark_reviewed") == "rejected"
    # The run detail reflects the final status.
    assert (client.get(f"/api/runs/{run_id}").json()["human_review_status"]
            == "rejected")


def test_review_action_unknown_action_422(client, ap_run):
    r = client.post(
        f"/api/runs/{ap_run['run_id']}/review-actions",
        json={"action": "do_magic", "actor": "tester"},
    )
    assert r.status_code == 422


def test_review_action_unknown_run_404(client):
    r = client.post(
        "/api/runs/not_a_run/review-actions",
        json={"action": "mark_reviewed", "actor": "tester"},
    )
    assert r.status_code == 404


def test_runs_list_reflects_created_runs(client, ap_run):
    r = client.get("/api/runs", params={"limit": 100})
    assert r.status_code == 200
    runs = r.json()["runs"]
    ids = {row["run_id"] for row in runs}
    assert ap_run["run_id"] in ids
    row = next(x for x in runs if x["run_id"] == ap_run["run_id"])
    assert row["workflow_title"]
    assert row["status"] == "completed"
    assert row["validation_passed"] is True
    assert row["finding_count"] == len(ap_run["findings"])
    assert row["artifact_count"] == len(ap_run["artifacts"])


def test_audit_events_present(client, ap_run):
    r = client.get(f"/api/runs/{ap_run['run_id']}/audit")
    assert r.status_code == 200
    types = [e["event_type"] for e in r.json()["events"]]
    assert "run_created" in types
    assert "run_completed" in types
    for e in r.json()["events"]:
        assert {"event_type", "actor", "timestamp", "details"} <= set(e)


def test_run_detail_404(client):
    assert client.get("/api/runs/does_not_exist").status_code == 404


def test_rehydration_after_restart(api_settings, ap_run):
    """A FRESH app instance against the same ledger serves the same run."""
    from api.main import create_app

    app2 = create_app(api_settings)
    with TestClient(app2) as c2:
        r = c2.get(f"/api/runs/{ap_run['run_id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "completed"
        assert len(body["findings"]) == len(ap_run["findings"])
        assert body["preflight"] is not None
        assert body["ai"] is not None
        assert {a["file_name"] for a in body["artifacts"]} == {
            a["file_name"] for a in ap_run["artifacts"]}
        # Artifact downloads still work from the rehydrated instance.
        artifact = body["artifacts"][0]
        assert c2.get(artifact["download_url"]).status_code == 200
