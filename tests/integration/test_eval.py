"""Integration tests for the Phase-7 evaluation harness (src/eval).

Runs the harness end-to-end across all three MVP workflows on their bundled
synthetic known-answer datasets (mock LLM, in-memory ledger), asserts the
spec's metrics are produced, and asserts the known-answer checks pass. All
on-disk output (the report + export packets) goes under tmp_path so the test
leaves no shared state.
"""
from __future__ import annotations

import json

import pytest

from src.eval import harness, metrics
from src.eval.harness import DEFAULT_WORKFLOWS, run_eval, run_workflow_eval
from src.eval.metrics import WorkflowMetrics

# The Phase-7 "Evaluation metrics" the spec requires the harness to track.
SPEC_METRIC_FIELDS = (
    "transactions_processed",
    "rows_matched",
    "rows_unmatched",
    "findings_generated",
    "validation_warnings",
    "llm_outputs_rejected",
    "manual_overrides",
    "export_packets_generated",
    "runtime_seconds",
)


def test_default_workflows_are_the_seven_known_answer_workflows():
    assert set(DEFAULT_WORKFLOWS) == {
        "bank_reconciliation",
        "budget_variance",
        "report_review",
        "transaction_search",
        "ap_duplicate_review",
        "je_upload_prep",
        "po_invoice_review",
    }


def test_run_eval_end_to_end_all_pass(tmp_path):
    report = run_eval(out_dir=tmp_path, export=True, write=True)

    # A report row exists for each known-answer workflow and all of them ran.
    assert set(report["workflows"]) == set(DEFAULT_WORKFLOWS)
    totals = report["totals"]
    assert totals["workflows_evaluated"] == 7
    assert totals["workflows_ran"] == 7
    assert totals["workflows_known_answer_passed"] == 7
    assert totals["all_passed"] is True

    # The report file was written and is valid JSON matching the returned dict.
    report_file = tmp_path / "eval_report.json"
    assert report_file.is_file()
    on_disk = json.loads(report_file.read_text(encoding="utf-8"))
    assert on_disk["totals"]["all_passed"] is True
    assert set(on_disk["workflows"]) == set(DEFAULT_WORKFLOWS)


def test_every_workflow_produces_all_spec_metrics(tmp_path):
    report = run_eval(out_dir=tmp_path, export=True, write=False)
    for wf_type, wf in report["workflows"].items():
        assert wf["ran"] is True, f"{wf_type} did not run: {wf.get('error')}"
        m = wf["metrics"]
        assert m is not None
        for field_name in SPEC_METRIC_FIELDS:
            assert field_name in m, f"{wf_type} missing metric {field_name}"
        # Automated run: no human in the loop.
        assert m["manual_overrides"] == 0
        # Mock-default LLM outputs validate, so none are rejected.
        assert m["llm_outputs_rejected"] == 0
        assert m["runtime_seconds"] >= 0.0


