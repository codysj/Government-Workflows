"""Preflight / capability tests for the budget_variance workflow.

Exercises the workflow's CAPABILITY spec, its domain ``detect_conditions``
detector (rollup + basis mismatch), the PASS/PARTIAL/FAIL status outcomes via
the shared ``run_preflight`` engine, the optional ``column_mappings`` override
on the deterministic analysis, and source-row preservation under override.

Synthetic fixtures only; the mock LLM is never called here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.core.preflight import (
    build_column_mappings,
    profile_input,
    run_preflight,
    _mappings_by_input,
)
from src.core.schemas import (
    ParsedTable,
    PreflightConditionCode,
    PreflightStatus,
)
from src.workflows import budget_variance as bv
from src.workflows.budget_variance import CAPABILITY, detect_conditions

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "budget_variance"
MESSY = DATA / "messy"
BUDGET = DATA / "budget.csv"
ACTUALS = DATA / "actuals.csv"
COA = DATA / "chart_of_accounts.csv"


def _parsed(name: str, df: pd.DataFrame) -> ParsedTable:
    return ParsedTable(
        file_id=name,
        table_name=name,
        file_name=f"{name}.csv",
        file_type="csv",
        parser_used="test",
        column_names=list(df.columns),
        row_count=len(df),
        dataframe=df,
    )


# --------------------------------------------------------------------------- #
# CAPABILITY declaration
# --------------------------------------------------------------------------- #
def test_capability_declares_required_inputs_and_columns():
    assert CAPABILITY.workflow_type == "budget_variance"
    assert CAPABILITY.required_inputs == ["budget", "actuals"]
    assert "chart_of_accounts" in CAPABILITY.optional_inputs
    # csv + xlsx accepted.
    assert set(CAPABILITY.accepted_file_types["*"]) == {"csv", "xlsx"}
    # A join key (fund) + amount required on BOTH files.
    for side in ("budget", "actuals"):
        req = CAPABILITY.required_semantic_columns[side]
        assert "fund" in req
        assert "amount" in req
        # Remaining join keys are optional refinements.
        opt = CAPABILITY.optional_semantic_columns[side]
        assert {"account_code", "department", "object"}.issubset(set(opt))


def test_capability_supported_and_unsupported_patterns():
    assert any("variance" in p for p in CAPABILITY.supported_patterns)
    assert "possible_account_rollup" in CAPABILITY.partially_supported_patterns
    assert "possible_budget_basis_mismatch" in CAPABILITY.partially_supported_patterns
    assert CAPABILITY.unsupported_patterns  # rollup / basis conversion declared


# --------------------------------------------------------------------------- #
# run_preflight: PASS / PARTIAL / FAIL
# --------------------------------------------------------------------------- #
def test_preflight_pass_on_clean_sample():
    report = run_preflight(
        CAPABILITY,
        {"budget": BUDGET, "actuals": ACTUALS, "chart_of_accounts": COA},
        detect_conditions=detect_conditions,
    )
    assert report.status == PreflightStatus.PASS
    assert report.llm_allowed is True
    assert report.partial is False
    assert report.findings == []
    assert report.supported_checks  # populated on PASS


def test_preflight_fail_on_missing_required_columns():
    report = run_preflight(
        CAPABILITY,
        {
            "budget": MESSY / "fail_missing_columns_budget.csv",
            "actuals": ACTUALS,
        },
        detect_conditions=detect_conditions,
    )
    assert report.status == PreflightStatus.FAIL
    assert report.llm_allowed is False
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.MISSING_REQUIRED_COLUMN in codes
    assert PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION in codes
    assert any(f.blocks_run for f in report.findings)
    assert report.next_steps


def test_preflight_partial_on_account_rollup():
    report = run_preflight(
        CAPABILITY,
        {
            "budget": MESSY / "partial_rollup_budget.csv",
            "actuals": MESSY / "partial_rollup_actuals.csv",
        },
        detect_conditions=detect_conditions,
    )
    assert report.status == PreflightStatus.PARTIAL
    assert report.llm_allowed is True
    assert report.partial is True
    assert (
        PreflightConditionCode.POSSIBLE_ACCOUNT_ROLLUP.value
        in report.unsupported_conditions
    )
    # No blocking finding on a PARTIAL.
    assert not any(f.blocks_run for f in report.findings)


def test_preflight_partial_on_budget_basis_mismatch():
    # Budget aggregated to 2 fund-level rows vs actuals at 6 account rows.
    budget = _parsed(
        "budget",
        pd.DataFrame(
            {"fund": ["General", "Water"], "budget_amount": ["100000", "70000"]}
        ),
    )
    actuals = _parsed(
        "actuals",
        pd.DataFrame(
            {
                "fund": ["General"] * 5 + ["Water"],
                "account": ["1", "2", "3", "4", "5", "6"],
                "actual_amount": [
                    "11000",
                    "22000",
                    "33000",
                    "5500",
                    "5500",
                    "44000",
                ],
            }
        ),
    )
    report = run_preflight(
        CAPABILITY,
        {"budget": budget, "actuals": actuals},
        detect_conditions=detect_conditions,
    )
    assert report.status == PreflightStatus.PARTIAL
    assert (
        PreflightConditionCode.POSSIBLE_BUDGET_BASIS_MISMATCH.value
        in report.unsupported_conditions
    )


# --------------------------------------------------------------------------- #
# detect_conditions returns [] on clean data
# --------------------------------------------------------------------------- #
def test_detect_conditions_empty_on_clean():
    profiles = {
        "budget": profile_input("budget", BUDGET, CAPABILITY.accepted_file_types),
        "actuals": profile_input("actuals", ACTUALS, CAPABILITY.accepted_file_types),
    }
    mappings = _mappings_by_input(build_column_mappings(CAPABILITY, profiles))
    out = detect_conditions(
        profiles, mappings, {"budget": BUDGET, "actuals": ACTUALS}, None
    )
    assert out == []


def test_detect_conditions_empty_when_file_missing():
    # Missing files are handled by the generic engine; the domain detector must
    # not crash and returns [].
    profiles = {
        "budget": profile_input("budget", BUDGET, CAPABILITY.accepted_file_types),
        "actuals": profile_input(
            "actuals", MESSY / "does_not_exist.csv", CAPABILITY.accepted_file_types
        ),
    }
    mappings = _mappings_by_input(build_column_mappings(CAPABILITY, profiles))
    assert detect_conditions(profiles, mappings, {"budget": BUDGET}, None) == []


# --------------------------------------------------------------------------- #
# column_mappings override is honored by the deterministic analysis
# --------------------------------------------------------------------------- #
def test_column_mappings_override_amount_column():
    budget = _parsed(
        "budget",
        pd.DataFrame(
            {
                "fund": ["General"],
                "account": ["5001"],
                "department": ["Police"],
                "object": ["Salaries"],
                "approved_budget": ["50000"],  # non-standard amount header
            }
        ),
    )
    actuals = _parsed(
        "actuals",
        pd.DataFrame(
            {
                "fund": ["General"],
                "account": ["5001"],
                "department": ["Police"],
                "object": ["Salaries"],
                "actual_amount": ["60000"],
            }
        ),
    )
    det = bv.analyze(
        budget,
        actuals,
        column_mappings={"budget": {"amount": "approved_budget"}},
    )
    assert det.summary["joined_lines"] == 1
    row = det.result_tables["variances"].iloc[0]
    assert row["budget_amount"] == 50000.0
    assert row["actual_amount"] == 60000.0
    assert row["dollar_variance"] == 10000.0


def test_column_mappings_override_join_key_column():
    # The account key lives under a non-standard header 'gl_code'; map it via
    # the 'account_code' semantic so the join still matches.
    budget = _parsed(
        "budget",
        pd.DataFrame(
            {
                "fund": ["General", "General"],
                "gl_code": ["5001", "5002"],
                "budget_amount": ["50000", "30000"],
            }
        ),
    )
    actuals = _parsed(
        "actuals",
        pd.DataFrame(
            {
                "fund": ["General", "General"],
                "gl_code": ["5001", "5002"],
                "actual_amount": ["55000", "31000"],
            }
        ),
    )
    mapping = {
        "budget": {"account_code": "gl_code"},
        "actuals": {"account_code": "gl_code"},
    }
    det = bv.analyze(budget, actuals, column_mappings=mapping)
    assert "account" in det.summary["join_keys"]
    assert det.summary["joined_lines"] == 2
    assert det.summary["budget_only"] == 0
    assert det.summary["actual_only"] == 0


def test_none_column_mappings_keeps_current_behavior():
    # Passing None must reproduce the known-answer counts of the clean sample.
    det = bv.analyze(BUDGET, ACTUALS, chart_of_accounts=COA, column_mappings=None)
    assert det.summary["joined_lines"] == 4
    assert det.summary["join_keys"] == ["fund", "account", "department", "object"]


# --------------------------------------------------------------------------- #
# Source-row preservation under override
# --------------------------------------------------------------------------- #
def test_source_rows_preserved_under_override():
    budget = _parsed(
        "budget",
        pd.DataFrame(
            {
                "fund": ["General", "General"],
                "account": ["5001", "5002"],
                "approved_budget": ["50000", "200000"],
            }
        ),
    )
    actuals = _parsed(
        "actuals",
        pd.DataFrame(
            {
                "fund": ["General", "General"],
                "account": ["5001", "5002"],
                "actual_amount": ["50500", "240000"],  # 5002 flags (+40000)
            }
        ),
    )
    det = bv.analyze(
        budget,
        actuals,
        column_mappings={"budget": {"amount": "approved_budget"}},
    )
    variance = [f for f in det.findings if f.source_rows]
    assert variance
    f = variance[0]
    # Each variance finding carries one budget + one actual source row, with the
    # original positional index preserved.
    tables = {s.table_name for s in f.source_rows}
    assert tables == {"budget", "actuals"}
    for s in f.source_rows:
        assert isinstance(s.row_index, int)
        assert s.row_index in (0, 1)
        assert s.source_values
