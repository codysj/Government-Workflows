"""Known-answer tests for Workflow 3 — Financial Report Consistency Review.

Asserts each engineered issue in data/synthetic/report_review/ is detected with
the correct rule + severity + source rows, that source-row references are
preserved, the mock LLM shape is correct + source-cited, validation behaves,
and export_artifacts writes all five files. Uses tmp_path; no shared on-disk DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.schemas import Severity
from src.workflows import report_review as rr

DATA = Path("data/synthetic/report_review")
REPORT = DATA / "report_table.csv"
COA = DATA / "chart_of_accounts.csv"
PRIOR = DATA / "prior_version.csv"
CONFIG = DATA / "checklist_config.json"


@pytest.fixture
def det():
    cfg = rr.ReportReviewConfig.from_json(CONFIG)
    return rr.run_deterministic(
        REPORT,
        chart_of_accounts_path=COA,
        prior_version_path=PRIOR,
        config=cfg,
    )


def _by_rule(det, rule):
    return [f for f in det.findings if f.rule_used == rule]


# --------------------------------------------------------------------------- #
# Known-answer detections
# --------------------------------------------------------------------------- #
def test_subtotal_mismatch_detected(det):
    hits = _by_rule(det, "subtotal_equals_line_item_sum")
    assert len(hits) == 1
    f = hits[0]
    assert f.severity == Severity.HIGH
    # Expenditures: stated 700000 vs computed 810000.
    assert f.computed_values["section"] == "Expenditures"
    assert f.computed_values["stated_subtotal"] == "700000"
    assert f.computed_values["computed_line_item_sum"] == "810000"
    assert f.source_rows  # cites the line items + subtotal row


def test_revenues_subtotal_not_flagged(det):
    # Control: Revenues subtotal ties out, so only one subtotal mismatch total.
    sections = {
        f.computed_values["section"]
        for f in _by_rule(det, "subtotal_equals_line_item_sum")
    }
    assert "Revenues" not in sections


def test_invalid_account_code_detected(det):
    hits = _by_rule(det, "account_code_in_chart_of_accounts")
    codes = {f.computed_values["account_code"] for f in hits}
    assert codes == {"5099"}
    assert hits[0].severity == Severity.HIGH
    assert len(hits[0].source_rows) == 1


def test_duplicate_line_detected(det):
    hits = _by_rule(det, "no_duplicate_account_lines")
    assert len(hits) == 1
    f = hits[0]
    assert f.computed_values["account_code"] == "5030"
    assert f.severity == Severity.MEDIUM
    # cites the first occurrence + the duplicate.
    assert len(f.source_rows) == 2


def test_missing_section_detected(det):
    hits = _by_rule(det, "required_section_present")
    missing = {f.computed_values["missing_section"] for f in hits}
    assert missing == {"Fund Balance"}
    assert hits[0].severity == Severity.HIGH


def test_inconsistent_naming_detected(det):
    hits = _by_rule(det, "consistent_account_naming")
    assert len(hits) == 1
    f = hits[0]
    assert f.computed_values["account_code"] == "5040"
    assert set(f.computed_values["names"]) == {"Professional Services", "Prof. Services"}
    assert f.severity == Severity.MEDIUM
    assert len(f.source_rows) == 2


def test_large_change_from_prior_detected(det):
    hits = _by_rule(det, "large_change_from_prior_version")
    codes = {f.computed_values["account_code"] for f in hits}
    # Salaries 250000 -> 400000 (+60%) is the only large change.
    assert "5010" in codes
    sal = [f for f in hits if f.computed_values["account_code"] == "5010"][0]
    assert sal.computed_values["prior_amount"] == "250000"
    assert sal.computed_values["current_amount"] == "400000"


def test_all_known_issues_present(det):
    rules = {f.rule_used for f in det.findings}
    assert {
        "subtotal_equals_line_item_sum",
        "account_code_in_chart_of_accounts",
        "no_duplicate_account_lines",
        "required_section_present",
        "consistent_account_naming",
        "large_change_from_prior_version",
    } <= rules
    assert det.summary["total_findings"] == len(det.findings)
    assert det.summary["requires_human_review"] is True


# --------------------------------------------------------------------------- #
# Source-row preservation
# --------------------------------------------------------------------------- #
def test_source_rows_preserved(det):
    for f in det.findings:
        for ref in f.source_rows:
            assert ref.table_name in {"report", "prior"}
            assert isinstance(ref.row_index, int)
            assert ref.source_values  # original values carried through
    # The invalid-code finding points at the actual 5099 row.
    inv = _by_rule(det, "account_code_in_chart_of_accounts")[0]
    ref = inv.source_rows[0]
    assert ref.source_values.get("account_code") == "5099"


# --------------------------------------------------------------------------- #
# Mock LLM shape + source citation
# --------------------------------------------------------------------------- #
def test_mock_llm_shape_and_citations(det):
    resp = rr.mock_llm_response(det)
    for key in (
        "summary",
        "categorized_exceptions",
        "referenced_source_rows",
        "suggested_review_steps",
        "draft_memo",
    ):
        assert key in resp
    valid = {f"{s.table_name}:{s.row_index}" for f in det.findings for s in f.source_rows}
    for r in resp["referenced_source_rows"]:
        assert r in valid  # never invents a reference
    assert resp["categorized_exceptions"]


def test_validation_passes_for_mock(det):
    resp = rr.mock_llm_response(det)
    result = rr.validate_llm_output(resp, det)
    assert result.passed is True
    assert result.invented_reference_detected is False
    assert result.errors == []


def test_validation_flags_invented_reference(det):
    resp = rr.mock_llm_response(det)
    resp["referenced_source_rows"].append("report:9999")
    result = rr.validate_llm_output(resp, det)
    assert result.passed is False
    assert result.invented_reference_detected is True


def test_validation_flags_approval_language(det):
    resp = rr.mock_llm_response(det)
    resp["draft_memo"] = "This report is correct and approved for filing."
    result = rr.validate_llm_output(resp, det)
    assert result.passed is False
    assert any("approval" in e.lower() for e in result.errors)


# --------------------------------------------------------------------------- #
# Export artifacts
# --------------------------------------------------------------------------- #
def test_export_artifacts(tmp_path, det):
    resp = rr.mock_llm_response(det)
    validation = rr.validate_llm_output(resp, det)
    paths = rr.export_artifacts(tmp_path, det, resp, validation, audit_events=[])
    for name in rr.EXPORT_FILE_NAMES:
        assert name in paths
        assert paths[name].exists()
    # validation_report.json is valid JSON.
    vr = json.loads(paths["validation_report.json"].read_text(encoding="utf-8"))
    assert "passed" in vr
    # flagged_issues.csv has one row per finding.
    import csv

    with paths["flagged_issues.csv"].open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(det.findings)


# --------------------------------------------------------------------------- #
# End-to-end run (mock provider, no ledger/audit)
# --------------------------------------------------------------------------- #
def test_run_end_to_end(tmp_path):
    result = rr.run(
        {
            "report_table": str(REPORT),
            "chart_of_accounts": str(COA),
            "prior_version": str(PRIOR),
            "checklist_config": str(CONFIG),
        },
        export_dir=tmp_path,
    )
    assert result["workflow_type"] == "report_review"
    assert result["validation"].passed is True
    assert result["llm_response"].model_provider == "mock"
    for name in rr.EXPORT_FILE_NAMES:
        assert Path(result["export_paths"][name]).exists()


def test_register_dict_registry():
    registry: dict = {}
    rr.register(registry)
    assert registry["report_review"] is rr.run
