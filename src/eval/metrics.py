"""Evaluation metrics + known-answer expectations (Phase 7).

This module is pure / deterministic: given a workflow's uniform run result it
derives the spec's evaluation metrics, and it holds the bundled known-answer
expectations for each MVP workflow's synthetic dataset.

Spec (Phase 7 "Evaluation metrics") tracks:
    Transactions processed
    Rows matched
    Rows unmatched
    Findings generated
    Validation warnings
    LLM outputs rejected
    Manual overrides
    Export packets generated
    Runtime per workflow

All counting here is DETERMINISTIC code only. The metrics are derived from the
deterministic findings + summary + validation result that the workflow already
produced; nothing is asked of the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Finding-type classification (so metrics work across every workflow uniformly)
# --------------------------------------------------------------------------- #
# A "matched" row is one the deterministic matcher tied together; an "unmatched"
# row is an exception the matcher could not resolve. These map onto the shared
# FindingType enum values produced by the workflow modules.
_MATCHED_TYPES = {"matched"}
_UNMATCHED_TYPES = {
    "unmatched_bank",
    "unmatched_ledger",
    "timing_difference",
    "duplicate",
}


# --------------------------------------------------------------------------- #
# Metrics container
# --------------------------------------------------------------------------- #
@dataclass
class WorkflowMetrics:
    """The Phase-7 evaluation metrics for a single workflow run."""

    workflow_type: str
    transactions_processed: int = 0
    rows_matched: int = 0
    rows_unmatched: int = 0
    findings_generated: int = 0
    validation_warnings: int = 0
    llm_outputs_rejected: int = 0
    manual_overrides: int = 0          # always 0 in an automated run
    export_packets_generated: int = 0
    runtime_seconds: float = 0.0
    # Extra, non-spec context that aids defensibility / debugging.
    validation_passed: bool = True
    invented_reference_detected: bool = False
    findings_by_type: dict[str, int] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_type": self.workflow_type,
            "transactions_processed": self.transactions_processed,
            "rows_matched": self.rows_matched,
            "rows_unmatched": self.rows_unmatched,
            "findings_generated": self.findings_generated,
            "validation_warnings": self.validation_warnings,
            "llm_outputs_rejected": self.llm_outputs_rejected,
            "manual_overrides": self.manual_overrides,
            "export_packets_generated": self.export_packets_generated,
            "runtime_seconds": round(self.runtime_seconds, 6),
            "validation_passed": self.validation_passed,
            "invented_reference_detected": self.invented_reference_detected,
            "findings_by_type": dict(self.findings_by_type),
        }


# --------------------------------------------------------------------------- #
# Metric derivation
# --------------------------------------------------------------------------- #
def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _transactions_processed(workflow_type: str, summary: dict[str, Any]) -> int:
    """Best-effort 'transactions processed' from each workflow's summary.

    The number of source rows the deterministic step actually processed. We read
    it from the workflow's own summary (it is the workflow that knows what a
    'transaction' is) and fall back to 0 when a workflow has no tabular input.
    """
    if workflow_type == "bank_reconciliation":
        return _as_int(summary.get("bank_rows")) + _as_int(summary.get("ledger_rows"))
    if workflow_type == "budget_variance":
        # Joined lines + the rows that appeared in only one file.
        return (
            _as_int(summary.get("joined_lines"))
            + _as_int(summary.get("budget_only"))
            + _as_int(summary.get("actual_only"))
        )
    if workflow_type == "report_review":
        # report_review summarizes by findings; the processed unit is report
        # lines, which it does not expose as a count. Use total_findings as a
        # floor only if no better signal exists.
        return _as_int(summary.get("rows_processed"), 0)
    if workflow_type == "transaction_search":
        # Rows scanned across every loaded Tyler export.
        return sum(
            _as_int(f.get("row_count"))
            for f in (summary.get("input_files") or [])
            if isinstance(f, dict)
        )
    if workflow_type == "po_invoice_review":
        return _as_int(summary.get("po_rows")) + _as_int(summary.get("ap_rows"))
    if workflow_type == "je_upload_prep":
        return _as_int(summary.get("row_count"))
    return 0


def compute_metrics(
    *,
    workflow_type: str,
    findings: list[Any],
    summary: dict[str, Any],
    validation: Optional[Any],
    export_paths: Optional[dict[str, Any]],
    runtime_seconds: float,
    refused: bool = False,
) -> WorkflowMetrics:
    """Derive the Phase-7 metrics from a workflow's uniform run result.

    Arguments are the plain pieces of the registry's run-result dict so this is
    callable from the harness regardless of the concrete result object type.
    """
    findings = list(findings or [])
    summary = dict(summary or {})

    by_type: dict[str, int] = {}
    for f in findings:
        ft = getattr(getattr(f, "finding_type", None), "value", None) or str(
            getattr(f, "finding_type", "other")
        )
        by_type[ft] = by_type.get(ft, 0) + 1

    rows_matched = sum(by_type.get(t, 0) for t in _MATCHED_TYPES)
    rows_unmatched = sum(by_type.get(t, 0) for t in _UNMATCHED_TYPES)

    warnings = 0
    rejected = 0
    passed = True
    invented = False
    if validation is not None:
        warnings = len(getattr(validation, "warnings", []) or [])
        passed = bool(getattr(validation, "passed", True))
        invented = bool(getattr(validation, "invented_reference_detected", False))
        # An LLM output is "rejected" when validation did not pass (errors found
        # or an invented reference detected). Mock-default outputs pass, so this
        # is normally 0 in the automated run.
        rejected = 0 if passed else 1

    export_count = len(export_paths or {})

    return WorkflowMetrics(
        workflow_type=workflow_type,
        transactions_processed=_transactions_processed(workflow_type, summary),
        rows_matched=rows_matched,
        rows_unmatched=rows_unmatched,
        findings_generated=len(findings),
        validation_warnings=warnings,
        llm_outputs_rejected=rejected,
        manual_overrides=0,  # automated run: no human in the loop
        export_packets_generated=export_count,
        runtime_seconds=runtime_seconds,
        validation_passed=passed,
        invented_reference_detected=invented,
        findings_by_type=by_type,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Known-answer expectations
# --------------------------------------------------------------------------- #
# Each entry maps a workflow_type to the expected values for its bundled
# synthetic dataset. ``summary`` keys are checked against the workflow's own
# deterministic summary; ``findings_by_type`` against the derived metric counts.
# These are the spec's "known-answer" datasets (Phase 7 "Synthetic test
# datasets"): known matched/unmatched, known variances/missing accounts, known
# consistency issues. Values were captured from the deterministic output and are
# reproducible by construction.
KNOWN_ANSWERS: dict[str, dict[str, Any]] = {
    "bank_reconciliation": {
        # Known matched transactions / unmatched bank / unmatched ledger /
        # timing difference (a duplicate Acme payment surfaces as an unmatched
        # bank item under the configured tolerances).
        "summary": {
            "bank_rows": 7,
            "ledger_rows": 6,
            "matched": 4,
            "timing_differences": 1,
            "unmatched_bank": 2,
            "unmatched_ledger": 1,
        },
        "findings_by_type": {
            "matched": 4,
            "timing_difference": 1,
            "unmatched_bank": 2,
            "unmatched_ledger": 1,
        },
        "metrics": {
            "rows_matched": 4,
            "rows_unmatched": 4,
            "findings_generated": 8,
        },
    },
    "budget_variance": {
        # Known large dollar variance (Fire Salaries +35000), known large pct
        # variance (Parks Supplies +50%), known budget-only / actual-only /
        # missing account.
        "summary": {
            "joined_lines": 4,
            "flagged_variances": 2,
            "budget_only": 1,
            "actual_only": 1,
            "missing_accounts": 1,
        },
        "metrics": {
            "findings_generated": 5,
        },
    },
    "report_review": {
        # Known subtotal mismatch, invalid account code, duplicate line, missing
        # section, inconsistent naming, plus large-change-from-prior findings.
        "summary": {
            "total_findings": 7,
        },
        "findings_by_rule": {
            "subtotal_equals_line_item_sum": 1,
            "required_section_present": 1,
            "no_duplicate_account_lines": 1,
            "account_code_in_chart_of_accounts": 1,
            "consistent_account_naming": 1,
            "large_change_from_prior_version": 2,
        },
    },
    # ------------------------------------------------------------------ #
    # Tyler/Munis-era workflows on the City of Riverbend dataset
    # (data/synthetic/tyler; anomalies planted per known_answers.json).
    # ------------------------------------------------------------------ #
    "transaction_search": {
        # Sample query Q1: payments to Cascade Paving over $5,000 between
        # March and May 2026 -> exactly 2 AP invoice rows ($16,800 total).
        "summary": {
            "total_matches": 2,
            "total_signed_amount": "16800.00",
            "truncated": False,
        },
        "findings_by_type": {
            "search_match": 2,
        },
    },
    "ap_duplicate_review": {
        # Planted D1-D8 anomalies (known_answers.json ap_anomalies).
        "summary": {
            "total_findings": 15,
        },
        "findings_by_rule": {
            "duplicate_invoice_number": 1,            # D1
            "invoice_paid_by_multiple_checks": 2,     # D1b
            "same_vendor_same_amount_near_date": 4,   # D2
            "similar_vendor_names_same_amount": 1,    # D3
            "missing_po_over_threshold": 1,           # D4
            "payment_before_invoice_date": 1,         # D5
            "inactive_vendor_payment": 1,             # D6
            "unknown_vendor_payment": 1,              # D7
            "split_payment_pattern": 3,               # D8
        },
        "findings_by_type": {
            "duplicate_payment": 11,
            "vendor_anomaly": 2,
            "missing_reference": 2,
        },
    },
    "je_upload_prep": {
        # The VALID draft: balanced, all accounts active and in the COA, all
        # dates in period -> upload-ready with two round-dollar warnings only.
        "summary": {
            "upload_ready": True,
            "total_findings": 2,
            "blocking_findings": 0,
            "warning_findings": 2,
            "total_debits": "17500.00",
            "total_credits": "17500.00",
            "row_count": 7,
        },
        "findings_by_type": {
            "je_validation": 2,
        },
    },
    "po_invoice_review": {
        # Planted P1-P8 anomalies (known_answers.json po_anomalies), with the
        # missing-PO check split into hard-missing (P3a) + over-threshold (P3b).
        "summary": {
            "total_findings": 9,
            "po_rows": 18,
            "ap_rows": 66,
        },
        "findings_by_rule": {
            "invoice_exceeds_po": 1,        # P1
            "wrong_vendor": 1,              # P2
            "missing_po": 1,                # P3a
            "missing_po_over_threshold": 1, # P3b
            "closed_po_usage": 1,           # P4
            "unit_price_mismatch": 1,       # P5
            "quantity_mismatch": 1,         # P6
            "received_not_invoiced": 1,     # P7
            "invoiced_not_received": 1,     # P8
        },
        "findings_by_type": {
            "po_mismatch": 7,
            "missing_reference": 2,
        },
    },
}


@dataclass
class KnownAnswerResult:
    workflow_type: str
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_type": self.workflow_type,
            "passed": self.passed,
            "checks": self.checks,
        }


def known_answer_check(
    *,
    workflow_type: str,
    metrics: WorkflowMetrics,
    summary: dict[str, Any],
    findings: list[Any],
) -> KnownAnswerResult:
    """Compare deterministic output against the bundled known-answer expectations.

    Returns a per-field pass/fail breakdown. If a workflow has no known-answer
    entry, the result is a pass with an empty check list (nothing to verify).
    """
    expected = KNOWN_ANSWERS.get(workflow_type)
    if not expected:
        return KnownAnswerResult(workflow_type=workflow_type, passed=True, checks=[])

    summary = dict(summary or {})
    checks: list[dict[str, Any]] = []

    def _record(name: str, exp: Any, got: Any) -> None:
        checks.append(
            {"check": name, "expected": exp, "actual": got, "passed": exp == got}
        )

    for key, exp in expected.get("summary", {}).items():
        _record(f"summary.{key}", exp, summary.get(key))

    for key, exp in expected.get("metrics", {}).items():
        _record(f"metrics.{key}", exp, getattr(metrics, key, None))

    for key, exp in expected.get("findings_by_type", {}).items():
        _record(f"findings_by_type.{key}", exp, metrics.findings_by_type.get(key, 0))

    # findings_by_rule lives in the workflow summary (report_review).
    by_rule = dict(summary.get("findings_by_rule", {}) or {})
    for key, exp in expected.get("findings_by_rule", {}).items():
        _record(f"findings_by_rule.{key}", exp, by_rule.get(key, 0))

    passed = all(c["passed"] for c in checks)
    return KnownAnswerResult(
        workflow_type=workflow_type, passed=passed, checks=checks
    )
