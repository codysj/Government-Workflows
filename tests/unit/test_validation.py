"""Unit tests for the canonical validator (``src.core.validation``).

Covers the spec Phase 2 validation rules a passing case and failing cases:
invented source row, invented number, final-approval language, missing refs,
and an invalid-JSON case.
"""
from __future__ import annotations

import pytest

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


def test_invented_number_fails():
    det = _Det([_finding("report", 0, amount="100")])
    # 100 is supported (in computed values); 777 is invented.
    resp = {
        "summary": "There are 777 dollars unaccounted for at report:0.",
        "referenced_source_rows": ["report:0"],
    }
    result = validate_llm_output(resp, det)
    assert result.numeric_claims_checked >= 1
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


def test_missing_references_allowed_when_not_required():
    det = _Det([_finding("report", 0)])
    resp = {"summary": "No refs, as run Q&A may legitimately answer."}
    result = validate_llm_output(resp, det, require_references=False)
    assert result.passed is True


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


def test_normalized_claims_match_the_data():
    """Real-model phrasing of supported values passes."""
    f = _finding("budget", 1, amount="12450.00")
    f.computed_values.update({"pct_variance": "17.5000", "invoice": "INV-10234",
                              "invoice_date": "2026-03-10"})
    resp = {
        "summary": ("Invoice INV-10234 dated March 10, 2026 (03/10/2026) for "
                    "$12,450.00 (i.e. $12,450) is 17.50% over; see budget:1. "
                    "It is the 1st of 1 finding(s) in March 2026, a 1-row issue."),
        "referenced_source_rows": ["budget:1"],
    }
    result = validate_llm_output(resp, _Det([f]))
    assert result.passed is True, result.errors


@pytest.mark.parametrize("claim", [
    "$12,451.00", "17.6%", "INV-10235", "2026-03-11", "April 2026", "budget:2",
])
def test_near_miss_claims_fail(claim):
    """Values one step away from the data are still caught."""
    f = _finding("budget", 1, amount="12450.00")
    f.computed_values.update({"pct_variance": "17.5000", "invoice": "INV-10234",
                              "invoice_date": "2026-03-10"})
    resp = {"summary": f"See budget:1. Also {claim}.", "referenced_source_rows": ["budget:1"]}
    assert validate_llm_output(resp, _Det([f])).passed is False


def test_whole_numbers_do_not_round_match_small_values():
    f = _finding("report", 0, amount="2.7")
    resp = {"summary": "There are 3 problems at report:0.", "referenced_source_rows": ["report:0"]}
    assert validate_llm_output(resp, _Det([f])).passed is False


# --- Review-found bypasses: each invented claim below must be rejected. ---
def _rich_det():
    """Rows 46-58 cited, ~13 findings: lots of small numbers in the context."""
    findings = []
    for row in range(46, 59):
        f = _finding("ap_invoices", row, amount="1250.00")
        f.computed_values.update({"po_number": "PO-3001", "invoice_date": "2026-03-10"})
        findings.append(f)
    return _Det(findings)


@pytest.mark.parametrize("resp", [
    {"summary": "See ap_invoices:46. Invoice exceeds PO by $50, roughly 47% over."},
    {"summary": "See ap_invoices:46. There are 50 duplicate pairs."},
    {"summary": "See ap_invoices:46.", "categorized_exceptions": [{"amount": 99999.99}]},
    {"summary": "See ap_invoices:46.", "suggested_review_steps": [
        {"step": "check", "referenced_source_rows": ["ap_invoices:999"]}]},
    {"summary": "See ap_invoices:46.", "notes": {"$99,999 overpaid": "see row"}},
    {"summary": "See ap_invoices:46. A $2 million shortfall exists."},
    {"summary": "See ap_invoices:46. Twelve thousand dollars is unexplained."},
    {"summary": "See ap_invoices:46. There are nineteen duplicate invoices."},
    {"summary": "See ap_invoices:46 and AP_INVOICES:999."},
    {"summary": "See ap_invoices:46. It was paid on March 3rd."},
    {"summary": "See ap_invoices:46. This reconciliation is approved."},
    {"summary": "See ap_invoices:46 and rows 46, 47, and 99."},
], ids=["dollar=row", "count=row", "nested-number", "nested-ref", "dict-key",
        "scale-word", "spelled-amount", "word-count", "uppercase-ref",
        "date-no-year", "approval-regex", "row-list"])
def test_review_bypasses_are_rejected(resp):
    resp.setdefault("referenced_source_rows", ["ap_invoices:46"])
    assert validate_llm_output(resp, _rich_det()).passed is False


@pytest.mark.parametrize("text", [
    "PO 3001 and invoice #3001 relate to ap_invoices:46.",
    "Meet at 10:00 AM to review 100% of rows 46, 47, and 48.",
    "Finding 5: request Form 1099 and a W-9; see ap_invoices:46.",
    "1. Confirm ap_invoices:46\n2. Confirm ap_invoices:47\n3. Escalate",
    "It is three days apart; dated March 10 (ap_invoices:46).",
    "Bank rows 13 were reviewed for ap_invoices:46.",
])
def test_ordinary_phrasing_is_not_flagged(text):
    resp = {"summary": text, "referenced_source_rows": ["ap_invoices:46"]}
    result = validate_llm_output(resp, _rich_det())
    assert result.passed is True, result.errors


def test_absurd_precision_does_not_crash():
    resp = {"summary": "Rate 0.000000000000000000000000000001 at ap_invoices:46.",
            "referenced_source_rows": ["ap_invoices:46"]}
    assert validate_llm_output(resp, _rich_det()).passed is False


def test_capped_prompt_counts_are_supported():
    """Over 150 findings the prompt lists 150 and says how many more exist."""
    det = _Det([_finding("ledger", i) for i in range(160)])
    resp = {"summary": "150 findings are listed; 10 more are summarized. See ledger:0.",
            "referenced_source_rows": ["ledger:0"]}
    assert validate_llm_output(resp, det).passed is True
