"""Run-scoped Q&A assistant API: POST /api/runs/{id}/questions."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qa_run(client) -> dict:
    r = client.post(
        "/api/workflows/bank_reconciliation/runs",
        data={"use_sample": "true", "actor": "tester"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ask(client, run_id, question, actor="tester"):
    return client.post(
        f"/api/runs/{run_id}/questions",
        json={"question": question, "actor": actor},
    )


def test_grounded_question_returns_cited_draft(client, qa_run):
    r = _ask(client, qa_run["run_id"],
             "Why might these unmatched items not reconcile?")
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["answerable"] is True
    assert turn["referenced_source_rows"]
    assert turn["validation"]["passed"] is True
    assert turn["validation"]["invented_reference_detected"] is False


def test_ungrounded_question_is_refused(client, qa_run):
    r = _ask(client, qa_run["run_id"],
             "Recommend unrelated zebra giraffe spaceship stocks")
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["answerable"] is False
    assert turn["referenced_source_rows"] == []


def test_empty_question_rejected(client, qa_run):
    r = _ask(client, qa_run["run_id"], "   ")
    assert r.status_code == 422


def test_question_unknown_run_404(client):
    r = _ask(client, "not_a_run", "anything")
    assert r.status_code == 404


def test_turn_appears_on_run_detail_audit_and_usage(client, qa_run):
    run_id = qa_run["run_id"]
    _ask(client, run_id, "What do the flagged items show?")

    # Run detail carries the Q&A turns separately from the workflow draft.
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["qa_turns"], "qa_turns should be present on the run detail"
    assert detail["ai"] is not None  # workflow draft still the active AI section
    assert all(
        "question" in t and "answer" in t and "validation" in t
        for t in detail["qa_turns"]
    )

    # Audit trail records the Q&A interaction.
    events = [e["event_type"] for e in client.get(f"/api/runs/{run_id}/audit").json()["events"]]
    assert "llm_response_received" in events

    # AI usage log lists the Q&A turn(s) for this run.
    rows = client.get("/api/ai-usage").json()["rows"]
    assert any(row["run_id"] == run_id for row in rows)
