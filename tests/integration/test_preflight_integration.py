"""Integration tests for the PREFLIGHT / CAPABILITY layer wired into the runner.

Exercises ``app.workflow_registry.run_workflow`` through the shared
ledger/audit/validation/export pipeline for the three preflight outcomes:

* PASS    — clean inputs run normally; the LLM is called; the consolidated
            packet now also carries ``preflight_report.json`` +
            ``preflight_summary.md``; the preflight block is recorded in the
            ledger.
* FAIL    — a missing required column blocks the run: the workflow is NOT run,
            NO LLM response is stored, a FAILED-preflight packet (the two
            preflight files only) is generated, the audit shows the
            blocked/failed lifecycle, and validation passes (there is no LLM
            output to validate) but WOULD flag an injected LLM output.
* PARTIAL — a messy-but-runnable input set runs deterministically; the preflight
            unsupported-condition findings are appended to ``result.findings``;
            ``summary['partial']`` is True; the LLM is called; and the partial
            validation constraint rejects a resolution-claim about an
            unsupported condition.

All synthetic data, mock LLM, no API key / no internet.
"""
from __future__ import annotations

import pytest

from app import workflow_registry as wfr
from src.core.audit_log import AuditLog
from src.core.review_packet import (
    FAILED_PREFLIGHT_PACKET_FILE_NAMES,
    PACKET_FILE_NAMES,
    PREFLIGHT_REPORT_JSON,
    PREFLIGHT_SUMMARY_MD,
)
from src.core.run_ledger import RunLedger
from src.core.schemas import LLMResponse
from src.core.validation import validate_with_preflight

SD = wfr.SYNTHETIC_DIR
BANK = SD / "bank_reconciliation"


@pytest.fixture(autouse=True)
def _isolate_freeform_discovery_log(tmp_path, monkeypatch):
    """Keep the freeform discovery log out of the real docs file (mirrors the
    app-registry integration suite's autouse fixture)."""
    from src.workflows import freeform
    monkeypatch.setattr(
        freeform, "DISCOVERY_LOG_PATH",
        tmp_path / "freeform_task_observations.md")


def _pipeline(tmp_path):
    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    return ledger, audit


def _clean_bank_inputs():
    return {"bank": str(BANK / "bank.csv"), "ledger": str(BANK / "ledger.csv")}


def _fail_bank_inputs():
    # bank file is missing the required 'amount' column.
    return {
        "bank": str(BANK / "messy" / "fail_missing_amount_bank.csv"),
        "ledger": str(BANK / "ledger.csv"),
    }


def _partial_bank_inputs():
    # sign-convention mismatch -> non-blocking PARTIAL.
    return {
        "bank": str(BANK / "messy" / "partial_sign_bank.csv"),
        "ledger": str(BANK / "messy" / "partial_sign_ledger.csv"),
    }


