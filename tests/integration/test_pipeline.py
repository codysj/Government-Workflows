"""End-to-end tests for the shared workflow pipeline (src.core.pipeline).

Exercises ``src.core.pipeline.run_workflow`` for ALL four workflows (now
including bank reconciliation, which was previously disabled in the UI) on
synthetic data with the mock LLM. Asserts ledger records, audit events, the
consolidated review packet, per-run export isolation, and Settings-config
threading. No API key / no internet.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core import pipeline
from src.workflows import registry
from src.core.audit_log import AuditLog
from src.core.review_packet import PACKET_FILE_NAMES
from src.core.run_ledger import RunLedger

SD = registry.SYNTHETIC_DIR


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


def _transaction_search_inputs():
    return {
        "query": registry.get_descriptor("transaction_search").sample_text["query"],
        "gl_detail": str(SD / "tyler" / "gl_detail.csv"),
        "ap_invoices": str(SD / "tyler" / "ap_invoice_detail.csv"),
        "checks": str(SD / "tyler" / "check_register.csv"),
        "purchase_orders": str(SD / "tyler" / "purchase_orders.csv"),
    }


def _ap_duplicate_inputs():
    return {
        "ap_invoices": str(SD / "tyler" / "ap_invoice_detail.csv"),
        "vendor_list": str(SD / "tyler" / "vendor_list.csv"),
        "check_register": str(SD / "tyler" / "check_register.csv"),
        "purchase_orders": str(SD / "tyler" / "purchase_orders.csv"),
    }


def _je_upload_inputs():
    return {
        "je_draft": str(SD / "je_upload_prep" / "je_draft_valid.csv"),
        "chart_of_accounts": str(SD / "tyler" / "chart_of_accounts.csv"),
        "gl_detail": str(SD / "tyler" / "gl_detail.csv"),
        "config": str(SD / "je_upload_prep" / "je_config.json"),
    }


def _po_invoice_inputs():
    return {
        "purchase_orders": str(SD / "tyler" / "purchase_orders.csv"),
        "ap_invoices": str(SD / "tyler" / "ap_invoice_detail.csv"),
        "vendor_list": str(SD / "tyler" / "vendor_list.csv"),
        "check_register": str(SD / "tyler" / "check_register.csv"),
    }


ALL_CASES = [
    ("bank_reconciliation", _bank_inputs),
    ("budget_variance", _budget_inputs),
    ("report_review", _report_inputs),
    ("transaction_search", _transaction_search_inputs),
    ("ap_duplicate_review", _ap_duplicate_inputs),
    ("je_upload_prep", _je_upload_inputs),
    ("po_invoice_review", _po_invoice_inputs),
    ("freeform", _freeform_inputs),
]


def test_bank_reconciliation_has_sample_data():
    d = registry.get_descriptor("bank_reconciliation")
    assert d.example_files  # has bundled sample data


@pytest.mark.parametrize("wtype,make_inputs", ALL_CASES)
def test_workflow_runs_end_to_end(tmp_path, wtype, make_inputs):
    ledger, audit = _pipeline(tmp_path)
    result = pipeline.run_workflow(
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


def test_all_eight_descriptors_resolve_in_ui_order():
    """Every workflow (4 original + 4 Tyler-era) resolves to an available
    descriptor with uploads + module registration for preflight."""
    expected = {
        "bank_reconciliation", "budget_variance", "report_review",
        "transaction_search", "ap_duplicate_review", "je_upload_prep",
        "po_invoice_review", "freeform",
    }
    assert set(registry.DESCRIPTORS) == expected
    for wtype in registry.DESCRIPTORS:
        d = registry.get_descriptor(wtype)
        assert d.title
        if wtype != "freeform":
            assert d.uploads
            # Example files exist on disk so the 'use example files' path works.
            for rel in (d.example_files or {}).values():
                assert (registry.SYNTHETIC_DIR / rel).is_file(), rel


def test_tyler_descriptor_example_run_matches_known_answers(tmp_path):
    """The Riverbend sample data yields the planted known-answer counts."""
    ledger, audit = _pipeline(tmp_path)
    ap = pipeline.run_workflow("ap_duplicate_review", _ap_duplicate_inputs(),
                          ledger=ledger, audit=audit,
                          export_dir=tmp_path / "ap")
    assert ap.summary["total_findings"] == 13
    po = pipeline.run_workflow("po_invoice_review", _po_invoice_inputs(),
                          ledger=ledger, audit=audit,
                          export_dir=tmp_path / "po")
    assert po.summary["total_findings"] == 9
    ts = pipeline.run_workflow("transaction_search", _transaction_search_inputs(),
                          ledger=ledger, audit=audit,
                          export_dir=tmp_path / "ts")
    assert ts.summary["total_matches"] == 2


def test_transaction_search_blocks_without_query(tmp_path):
    """No query -> preflight FAIL: blocked, no findings, no LLM call."""
    ledger, audit = _pipeline(tmp_path)
    inputs = _transaction_search_inputs()
    inputs.pop("query")
    result = pipeline.run_workflow(
        "transaction_search", inputs, ledger=ledger, audit=audit,
        export_dir=tmp_path / "exports")
    assert result.blocked
    assert result.preflight_status == "fail"
    assert result.llm_called is False
    assert result.findings == []
    events = [e["event_type"] for e in audit.list_events(result.run_id)]
    assert "run_failed" in events


def test_je_upload_prep_invalid_draft_fails_closed_in_app(tmp_path):
    """The invalid JE draft completes as a run but writes NO upload workbook."""
    ledger, audit = _pipeline(tmp_path)
    inputs = _je_upload_inputs()
    inputs["je_draft"] = str(SD / "je_upload_prep" / "je_draft_invalid.csv")
    result = pipeline.run_workflow(
        "je_upload_prep", inputs, ledger=ledger, audit=audit,
        export_dir=tmp_path / "exports")
    assert not result.blocked
    assert result.summary.get("upload_ready") is False
    assert result.summary.get("blocking_findings", 0) >= 1
    # FAIL CLOSED: no upload workbook anywhere under the export dir.
    assert not list((tmp_path / "exports").rglob("je_upload.xlsx"))
    assert not list((tmp_path / "exports").rglob("je_upload.csv"))
    # The structured error report exists in the per-run dir.
    run_dir = tmp_path / "exports" / result.run_id
    assert (run_dir / "je_validation_errors.csv").is_file()


def test_runs_isolated_in_per_run_subdirectories(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    r1 = pipeline.run_workflow("bank_reconciliation", _bank_inputs(),
                          ledger=ledger, audit=audit, export_dir=tmp_path / "ex")
    r2 = pipeline.run_workflow("bank_reconciliation", _bank_inputs(),
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
    result = pipeline.run_workflow(
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
    loose = pipeline.run_workflow(
        "bank_reconciliation", _bank_inputs(), ledger=ledger, audit=audit,
        export_dir=tmp_path / "loose",
        config={"amount_tolerance": 0.0, "date_tolerance_days": 3})
    tight = pipeline.run_workflow(
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


def test_erp_headers_reconcile_like_the_clean_file(tmp_path):
    """An ERP export with different headers ("Posting Date", "Transaction
    Amount") reconciles to the same result as the clean file: preflight
    resolves the columns and the workflow uses exactly what preflight
    resolved (previously preflight said PASS and the workflow crashed)."""
    ledger, audit = _pipeline(tmp_path)
    erp_inputs = _erp_bank_inputs()
    renamed = tmp_path / "bank_clean_headers.csv"
    lines = Path(erp_inputs["bank"]).read_text(encoding="utf-8").splitlines()
    renamed.write_text("\n".join(["date,description,amount"] + lines[1:]) + "\n",
                       encoding="utf-8")
    clean = pipeline.run_workflow(
        "bank_reconciliation", {**erp_inputs, "bank": str(renamed)},
        ledger=ledger, audit=audit, export_dir=tmp_path / "a")
    erp = pipeline.run_workflow("bank_reconciliation", _erp_bank_inputs(),
                                ledger=ledger, audit=audit, export_dir=tmp_path / "b")
    assert not erp.blocked
    assert erp.summary["column_mappings_used"]["bank"]["date"] == "posting_date"
    for key in ("matched", "unmatched_bank", "unmatched_ledger"):
        assert erp.summary[key] == clean.summary[key], key
    assert len(erp.findings) == len(clean.findings)


def test_uploaded_threshold_file_takes_precedence_over_settings(tmp_path):
    """An uploaded thresholds path is not overridden by Settings config."""
    ledger, audit = _pipeline(tmp_path)
    inputs = _budget_inputs()
    inputs["thresholds"] = str(SD / "budget_variance" / "thresholds.json")
    result = pipeline.run_workflow(
        "budget_variance", inputs, ledger=ledger, audit=audit,
        export_dir=tmp_path / "ex",
        config={"variance_dollar_threshold": 1.0, "variance_threshold_pct": 0.1})
    # thresholds.json uses 10000 / 10.0 -> exactly 2 flagged (not "everything").
    assert result.summary["flagged_variances"] == 2


def test_excel_inputs_get_provenance_hashes(tmp_path):
    ledger, audit = _pipeline(tmp_path)
    result = pipeline.run_workflow(
        "transaction_search",
        {"query": "payments over 5000", "gl_detail": str(SD / "tyler" / "gl_detail.xlsx")},
        ledger=ledger, audit=audit)
    files = ledger.list_input_files(result.run_id)
    assert [f["file_name"] for f in files] == ["gl_detail.xlsx"]
    assert len(files[0]["file_hash"]) == 64


def test_tyler_combined_budget_to_actual_through_the_pipeline(tmp_path):
    """One Munis budget-to-actual export used for both sides: the workflow's own
    column logic (Revised Budget vs YTD Actual) must win over preflight's
    uncertain guesses (review finding: forwarding them gave 0 findings)."""
    ledger, audit = _pipeline(tmp_path)
    b2a = str(SD / "budget_variance" / "tyler_style_budget_to_actual.csv")
    r = pipeline.run_workflow("budget_variance", {"budget": b2a, "actuals": b2a},
                              ledger=ledger, audit=audit)
    assert r.preflight_status == "pass"
    assert r.summary["budget_amount_column"] == "revised_budget"
    assert r.summary["actual_amount_column"] == "ytd_actual"
    assert r.summary["flagged_variances"] == 4
