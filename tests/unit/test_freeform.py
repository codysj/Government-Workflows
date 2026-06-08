"""Unit tests for Guided Freeform Mode (Phase 5).

All tests use tmp_path / in-memory SQLite and the default mock provider, so they
do not depend on a shared on-disk DB or any network/API key.

Run only this file:
    .venv\\Scripts\\python.exe -m pytest tests/unit/test_freeform.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.workflows import freeform
from src.workflows.freeform import SensitivityNotConfirmedError


def _valid_inputs(**overrides):
    base = {
        "task_type": "grant_reimbursement_summary",
        "desired_output": "one-page reimbursement memo",
        "relevant_context": "Quarterly federal grant draw-down.",
        "uploaded_files": [],
        "sensitivity_confirmation": True,
        "human_review_confirmation": True,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def pipeline(tmp_path):
    """In-memory ledger + audit writing JSONL under tmp_path."""
    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, audit_dir=tmp_path / "audit", write_jsonl=True)
    yield ledger, audit
    ledger.close()


# --------------------------------------------------------------------------- #
# Refusal: missing sensitivity confirmation blocks the run.
# --------------------------------------------------------------------------- #
def test_missing_sensitivity_confirmation_blocks_run(tmp_path, pipeline):
    ledger, audit = pipeline
    log_path = tmp_path / "freeform_task_observations.md"
    inputs = _valid_inputs(sensitivity_confirmation=False)

    with pytest.raises(SensitivityNotConfirmedError):
        freeform.run(
            inputs,
            ledger=ledger,
            audit=audit,
            run_id="run-block",
            discovery_log_path=log_path,
            export_dir=tmp_path / "out",
        )

    # No discovery observation and no exports should have been produced.
    assert not log_path.exists()
    assert not (tmp_path / "out").exists()
    # A run_failed audit event was recorded for the refusal.
    events = audit.list_events("run-block")
    types = {e["event_type"] for e in events}
    assert "run_failed" in types


def test_default_sensitivity_is_refused(tmp_path):
    # The dataclass default must be unconfirmed (fail-closed).
    req = freeform.FreeformRequest(task_type="x")
    assert req.sensitivity_confirmation is False
    with pytest.raises(SensitivityNotConfirmedError):
        freeform.run_deterministic(req)


# --------------------------------------------------------------------------- #
# Happy path: valid structured request -> logged, validated mock response.
# --------------------------------------------------------------------------- #
def test_valid_request_produces_logged_validated_response(tmp_path, pipeline):
    ledger, audit = pipeline
    log_path = tmp_path / "freeform_task_observations.md"
    out_dir = tmp_path / "out"

    result = freeform.run(
        _valid_inputs(),
        ledger=ledger,
        audit=audit,
        run_id="run-ok",
        discovery_log_path=log_path,
        export_dir=out_dir,
    )

    # Mock LLM response present and validated against real source refs.
    assert result["response_json"]["summary"]
    validation = result["validation"]
    assert validation.passed is True
    assert validation.invented_reference_detected is False
    # The mock cites the real request source row.
    assert "freeform_request:0" in validation.checked_source_refs

    # Findings + LLM response + validation persisted to the ledger. Read them
    # back through public accessors (no reaching into the raw connection).
    assert len(ledger.list_findings("run-ok")) >= 1
    assert len(ledger.list_llm_responses("run-ok")) == 1
    assert len(ledger.list_validation_results("run-ok")) == 1

    # Export packet written.
    for name in freeform.EXPORT_FILE_NAMES:
        assert (out_dir / name).exists(), f"missing export {name}"


# --------------------------------------------------------------------------- #
# Discovery log: task_type is appended for future workflow discovery.
# --------------------------------------------------------------------------- #
def test_task_type_appended_to_discovery_log(tmp_path, pipeline):
    ledger, audit = pipeline
    log_path = tmp_path / "freeform_task_observations.md"

    freeform.run(
        _valid_inputs(task_type="vendor_w9_followup"),
        ledger=ledger,
        audit=audit,
        run_id="run-disc-1",
        discovery_log_path=log_path,
    )
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "# Guided Freeform — Task Observations" in text  # header created
    assert "vendor_w9_followup" in text
    assert "run-disc-1" in text

    # A second run appends (append-only) without duplicating the header.
    freeform.run(
        _valid_inputs(task_type="cash_receipt_anomaly_note"),
        ledger=ledger,
        audit=audit,
        run_id="run-disc-2",
        discovery_log_path=log_path,
    )
    text2 = log_path.read_text(encoding="utf-8")
    assert text2.count("# Guided Freeform — Task Observations") == 1
    assert "cash_receipt_anomaly_note" in text2
    assert "vendor_w9_followup" in text2  # earlier entry preserved


# --------------------------------------------------------------------------- #
# Audit events are written through the shared pipeline.
# --------------------------------------------------------------------------- #
def test_audit_events_written(tmp_path, pipeline):
    ledger, audit = pipeline
    log_path = tmp_path / "freeform_task_observations.md"

    freeform.run(
        _valid_inputs(),
        ledger=ledger,
        audit=audit,
        run_id="run-audit",
        discovery_log_path=log_path,
        export_dir=tmp_path / "out",
    )
    events = audit.list_events("run-audit")
    types = [e["event_type"] for e in events]
    for expected in (
        "run_created",
        "deterministic_analysis_completed",
        "llm_request_sent",
        "llm_response_received",
        "validation_completed",
        "export_generated",
        "run_completed",
    ):
        assert expected in types, f"missing audit event {expected}"


# --------------------------------------------------------------------------- #
# Attached file metadata is recorded as findings with source refs.
# --------------------------------------------------------------------------- #
def test_attached_file_metadata_recorded(tmp_path, pipeline):
    ledger, audit = pipeline
    log_path = tmp_path / "freeform_task_observations.md"
    sample = tmp_path / "ledger_export.csv"
    sample.write_text("a,b\n1,2\n", encoding="utf-8")

    result = freeform.run(
        _valid_inputs(uploaded_files=[sample]),
        ledger=ledger,
        audit=audit,
        run_id="run-files",
        discovery_log_path=log_path,
    )
    det = result["deterministic"]
    # request finding + one file finding.
    assert len(det.findings) == 2
    file_refs = [
        f"{s.table_name}:{s.row_index}"
        for f in det.findings
        for s in f.source_rows
    ]
    assert "freeform_files:0" in file_refs
    # File metadata captured deterministically (name + hash), no content parsing.
    file_meta = det.request.uploaded_files[0]
    assert file_meta["file_name"] == "ledger_export.csv"
    assert "sha256" in file_meta


# --------------------------------------------------------------------------- #
# Validation catches an invented source reference (guardrail not bypassed).
# --------------------------------------------------------------------------- #
def test_validation_flags_invented_reference(tmp_path):
    req = freeform.FreeformRequest(
        task_type="t", sensitivity_confirmation=True
    )
    det = freeform.run_deterministic(req)
    bad_response = {
        "summary": "draft",
        "referenced_source_rows": ["nonexistent:99"],
        "needs_human_review": True,
    }
    validation = freeform.validate_llm_output(bad_response, det)
    assert validation.passed is False
    assert validation.invented_reference_detected is True


# --------------------------------------------------------------------------- #
# Registry wiring: register() exposes run under the workflow type.
# --------------------------------------------------------------------------- #
def test_register_into_dict_and_module_registry():
    reg: dict = {}
    freeform.register(reg)
    assert reg["freeform"] is freeform.run
    assert freeform.WORKFLOW_REGISTRY["freeform"] is freeform.run
    assert freeform.WORKFLOW["workflow_type"] == "freeform"


# --------------------------------------------------------------------------- #
# Mock mode default: runs with no provider, no ledger, no audit, no internet.
# --------------------------------------------------------------------------- #
def test_runs_with_no_dependencies(tmp_path):
    log_path = tmp_path / "freeform_task_observations.md"
    result = freeform.run(
        _valid_inputs(),
        run_id="run-bare",
        discovery_log_path=log_path,
    )
    assert result["llm_response"].model_provider == "mock"
    assert result["validation"].passed is True
    assert log_path.exists()