# --------------------------------------------------------------------------- #
# PASS
# --------------------------------------------------------------------------- #
def test_clean_run_passes_preflight_and_records_it(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _clean_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex", actor="tester")

    assert not result.refused
    assert result.preflight_status == "pass"
    assert result.partial is False
    assert result.llm_called is True
    assert result.summary["preflight_status"] == "pass"
    assert result.summary["llm_called"] is True
    # Provenance recorded.
    assert "detected_columns" in result.summary
    assert "parse_confidence" in result.summary
    assert result.summary["supported_checks"]

    run = ledger.get_run(result.run_id)
    # Preflight block surfaced by the ledger.
    assert run["preflight"] is not None
    assert run["preflight"]["status"] == "pass"
    # The run still produced findings + an LLM response + validation.
    assert run["llm_responses"]
    assert run["validation_results"]

    # The consolidated packet AND the preflight artifacts exist on disk + ledger.
    run_dir = tmp_path / "ex" / result.run_id
    for name in PACKET_FILE_NAMES:
        assert (run_dir / name).is_file()
    assert (run_dir / PREFLIGHT_REPORT_JSON).is_file()
    assert (run_dir / PREFLIGHT_SUMMARY_MD).is_file()
    artifact_names = {a["file_name"] for a in run["export_artifacts"]}
    assert PREFLIGHT_REPORT_JSON in artifact_names
    assert PREFLIGHT_SUMMARY_MD in artifact_names


def test_get_preflight_accessor(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _clean_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex")
    pf = ledger.get_preflight(result.run_id)
    assert pf is not None
    assert pf["status"] == "pass"
    assert pf["workflow_type"] == "bank_reconciliation"


# --------------------------------------------------------------------------- #
# FAIL
# --------------------------------------------------------------------------- #
def test_failed_preflight_does_not_run_workflow_or_llm(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _fail_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex", actor="tester")

    # Blocked: refused, no findings, no LLM response.
    assert result.refused is True
    assert result.blocked is True
    assert result.preflight_status == "fail"
    assert result.findings == []
    assert result.llm_response is None
    assert result.llm_called is False
    assert result.preflight is not None
    # A blocking condition + concrete next steps.
    codes = result.preflight.unsupported_conditions
    assert "missing_required_column" in codes
    assert "needs_human_configuration" in codes
    assert result.preflight.next_steps

    run = ledger.get_run(result.run_id)
    assert run["status"] == "failed"
    # NO llm response stored on a failed-preflight run.
    assert run["llm_responses"] == []
    assert run["findings"] == []
    assert run["preflight"]["status"] == "fail"

    # FAILED-preflight packet: the two preflight files ONLY.
    run_dir = tmp_path / "ex" / result.run_id
    for name in FAILED_PREFLIGHT_PACKET_FILE_NAMES:
        assert (run_dir / name).is_file()
    # The full review packet is NOT generated for a blocked run.
    assert not (run_dir / "review_packet.md").exists()
    assert set(result.export_paths) == set(FAILED_PREFLIGHT_PACKET_FILE_NAMES)

    # Audit shows the blocked/failed lifecycle (no completion).
    events = [e["event_type"] for e in audit.list_events(result.run_id)]
    assert events.count("run_created") == 1
    assert "run_failed" in events
    assert "run_completed" not in events


def test_failed_run_validation_passes_but_flags_injected_llm(tmp_path):
    """A failed-preflight run has no LLM to validate (passes); but validation
    WOULD flag an LLM output if one were present on a fail."""
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _fail_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex")

    # No LLM output -> the FAIL constraint passes.
    ok = validate_with_preflight(
        None, None, preflight_status="fail",
        unsupported_condition_codes=result.preflight.unsupported_conditions)
    assert ok.passed is True

    # An injected LLM output on a FAILED run is rejected.
    injected = {"summary": "Everything reconciled cleanly."}
    bad = validate_with_preflight(
        injected, None, preflight_status="fail",
        unsupported_condition_codes=result.preflight.unsupported_conditions)
    assert bad.passed is False
    assert any("FAILED-preflight" in e for e in bad.errors)


# --------------------------------------------------------------------------- #
# PARTIAL
# --------------------------------------------------------------------------- #
def test_partial_run_appends_unsupported_findings_and_calls_llm(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _partial_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex", actor="tester")

    assert not result.refused
    assert result.preflight_status == "partial"
    assert result.partial is True
    assert result.summary["partial"] is True
    assert result.summary["preflight_status"] == "partial"
    assert result.llm_called is True
    assert result.summary["llm_called"] is True

    # The preflight unsupported-condition finding is now a deterministic finding.
    pf_findings = [f for f in result.findings
                   if f.rule_used.startswith("preflight:")]
    assert pf_findings, "expected preflight conditions merged into findings"
    assert all(f.requires_human_review for f in pf_findings)
    assert any("sign_convention" in f.rule_used for f in pf_findings)

    run = ledger.get_run(result.run_id)
    assert run["status"] == "completed"
    assert run["preflight"]["status"] == "partial"
    # The merged findings were re-persisted to the ledger.
    persisted_rules = {f["rule_used"] for f in run["findings"]}
    assert any(r.startswith("preflight:") for r in persisted_rules)
    # The LLM ran (deterministic findings may be explained, not the unsupported
    # logic).
    assert run["llm_responses"]


def test_partial_validation_rejects_resolution_claim(tmp_path):
    """On a PARTIAL run the LLM may explain an unsupported condition but must NOT
    claim it was resolved/handled."""
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _partial_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex")
    codes = result.preflight.unsupported_conditions

    # Explaining the condition is allowed.
    explain = {"summary": "There may be a sign convention difference to review.",
               "referenced_source_rows": []}
    ok = validate_with_preflight(
        explain, [], preflight_status="partial",
        unsupported_condition_codes=codes,
        require_references=False, missing_references_is_error=False)
    assert ok.passed is True

    # Claiming it was resolved/reconciled is rejected.
    claim = {"summary": "The sign convention mismatch was fully reconciled.",
             "referenced_source_rows": []}
    bad = validate_with_preflight(
        claim, [], preflight_status="partial",
        unsupported_condition_codes=codes,
        require_references=False, missing_references_is_error=False)
    assert bad.passed is False
    assert any("unsupported preflight condition" in e for e in bad.errors)


def test_partial_packet_includes_preflight_artifacts(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _partial_bank_inputs(),
        ledger=ledger, audit=audit, export_dir=tmp_path / "ex")
    run_dir = tmp_path / "ex" / result.run_id
    for name in (*PACKET_FILE_NAMES, PREFLIGHT_REPORT_JSON, PREFLIGHT_SUMMARY_MD):
        assert (run_dir / name).is_file()
    # The run manifest carries the top-level preflight block + partial status.
    run = ledger.get_run(result.run_id)
    from src.core.review_packet import build_run_manifest
    manifest = build_run_manifest(run, [])
    assert manifest["preflight"]["status"] == "partial"
    assert manifest["preflight"]["partial"] is True
    assert manifest["preflight"]["unsupported_conditions"]
