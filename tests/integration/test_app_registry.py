"""End-to-end tests for the Streamlit app's workflow registry pipeline.

Exercises ``app.workflow_registry.run_workflow`` for ALL four workflows (now
including bank reconciliation, which was previously disabled in the UI) on
synthetic data with the mock LLM. Asserts ledger records, audit events, the
consolidated review packet, per-run export isolation, and Settings-config
threading. No API key / no internet.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import workflow_registry as wfr
from src.core.audit_log import AuditLog
from src.core.review_packet import PACKET_FILE_NAMES
from src.core.run_ledger import RunLedger

SD = wfr.SYNTHETIC_DIR


@pytest.fixture(autouse=True)
def _isolate_freeform_discovery_log(tmp_path, monkeypatch):
    """Redirect the guided-freeform discovery log to a temp file so tests never
    append to the real docs/research/freeform_task_observations.md."""
    from src.workflows import freeform
    monkeypatch.setattr(
        freeform, "DISCOVERY_LOG_PATH",
        tmp_path / "freeform_task_observations.md")


def _pipeline(tmp_path):
    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    return ledger, audit


def _bank_inputs():
    return {
        "bank": str(SD / "bank_reconciliation" / "bank.csv"),
        "ledger": str(SD / "bank_reconciliation" / "ledger.csv"),
    }


def _budget_inputs():
    return {
        "budget": str(SD / "budget_variance" / "budget.csv"),
        "actuals": str(SD / "budget_variance" / "actuals.csv"),
    }


def _report_inputs():
    return {
        "report_table": str(SD / "report_review" / "report_table.csv"),
        "chart_of_accounts": str(SD / "report_review" / "chart_of_accounts.csv"),
        "prior_version": str(SD / "report_review" / "prior_version.csv"),
    }


def _freeform_inputs(sensitivity=True):
    return {
        "task_type": "reconcile petty cash",
        "desired_output": "a short summary",
        "relevant_context": "synthetic only",
        "sensitivity_confirmation": sensitivity,
        "human_review_confirmation": True,
        "uploaded_files": [],
    }


ALL_CASES = [
    ("bank_reconciliation", _bank_inputs),
    ("budget_variance", _budget_inputs),
    ("report_review", _report_inputs),
    ("freeform", _freeform_inputs),
]


def test_bank_reconciliation_is_available_in_ui():
    d = wfr.get_descriptor("bank_reconciliation")
    assert d.available is True
    assert d.example_files  # has bundled sample data


@pytest.mark.parametrize("wtype,make_inputs", ALL_CASES)
def test_workflow_runs_end_to_end(tmp_path, wtype, make_inputs):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        wtype, make_inputs(), ledger=ledger, audit=audit,
        export_dir=tmp_path / "exports", actor="tester",
    )
    assert not result.refused
    assert result.findings  # every workflow produces deterministic findings
    assert result.summary.get("validation_status") == "passed"

    run = ledger.get_run(result.run_id)
    assert run is not None
    # Ledger persisted findings + LLM response + validation.
    assert len(run["findings"]) >= 1
    assert len(run["llm_responses"]) == 1
    assert len(run["validation_results"]) == 1
    # Audit events written, including run lifecycle (exactly once each).
    events = [e["event_type"] for e in audit.list_events(result.run_id)]
    assert events.count("run_created") == 1
    assert events.count("run_completed") == 1
    assert "validation_completed" in events

    # Consolidated review packet generated + recorded.
    artifact_names = {a["file_name"] for a in run["export_artifacts"]}
    for name in PACKET_FILE_NAMES:
        assert name in artifact_names
    # Packet files exist on disk under a per-run subdirectory.
    run_dir = tmp_path / "exports" / result.run_id
    for name in PACKET_FILE_NAMES:
        assert (run_dir / name).is_file()


def test_runs_isolated_in_per_run_subdirectories(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    r1 = wfr.run_workflow("bank_reconciliation", _bank_inputs(),
                          ledger=ledger, audit=audit, export_dir=tmp_path / "ex")
    r2 = wfr.run_workflow("bank_reconciliation", _bank_inputs(),
                          ledger=ledger, audit=audit, export_dir=tmp_path / "ex")
    assert r1.run_id != r2.run_id
    assert (tmp_path / "ex" / r1.run_id).is_dir()
    assert (tmp_path / "ex" / r2.run_id).is_dir()
    # Each run's artifacts point at its own subdir (no overwrite).
    for r in (r1, r2):
        for path in r.export_paths.values():
            assert r.run_id in Path(path).parts


def test_freeform_refuses_without_sensitivity_confirmation(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "freeform", _freeform_inputs(sensitivity=False),
        ledger=ledger, audit=audit, export_dir=tmp_path / "exports")
    assert result.refused
    assert "sensitivity" in result.refusal_reason.lower() or result.refusal_reason
    run = ledger.get_run(result.run_id)
    assert run["status"] == "failed"
    events = [e["event_type"] for e in audit.list_events(result.run_id)]
    assert "run_failed" in events


def test_settings_config_threads_into_bank_tolerance(tmp_path):
    """date_tolerance_days from config changes deterministic matching.

    The synthetic bank/ledger have a utility payment one day apart (bank
    2026-01-08 vs ledger 2026-01-09). With a 3-day tolerance it is a timing
    difference; with 0 tolerance it becomes unmatched on both sides.
    """
    ledger, audit = _pipeline(tmp_path)
    loose = wfr.run_workflow(
        "bank_reconciliation", _bank_inputs(), ledger=ledger, audit=audit,
        export_dir=tmp_path / "loose",
        config={"amount_tolerance": 0.0, "date_tolerance_days": 3})
    tight = wfr.run_workflow(
        "bank_reconciliation", _bank_inputs(), ledger=ledger, audit=audit,
        export_dir=tmp_path / "tight",
        config={"amount_tolerance": 0.0, "date_tolerance_days": 0})
    assert loose.summary["timing_differences"] >= 1
    assert tight.summary["timing_differences"] == 0
    assert tight.summary["unmatched_bank"] > loose.summary["unmatched_bank"]


def _erp_bank_inputs():
    return {
        "bank": str(SD / "bank_reconciliation" / "erp_style_bank.csv"),
        "ledger": str(SD / "bank_reconciliation" / "ledger.csv"),
    }


def test_erp_file_requires_preset(tmp_path):
    """Without a source-format preset, an ERP-style file has no detectable
    date column and the run fails — proving the preset is doing real work."""
    ledger, audit = _pipeline(tmp_path)
    with pytest.raises(Exception):
        wfr.run_workflow("bank_reconciliation", _erp_bank_inputs(),
                         ledger=ledger, audit=audit, export_dir=tmp_path / "ex")


def test_source_format_preset_normalizes_erp_file(tmp_path):
    """With the preset, the ERP-style file reconciles; the run records the
    applied preset and keeps the ORIGINAL file's hash in the audit trail."""
    ledger, audit = _pipeline(tmp_path)
    result = wfr.run_workflow(
        "bank_reconciliation", _erp_bank_inputs(), ledger=ledger, audit=audit,
        export_dir=tmp_path / "ex", source_formats={"bank": "generic_erp"})
    assert not result.refused
    assert result.findings
    assert result.summary["validation_status"] == "passed"
    # Applied preset recorded in summary + audited.
    assert result.summary.get("source_formats") == {"bank": "generic_erp"}
    events = [e["event_type"] for e in audit.list_events(result.run_id)]
    assert "file_parsed" in events
    # Input-file metadata reflects the ORIGINAL upload (source of record).
    run = ledger.get_run(result.run_id)
    names = {f["file_name"] for f in run["input_files"]}
    assert "erp_style_bank.csv" in names
    # run_manifest captures the original source-file hash.
    manifest = next(a for a in run["export_artifacts"]
                    if a["file_name"] == "run_manifest.json")
    assert Path(manifest["path"]).is_file()


