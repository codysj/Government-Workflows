"""Tests for the SQLite run ledger (Phase 3.4).

Covers create/list/read/write round-trips, audit-event persistence, and — most
importantly — the thread-safety regression: a file-backed ledger must be usable
from a thread other than the one that constructed it (Streamlit reruns the
script on rotating ScriptRunner threads). Before the per-operation-connection
fix this raised:

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.
"""
from __future__ import annotations

import threading

import pytest

from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    ArtifactType,
    DeterministicFinding,
    EventType,
    ExportArtifact,
    FindingType,
    HumanReviewAction,
    InputFile,
    LLMResponse,
    Severity,
    SourceRowRef,
    ValidationResult,
    WorkflowRun,
)


def _make_llm(provider="mock", name="mock-llm", template="bank.v1"):
    return LLMResponse(
        prompt_template_version=template, model_provider=provider,
        model_name=name, response_json={"summary": "ok"},
        referenced_source_rows=["bank:0"],
    )


def _make_run(run_id: str = "run-1", workflow_type: str = "bank_reconciliation") -> WorkflowRun:
    return WorkflowRun(
        run_id=run_id,
        workflow_type=workflow_type,
        created_by="tester",
        input_files=[
            InputFile(
                file_name="bank.csv",
                file_type="csv",
                file_hash="abc123",
                parser_used="csv_loader",
                row_count=3,
                column_names=["date", "amount"],
            )
        ],
        summary={"matched": 2},
    )


def _make_finding(run_id: str = "run-1") -> DeterministicFinding:
    return DeterministicFinding(
        finding_type=FindingType.UNMATCHED_BANK,
        severity=Severity.MEDIUM,
        description="Unmatched bank item",
        source_rows=[
            SourceRowRef(
                file_id="f1", table_name="bank", row_index=0,
                column_names=["amount"], source_values={"amount": 100},
            )
        ],
        computed_values={"amount": 100},
        rule_used="unmatched",
        requires_human_review=True,
    )


# --------------------------------------------------------------------------- #
# Round-trip: create / list / read / write
# --------------------------------------------------------------------------- #
def test_create_list_get_roundtrip(tmp_path):
    db = str(tmp_path / "ledger.db")
    ledger = RunLedger(db)
    ledger.create_run(_make_run())

    runs = ledger.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["workflow_type"] == "bank_reconciliation"
    assert runs[0]["summary"] == {"matched": 2}

    run = ledger.get_run("run-1")
    assert run is not None
    assert len(run["input_files"]) == 1
    assert run["input_files"][0]["file_name"] == "bank.csv"


def test_store_children_and_readback(tmp_path):
    db = str(tmp_path / "ledger.db")
    ledger = RunLedger(db)
    ledger.create_run(_make_run())

    ledger.store_findings("run-1", [_make_finding()])
    ledger.store_llm_response(
        "run-1",
        LLMResponse(
            prompt_template_version="bank.v1",
            model_provider="mock",
            model_name="mock-llm",
            response_json={"summary": "ok"},
        ),
    )
    ledger.store_validation_result(
        "run-1", ValidationResult(passed=True, numeric_claims_checked=1)
    )
    ledger.store_human_review_action(
        "run-1",
        HumanReviewAction(run_id="run-1", action="mark_reviewed", actor="tester"),
    )
    ledger.store_export_artifact(
        "run-1",
        ExportArtifact(
            run_id="run-1",
            artifact_type=ArtifactType.MARKDOWN,
            file_name="summary.md",
            path="exports/run-1/summary.md",
            sha256="deadbeef",
        ),
    )

    run = ledger.get_run("run-1")
    assert len(run["findings"]) == 1
    assert len(run["llm_responses"]) == 1
    assert len(run["validation_results"]) == 1
    assert len(run["human_review_actions"]) == 1
    assert len(run["export_artifacts"]) == 1