def test_known_answer_bank_reconciliation(tmp_path):
    res = run_workflow_eval("bank_reconciliation", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    m = res.metrics
    # Known matched / unmatched / timing items in the synthetic bank dataset.
    assert m.rows_matched == 4
    assert m.findings_by_type["matched"] == 4
    assert m.findings_by_type["timing_difference"] == 1
    assert m.findings_by_type["unmatched_bank"] == 2
    assert m.findings_by_type["unmatched_ledger"] == 1
    assert m.transactions_processed == 13  # 7 bank + 6 ledger rows
    assert m.export_packets_generated >= 1


def test_known_answer_budget_variance(tmp_path):
    res = run_workflow_eval("budget_variance", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    s = res.metrics.summary
    # Known large dollar + large pct variance, budget-only, actual-only, missing.
    assert s["flagged_variances"] == 2
    assert s["joined_lines"] == 4
    assert s["budget_only"] == 1
    assert s["actual_only"] == 1
    assert s["missing_accounts"] == 1
    assert res.metrics.findings_generated == 5


def test_known_answer_report_review(tmp_path):
    res = run_workflow_eval("report_review", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    s = res.metrics.summary
    # Known subtotal mismatch, invalid account code, duplicate line, missing
    # section, inconsistent naming, large change from prior.
    assert s["total_findings"] == 7
    by_rule = s["findings_by_rule"]
    assert by_rule["subtotal_equals_line_item_sum"] == 1
    assert by_rule["account_code_in_chart_of_accounts"] == 1
    assert by_rule["no_duplicate_account_lines"] == 1
    assert by_rule["required_section_present"] == 1
    assert by_rule["consistent_account_naming"] == 1
    assert by_rule["large_change_from_prior_version"] == 2


def test_known_answer_transaction_search(tmp_path):
    res = run_workflow_eval("transaction_search", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    s = res.metrics.summary
    # Known-answer Q1: 2 Cascade Paving AP invoices over $5,000, Mar-May 2026.
    assert s["total_matches"] == 2
    assert s["total_signed_amount"] == "16800.00"
    assert res.metrics.findings_by_type["search_match"] == 2
    assert res.metrics.transactions_processed == 66  # AP rows scanned


def test_known_answer_ap_duplicate_review(tmp_path):
    res = run_workflow_eval("ap_duplicate_review", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    s = res.metrics.summary
    # Planted D1-D8 anomalies in the Riverbend AP export.
    assert s["total_findings"] == 15
    by_rule = s["findings_by_rule"]
    assert by_rule["duplicate_invoice_number"] == 1
    assert by_rule["inactive_vendor_payment"] == 1
    assert by_rule["unknown_vendor_payment"] == 1
    assert by_rule["split_payment_pattern"] == 3
    assert res.metrics.findings_by_type["duplicate_payment"] == 11


def test_known_answer_je_upload_prep(tmp_path):
    res = run_workflow_eval("je_upload_prep", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    s = res.metrics.summary
    # The VALID draft is upload-ready (balanced, valid accounts, in period).
    assert s["upload_ready"] is True
    assert s["blocking_findings"] == 0
    assert s["total_debits"] == s["total_credits"] == "17500.00"
    # The upload workbook was actually produced on the export path.
    assert res.metrics.export_packets_generated >= 1


def test_known_answer_po_invoice_review(tmp_path):
    res = run_workflow_eval("po_invoice_review", export_dir=tmp_path)
    assert res.ran and res.known_answer.passed
    s = res.metrics.summary
    # Planted P1-P8 anomalies (missing PO split into P3a + P3b).
    assert s["total_findings"] == 9
    by_rule = s["findings_by_rule"]
    for rule in ("invoice_exceeds_po", "wrong_vendor", "missing_po",
                 "missing_po_over_threshold", "closed_po_usage",
                 "unit_price_mismatch", "quantity_mismatch",
                 "received_not_invoiced", "invoiced_not_received"):
        assert by_rule[rule] == 1, rule
    assert res.metrics.transactions_processed == 84  # 18 PO + 66 AP rows


def test_known_answer_checks_have_field_level_detail(tmp_path):
    res = run_workflow_eval("budget_variance", export_dir=tmp_path)
    ka = res.known_answer
    assert ka.passed
    assert ka.checks, "expected per-field known-answer checks"
    for c in ka.checks:
        assert {"check", "expected", "actual", "passed"} <= set(c)
        assert c["passed"] is True


def test_validation_passes_with_mock_provider(tmp_path):
    # Mock-default path: every workflow validates with no invented references.
    report = run_eval(out_dir=tmp_path, export=False, write=False)
    for wf_type, wf in report["workflows"].items():
        m = wf["metrics"]
        assert m["validation_passed"] is True, wf_type
        assert m["invented_reference_detected"] is False, wf_type


def test_exports_can_be_skipped(tmp_path):
    report = run_eval(out_dir=tmp_path, export=False, write=False)
    for wf in report["workflows"].values():
        assert wf["metrics"]["export_packets_generated"] == 0


def test_compute_metrics_is_deterministic_unit():
    # Two runs of the same workflow yield identical metric counts (reproducible).
    r1 = run_workflow_eval("budget_variance")
    r2 = run_workflow_eval("budget_variance")
    d1 = r1.metrics.to_dict()
    d2 = r2.metrics.to_dict()
    # Drop the wall-clock runtime, which legitimately varies.
    d1.pop("runtime_seconds")
    d2.pop("runtime_seconds")
    assert d1 == d2


def test_unknown_workflow_returns_error_not_raise():
    res = run_workflow_eval("does_not_exist")
    assert res.ran is False
    assert res.metrics is None
    assert "unknown workflow" in (res.error or "")


def test_metrics_to_dict_shape():
    m = WorkflowMetrics(workflow_type="x", findings_generated=3)
    d = m.to_dict()
    for field_name in SPEC_METRIC_FIELDS:
        assert field_name in d
    assert d["workflow_type"] == "x"
    assert d["findings_generated"] == 3
