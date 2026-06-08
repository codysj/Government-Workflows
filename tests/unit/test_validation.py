"""Unit tests for the canonical validator (``src.core.validation``).

Covers the spec Phase 2 validation rules a passing case and failing cases:
invented source row, invented number, final-approval language, missing refs,
and an invalid-JSON case.
"""
from __future__ import annotations

from src.core.schemas import (
    DeterministicFinding,
    FindingType,
    Severity,
    SourceRowRef,
)
from src.core.validation import validate_llm_output


def _finding(table: str, row: int, *, amount: str = "100") -> DeterministicFinding:
    ref = SourceRowRef(
        file_id="f1",
        table_name=table,
        row_index=row,
        column_names=["amount"],
        source_values={"amount": amount},
    )
    return DeterministicFinding(
        finding_type=FindingType.OTHER,
        severity=Severity.MEDIUM,
        description=f"finding on {table}:{row}",
        source_rows=[ref],
        computed_values={"amount": amount},
        rule_used="test_rule",
        requires_human_review=True,
    )


class _Det:
    """Minimal deterministic-output stand-in exposing ``.findings``/``.summary``."""

    def __init__(self, findings):
        self.findings = findings
        self.summary = {"total_findings": len(findings)}


def test_passing_case():
    det = _Det([_finding("report", 0, amount="250")])
    resp = {
        "summary": "Flagged 1 issue; see source row report:0 (amount 250).",
        "referenced_source_rows": ["report:0"],
    }
    result = validate_llm_output(resp, det)
    assert result.passed is True
    assert result.errors == []
    assert result.invented_reference_detected is False
    assert "report:0" in result.checked_source_refs


def test_invented_source_row_fails():
    det = _Det([_finding("report", 0)])
    resp = {
        "summary": "References a row that does not exist.",
        "referenced_source_rows": ["report:0", "report:9999"],
    }
    result = validate_llm_output(resp, det)
    assert result.passed is False
    assert result.invented_reference_detected is True
    assert any("invented" in e.lower() for e in result.errors)


def test_invented_number_warns():
    det = _Det([_finding("report", 0, amount="100")])
    # 100 is supported (in computed values); 777 is invented.
    resp = {
        "summary": "There are 777 dollars unaccounted for at report:0.",
        "referenced_source_rows": ["report:0"],
    }
    result = validate_llm_output(resp, det)
    assert result.numeric_claims_checked >= 1
    assert any("777" in w for w in result.warnings)


def test_invented_number_as_error():
    det = _Det([_finding("report", 0, amount="100")])
    resp = {
        "summary": "Total is 777 at report:0.",
        "referenced_source_rows": ["report:0"],
    }
    result = validate_llm_output(
        resp, det, numeric_claims_are_warnings=False
    )
    assert result.passed is False
    assert any("777" in e for e in result.errors)


def test_final_approval_language_fails():
    det = _Det([_finding("report", 0)])
    resp = {
        "summary": "Reviewed report:0.",
        "draft_memo": "This report is correct and approved for filing.",
        "referenced_source_rows": ["report:0"],
    }
    result = validate_llm_output(resp, det)
    assert result.passed is False
    assert any("approval" in e.lower() for e in result.errors)


def test_missing_references_error_by_default():
    det = _Det([_finding("report", 0)])
    resp = {"summary": "I have no idea which rows this refers to."}
    result = validate_llm_output(resp, det)
    assert result.passed is False
    assert any("source reference" in e.lower() for e in result.errors)


def test_missing_references_as_warning_when_configured():
    det = _Det([_finding("report", 0)])
    resp = {"summary": "No refs but allowed as warning."}
    result = validate_llm_output(
        resp, det, missing_references_is_error=False
    )
    assert result.passed is True
    assert any("source reference" in w.lower() for w in result.warnings)


def test_account_code_not_in_context_fails():
    det = _Det([_finding("report", 0)])
    resp = {
        "summary": "Cites an account code report:0.",
        "referenced_source_rows": ["report:0"],
        "account_codes": ["4010", "9999"],
    }
    result = validate_llm_output(
        resp, det, valid_account_codes={"4010", "4020"}
    )
    assert result.passed is False
    assert any("9999" in e for e in result.errors)


def test_invalid_json_when_schema_required_fails():
    det = _Det([_finding("report", 0)])
    result = validate_llm_output("this is not json", det, require_schema_json=True)
    assert result.passed is False
    assert any("json" in e.lower() for e in result.errors)


def test_json_string_is_parsed_when_valid():
    det = _Det([_finding("report", 0)])
    result = validate_llm_output(
        '{"summary": "ok report:0", "referenced_source_rows": ["report:0"]}',
        det,
    )
    assert result.passed is True