def test_update_run_status(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    ledger.update_run_status("run-1", "completed", "approved", {"matched": 5})
    run = ledger.get_run("run-1")
    assert run["status"] == "completed"
    assert run["human_review_status"] == "approved"
    assert run["summary"] == {"matched": 5}


def test_get_run_missing_returns_none(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    assert ledger.get_run("nope") is None


# --------------------------------------------------------------------------- #
# Atomic human-review status primitive (GW-3, additive)
# --------------------------------------------------------------------------- #
def test_apply_human_review_status_unconditional(tmp_path):
    """An unconditional update always wins and returns the persisted value."""
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())  # starts pending

    assert ledger.apply_human_review_status("run-1", "approved") == "approved"
    assert ledger.get_run("run-1")["human_review_status"] == "approved"
    # A later unconditional decision overwrites the prior one (latest wins).
    assert ledger.apply_human_review_status("run-1", "rejected") == "rejected"
    assert ledger.get_run("run-1")["human_review_status"] == "rejected"


def test_apply_human_review_status_guard_holds(tmp_path):
    """A guarded update only lands when the row is in the expected state."""
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())  # pending

    # Guard matches -> the update lands.
    result = ledger.apply_human_review_status(
        "run-1", "in_review", expected_current="pending")
    assert result == "in_review"
    assert ledger.get_run("run-1")["human_review_status"] == "in_review"


def test_apply_human_review_status_guard_blocks_downgrade(tmp_path):
    """A guarded engagement update can never overwrite a terminal status."""
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    ledger.apply_human_review_status("run-1", "approved")  # terminal

    # Engagement action expects pending; guard fails -> no-op, status preserved.
    result = ledger.apply_human_review_status(
        "run-1", "in_review", expected_current="pending")
    assert result == "approved"
    assert ledger.get_run("run-1")["human_review_status"] == "approved"


def test_apply_human_review_status_unknown_run_returns_none(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    assert ledger.apply_human_review_status("nope", "approved") is None


def test_apply_human_review_status_leaves_other_columns_unchanged(tmp_path):
    """Only human_review_status is touched; status/summary/retention stay."""
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    ledger.update_run_status(
        "run-1", "completed", summary={"matched": 9},
        retention_category="draft_working")

    ledger.apply_human_review_status("run-1", "approved")
    run = ledger.get_run("run-1")
    assert run["human_review_status"] == "approved"
    assert run["status"] == "completed"
    assert run["summary"] == {"matched": 9}
    assert run["retention_category"] == "draft_working"


def test_update_run_status_still_works_unchanged(tmp_path):
    """The pre-existing update_run_status path is unaffected by the new method."""
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    ledger.update_run_status("run-1", "completed", "in_review", {"matched": 3})
    run = ledger.get_run("run-1")
    assert run["status"] == "completed"
    assert run["human_review_status"] == "in_review"
    assert run["summary"] == {"matched": 3}


def test_review_transition_concurrency_no_downgrade(tmp_path):
    """Interleaved decision + engagement transitions never downgrade a terminal.

    Deterministic (no sleeps): a decision (approve) followed by an engagement
    action (mark_reviewed) must leave the run approved, regardless of order,
    because the engagement transition is guarded on the expected pending state.
    """
    from app.workflow_registry import apply_review_status_transition

    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())

    # Decision lands first, engagement second: engagement must not downgrade.
    assert apply_review_status_transition(ledger, "run-1", "approve_draft") == (
        "approved")
    assert apply_review_status_transition(ledger, "run-1", "mark_reviewed") == (
        "approved")
    assert ledger.get_run("run-1")["human_review_status"] == "approved"

    # Fresh run: engagement first (pending->in_review), then a decision wins.
    ledger.create_run(_make_run("run-2"))
    assert apply_review_status_transition(ledger, "run-2", "mark_reviewed") == (
        "in_review")
    assert apply_review_status_transition(
        ledger, "run-2", "reject_ai_explanation") == "rejected"
    assert ledger.get_run("run-2")["human_review_status"] == "rejected"


def test_review_transition_concurrency_threaded(tmp_path):
    """Two threads racing a decision vs an engagement leave a terminal status."""
    from app.workflow_registry import apply_review_status_transition

    db = str(tmp_path / "ledger.db")
    ledger = RunLedger(db)
    ledger.create_run(_make_run())
    ledger.apply_human_review_status("run-1", "approved")  # already terminal

    errors: list[str] = []

    def engage():
        try:
            for _ in range(50):
                apply_review_status_transition(ledger, "run-1", "mark_reviewed")
        except Exception as exc:  # pragma: no cover
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=engage) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # No interleaving of guarded engagement updates can clobber the decision.
    assert ledger.get_run("run-1")["human_review_status"] == "approved"


