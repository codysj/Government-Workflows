"""Known-answer tests for the budget-to-actual variance workflow.

Uses the synthetic dataset under data/synthetic/budget_variance/ and tmp_path
for exports. No shared on-disk DB; the mock LLM provider needs no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.core.schemas import FindingType
from src.workflows import budget_variance as bv

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "budget_variance"
BUDGET = DATA / "budget.csv"
ACTUALS = DATA / "actuals.csv"
COA = DATA / "chart_of_accounts.csv"
THRESHOLDS = DATA / "thresholds.json"


@pytest.fixture
def det():
    return bv.analyze(
        BUDGET, ACTUALS, chart_of_accounts=COA, thresholds=THRESHOLDS
    )


# --------------------------------------------------------------------------- #
# Deterministic known answers
# --------------------------------------------------------------------------- #
def test_summary_counts(det):
    s = det.summary
    assert s["joined_lines"] == 4
    assert s["flagged_variances"] == 2
    assert s["budget_only"] == 1
    assert s["actual_only"] == 1
    assert s["missing_accounts"] == 1
    assert s["join_keys"] == ["fund", "account", "department", "object"]


def test_variance_math_large_dollar(det):
    df = det.result_tables["variances"]
    fire = df[df["account"] == "5002"].iloc[0]
    assert fire["budget_amount"] == 200000.0
    assert fire["actual_amount"] == 235000.0
    assert fire["dollar_variance"] == 35000.0
    assert fire["pct_variance"] == pytest.approx(17.5)
    assert bool(fire["flagged"]) is True


def test_variance_math_large_pct(det):
    df = det.result_tables["variances"]
    parks = df[df["account"] == "5003"].iloc[0]
    assert parks["dollar_variance"] == 1000.0
    assert parks["pct_variance"] == pytest.approx(50.0)
    assert bool(parks["flagged"]) is True  # flagged on pct, not dollars


def test_small_variances_not_flagged(det):
    df = det.result_tables["variances"]
    police = df[df["account"] == "5001"].iloc[0]
    water = df[df["account"] == "6001"].iloc[0]
    assert bool(police["flagged"]) is False
    assert bool(water["flagged"]) is False
    assert police["dollar_variance"] == 500.0
    assert water["dollar_variance"] == -1000.0


def test_budget_only_actual_only_missing(det):
    by_subtype = {}
    for f in det.findings:
        sub = f.computed_values.get("subtype")
        if sub:
            by_subtype.setdefault(sub, []).append(f)
    assert len(by_subtype.get("budget_only", [])) == 1
    assert len(by_subtype.get("actual_only", [])) == 1
    assert len(by_subtype.get("missing_account", [])) == 1
    # budget-only is account 5010, actual-only is 5020, missing is 5099.
    assert by_subtype["budget_only"][0].computed_values["key"]["account"] == "5010"
    assert by_subtype["actual_only"][0].computed_values["key"]["account"] == "5020"
    assert by_subtype["missing_account"][0].computed_values["key"]["account"] == "5099"


def test_flagged_findings_count_and_type(det):
    variance_findings = [
        f for f in det.findings if f.finding_type == FindingType.VARIANCE
    ]
    assert len(variance_findings) == 2
    for f in variance_findings:
        assert f.requires_human_review is True


def test_grouping_by_fund(det):
    by_fund = det.result_tables["by_fund"]
    gen = by_fund[by_fund["fund"] == "General"].iloc[0]
    # General joined lines: 5001 (50000), 5002 (200000), 5003 (2000) = 252000
    assert gen["budget_amount"] == 252000.0
    assert gen["actual_amount"] == 288500.0


# --------------------------------------------------------------------------- #
# Source-row preservation
# --------------------------------------------------------------------------- #
def test_source_rows_preserved(det):
    variance_findings = [
        f for f in det.findings if f.finding_type == FindingType.VARIANCE
    ]
    f = variance_findings[0]
    assert len(f.source_rows) == 2  # one budget row + one actual row
    tables = {s.table_name for s in f.source_rows}
    assert tables == {"budget", "actuals"}
    for s in f.source_rows:
        assert isinstance(s.row_index, int)
        assert s.source_values  # carries the original cells


# --------------------------------------------------------------------------- #
# Mock LLM output shape + validation
# --------------------------------------------------------------------------- #
def test_mock_llm_shape_and_no_invented_refs(det):
    llm = bv.run_llm(det)  # default mock provider
    rj = llm.response_json
    assert "summary" in rj
    assert isinstance(rj["categorized_exceptions"], list)
    assert isinstance(rj["referenced_source_rows"], list)
    assert isinstance(rj["suggested_review_steps"], list)
    assert rj["draft_memo"]
    # Mock cites two flagged variances.
    assert len(rj["categorized_exceptions"]) == 2

    val = bv.validate_llm(det, llm)
    assert val.passed is True
    assert val.invented_reference_detected is False
    assert val.numeric_claims_checked > 0


def test_validation_catches_invented_reference(det):
    llm = bv.run_llm(det)
    llm.referenced_source_rows.append("nonexistent_table:999")
    val = bv.validate_llm(det, llm)
    assert val.passed is False
    assert val.invented_reference_detected is True


# --------------------------------------------------------------------------- #
# Registry + class protocol
# --------------------------------------------------------------------------- #
def test_registry_registration():
    assert "budget_variance" in bv.WORKFLOW_REGISTRY
    cls = bv.WORKFLOW_REGISTRY["budget_variance"]
    assert cls is bv.BudgetVarianceWorkflow
    assert cls.workflow_type == "budget_variance"


def test_workflow_class_end_to_end():
    wf = bv.BudgetVarianceWorkflow()
    inputs = {
        "budget": BUDGET,
        "actuals": ACTUALS,
        "chart_of_accounts": COA,
        "thresholds": THRESHOLDS,
    }
    result = wf.run(inputs)
    assert result.workflow_type == "budget_variance"
    assert result.deterministic.summary["flagged_variances"] == 2
    assert result.validation.passed is True


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def test_export_artifacts_writes_required_files(tmp_path):
    wf = bv.BudgetVarianceWorkflow()
    inputs = {
        "budget": BUDGET,
        "actuals": ACTUALS,
        "chart_of_accounts": COA,
        "thresholds": THRESHOLDS,
    }
    result = wf.run(inputs)
    artifacts = wf.export_artifacts(
        result, tmp_path, run_id="r1", audit_events=[{"event_type": "x"}]
    )
    names = {a.file_name for a in artifacts}
    assert names == {
        "variance_summary.md",
        "flagged_variances.csv",
        "variance_commentary_draft.md",
        "validation_report.json",
        "audit_log.json",
    }
    for a in artifacts:
        p = Path(a.path)
        assert p.exists()
        assert a.sha256

    # flagged_variances.csv contains exactly the 2 flagged lines.
    flagged = pd.read_csv(tmp_path / "flagged_variances.csv")
    assert len(flagged) == 2
    assert set(flagged["account"].astype(str)) == {"5002", "5003"}

    # validation_report.json is valid JSON and reports passed.
    report = json.loads((tmp_path / "validation_report.json").read_text())
    assert report["passed"] is True


def test_thresholds_dict_override(tmp_path):
    # Lower the dollar threshold so the small +500 line also flags.
    det = bv.analyze(
        BUDGET,
        ACTUALS,
        chart_of_accounts=COA,
        thresholds={"dollar_threshold": 400, "pct_threshold": 100},
    )
    # All 4 joined lines exceed 400 dollars: 5001 (+500), 5002 (+35000),
    # 5003 (+1000), 6001 (-1000).
    assert det.summary["flagged_variances"] == 4