def test_erp_budget_normalizes_via_preset(tmp_path):
    """ERP-style budget/actuals reconcile to the SAME findings as the standard
    files once the preset is applied, and fail cleanly without it."""
    ledger, audit = _pipeline(tmp_path)
    std = wfr.run_workflow("budget_variance", _budget_inputs(),
                           ledger=ledger, audit=audit, export_dir=tmp_path / "std")
    erp_inputs = {
        "budget": str(SD / "budget_variance" / "erp_style_budget.csv"),
        "actuals": str(SD / "budget_variance" / "erp_style_actuals.csv"),
    }
    erp = wfr.run_workflow(
        "budget_variance", erp_inputs, ledger=ledger, audit=audit,
        export_dir=tmp_path / "erp",
        source_formats={"budget": "generic_erp", "actuals": "generic_erp"})
    assert erp.summary["flagged_variances"] == std.summary["flagged_variances"] == 2
    assert len(erp.findings) == len(std.findings)
    assert erp.summary.get("source_formats") == {
        "budget": "generic_erp", "actuals": "generic_erp"}
    # Without the preset the ERP headers have no join keys -> clean failure.
    with pytest.raises(Exception):
        wfr.run_workflow("budget_variance", erp_inputs, ledger=ledger,
                         audit=audit, export_dir=tmp_path / "nofmt")