def test_persists_across_instances(tmp_path):
    """A new RunLedger on the same file sees prior data (real persistence)."""
    db = str(tmp_path / "ledger.db")
    RunLedger(db).create_run(_make_run())
    # Fresh instance, same file.
    assert len(RunLedger(db).list_runs()) == 1


def test_in_memory_ledger_shares_state(tmp_path):
    """:memory: keeps one connection so writes/reads share the same DB."""
    ledger = RunLedger(":memory:")
    ledger.create_run(_make_run())
    assert len(ledger.list_runs()) == 1
    ledger.close()


# --------------------------------------------------------------------------- #
# Audit events through the ledger + AuditLog
# --------------------------------------------------------------------------- #
def test_audit_events_via_ledger(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    audit.run_created("run-1", "tester", note="created")
    audit.run_completed("run-1", "tester")
    events = audit.list_events("run-1")
    assert [e["event_type"] for e in events] == [
        EventType.RUN_CREATED.value,
        EventType.RUN_COMPLETED.value,
    ]
    assert events[0]["details"]["note"] == "created"


# --------------------------------------------------------------------------- #
# AI interaction history (Tier 1 searchable AI usage log)
# --------------------------------------------------------------------------- #
def test_list_llm_interactions(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run("run-a", "bank_reconciliation"))
    ledger.update_run_status("run-a", "completed",
                             summary={"validation_status": "passed"})
    ledger.store_llm_response("run-a", _make_llm(template="bank.v1"))
    # Second run, approved by a human -> final.
    ledger.create_run(_make_run("run-b", "budget_variance"))
    ledger.store_llm_response("run-b", _make_llm(template="budget.v1"))
    ledger.store_human_review_action("run-b", HumanReviewAction(
        run_id="run-b", action="approve_draft", actor="director"))

    rows = ledger.list_llm_interactions()
    assert len(rows) == 2
    by_run = {r["run_id"]: r for r in rows}
    assert by_run["run-a"]["workflow_type"] == "bank_reconciliation"
    assert by_run["run-a"]["prompt_template_version"] == "bank.v1"
    assert by_run["run-a"]["validation_status"] == "passed"
    assert by_run["run-a"]["ai_draft_status"] == "draft"
    assert by_run["run-b"]["ai_draft_status"] == "final (human-approved)"


def test_list_llm_interactions_empty(tmp_path):
    assert RunLedger(str(tmp_path / "ledger.db")).list_llm_interactions() == []


# --------------------------------------------------------------------------- #
# Thread-safety regression (the reported Streamlit bug)
# --------------------------------------------------------------------------- #
def test_file_ledger_usable_from_other_thread(tmp_path):
    """Construct in the main thread, read+write from another thread."""
    db = str(tmp_path / "ledger.db")
    ledger = RunLedger(db)
    ledger.create_run(_make_run("run-main"))

    errors: list[str] = []

    def worker():
        try:
            # All three operations the Streamlit pages call.
            ledger.list_runs()
            ledger.get_run("run-main")
            ledger.create_run(_make_run("run-worker"))
            ledger.store_findings("run-worker", [_make_finding("run-worker")])
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(f"{type(exc).__name__}: {exc}")

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert errors == [], f"cross-thread ledger use failed: {errors}"
    # Data written from the worker thread is visible from the main thread.
    assert {r["run_id"] for r in ledger.list_runs()} == {"run-main", "run-worker"}


def test_audit_append_from_other_thread(tmp_path):
    db = str(tmp_path / "ledger.db")
    ledger = RunLedger(db)
    ledger.create_run(_make_run("run-1"))
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))

    errors: list[str] = []

    def worker():
        try:
            audit.run_created("run-1", "tester")
            audit.list_events("run-1")
        except Exception as exc:  # pragma: no cover
            errors.append(f"{type(exc).__name__}: {exc}")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert errors == []
    assert len(audit.list_events("run-1")) == 1


def test_concurrent_writes_do_not_raise(tmp_path):
    """Several threads writing distinct runs to the same file ledger succeed."""
    db = str(tmp_path / "ledger.db")
    ledger = RunLedger(db)
    errors: list[str] = []

    def writer(i: int):
        try:
            ledger.create_run(_make_run(f"run-{i}"))
        except Exception as exc:  # pragma: no cover
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(ledger.list_runs()) == 8
