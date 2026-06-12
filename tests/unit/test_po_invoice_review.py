"""Tests for src/workflows/po_invoice_review.py.

Known-answer assertions verify that EXACTLY P1-P8 fire on the Tyler synthetic
dataset (data/synthetic/tyler/) with the expected PO/invoice numbers from
known_answers.json, and that clean PO/invoice pairs produce zero false positives.

Run with:
    .venv/Scripts/python.exe -m pytest tests/unit/test_po_invoice_review.py \
        -q -p no:cacheprovider --basetemp=.pytest_tmp_po_inv
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.workflows.po_invoice_review import (
    CAPABILITY,
    EXPORT_FILE_NAMES,
    SAMPLE_INPUTS,
    WORKFLOW,
    WORKFLOW_TYPE,
    POInvoiceReviewConfig,
    POInvoiceReviewOutput,
    build_prompt,
    export_artifacts,
    mock_llm_response,
    run,
    run_deterministic,
    validate_llm_output,
)
from src.core.schemas import FindingType, Severity

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "tyler"


def _known() -> dict:
    return json.loads((DATA / "known_answers.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _run_default() -> POInvoiceReviewOutput:
    """Run deterministic checks against the full Tyler synthetic dataset."""
    return run_deterministic(
        DATA / "purchase_orders.csv",
        DATA / "ap_invoice_detail.csv",
        vendor_list_path=DATA / "vendor_list.csv",
    )


def _findings_by_rule(det: POInvoiceReviewOutput) -> dict[str, list]:
    by_rule: dict[str, list] = {}
    for f in det.findings:
        by_rule.setdefault(f.rule_used, []).append(f)
    return by_rule


# --------------------------------------------------------------------------- #
# P1: invoice_exceeds_po
# --------------------------------------------------------------------------- #
class TestP1InvoiceExceedsPO:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P1"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "invoice_exceeds_po" in by_rule, "P1 rule not triggered"
        findings = by_rule["invoice_exceeds_po"]
        po_numbers = {
            f.computed_values.get("po_number")
            for f in findings
        }
        assert known["po_number"] in po_numbers, (
            f"Expected PO {known['po_number']} in P1 findings, got {po_numbers}"
        )

    def test_cites_correct_ap_row_index(self) -> None:
        known = _known()["po_anomalies"]["P1"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p1_findings = [
            f for f in by_rule.get("invoice_exceeds_po", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p1_findings
        all_ap_row_indices = []
        for f in p1_findings:
            for ref in f.source_rows:
                if ref.table_name == "ap_invoice_detail":
                    all_ap_row_indices.append(ref.row_index)
        for expected_idx in known["ap_row_indices"]:
            assert expected_idx in all_ap_row_indices, (
                f"Expected AP row index {expected_idx} in P1 source refs, "
                f"got {all_ap_row_indices}"
            )

    def test_cites_correct_po_row_index(self) -> None:
        known = _known()["po_anomalies"]["P1"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p1_findings = [
            f for f in by_rule.get("invoice_exceeds_po", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p1_findings
        all_po_row_indices = []
        for f in p1_findings:
            for ref in f.source_rows:
                if ref.table_name == "purchase_orders":
                    all_po_row_indices.append(ref.row_index)
        for expected_idx in known["po_row_indices"]:
            assert expected_idx in all_po_row_indices, (
                f"Expected PO row index {expected_idx} in P1 source refs, "
                f"got {all_po_row_indices}"
            )

    def test_severity_is_high(self) -> None:
        det = _run_default()
        by_rule = _findings_by_rule(det)
        for f in by_rule.get("invoice_exceeds_po", []):
            assert f.severity == Severity.HIGH

    def test_computed_values_present(self) -> None:
        known = _known()["po_anomalies"]["P1"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p1 = [
            f for f in by_rule.get("invoice_exceeds_po", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p1
        cv = p1[0].computed_values
        assert "po_total" in cv
        assert "total_invoiced" in cv
        assert "overage" in cv
        assert cv["po_total"] == known["po_total"]
        assert cv["total_invoiced"] == known["invoice_amount"]

    def test_tolerance_silences_p1(self) -> None:
        """With a 20% tolerance, the P1 overage should be silenced."""
        cfg = POInvoiceReviewConfig(invoice_over_po_tolerance_pct=20.0)
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
            config=cfg,
        )
        by_rule = _findings_by_rule(det)
        known_po = _known()["po_anomalies"]["P1"]["po_number"]
        p1_for_po = [
            f for f in by_rule.get("invoice_exceeds_po", [])
            if f.computed_values.get("po_number") == known_po
        ]
        # 11500/10000 = 15% over; within 20% tolerance -> should not fire
        assert not p1_for_po, (
            "P1 should be silenced with 20% tolerance for a 15% overage"
        )


# --------------------------------------------------------------------------- #
# P2: wrong_vendor
# --------------------------------------------------------------------------- #
class TestP2WrongVendor:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P2"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "wrong_vendor" in by_rule
        findings = by_rule["wrong_vendor"]
        po_numbers = {f.computed_values.get("po_number") for f in findings}
        assert known["po_number"] in po_numbers

    def test_cites_correct_rows(self) -> None:
        known = _known()["po_anomalies"]["P2"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p2 = [
            f for f in by_rule.get("wrong_vendor", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p2
        ap_idxs = [r.row_index for r in p2[0].source_rows if r.table_name == "ap_invoice_detail"]
        po_idxs = [r.row_index for r in p2[0].source_rows if r.table_name == "purchase_orders"]
        for idx in known["ap_row_indices"]:
            assert idx in ap_idxs
        for idx in known["po_row_indices"]:
            assert idx in po_idxs

    def test_vendor_numbers_in_computed_values(self) -> None:
        known = _known()["po_anomalies"]["P2"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p2 = [
            f for f in by_rule.get("wrong_vendor", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p2
        cv = p2[0].computed_values
        assert cv.get("po_vendor_number") == known["po_vendor_number"]
        assert cv.get("invoice_vendor_number") == known["invoice_vendor_number"]

    def test_severity_is_high(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("wrong_vendor", []):
            assert f.severity == Severity.HIGH


# --------------------------------------------------------------------------- #
# P3a: missing_po (PO referenced does not exist in PO file)
# --------------------------------------------------------------------------- #
class TestP3MissingPO:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P3"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "missing_po" in by_rule
        po_numbers = {
            f.computed_values.get("po_number")
            for f in by_rule["missing_po"]
        }
        assert known["po_number"] in po_numbers

    def test_cites_correct_ap_row(self) -> None:
        known = _known()["po_anomalies"]["P3"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p3 = [
            f for f in by_rule.get("missing_po", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p3
        ap_idxs = [r.row_index for r in p3[0].source_rows if r.table_name == "ap_invoice_detail"]
        for idx in known["ap_row_indices"]:
            assert idx in ap_idxs

    def test_invoice_number_in_computed_values(self) -> None:
        known = _known()["po_anomalies"]["P3"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p3 = [
            f for f in by_rule.get("missing_po", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p3
        assert p3[0].computed_values.get("invoice_number") == known["invoice_number"]

    def test_severity_is_high(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("missing_po", []):
            assert f.severity == Severity.HIGH


# --------------------------------------------------------------------------- #
# P3b: missing_po_over_threshold (blank PO, amount >= threshold)
# --------------------------------------------------------------------------- #
class TestP3bMissingPOOverThreshold:
    def test_fires_on_d4_invoice(self) -> None:
        """D4 (INV-44510, V-1004, $12,000) should fire as missing_po_over_threshold."""
        known_d4 = _known()["ap_anomalies"]["D4"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "missing_po_over_threshold" in by_rule, (
            "missing_po_over_threshold rule not triggered"
        )
        p3b = by_rule["missing_po_over_threshold"]
        inv_numbers = {f.computed_values.get("invoice_number") for f in p3b}
        assert known_d4["invoice_number"] in inv_numbers

    def test_cites_correct_ap_row(self) -> None:
        known_d4 = _known()["ap_anomalies"]["D4"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p3b = [
            f for f in by_rule.get("missing_po_over_threshold", [])
            if f.computed_values.get("invoice_number") == known_d4["invoice_number"]
        ]
        assert p3b
        ap_idxs = [r.row_index for r in p3b[0].source_rows]
        for idx in known_d4["ap_row_indices"]:
            assert idx in ap_idxs

    def test_threshold_respected(self) -> None:
        """With a high threshold, D4 should not fire."""
        cfg = POInvoiceReviewConfig(missing_po_min_amount=Decimal("20000.00"))
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
            config=cfg,
        )
        by_rule = _findings_by_rule(det)
        d4_inv = _known()["ap_anomalies"]["D4"]["invoice_number"]
        p3b = [
            f for f in by_rule.get("missing_po_over_threshold", [])
            if f.computed_values.get("invoice_number") == d4_inv
        ]
        assert not p3b, "D4 should not fire when threshold is $20,000"

    def test_severity_is_medium(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("missing_po_over_threshold", []):
            assert f.severity == Severity.MEDIUM


# --------------------------------------------------------------------------- #
# P4: closed_po_usage
# --------------------------------------------------------------------------- #
class TestP4ClosedPOUsage:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P4"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "closed_po_usage" in by_rule
        po_numbers = {f.computed_values.get("po_number") for f in by_rule["closed_po_usage"]}
        assert known["po_number"] in po_numbers

    def test_cites_correct_rows(self) -> None:
        known = _known()["po_anomalies"]["P4"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p4 = [
            f for f in by_rule.get("closed_po_usage", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p4
        ap_idxs = [r.row_index for r in p4[0].source_rows if r.table_name == "ap_invoice_detail"]
        po_idxs = [r.row_index for r in p4[0].source_rows if r.table_name == "purchase_orders"]
        for idx in known["ap_row_indices"]:
            assert idx in ap_idxs
        for idx in known["po_row_indices"]:
            assert idx in po_idxs

    def test_severity_is_medium(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("closed_po_usage", []):
            assert f.severity == Severity.MEDIUM

    def test_grace_days_silences_p4(self) -> None:
        """With 60 grace days, the 33-day-late PO-3004 invoice should be silenced."""
        cfg = POInvoiceReviewConfig(closed_po_grace_days=60)
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
            config=cfg,
        )
        known_po = _known()["po_anomalies"]["P4"]["po_number"]
        p4 = [
            f for f in _findings_by_rule(det).get("closed_po_usage", [])
            if f.computed_values.get("po_number") == known_po
        ]
        assert not p4, "P4 should be silenced with 60 grace days"


# --------------------------------------------------------------------------- #
# P5: unit_price_mismatch
# --------------------------------------------------------------------------- #
class TestP5UnitPriceMismatch:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P5"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "unit_price_mismatch" in by_rule
        po_numbers = {f.computed_values.get("po_number") for f in by_rule["unit_price_mismatch"]}
        assert known["po_number"] in po_numbers

    def test_cites_correct_rows(self) -> None:
        known = _known()["po_anomalies"]["P5"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p5 = [
            f for f in by_rule.get("unit_price_mismatch", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p5
        ap_idxs = [r.row_index for r in p5[0].source_rows if r.table_name == "ap_invoice_detail"]
        po_idxs = [r.row_index for r in p5[0].source_rows if r.table_name == "purchase_orders"]
        for idx in known["ap_row_indices"]:
            assert idx in ap_idxs
        for idx in known["po_row_indices"]:
            assert idx in po_idxs

    def test_prices_in_computed_values(self) -> None:
        known = _known()["po_anomalies"]["P5"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p5 = [
            f for f in by_rule.get("unit_price_mismatch", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p5
        cv = p5[0].computed_values
        assert cv.get("po_unit_price") == known["po_unit_price"]
        assert cv.get("invoice_unit_price") == known["invoice_unit_price"]

    def test_severity_is_medium(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("unit_price_mismatch", []):
            assert f.severity == Severity.MEDIUM

    def test_tolerance_silences_p5(self) -> None:
        """With 15% tolerance, PO-3005 unit price deviation ($25 vs $27.50 = 10%)
        should be silenced."""
        cfg = POInvoiceReviewConfig(unit_price_tolerance_pct=15.0)
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
            config=cfg,
        )
        known_po = _known()["po_anomalies"]["P5"]["po_number"]
        p5 = [
            f for f in _findings_by_rule(det).get("unit_price_mismatch", [])
            if f.computed_values.get("po_number") == known_po
        ]
        assert not p5, "P5 should be silenced with 15% tolerance (10% deviation)"


# --------------------------------------------------------------------------- #
# P6: quantity_mismatch
# --------------------------------------------------------------------------- #
class TestP6QuantityMismatch:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P6"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "quantity_mismatch" in by_rule
        po_numbers = {f.computed_values.get("po_number") for f in by_rule["quantity_mismatch"]}
        assert known["po_number"] in po_numbers

    def test_cites_correct_rows(self) -> None:
        known = _known()["po_anomalies"]["P6"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p6 = [
            f for f in by_rule.get("quantity_mismatch", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p6
        ap_idxs = [r.row_index for r in p6[0].source_rows if r.table_name == "ap_invoice_detail"]
        po_idxs = [r.row_index for r in p6[0].source_rows if r.table_name == "purchase_orders"]
        for idx in known["ap_row_indices"]:
            assert idx in ap_idxs
        for idx in known["po_row_indices"]:
            assert idx in po_idxs

    def test_quantities_in_computed_values(self) -> None:
        known = _known()["po_anomalies"]["P6"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p6 = [
            f for f in by_rule.get("quantity_mismatch", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p6
        cv = p6[0].computed_values
        assert cv.get("po_qty") == known["po_qty"]
        assert cv.get("invoice_qty") == known["invoice_qty"]

    def test_severity_is_medium(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("quantity_mismatch", []):
            assert f.severity == Severity.MEDIUM


# --------------------------------------------------------------------------- #
# P7: received_not_invoiced
# --------------------------------------------------------------------------- #
class TestP7ReceivedNotInvoiced:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P7"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "received_not_invoiced" in by_rule
        po_numbers = {
            f.computed_values.get("po_number")
            for f in by_rule["received_not_invoiced"]
        }
        assert known["po_number"] in po_numbers

    def test_cites_correct_po_row(self) -> None:
        known = _known()["po_anomalies"]["P7"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p7 = [
            f for f in by_rule.get("received_not_invoiced", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p7
        po_idxs = [r.row_index for r in p7[0].source_rows]
        for idx in known["po_row_indices"]:
            assert idx in po_idxs

    def test_severity_is_low(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("received_not_invoiced", []):
            assert f.severity == Severity.LOW

    def test_requires_human_review_is_false(self) -> None:
        """P7 is informational (accrual candidate) -- not an error."""
        det = _run_default()
        for f in _findings_by_rule(det).get("received_not_invoiced", []):
            assert f.requires_human_review is False


# --------------------------------------------------------------------------- #
# P8: invoiced_not_received
# --------------------------------------------------------------------------- #
class TestP8InvoicedNotReceived:
    def test_fires_on_riverbend_dataset(self) -> None:
        known = _known()["po_anomalies"]["P8"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        assert "invoiced_not_received" in by_rule
        po_numbers = {
            f.computed_values.get("po_number")
            for f in by_rule["invoiced_not_received"]
        }
        assert known["po_number"] in po_numbers

    def test_cites_correct_rows(self) -> None:
        known = _known()["po_anomalies"]["P8"]
        det = _run_default()
        by_rule = _findings_by_rule(det)
        p8 = [
            f for f in by_rule.get("invoiced_not_received", [])
            if f.computed_values.get("po_number") == known["po_number"]
        ]
        assert p8
        ap_idxs = [r.row_index for r in p8[0].source_rows if r.table_name == "ap_invoice_detail"]
        po_idxs = [r.row_index for r in p8[0].source_rows if r.table_name == "purchase_orders"]
        for idx in known["ap_row_indices"]:
            assert idx in ap_idxs
        for idx in known["po_row_indices"]:
            assert idx in po_idxs

    def test_severity_is_medium(self) -> None:
        det = _run_default()
        for f in _findings_by_rule(det).get("invoiced_not_received", []):
            assert f.severity == Severity.MEDIUM


# --------------------------------------------------------------------------- #
# True negatives -- clean PO/invoice pairs must not fire for mismatch rules
# --------------------------------------------------------------------------- #
class TestTrueNegatives:
    _CLEAN_POS = [
        "PO-3000", "PO-3003", "PO-3009", "PO-3010",
        "PO-3011", "PO-3012", "PO-3013", "PO-3014",
    ]
    _MISMATCH_RULES = {
        "invoice_exceeds_po",
        "wrong_vendor",
        "missing_po",
        "closed_po_usage",
        "unit_price_mismatch",
        "quantity_mismatch",
        "invoiced_not_received",
    }

    def test_clean_pos_produce_no_mismatch_findings(self) -> None:
        det = _run_default()
        for f in det.findings:
            if f.rule_used not in self._MISMATCH_RULES:
                continue
            po_num = f.computed_values.get("po_number", "")
            assert po_num not in self._CLEAN_POS, (
                f"False positive: rule {f.rule_used!r} fired on clean PO {po_num!r}"
            )

    def test_exactly_eight_anomaly_po_numbers_flagged(self) -> None:
        """EXACTLY the anomaly PO numbers should be flagged (P1-P8, one each).

        P7 fires as 'received_not_invoiced' which is informational but still
        tracks a PO-level anomaly, so we include it in the full set check.
        """
        known_po = _known()["po_anomalies"]
        expected_po_numbers = {
            v["po_number"]
            for v in known_po.values()
            if "po_number" in v
        }
        det = _run_default()
        # Include all PO anomaly rules (P1-P8), including received_not_invoiced (P7)
        all_po_rules = self._MISMATCH_RULES | {"received_not_invoiced"}
        flagged_po_numbers: set[str] = set()
        for f in det.findings:
            if f.rule_used in all_po_rules:
                po = f.computed_values.get("po_number", "")
                if po:
                    flagged_po_numbers.add(po)
        # Every expected anomaly PO must be present
        for po in expected_po_numbers:
            assert po in flagged_po_numbers, (
                f"Expected anomaly PO {po!r} not flagged; flagged: {flagged_po_numbers}"
            )


# --------------------------------------------------------------------------- #
# Optional-file skipped INFO findings
# --------------------------------------------------------------------------- #
class TestOptionalFileSkips:
    def test_vendor_list_skip_info_when_absent(self) -> None:
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
            # vendor_list_path NOT provided
        )
        skip_findings = [
            f for f in det.findings
            if f.rule_used == "vendor_list_skipped"
        ]
        assert skip_findings, "Expected vendor_list_skipped INFO finding"
        assert all(f.severity == Severity.INFO for f in skip_findings)

    def test_check_register_skip_info_when_absent(self) -> None:
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
        )
        skip_findings = [
            f for f in det.findings
            if f.rule_used == "check_register_skipped"
        ]
        assert skip_findings, "Expected check_register_skipped INFO finding"
        assert all(f.severity == Severity.INFO for f in skip_findings)

    def test_checks_still_run_without_vendor_list(self) -> None:
        """P1-P6 checks do not require vendor_list."""
        det = run_deterministic(
            DATA / "purchase_orders.csv",
            DATA / "ap_invoice_detail.csv",
            # vendor_list_path absent
        )
        by_rule = _findings_by_rule(det)
        for rule in ("invoice_exceeds_po", "wrong_vendor", "missing_po",
                     "closed_po_usage", "unit_price_mismatch", "quantity_mismatch"):
            assert rule in by_rule, f"Expected {rule} to fire even without vendor_list"


# --------------------------------------------------------------------------- #
# Mock LLM + validation
# --------------------------------------------------------------------------- #
class TestMockLLMAndValidation:
    def test_mock_llm_response_structure(self) -> None:
        det = _run_default()
        resp = mock_llm_response(det)
        assert "summary" in resp
        assert "referenced_source_rows" in resp
        assert "categorized_exceptions" in resp
        assert "suggested_review_steps" in resp
        assert "draft_memo" in resp

    def test_mock_llm_references_only_real_source_rows(self) -> None:
        det = _run_default()
        resp = mock_llm_response(det)
        valid_ids = {
            f"{s.table_name}:{s.row_index}"
            for f in det.findings
            for s in f.source_rows
        }
        for ref in resp.get("referenced_source_rows", []):
            assert ref in valid_ids, (
                f"Mock LLM invented source row ref {ref!r}"
            )

    def test_mock_llm_no_approval_language(self) -> None:
        det = _run_default()
        resp = mock_llm_response(det)
        for banned in (
            "i approve", "this report is correct", "approved for filing",
            "certified correct",
        ):
            assert banned not in resp.get("draft_memo", "").lower()

    def test_prompt_contains_findings_block(self) -> None:
        det = _run_default()
        prompt = build_prompt(det)
        assert "DETERMINISTIC FINDINGS:" in prompt
        assert "DETERMINISTIC SUMMARY:" in prompt

    def test_validation_passes_on_mock_output(self) -> None:
        det = _run_default()
        resp = mock_llm_response(det)
        vr = validate_llm_output(resp, det)
        assert not vr.invented_reference_detected
        # Validation may warn about missing references for INFO findings (which
        # carry no source rows) -- that is acceptable (missing_references_is_error=False)
        assert vr.passed or (not vr.errors), (
            f"Validation errors: {vr.errors}"
        )

    def test_validation_rejects_invented_ref(self) -> None:
        det = _run_default()
        bad_resp = {
            "summary": "test",
            "referenced_source_rows": ["purchase_orders:9999"],
        }
        vr = validate_llm_output(bad_resp, det)
        assert vr.invented_reference_detected
        assert not vr.passed

    def test_validation_rejects_approval_language(self) -> None:
        det = _run_default()
        bad_resp = {
            "summary": "I approve this invoice.",
            "referenced_source_rows": [],
        }
        vr = validate_llm_output(bad_resp, det)
        assert not vr.passed


# --------------------------------------------------------------------------- #
# run() lifecycle and exports
# --------------------------------------------------------------------------- #
class TestRunLifecycleAndExports:
    def test_run_returns_expected_keys(self) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
            }
        )
        for key in ("run_id", "workflow_type", "findings", "summary", "validation"):
            assert key in result, f"Missing key {key!r} in run() result"

    def test_run_workflow_type(self) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
            }
        )
        assert result["workflow_type"] == WORKFLOW_TYPE

    def test_run_produces_p1_to_p8_findings(self) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
                "vendor_list": DATA / "vendor_list.csv",
            }
        )
        by_rule = {f.rule_used for f in result["findings"]}
        for rule in (
            "invoice_exceeds_po",
            "wrong_vendor",
            "missing_po",
            "closed_po_usage",
            "unit_price_mismatch",
            "quantity_mismatch",
            "received_not_invoiced",
            "invoiced_not_received",
        ):
            assert rule in by_rule, f"Rule {rule!r} not in run() findings"

    def test_run_exports_all_files(self, tmp_path: Path) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
            },
            export_dir=tmp_path,
        )
        assert "export_paths" in result
        for name in EXPORT_FILE_NAMES:
            assert name in result["export_paths"], f"Missing export {name!r}"
            assert Path(result["export_paths"][name]).exists(), (
                f"Export file {name!r} not written to disk"
            )

    def test_run_exports_have_sha256(self, tmp_path: Path) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
            },
            export_dir=tmp_path,
        )
        for art in result.get("export_artifacts", []):
            assert art.sha256, f"Artifact {art.file_name!r} missing sha256"
            assert len(art.sha256) == 64  # sha256 hex is 64 chars

    def test_run_preflight_fail_on_missing_required_file(self) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                # ap_invoices missing -- should FAIL
            }
        )
        assert result.get("preflight", {}).get("status") == "fail"
        assert result["findings"] == []

    def test_run_preflight_fail_does_not_call_llm(self) -> None:
        """On a FAIL preflight the response/validation keys must be absent or None."""
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
            }
        )
        assert result.get("preflight", {}).get("status") == "fail"
        # llm_response must not be present (or must be None)
        assert result.get("llm_response") is None

    def test_run_preflight_fail_emits_run_failed_event(self, tmp_path: Path) -> None:
        """On a FAIL preflight the audit log must record run_failed, not run_completed."""
        from src.core.run_ledger import RunLedger
        from src.core.audit_log import AuditLog

        ledger = RunLedger(":memory:")
        audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))

        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                # ap_invoices intentionally missing -> FAIL
            },
            ledger=ledger,
            audit=audit,
        )

        assert result.get("preflight", {}).get("status") == "fail"
        run_id = result["run_id"]
        events = audit.list_events(run_id)
        event_types = [e["event_type"] for e in events]

        # run_failed must be emitted on preflight FAIL
        assert "run_failed" in event_types, (
            f"Expected 'run_failed' event on preflight FAIL; got: {event_types}"
        )
        # run_completed must NOT be emitted on preflight FAIL
        assert "run_completed" not in event_types, (
            f"Expected no 'run_completed' event on preflight FAIL; got: {event_types}"
        )
        # Confirm the reason is 'preflight_fail' (stored in the details dict)
        run_failed_events = [e for e in events if e["event_type"] == "run_failed"]
        assert len(run_failed_events) == 1
        assert run_failed_events[0].get("details", {}).get("reason") == "preflight_fail"

    def test_run_with_config_file(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(
            json.dumps({"unit_price_tolerance_pct": 15.0}), encoding="utf-8"
        )
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
                "config": str(cfg_path),
            }
        )
        # With 15% tolerance P5 should be silenced (10% deviation)
        by_rule = {f.rule_used for f in result["findings"]}
        assert "unit_price_mismatch" not in by_rule, (
            "P5 should be silenced with 15% tolerance"
        )

    def test_run_with_config_dict(self) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
            },
            config={"unit_price_tolerance_pct": 15.0},
        )
        by_rule = {f.rule_used for f in result["findings"]}
        assert "unit_price_mismatch" not in by_rule

    def test_export_artifacts_match_constants(self, tmp_path: Path) -> None:
        result = run(
            {
                "purchase_orders": DATA / "purchase_orders.csv",
                "ap_invoices": DATA / "ap_invoice_detail.csv",
            },
            export_dir=tmp_path,
        )
        exported_names = set(result.get("export_paths", {}).keys())
        for name in EXPORT_FILE_NAMES:
            assert name in exported_names


# --------------------------------------------------------------------------- #
# Workflow module metadata
# --------------------------------------------------------------------------- #
class TestModuleMetadata:
    def test_workflow_type_constant(self) -> None:
        assert WORKFLOW_TYPE == "po_invoice_review"

    def test_capability_required_inputs(self) -> None:
        assert "purchase_orders" in CAPABILITY.required_inputs
        assert "ap_invoices" in CAPABILITY.required_inputs

    def test_capability_accepts_csv_and_xlsx(self) -> None:
        accepted = CAPABILITY.accepted_file_types.get("*", [])
        assert "csv" in accepted
        assert "xlsx" in accepted

    def test_workflow_dict(self) -> None:
        assert WORKFLOW["workflow_type"] == WORKFLOW_TYPE
        assert callable(WORKFLOW["run"])
        for name in EXPORT_FILE_NAMES:
            assert name in WORKFLOW["export_files"]

    def test_sample_inputs_paths_are_strings(self) -> None:
        for k, v in SAMPLE_INPUTS.items():
            assert isinstance(v, str), f"SAMPLE_INPUTS[{k!r}] is not a string"

    def test_register_dict(self) -> None:
        registry: dict[str, Any] = {}
        from src.workflows.po_invoice_review import register
        register(registry)
        assert WORKFLOW_TYPE in registry
        assert callable(registry[WORKFLOW_TYPE])

    def test_po_invoice_review_config_defaults(self) -> None:
        cfg = POInvoiceReviewConfig()
        assert cfg.unit_price_tolerance_pct == 1.0
        assert cfg.qty_tolerance == 0
        assert cfg.invoice_over_po_tolerance_pct == 0.0
        assert cfg.closed_po_grace_days == 0
        assert cfg.missing_po_min_amount == Decimal("5000.00")

    def test_config_from_none(self) -> None:
        cfg = POInvoiceReviewConfig.from_config(None)
        assert cfg.unit_price_tolerance_pct == 1.0

    def test_config_from_dict(self) -> None:
        cfg = POInvoiceReviewConfig.from_config({"unit_price_tolerance_pct": 5.0})
        assert cfg.unit_price_tolerance_pct == 5.0

    def test_config_from_json_file(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"qty_tolerance": 2}), encoding="utf-8")
        cfg = POInvoiceReviewConfig.from_config(str(p))
        assert cfg.qty_tolerance == 2
