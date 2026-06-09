"""Preflight / capability tests for Workflow 3 — report_review.

Exercises the workflow's PREFLIGHT integration against the shared engine
(``src.core.preflight.run_preflight``):

  * CAPABILITY declares the right required/optional semantic columns.
  * A clean long-layout report -> PASS; detect_conditions returns [].
  * A report missing the required ``amount`` column -> FAIL (blocking).
  * A report with unrecognized line_type values -> PARTIAL with the expected
    POSSIBLE_UNKNOWN_REPORT_STRUCTURE domain condition.
  * The ``column_mappings`` override is honored by run_deterministic.
  * Source-row indices survive the mapping override.

Synthetic data only. No shared on-disk DB; no network; mock LLM is not invoked.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.core.preflight import (
    build_column_mappings,
    profile_input,
    run_preflight,
)
from src.core.preflight import _mappings_by_input  # internal helper, stable in contract
from src.core.schemas import PreflightConditionCode, PreflightStatus
from src.workflows import report_review as rr

MESSY = Path("data/synthetic/report_review/messy")
CLEAN = MESSY / "clean_pass.csv"
FAIL = MESSY / "fail_missing_amount.csv"
PARTIAL = MESSY / "partial_unknown_line_types.csv"


# --------------------------------------------------------------------------- #
# CAPABILITY spec
# --------------------------------------------------------------------------- #
def test_capability_declares_required_semantic_columns():
    cap = rr.CAPABILITY
    assert cap.workflow_type == "report_review"
    assert cap.required_inputs == ["report_table"]
    assert set(cap.optional_inputs) == {"chart_of_accounts", "prior_version"}
    req = cap.required_semantic_columns["report_table"]
    assert set(req) == {"section", "line_type", "amount"}
    # account_code / account_name are optional but recommended.
    opt = cap.optional_semantic_columns["report_table"]
    assert {"account_code", "account_name"} <= set(opt)


def test_capability_accepts_csv_and_xlsx():
    accepted = rr.CAPABILITY.accepted_file_types
    types = accepted.get("*") or accepted.get("report_table")
    assert "csv" in types and "xlsx" in types


def test_capability_lists_supported_and_unsupported_patterns():
    cap = rr.CAPABILITY
    assert "subtotal_vs_line_item" in cap.supported_patterns
    assert "wide_pivoted_report" in cap.unsupported_patterns
    assert "multi_level_nested_rollup" in cap.unsupported_patterns


# --------------------------------------------------------------------------- #
# run_preflight statuses
# --------------------------------------------------------------------------- #
def test_clean_report_passes():
    report = run_preflight(
        rr.CAPABILITY,
        {"report_table": str(CLEAN)},
        detect_conditions=rr.detect_conditions,
    )
    assert report.status == PreflightStatus.PASS
    assert report.llm_allowed is True
    assert report.partial is False
    assert report.unsupported_conditions == []
    # Supported checks are surfaced on PASS.
    assert "subtotal_vs_line_item" in report.supported_checks


def test_missing_required_column_fails():
    report = run_preflight(
        rr.CAPABILITY,
        {"report_table": str(FAIL)},
        detect_conditions=rr.detect_conditions,
    )
    assert report.status == PreflightStatus.FAIL
    assert report.llm_allowed is False
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.MISSING_REQUIRED_COLUMN in codes
    assert any(f.blocks_run for f in report.findings)
    # The blocking column is the required 'amount' field.
    missing = [
        f for f in report.findings
        if f.code == PreflightConditionCode.MISSING_REQUIRED_COLUMN
    ]
    assert any(f.affected_column == "amount" for f in missing)
    assert report.next_steps  # concrete next steps are provided


def test_unknown_line_types_partial():
    report = run_preflight(
        rr.CAPABILITY,
        {"report_table": str(PARTIAL)},
        detect_conditions=rr.detect_conditions,
    )
    assert report.status == PreflightStatus.PARTIAL
    assert report.partial is True
    assert report.llm_allowed is True  # LLM may explain deterministic findings
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.POSSIBLE_UNKNOWN_REPORT_STRUCTURE in codes
    # No domain condition blocks the run.
    domain = [
        f for f in report.findings
        if f.code == PreflightConditionCode.POSSIBLE_UNKNOWN_REPORT_STRUCTURE
    ]
    assert all(f.blocks_run is False for f in domain)


def test_wide_pivoted_layout_detected(tmp_path):
    # No single 'amount' column; several period columns spread across.
    p = tmp_path / "wide.csv"
    pd.DataFrame(
        {
            "section": ["Revenues", "Expenditures"],
            "line_item": ["Property Tax", "Salaries"],
            "q1": ["100", "200"],
            "q2": ["110", "210"],
            "q3": ["120", "220"],
            "q4": ["130", "230"],
        }
    ).to_csv(p, index=False)
    profiles = {
        "report_table": profile_input(
            "report_table", str(p), rr.CAPABILITY.accepted_file_types
        )
    }
    mappings = _mappings_by_input(build_column_mappings(rr.CAPABILITY, profiles))
    findings = rr.detect_conditions(profiles, mappings, {"report_table": str(p)}, None)
    codes = {f.code for f in findings}
    assert PreflightConditionCode.UNSUPPORTED_PATTERN_DETECTED in codes


def test_multi_level_rollup_detected(tmp_path):
    # A numeric 'level' column with >=3 depths signals nested rollups.
    p = tmp_path / "nested.csv"
    pd.DataFrame(
        {
            "section": ["A", "A", "A", "A"],
            "line_type": ["line_item", "subtotal", "subtotal", "grand_total"],
            "level": ["3", "2", "1", "0"],
            "amount": ["100", "100", "100", "100"],
        }
    ).to_csv(p, index=False)
    profiles = {
        "report_table": profile_input(
            "report_table", str(p), rr.CAPABILITY.accepted_file_types
        )
    }
    mappings = _mappings_by_input(build_column_mappings(rr.CAPABILITY, profiles))
    findings = rr.detect_conditions(profiles, mappings, {"report_table": str(p)}, None)
    codes = {f.code for f in findings}
    assert PreflightConditionCode.POSSIBLE_ACCOUNT_ROLLUP in codes


# --------------------------------------------------------------------------- #
# detect_conditions on clean data
# --------------------------------------------------------------------------- #
def test_detect_conditions_empty_on_clean_data():
    profiles = {
        "report_table": profile_input(
            "report_table", str(CLEAN), rr.CAPABILITY.accepted_file_types
        )
    }
    mappings = _mappings_by_input(build_column_mappings(rr.CAPABILITY, profiles))
    findings = rr.detect_conditions(
        profiles, mappings, {"report_table": str(CLEAN)}, None
    )
    assert findings == []


def test_detect_conditions_never_crashes_on_missing_input():
    # Required file absent -> detector returns [] (engine handles the block).
    findings = rr.detect_conditions({}, {}, {}, None)
    assert findings == []


# --------------------------------------------------------------------------- #
# column_mappings override + source-row preservation
# --------------------------------------------------------------------------- #
def test_column_mappings_override_is_honored(tmp_path):
    # Amount column is named 'usd_value'; auto-detect would miss it.
    p = tmp_path / "mapped.csv"
    pd.DataFrame(
        {
            "section": ["Rev", "Rev", "Rev"],
            "account_code": ["4010", "4020", ""],
            "account_name": ["A", "B", "Total"],
            "line_type": ["line_item", "line_item", "subtotal"],
            "usd_value": ["100", "200", "999"],  # subtotal 999 != 300 -> mismatch
        }
    ).to_csv(p, index=False)

    det = rr.run_deterministic(
        p, column_mappings={"report_table": {"amount": "usd_value"}}
    )
    hits = [
        f for f in det.findings if f.rule_used == "subtotal_equals_line_item_sum"
    ]
    assert len(hits) == 1
    assert hits[0].computed_values["stated_subtotal"] == "999"
    assert hits[0].computed_values["computed_line_item_sum"] == "300"


def test_none_mapping_keeps_default_behavior():
    # With column_mappings=None, behavior matches the existing known-answer path.
    cfg = rr.ReportReviewConfig.from_json(
        Path("data/synthetic/report_review/checklist_config.json")
    )
    base = rr.run_deterministic(
        Path("data/synthetic/report_review/report_table.csv"),
        chart_of_accounts_path=Path("data/synthetic/report_review/chart_of_accounts.csv"),
        prior_version_path=Path("data/synthetic/report_review/prior_version.csv"),
        config=cfg,
    )
    explicit_none = rr.run_deterministic(
        Path("data/synthetic/report_review/report_table.csv"),
        chart_of_accounts_path=Path("data/synthetic/report_review/chart_of_accounts.csv"),
        prior_version_path=Path("data/synthetic/report_review/prior_version.csv"),
        config=cfg,
        column_mappings=None,
    )
    assert base.summary["total_findings"] == explicit_none.summary["total_findings"]


def test_source_rows_preserved_under_override(tmp_path):
    p = tmp_path / "mapped.csv"
    pd.DataFrame(
        {
            "section": ["Rev", "Rev", "Rev"],
            "account_code": ["4010", "4020", ""],
            "account_name": ["A", "B", "Total"],
            "line_type": ["line_item", "line_item", "subtotal"],
            "usd_value": ["100", "200", "999"],
        }
    ).to_csv(p, index=False)

    det = rr.run_deterministic(
        p, column_mappings={"report_table": {"amount": "usd_value"}}
    )
    refs = [ref for f in det.findings for ref in f.source_rows]
    assert refs
    for ref in refs:
        assert ref.table_name == "report"
        assert isinstance(ref.row_index, int)
        # Original mapped column value is carried through unchanged.
        assert "usd_value" in ref.source_values
    # The subtotal row is positional index 2 in the parsed frame.
    sub_idx = {
        ref.row_index
        for f in det.findings
        for ref in f.source_rows
        if ref.source_values.get("line_type") == "subtotal"
    }
    assert 2 in sub_idx
