"""Preflight / capability tests for the Guided Freeform workflow.

Exercises the workflow's CAPABILITY spec, its light DRAFT-ONLY domain detector
(detect_conditions), the column_mappings override on the deterministic
run_deterministic(), and source-row preservation. Uses the shared run_preflight
engine from src.core.preflight against tiny synthetic STRUCTURED-request
fixtures (freeform inputs are structured fields, not tabular files).

Run only this file:
    .venv\\Scripts\\python.exe -m pytest tests/unit/test_freeform_preflight.py -q \\
        --basetemp=.pytmp/freeform
"""
from __future__ import annotations

import json
from pathlib import Path

from src.core.preflight import run_preflight
from src.core.schemas import PreflightConditionCode, PreflightStatus
from src.workflows.freeform import (
    CAPABILITY,
    FreeformRequest,
    detect_conditions,
    run_deterministic,
)

_REPO = Path(__file__).resolve().parents[2]
_MESSY = _REPO / "data" / "synthetic" / "freeform" / "messy"


def _load(name: str) -> dict:
    return json.loads((_MESSY / name).read_text(encoding="utf-8"))


def _clean_inputs() -> dict:
    return _load("pass_clean_request.json")


def _fail_inputs() -> dict:
    return _load("fail_missing_sensitivity_request.json")


def _partial_inputs() -> dict:
    return _load("partial_authoritative_request.json")


def _codes(report) -> set[str]:
    return {f.code.value for f in report.findings}


# --------------------------------------------------------------------------- #
# CAPABILITY spec
# --------------------------------------------------------------------------- #
def test_capability_declares_no_required_tabular_columns():
    assert CAPABILITY.workflow_type == "freeform"
    # Freeform is the structured, draft-only mode: no required tabular FILES and
    # no required tabular semantic columns.
    assert CAPABILITY.required_inputs == []
    assert CAPABILITY.required_semantic_columns == {}
    assert CAPABILITY.optional_semantic_columns == {}
    # Accepted file types for optional uploads are the lightweight doc types.
    accepted = CAPABILITY.accepted_file_types["*"]
    for ext in ("csv", "xlsx", "txt", "md", "pdf", "json"):
        assert ext in accepted
    # Supported = structured request logging + draft; unsupported = authoritative
    # answers / taking over a failed formal workflow.
    assert "structured_request_logging" in CAPABILITY.supported_patterns
    assert "draft_only_plain_language_output" in CAPABILITY.supported_patterns
    assert "authoritative_financial_answers" in CAPABILITY.unsupported_patterns
    assert (
        "taking_over_a_failed_formal_workflow" in CAPABILITY.unsupported_patterns
    )


# --------------------------------------------------------------------------- #
# run_preflight status outcomes
# --------------------------------------------------------------------------- #
def test_preflight_pass_on_clean_request():
    report = run_preflight(
        CAPABILITY, _clean_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.PASS
    assert report.llm_allowed is True
    assert report.partial is False
    assert report.findings == []
    assert report.supported_checks  # populated on PASS


def test_preflight_fail_when_sensitivity_not_confirmed():
    report = run_preflight(
        CAPABILITY, _fail_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.FAIL
    assert report.llm_allowed is False  # the LLM must NOT be called on FAIL
    assert any(f.blocks_run for f in report.findings)
    assert PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION.value in _codes(report)
    assert report.next_steps  # concrete next steps surfaced
    assert report.supported_checks == []  # empty on FAIL


def test_preflight_fail_when_task_type_missing():
    inputs = {"sensitivity_confirmation": True, "task_type": ""}
    report = run_preflight(
        CAPABILITY, inputs, detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.FAIL
    assert any(
        f.code == PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION
        and f.affected_input == "task_type"
        and f.blocks_run
        for f in report.findings
    )


def test_preflight_partial_on_authoritative_wording():
    report = run_preflight(
        CAPABILITY, _partial_inputs(), detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.PARTIAL
    assert report.llm_allowed is True
    assert report.partial is True
    assert (
        PreflightConditionCode.POSSIBLE_UNKNOWN_REPORT_STRUCTURE.value
        in _codes(report)
    )
    # possible_* domain conditions are advisory only (PARTIAL, never FAIL).
    assert not any(f.blocks_run for f in report.findings)


# --------------------------------------------------------------------------- #
# detect_conditions in isolation
# --------------------------------------------------------------------------- #
def test_detect_conditions_clean_returns_empty():
    out = detect_conditions({}, {}, _clean_inputs(), None)
    assert out == []


def test_detect_conditions_blocks_on_missing_sensitivity():
    out = detect_conditions({}, {}, _fail_inputs(), None)
    assert any(
        f.code == PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION
        and f.affected_input == "sensitivity_confirmation"
        and f.blocks_run
        for f in out
    )


def test_detect_conditions_flags_authoritative_request():
    out = detect_conditions({}, {}, _partial_inputs(), None)
    assert any(
        f.code == PreflightConditionCode.POSSIBLE_UNKNOWN_REPORT_STRUCTURE
        for f in out
    )
    # Advisory only -> never blocks the run.
    assert all(f.blocks_run is False for f in out)


# --------------------------------------------------------------------------- #
# column_mappings override on the deterministic analysis
# --------------------------------------------------------------------------- #
def test_column_mappings_override_relabels_file_metadata():
    req = FreeformRequest(
        task_type="vendor_review",
        uploaded_files=[{"file_name": "export.csv", "as_of": "2026-01-31"}],
        sensitivity_confirmation=True,
    )
    # Without an override the recorded metadata is unchanged.
    base = run_deterministic(req)
    file_finding = base.findings[1]
    assert "date" not in file_finding.source_rows[0].source_values

    # With an override the human-approved semantic key is added; the original
    # value is copied and the positional row_index is preserved.
    mapped = run_deterministic(
        req, column_mappings={"freeform_files": {"date": "as_of"}}
    )
    ref = mapped.findings[1].source_rows[0]
    assert ref.source_values["date"] == "2026-01-31"
    assert ref.row_index == 0  # SourceRowRef anchor preserved


def test_column_mappings_none_keeps_current_behavior():
    req = FreeformRequest(
        task_type="vendor_review",
        uploaded_files=[{"file_name": "export.csv"}],
        sensitivity_confirmation=True,
    )
    default = run_deterministic(req)
    explicit_none = run_deterministic(req, column_mappings=None)
    assert default.summary == explicit_none.summary
    assert (
        default.findings[1].source_rows[0].source_values
        == explicit_none.findings[1].source_rows[0].source_values
    )


# --------------------------------------------------------------------------- #
# Source-row preservation
# --------------------------------------------------------------------------- #
def test_source_rows_preserved_through_deterministic():
    req = FreeformRequest(
        task_type="grant_summary",
        uploaded_files=[
            {"file_name": "a.csv"},
            {"file_name": "b.csv"},
        ],
        sensitivity_confirmation=True,
    )
    out = run_deterministic(req)
    # request finding + one finding per uploaded file.
    assert len(out.findings) == 3
    # The request row is index 0; the two files are positional indices 0 and 1.
    request_ref = out.findings[0].source_rows[0]
    assert request_ref.table_name == "freeform_request"
    assert request_ref.row_index == 0
    file_indices = [
        f.source_rows[0].row_index
        for f in out.findings
        if f.source_rows[0].table_name == "freeform_files"
    ]
    assert file_indices == [0, 1]
