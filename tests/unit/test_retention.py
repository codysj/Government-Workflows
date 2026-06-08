"""Tests for the records-retention category (Tier 1 extension).

Covers the schema default, ledger persistence (create_run + read-back and the
set_retention helper), the run_workflow wiring (summary + audit), and surfacing
in the review packet manifest + markdown.
"""
from __future__ import annotations

from app import workflow_registry as wfr
from src.core.audit_log import AuditLog
from src.core.review_packet import build_review_packet_markdown, build_run_manifest
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    InputFile,
    RetentionCategory,
    WorkflowRun,
)

SD = wfr.SYNTHETIC_DIR


def _make_run(run_id="run-r", retention=None) -> WorkflowRun:
    kwargs = dict(
        run_id=run_id,
        workflow_type="bank_reconciliation",
        created_by="tester",
        input_files=[InputFile(
            file_name="bank.csv", file_type="csv", file_hash="abc",
            parser_used="csv_loader", row_count=3, column_names=["date"])],
        summary={"matched": 2},
    )
    if retention is not None:
        kwargs["retention_category"] = retention
    return WorkflowRun(**kwargs)


def _bank_inputs():
    return {
        "bank": str(SD / "bank_reconciliation" / "bank.csv"),
        "ledger": str(SD / "bank_reconciliation" / "ledger.csv"),
    }


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_workflow_run_default_is_draft_working():
    run = WorkflowRun(workflow_type="x", created_by="t")
    assert run.retention_category == RetentionCategory.DRAFT_WORKING
    assert RetentionCategory.DRAFT_WORKING.value == "draft_working"


def test_retention_category_members():
    assert {c.value for c in RetentionCategory} == {
        "draft_working", "transitory", "administrative_record",
        "audit_record", "permanent",
    }


# --------------------------------------------------------------------------- #
# Ledger persistence
# --------------------------------------------------------------------------- #
def test_ledger_defaults_to_draft_working_when_unset(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())  # default category
    assert ledger.get_run("run-r")["retention_category"] == "draft_working"
    assert ledger.list_runs()[0]["retention_category"] == "draft_working"


def test_ledger_stores_and_returns_non_default(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run(retention=RetentionCategory.AUDIT_RECORD))
    assert ledger.get_run("run-r")["retention_category"] == "audit_record"
    assert ledger.list_runs()[0]["retention_category"] == "audit_record"


def test_set_retention_updates_value(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    ledger.set_retention("run-r", "permanent")
    assert ledger.get_run("run-r")["retention_category"] == "permanent"


def test_update_run_status_can_set_retention(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    ledger.create_run(_make_run())
    ledger.update_run_status("run-r", "completed",
                             retention_category="transitory")
    assert ledger.get_run("run-r")["retention_category"] == "transitory"


def test_existing_db_without_column_is_migrated(tmp_path):
    """An older DB lacking the column is migrated on the next open and reads
    back the default for legacy rows."""
    import sqlite3

    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, workflow_type TEXT, "
        "created_at TEXT, created_by TEXT, status TEXT, "
        "human_review_status TEXT, summary TEXT);")
    conn.execute(
        "INSERT INTO runs (run_id, workflow_type, summary) VALUES (?,?,?)",
        ("legacy-1", "bank_reconciliation", "{}"))
    conn.commit()
    conn.close()

    ledger = RunLedger(db)  # _create_tables runs the ALTER migration
    run = ledger.get_run("legacy-1")
    assert run["retention_category"] == "draft_working"


# --------------------------------------------------------------------------- #
# run_workflow wiring
# --------------------------------------------------------------------------- #
def test_run_workflow_records_retention(tmp_path):
    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    result = wfr.run_workflow(
        "bank_reconciliation", _bank_inputs(), ledger=ledger, audit=audit,
        export_dir=tmp_path / "exports", retention_category="audit_record")
    assert not result.refused
    assert result.summary["retention_category"] == "audit_record"

    run = ledger.get_run(result.run_id)
    assert run["retention_category"] == "audit_record"
    assert run["summary"]["retention_category"] == "audit_record"
    # Noted on the run_created audit event.
    created = next(e for e in audit.list_events(result.run_id)
                   if e["event_type"] == "run_created")
    assert created["details"]["retention_category"] == "audit_record"


def test_run_workflow_defaults_retention(tmp_path):
    ledger = RunLedger(":memory:")
    result = wfr.run_workflow(
        "bank_reconciliation", _bank_inputs(), ledger=ledger,
        export_dir=tmp_path / "exports")
    assert result.summary["retention_category"] == "draft_working"


def test_run_workflow_unknown_retention_falls_back(tmp_path):
    ledger = RunLedger(":memory:")
    result = wfr.run_workflow(
        "bank_reconciliation", _bank_inputs(), ledger=ledger,
        export_dir=tmp_path / "exports", retention_category="not_a_category")
    assert result.summary["retention_category"] == "draft_working"


# --------------------------------------------------------------------------- #
# Review packet surfacing
# --------------------------------------------------------------------------- #
def test_review_packet_includes_retention(tmp_path):
    ledger = RunLedger(":memory:")
    ledger.create_run(_make_run(retention=RetentionCategory.PERMANENT))
    run = ledger.get_run("run-r")
    md = build_review_packet_markdown(run, [])
    assert "- Retention category: permanent" in md
    manifest = build_run_manifest(run, [])
    assert manifest["retention_category"] == "permanent"


def test_review_packet_reads_retention_from_summary(tmp_path):
    """When the top-level field is absent, fall back to summary."""
    run = {"workflow_type": "x", "run_id": "r",
           "summary": {"retention_category": "transitory"}}
    md = build_review_packet_markdown(run, [])
    assert "- Retention category: transitory" in md
    assert build_run_manifest(run, [])["retention_category"] == "transitory"
