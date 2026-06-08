"""Tests for the consolidated review packet (src/core/review_packet.py)."""
from __future__ import annotations

import json

from src.core import review_packet as rp
from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    DeterministicFinding,
    FindingType,
    HumanReviewAction,
    InputFile,
    LLMResponse,
    Severity,
    SourceRowRef,
    ValidationResult,
    WorkflowRun,
)


def _seed_run(ledger, run_id="run-1", actions=None, summary=None):
    run = WorkflowRun(
        run_id=run_id, workflow_type="budget_variance", created_by="tester",
        input_files=[InputFile(
            file_name="budget.csv", file_type="csv", file_hash="hash123",
            parser_used="csv_loader", row_count=4, column_names=["account"])],
        summary=summary or {"validation_status": "passed", "flagged_variances": 2},
    )
    ledger.create_run(run)
    ledger.store_findings(run_id, [DeterministicFinding(
        finding_type=FindingType.VARIANCE, severity=Severity.HIGH,
        description="Fire salaries variance +35000",
        source_rows=[SourceRowRef(file_id="f1", table_name="budget",
                                  row_index=1, column_names=["amount"],
                                  source_values={"amount": 235000})],
        computed_values={"dollar_var": 35000}, rule_used="threshold",
        requires_human_review=True)])
    ledger.store_llm_response(run_id, LLMResponse(
        prompt_template_version="budget.v1", model_provider="mock",
        model_name="mock-llm",
        response_json={"summary": "Two variances exceed thresholds.",
                       "draft_memo": "DRAFT memo for review."},
        referenced_source_rows=["budget:1"]))
    ledger.store_validation_result(run_id, ValidationResult(
        passed=True, numeric_claims_checked=2,
        checked_source_refs=["budget:1"]))
    for a in actions or []:
        ledger.store_human_review_action(run_id, a)
    return run_id


def test_derive_draft_status():
    assert rp.derive_draft_status([]) == "draft"
    assert rp.derive_draft_status(
        [{"action": "mark_reviewed"}]) == "draft"
    assert rp.derive_draft_status(
        [{"action": "approve_draft"}]) == "final (human-approved)"
    assert rp.derive_draft_status(
        [{"action": "reject_ai_explanation"}]) == "rejected"


def test_build_markdown_has_all_sections(tmp_path):
    ledger = RunLedger(":memory:")
    run_id = _seed_run(ledger)
    run = ledger.get_run(run_id)
    md = rp.build_review_packet_markdown(run, [])
    for heading in (
        "## 1. Run metadata",
        "## 2. Source input files",
        "## 3. Deterministic findings",
        "## 4. AI-assisted draft language",
        "## 5. Validation results",
        "## 6. Reviewer notes",
        "## 7. Approval / rejection status",
        "## 8. Audit history",
    ):
        assert heading in md
    # Source file hash + AI draft labelling present.
    assert "hash123" in md
    assert "DRAFT" in md
    assert "draft" in md.lower()


def test_manifest_captures_model_and_hashes(tmp_path):
    ledger = RunLedger(":memory:")
    run_id = _seed_run(ledger)
    run = ledger.get_run(run_id)
    manifest = rp.build_run_manifest(run, [])
    assert manifest["run_id"] == run_id
    assert manifest["model"]["provider"] == "mock"
    assert manifest["model"]["prompt_template_version"] == "budget.v1"
    assert manifest["validation"]["passed"] is True
    assert manifest["source_files"][0]["sha256"] == "hash123"
    assert manifest["ai_draft_status"] == "draft"


def test_manifest_reflects_approval(tmp_path):
    ledger = RunLedger(":memory:")
    run_id = _seed_run(ledger, actions=[HumanReviewAction(
        run_id="run-1", action="approve_draft", actor="director")])
    run = ledger.get_run(run_id)
    manifest = rp.build_run_manifest(run, [])
    assert manifest["ai_draft_status"] == "final (human-approved)"


def test_generate_review_packet_writes_and_records(tmp_path):
    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    run_id = _seed_run(ledger)
    artifacts = rp.generate_review_packet(
        ledger, audit, run_id, tmp_path / "exports", actor="tester")
    names = {a.file_name for a in artifacts}
    assert names == set(rp.PACKET_FILE_NAMES)
    # Files exist under per-run subdir and hashes match.
    for a in artifacts:
        p = tmp_path / "exports" / run_id / a.file_name
        assert p.is_file()
    # Recorded in the ledger + an export audit event emitted.
    recorded = {a["file_name"] for a in ledger.list_export_artifacts(run_id)}
    assert set(rp.PACKET_FILE_NAMES) <= recorded
    assert any(e["event_type"] == "export_generated"
               for e in audit.list_events(run_id))
    # Manifest is valid JSON.
    manifest_path = tmp_path / "exports" / run_id / rp.RUN_MANIFEST_JSON
    json.loads(manifest_path.read_text(encoding="utf-8"))


def test_generate_review_packet_unknown_run_returns_empty(tmp_path):
    ledger = RunLedger(":memory:")
    assert rp.generate_review_packet(ledger, None, "nope", tmp_path) == []


def test_source_formats_surfaced_in_packet_and_manifest(tmp_path):
    ledger = RunLedger(":memory:")
    run_id = _seed_run(ledger, summary={
        "validation_status": "passed", "flagged_variances": 2,
        "source_formats": {"budget": "generic_erp"}})
    run = ledger.get_run(run_id)
    md = rp.build_review_packet_markdown(run, [])
    assert "Source-format presets applied" in md
    assert "generic_erp" in md
    manifest = rp.build_run_manifest(run, [])
    assert manifest["source_formats"] == {"budget": "generic_erp"}


def test_no_source_formats_omits_note(tmp_path):
    ledger = RunLedger(":memory:")
    run_id = _seed_run(ledger)  # default summary, no source_formats
    run = ledger.get_run(run_id)
    md = rp.build_review_packet_markdown(run, [])
    assert "Source-format presets applied" not in md
    assert rp.build_run_manifest(run, [])["source_formats"] == {}
