"""Unit tests for the shared preflight / capability layer."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.core.preflight import (
    HIGH_CONFIDENCE_THRESHOLD,
    build_column_mappings,
    profile_input,
    render_preflight_summary_md,
    run_preflight,
)
from src.core.schemas import (
    CapabilitySpec,
    PreflightConditionCode,
    PreflightFinding,
    PreflightStatus,
    Severity,
)
from src.normalize.cleaning import (
    amount_parse_confidence,
    best_semantic_column,
    clean_amount_series,
    date_parse_confidence,
    detect_footer_total_rows,
    detect_repeated_header_rows,
    normalize_columns,
)


# --------------------------------------------------------------------------- #
# Fixtures: tiny inline CSVs
# --------------------------------------------------------------------------- #
def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


CLEAN_BANK = (
    "Date,Amount,Description\n"
    "2024-01-01,100.00,Deposit A\n"
    "2024-01-02,250.50,Check 1001\n"
    "2024-01-03,75.25,ACH Payment\n"
)

CLEAN_LEDGER = (
    "Date,Amount,Description\n"
    "2024-01-01,100.00,Deposit A\n"
    "2024-01-02,250.50,Check 1001\n"
)

MESSY_BANK = (
    "Date,Amount,Description\n"
    "01/05/2024,\"$1,200.00\",Vendor X\n"
    "01/06/2024,($350.00),Refund Y\n"
    "Date,Amount,Description\n"  # repeated header row
    "01/07/2024,\"$2,000.00\",Vendor Z\n"
    "Total,\"$2,850.00\",\n"  # footer total row
)


def _spec(**over) -> CapabilitySpec:
    base = dict(
        workflow_type="bank_reconciliation",
        required_inputs=["bank", "ledger"],
        optional_inputs=[],
        accepted_file_types={"*": ["csv"]},
        required_semantic_columns={
            "bank": ["date", "amount"],
            "ledger": ["date", "amount"],
        },
        optional_semantic_columns={"bank": ["description"]},
        supported_patterns=["exact_match", "timing_difference", "duplicates"],
    )
    base.update(over)
    return CapabilitySpec(**base)


# --------------------------------------------------------------------------- #
# profile_input
# --------------------------------------------------------------------------- #
def test_profile_input_clean(tmp_path):
    p = _write(tmp_path, "bank.csv", CLEAN_BANK)
    prof = profile_input("bank", p, {"*": ["csv"]})
    assert prof.present
    assert prof.row_count == 3
    assert prof.detected_columns == ["date", "amount", "description"]
    assert prof.parse_confidence["date"] == 1.0
    assert prof.parse_confidence["amount"] == 1.0
    assert prof.repeated_header_rows == []
    assert prof.footer_total_rows == []


def test_profile_input_messy(tmp_path):
    p = _write(tmp_path, "bank.csv", MESSY_BANK)
    prof = profile_input("bank", p, {"*": ["csv"]})
    assert prof.present
    # repeated header row is at positional index 2
    assert 2 in prof.repeated_header_rows
    # footer "Total ..." row at the end is detected
    assert prof.footer_total_rows
    # currency/commas/parens still parse to high amount confidence
    assert prof.parse_confidence["amount"] >= 0.75


def test_profile_input_missing(tmp_path):
    prof = profile_input("bank", tmp_path / "nope.csv", {"*": ["csv"]})
    assert prof.present is False
    assert prof.row_count == 0


# --------------------------------------------------------------------------- #
# parse-confidence math + cleaning helpers
# --------------------------------------------------------------------------- #
def test_date_parse_confidence_math():
    s = pd.Series(["2024-01-01", "not a date", "", "01/02/2024"])
    # 3 non-blank, 2 parse -> 2/3
    assert date_parse_confidence(s) == pytest.approx(2 / 3)


def test_amount_parse_confidence_math():
    s = pd.Series(["$1,000.00", "(50.00)", "abc", ""])
    # 3 non-blank, 2 parse -> 2/3
    assert amount_parse_confidence(s) == pytest.approx(2 / 3)


def test_clean_amount_series_preserves_rows():
    s = pd.Series(["$1,200.00", "(350.00)", "", "−5.00"])
    out = clean_amount_series(s)
    assert len(out) == len(s)
    assert out.iloc[0] == "1200.00"
    assert out.iloc[1] == "-350.00"
    assert out.iloc[2] == ""  # blank preserved
    assert out.iloc[3] == "-5.00"  # unicode minus normalized


def test_detect_repeated_header_and_footer():
    df = normalize_columns(
        pd.DataFrame(
            {
                "date": ["2024-01-01", "date", "2024-01-03", "Total"],
                "amount": ["10", "amount", "30", "40"],
            }
        )
    )
    assert 1 in detect_repeated_header_rows(df)
    assert 3 in detect_footer_total_rows(df)


# --------------------------------------------------------------------------- #
# best_semantic_column
# --------------------------------------------------------------------------- #
def test_best_semantic_column_clean():
    df = normalize_columns(
        pd.DataFrame(
            {
                "Date": ["2024-01-01", "2024-01-02"],
                "Amount": ["100.00", "200.00"],
                "Description": ["a", "b"],
            }
        )
    )
    col, conf, ranked = best_semantic_column(df, "date")
    assert col == "date"
    assert conf >= HIGH_CONFIDENCE_THRESHOLD


def test_best_semantic_column_ambiguous():
    # Two equally plausible amount columns -> lowered confidence.
    df = normalize_columns(
        pd.DataFrame(
            {
                "debit": ["100.00", "200.00"],
                "credit": ["50.00", "75.00"],
            }
        )
    )
    col, conf, ranked = best_semantic_column(df, "amount")
    assert col is not None
    assert len(ranked) >= 2
    assert conf < HIGH_CONFIDENCE_THRESHOLD


# --------------------------------------------------------------------------- #
# build_column_mappings
# --------------------------------------------------------------------------- #
def test_build_column_mappings_auto(tmp_path):
    p = _write(tmp_path, "bank.csv", CLEAN_BANK)
    spec = _spec(required_inputs=["bank"], required_semantic_columns={"bank": ["date", "amount"]})
    profiles = {"bank": profile_input("bank", p, {"*": ["csv"]})}
    mappings = build_column_mappings(spec, profiles)
    by_sem = {m.semantic_name: m for m in mappings}
    assert by_sem["date"].source == "auto"
    assert by_sem["date"].mapped_column == "date"
    assert by_sem["amount"].source == "auto"


def test_build_column_mappings_human_override(tmp_path):
    p = _write(tmp_path, "bank.csv", CLEAN_BANK)
    spec = _spec(required_inputs=["bank"], required_semantic_columns={"bank": ["amount"]})
    profiles = {"bank": profile_input("bank", p, {"*": ["csv"]})}
    mappings = build_column_mappings(
        spec, profiles, approved_mappings={"bank": {"amount": "Description"}}
    )
    m = mappings[0]
    assert m.source == "human"
    assert m.mapped_column == "description"
    assert m.confidence == 1.0


def test_build_column_mappings_unmapped(tmp_path):
    csv = "foo,bar\n1,2\n"
    p = _write(tmp_path, "bank.csv", csv)
    spec = _spec(required_inputs=["bank"], required_semantic_columns={"bank": ["date"]})
    profiles = {"bank": profile_input("bank", p, {"*": ["csv"]})}
    mappings = build_column_mappings(spec, profiles)
    assert mappings[0].source == "unmapped"
    assert mappings[0].mapped_column is None


# --------------------------------------------------------------------------- #
# run_preflight: PASS / PARTIAL / FAIL
# --------------------------------------------------------------------------- #
def test_run_preflight_pass(tmp_path):
    bank = _write(tmp_path, "bank.csv", CLEAN_BANK)
    ledger = _write(tmp_path, "ledger.csv", CLEAN_LEDGER)
    report = run_preflight(_spec(), {"bank": bank, "ledger": ledger})
    assert report.status == PreflightStatus.PASS
    assert report.llm_allowed is True
    assert report.partial is False
    assert report.supported_checks  # populated on PASS
    # markdown renders without error
    assert "Preflight" in render_preflight_summary_md(report)


def test_run_preflight_fail_missing_file(tmp_path):
    bank = _write(tmp_path, "bank.csv", CLEAN_BANK)
    report = run_preflight(_spec(), {"bank": bank})  # ledger missing
    assert report.status == PreflightStatus.FAIL
    assert report.llm_allowed is False
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.MISSING_REQUIRED_FILE in codes
    assert any(f.blocks_run for f in report.findings)
    assert report.next_steps


def test_run_preflight_fail_unsupported_type(tmp_path):
    bank = _write(tmp_path, "bank.txt", CLEAN_BANK)
    ledger = _write(tmp_path, "ledger.csv", CLEAN_LEDGER)
    report = run_preflight(_spec(), {"bank": bank, "ledger": ledger})
    assert report.status == PreflightStatus.FAIL
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.UNSUPPORTED_FILE_TYPE in codes


def test_run_preflight_fail_missing_required_column(tmp_path):
    bank = _write(tmp_path, "bank.csv", "foo,bar\n1,2\n")
    ledger = _write(tmp_path, "ledger.csv", CLEAN_LEDGER)
    report = run_preflight(_spec(), {"bank": bank, "ledger": ledger})
    assert report.status == PreflightStatus.FAIL
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.MISSING_REQUIRED_COLUMN in codes
    assert PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION in codes


def test_run_preflight_fail_low_confidence_required(tmp_path):
    # date column present by name but values do not parse -> low confidence.
    bad = (
        "Date,Amount,Description\n"
        "xx,100.00,a\n"
        "yy,200.00,b\n"
        "zz,300.00,c\n"
    )
    bank = _write(tmp_path, "bank.csv", bad)
    ledger = _write(tmp_path, "ledger.csv", CLEAN_LEDGER)
    report = run_preflight(_spec(), {"bank": bank, "ledger": ledger})
    assert report.status == PreflightStatus.FAIL
    codes = {f.code for f in report.findings}
    assert PreflightConditionCode.LOW_DATE_PARSE_CONFIDENCE in codes


def test_run_preflight_partial_with_domain_finding(tmp_path):
    bank = _write(tmp_path, "bank.csv", CLEAN_BANK)
    ledger = _write(tmp_path, "ledger.csv", CLEAN_LEDGER)

    def detect_conditions(profiles, mappings, inputs, config):
        return [
            PreflightFinding(
                code=PreflightConditionCode.POSSIBLE_SIGN_CONVENTION_MISMATCH,
                severity=Severity.MEDIUM,
                message="Bank credits may use opposite sign from ledger.",
                affected_input="bank",
                suggested_action="Confirm sign convention before reconciling.",
                blocks_run=False,
            )
        ]

    report = run_preflight(
        _spec(), {"bank": bank, "ledger": ledger}, detect_conditions=detect_conditions
    )
    assert report.status == PreflightStatus.PARTIAL
    assert report.llm_allowed is True
    assert report.partial is True
    assert (
        PreflightConditionCode.POSSIBLE_SIGN_CONVENTION_MISMATCH.value
        in report.unsupported_conditions
    )


def test_llm_allowed_false_only_on_fail(tmp_path):
    bank = _write(tmp_path, "bank.csv", CLEAN_BANK)
    ledger = _write(tmp_path, "ledger.csv", CLEAN_LEDGER)
    ok = run_preflight(_spec(), {"bank": bank, "ledger": ledger})
    assert ok.llm_allowed is True
    fail = run_preflight(_spec(), {"bank": bank})
    assert fail.llm_allowed is False
    assert fail.status == PreflightStatus.FAIL