def test_erp_report_normalizes_via_preset(tmp_path):
    """ERP-style report produces the SAME findings as the standard report once
    the preset is applied, and fails cleanly without it."""
    ledger, audit = _pipeline(tmp_path)
    std_inputs = {
        "report_table": str(SD / "report_review" / "report_table.csv"),
        "chart_of_accounts": str(SD / "report_review" / "chart_of_accounts.csv"),
        "prior_version": str(SD / "report_review" / "prior_version.csv"),
    }
    std = wfr.run_workflow("report_review", std_inputs, ledger=ledger,
                           audit=audit, export_dir=tmp_path / "std")
    erp_inputs = dict(std_inputs)
    erp_inputs["report_table"] = str(SD / "report_review" / "erp_style_report.csv")
    erp = wfr.run_workflow(
        "report_review", erp_inputs, ledger=ledger, audit=audit,
        export_dir=tmp_path / "erp",
        source_formats={"report_table": "generic_erp"})
    assert len(erp.findings) == len(std.findings)
    assert erp.summary.get("findings_by_rule") == std.summary.get("findings_by_rule")
    assert erp.summary.get("source_formats") == {"report_table": "generic_erp"}
    # Without the preset the report has no 'section' column -> clean failure.
    with pytest.raises(Exception):
        wfr.run_workflow("report_review", erp_inputs, ledger=ledger,
                         audit=audit, export_dir=tmp_path / "nofmt")


def test_source_format_choices_exposed():
    labels = [c[0] for c in wfr.SOURCE_FORMAT_CHOICES]
    presets = [c[1] for c in wfr.SOURCE_FORMAT_CHOICES]
    assert presets[0] is None  # standard / auto-detect default
    assert "generic_erp" in presets
    assert any("ERP" in lbl for lbl in labels)


def test_uploaded_threshold_file_takes_precedence_over_settings(tmp_path):
    """An uploaded thresholds path is not overridden by Settings config."""
    ledger, audit = _pipeline(tmp_path)
    inputs = _budget_inputs()
    inputs["thresholds"] = str(SD / "budget_variance" / "thresholds.json")
    result = wfr.run_workflow(
        "budget_variance", inputs, ledger=ledger, audit=audit,
        export_dir=tmp_path / "ex",
        config={"variance_dollar_threshold": 1.0, "variance_threshold_pct": 0.1})
    # thresholds.json uses 10000 / 10.0 -> exactly 2 flagged (not "everything").
    assert result.summary["flagged_variances"] == 2
