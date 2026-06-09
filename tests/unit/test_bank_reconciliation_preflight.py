"""Preflight / capability tests for the bank_reconciliation workflow.

Exercises the workflow's CAPABILITY spec, its domain detector
(detect_conditions), the column_mappings override on the deterministic
reconcile(), and source-row preservation. Uses the shared run_preflight engine
from src.core.preflight against tiny synthetic fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.preflight import run_preflight
from src.core.schemas import (
    ColumnMapping,
    PreflightConditionCode,
    PreflightStatus,
)
from src.workflows.bank_reconciliation import (
    CAPABILITY,
    detect_conditions,
    reconcile,
)

_REPO = Path(__file__).resolve().parents[2]
_SD = _REPO / "data" / "synthetic" / "bank_reconciliation"
_MESSY = _SD / "messy"


# --------------------------------------------------------------------------- #
# Input helpers
# --------------------------------------------------------------------------- #
def _clean_inputs() -> dict:
    return {"bank": str(_SD / "bank.csv"), "ledger": str(_SD / "ledger.csv")}


def _fail_inputs() -> dict:
    # Bank file is missing the required `amount` column.
    return {
        "bank": str(_MESSY / "fail_missing_amount_bank.csv"),
        "ledger": str(_SD / "ledger.csv"),
    }


def _sign_inputs() -> dict:
    return {
        "bank": str(_MESSY / "partial_sign_bank.csv"),
        "ledger": str(_MESSY / "partial_sign_ledger.csv"),
    }


def _batch_inputs() -> dict:
    return {
        "bank": str(_MESSY / "partial_batch_bank.csv"),
        "ledger": str(_MESSY / "partial_batch_ledger.csv"),
    }


def _codes(report) -> set[str]:
    return {f.code.value for f in report.findings}


# --------------------------------------------------------------------------- #
# CAPABILITY spec
# --------------------------------------------------------------------------- #
def test_capability_declares_required_columns():
    assert CAPABILITY.workflow_type == "bank_reconciliation"
    assert CAPABILITY.required_inputs == ["bank", "ledger"]
    assert CAPABILITY.required_semantic_columns["bank"] == ["date", "amount"]
    assert CAPABILITY.required_semantic_columns["ledger"] == ["date", "amount"]
    # description is optional, not required.
    assert "description" in CAPABILITY.optional_semantic_columns["bank"]
    assert "description" not in CAPABILITY.required_semantic_columns["bank"]
    # Accepts csv + xlsx.
    accepted = CAPABILITY.accepted_file_types["*"]
    assert "csv" in accepted and "xlsx" in accepted
    # Supported / partial / unsupported patterns are declared.
    assert CAPABILITY.supported_patterns
    assert "many_to_one_batch_matching" in CAPABILITY.unsupported_patterns
    assert "multi_currency_reconciliation" in CAPABILITY.unsupported_patterns


# --------------------------------------------------------------------------- #
# run_preflight status outcomes
# --------------------------------------------------------------------------- #
def test_preflight_pass_on_clean_sample():
    report = run_preflight(
        CAPABILITY, _clean_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.PASS
    assert report.llm_allowed is True
    assert report.partial is False
    assert report.findings == []
    assert report.supported_checks  # populated on PASS


def test_preflight_fail_on_missing_required_column():
    report = run_preflight(
        CAPABILITY, _fail_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.FAIL
    assert report.llm_allowed is False
    assert any(f.blocks_run for f in report.findings)
    codes = _codes(report)
    assert PreflightConditionCode.MISSING_REQUIRED_COLUMN.value in codes
    assert PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION.value in codes
    assert report.next_steps  # concrete next steps surfaced
    assert report.supported_checks == []  # empty on FAIL


def test_preflight_partial_on_sign_convention_mismatch():
    report = run_preflight(
        CAPABILITY, _sign_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.PARTIAL
    assert report.llm_allowed is True
    assert report.partial is True
    assert (
        PreflightConditionCode.POSSIBLE_SIGN_CONVENTION_MISMATCH.value
        in _codes(report)
    )
    # No blocking finding -> not FAIL.
    assert not any(f.blocks_run for f in report.findings)


def test_preflight_partial_on_batch_matching():
    report = run_preflight(
        CAPABILITY, _batch_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.PARTIAL
    assert (
        PreflightConditionCode.POSSIBLE_BATCH_MATCHING.value in _codes(report)
    )
    assert not any(f.blocks_run for f in report.findings)


# --------------------------------------------------------------------------- #
# detect_conditions in isolation
# --------------------------------------------------------------------------- #
def test_detect_conditions_clean_returns_empty():
    # Build the same profiles/mappings the engine would, then call the detector.
    from src.core.preflight import (
        build_column_mappings,
        profile_input,
        _mappings_by_input,
    )

    inputs = _clean_inputs()
    profiles = {
        k: profile_input(k, inputs.get(k), CAPABILITY.accepted_file_types)
        for k in CAPABILITY.required_inputs
    }
    mappings = _mappings_by_input(build_column_mappings(CAPABILITY, profiles))
    out = detect_conditions(profiles, mappings, inputs, None)
    assert out == []


def test_detect_conditions_flags_sign_mismatch():
    from src.core.preflight import (
        build_column_mappings,
        profile_input,
        _mappings_by_input,
    )

    inputs = _sign_inputs()
    profiles = {
        k: profile_input(k, inputs.get(k), CAPABILITY.accepted_file_types)
        for k in CAPABILITY.required_inputs
    }
    mappings = _mappings_by_input(build_column_mappings(CAPABILITY, profiles))
    out = detect_conditions(profiles, mappings, inputs, None)
    assert any(
        f.code == PreflightConditionCode.POSSIBLE_SIGN_CONVENTION_MISMATCH
        for f in out
    )
    # Domain conditions are advisory only (PARTIAL, never FAIL).
    assert all(f.blocks_run is False for f in out)


# --------------------------------------------------------------------------- #
# column_mappings override on the deterministic analysis
# --------------------------------------------------------------------------- #
def test_column_mappings_override_is_honored(tmp_path):
    # Bank file uses non-standard headers that auto-detect cannot resolve.
    bank = tmp_path / "bank.csv"
    bank.write_text(
        "when,memo,money\n"
        "2026-01-03,Payroll deposit,12500.00\n"
        "2026-01-05,Vendor payment,2300.50\n",
        encoding="utf-8",
    )
    ledger = _SD / "ledger.csv"

    # Without a mapping, auto-detection fails to find amount/date -> ValueError.
    with pytest.raises(ValueError):
        reconcile(str(bank), str(ledger))

    # With an explicit override the columns resolve and matching runs.
    out = reconcile(
        str(bank),
        str(ledger),
        column_mappings={"bank": {"date": "when", "amount": "money"}},
    )
    assert out.summary["bank_rows"] == 2
    # The two known rows match the ledger by amount + date.
    assert out.summary["matched"] >= 2


def test_column_mappings_none_keeps_current_behavior():
    # Passing None must be identical to the existing default-call behavior.
    out_default = reconcile(str(_SD / "bank.csv"), str(_SD / "ledger.csv"))
    out_none = reconcile(
        str(_SD / "bank.csv"), str(_SD / "ledger.csv"), column_mappings=None
    )
    assert out_default.summary == out_none.summary


# --------------------------------------------------------------------------- #
# Source-row preservation
# --------------------------------------------------------------------------- #
def test_source_rows_preserved_through_reconciliation():
    out = reconcile(str(_SD / "bank.csv"), str(_SD / "ledger.csv"))
    assert out.findings
    for f in out.findings:
        for ref in f.source_rows:
            # Row index is the 0-based positional row in the parsed frame.
            assert ref.row_index >= 0
            assert ref.table_name in {"bank", "ledger"}
            assert ref.column_names  # columns captured for traceability

    # A specific known row: the second Acme payment (2300.50 on 2026-01-18) is
    # bank row index 6 (0-based) in bank.csv and has no same-date ledger match,
    # so it surfaces as an UNMATCHED_BANK finding citing that exact source row.
    unmatched_bank_rows = {
        ref.row_index
        for f in out.findings
        if f.rule_used == "bank_item_without_ledger_match"
        for ref in f.source_rows
    }
    assert 6 in unmatched_bank_rows
