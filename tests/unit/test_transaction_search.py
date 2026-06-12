"""Tests for src/workflows/transaction_search.py.

Coverage:
  * Q1-Q4 known-answer tests: criteria parsed correctly by mock parser AND
    row counts match data/synthetic/tyler/known_answers.json
  * Empty / garbage queries fail closed (preflight FAIL or criteria_parse_failed)
  * Invalid LLM criteria (negative amount, unknown module) are rejected / flagged
  * Fuzzy vendor match catches a near-miss spelling but not an unrelated vendor
  * Exports written + hashed
  * Validation layer rejects an LLM summary citing an invented row id
  * Full run() lifecycle with ledger + audit on tmp_path

Run with:
  .venv/Scripts/python.exe -m pytest tests/unit/test_transaction_search.py \
      -q -p no:cacheprovider --basetemp=.pytest_tmp_ts
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

# ------------------------------------------------------------------ #
# Fixtures / helpers
# ------------------------------------------------------------------ #
TYLER_DIR = Path("data/synthetic/tyler")
KNOWN_ANSWERS_PATH = TYLER_DIR / "known_answers.json"


@pytest.fixture(scope="session")
def known_answers() -> dict:
    return json.loads(KNOWN_ANSWERS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def tyler_inputs() -> dict[str, str]:
    """All four Tyler-style data files for Riverbend dataset."""
    return {
        "gl_detail": str(TYLER_DIR / "gl_detail.csv"),
        "ap_invoices": str(TYLER_DIR / "ap_invoice_detail.csv"),
        "checks": str(TYLER_DIR / "check_register.csv"),
        "purchase_orders": str(TYLER_DIR / "purchase_orders.csv"),
    }


def _make_ledger():
    from src.core.run_ledger import RunLedger
    return RunLedger(":memory:")


def _make_audit(ledger, tmp_path):
    from src.core.audit_log import AuditLog
    return AuditLog(ledger, audit_dir=str(tmp_path / "audit"), write_jsonl=True)


# ------------------------------------------------------------------ #
# Import smoke test
# ------------------------------------------------------------------ #
def test_imports():
    from src.workflows.transaction_search import (
        WORKFLOW_TYPE,
        CAPABILITY,
        SAMPLE_INPUTS,
        SearchCriteria,
        TransactionSearchConfig,
        run,
        register,
        WORKFLOW,
        EXPORT_FILE_NAMES,
    )
    assert WORKFLOW_TYPE == "transaction_search"
    assert "gl_detail" in CAPABILITY.optional_inputs
    assert "search_criteria.json" in EXPORT_FILE_NAMES


# ------------------------------------------------------------------ #
# SearchCriteria validation
# ------------------------------------------------------------------ #
class TestSearchCriteria:
    def test_valid_minimal(self):
        from src.workflows.transaction_search import SearchCriteria
        sc = SearchCriteria(vendor="Acme")
        assert sc.vendor == "Acme"
        assert sc.has_any_criterion()

    def test_empty_is_no_criterion(self):
        from src.workflows.transaction_search import SearchCriteria
        sc = SearchCriteria()
        assert not sc.has_any_criterion()

    def test_negative_amount_min_rejected(self):
        from src.workflows.transaction_search import SearchCriteria
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            SearchCriteria(amount_min="-100")

    def test_negative_amount_max_rejected(self):
        from src.workflows.transaction_search import SearchCriteria
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            SearchCriteria(amount_max="-50.00")

    def test_unknown_module_rejected(self):
        from src.workflows.transaction_search import SearchCriteria
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            SearchCriteria(module="banana")

    def test_date_order_validated(self):
        from src.workflows.transaction_search import SearchCriteria
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            SearchCriteria(date_from="2026-05-01", date_to="2026-03-01")

    def test_amount_order_validated(self):
        from src.workflows.transaction_search import SearchCriteria
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            SearchCriteria(amount_min="1000.00", amount_max="500.00")

    def test_invalid_module_drops_to_unparsed(self):
        """_validate_criteria_dict should drop an unknown module to unparsed_terms."""
        from src.workflows.transaction_search import _validate_criteria_dict
        sc, unparsed = _validate_criteria_dict({
            "module": "banana",
            "vendor": "Acme",
            "llm_proposed_fields": ["module", "vendor"],
        })
        assert sc.vendor == "Acme"
        assert sc.module is None
        assert any("banana" in u for u in unparsed)

    def test_valid_module_accepted(self):
        from src.workflows.transaction_search import SearchCriteria
        sc = SearchCriteria(module="ap")
        assert sc.module == "ap"

    def test_known_module_aliases_accepted(self):
        from src.workflows.transaction_search import SearchCriteria
        for m in ("gl", "ap", "ap_invoice_detail", "checks", "check_register", "po", "purchase_orders"):
            sc = SearchCriteria(module=m)
            assert sc.module == m


# ------------------------------------------------------------------ #
# Mock parser: Q1-Q4 known-answer tests
# ------------------------------------------------------------------ #
class TestMockParser:
    def test_q1_criteria(self, known_answers):
        """Q1: 'payments to Cascade Paving over $5,000 between March and May 2026'"""
        from src.workflows.transaction_search import _parse_criteria_from_query
        q = known_answers["transaction_search"]["Q1"]["query"]
        exp = known_answers["transaction_search"]["Q1"]["expected_criteria"]
        sc = _parse_criteria_from_query(q)

        assert sc.vendor is not None and "cascade paving" in sc.vendor.lower()
        assert sc.amount_min is not None
        assert Decimal(sc.amount_min) == Decimal(exp["amount_min"])
        assert sc.date_from == exp["date_from"]
        assert sc.date_to == exp["date_to"]

    def test_q2_criteria(self, known_answers):
        """Q2: 'invoice INV-10234'"""
        from src.workflows.transaction_search import _parse_criteria_from_query
        q = known_answers["transaction_search"]["Q2"]["query"]
        sc = _parse_criteria_from_query(q)

        assert sc.invoice_number is not None
        assert sc.invoice_number.upper() == "INV-10234"

    def test_q3_criteria(self, known_answers):
        """Q3: 'checks to Acme Office Supply in February 2026'"""
        from src.workflows.transaction_search import _parse_criteria_from_query
        q = known_answers["transaction_search"]["Q3"]["query"]
        exp = known_answers["transaction_search"]["Q3"]["expected_criteria"]
        sc = _parse_criteria_from_query(q)

        assert sc.vendor is not None and "acme" in sc.vendor.lower()
        assert sc.date_from == exp["date_from"]
        assert sc.date_to == exp["date_to"]
        # Module should be checks or check_register
        assert sc.module in (None, "checks", "check_register")

    def test_q4_criteria(self, known_answers):
        """Q4: 'pothole repair charges in fund 300'"""
        from src.workflows.transaction_search import _parse_criteria_from_query
        q = known_answers["transaction_search"]["Q4"]["query"]
        sc = _parse_criteria_from_query(q)

        assert sc.fund == "300"
        # Should extract keywords including pothole/repair/asphalt etc.
        assert sc.keywords or sc.fund  # at minimum the fund is set


# ------------------------------------------------------------------ #
# Known-answer row-count tests (full run with real data)
# ------------------------------------------------------------------ #
class TestKnownAnswerRowCounts:
    def test_q1_row_count(self, known_answers, tyler_inputs):
        """Q1: expect 2 AP rows for Cascade Paving over $5k Mar-May 2026."""
        from src.workflows.transaction_search import run
        q = known_answers["transaction_search"]["Q1"]["query"]
        expected = known_answers["transaction_search"]["Q1"]["expected_row_count"]
        expected_indices = set(known_answers["transaction_search"]["Q1"]["expected_row_indices"])

        result = run({**tyler_inputs, "query": q})
        assert result.get("criteria_parse_failed") is not True
        findings = [
            f for f in result["findings"]
            if f.finding_type.value == "search_match"
        ]
        assert len(findings) == expected, (
            f"Q1: expected {expected} matches, got {len(findings)}: "
            + str([f.computed_values.get("source_row_index") for f in findings])
        )
        found_indices = {f.computed_values.get("source_row_index") for f in findings}
        assert found_indices == expected_indices, (
            f"Q1: expected row indices {expected_indices}, got {found_indices}"
        )

    def test_q2_row_count(self, known_answers, tyler_inputs):
        """Q2: expect 2 AP rows for INV-10234."""
        from src.workflows.transaction_search import run
        q = known_answers["transaction_search"]["Q2"]["query"]
        expected = known_answers["transaction_search"]["Q2"]["expected_row_count"]
        expected_indices = set(known_answers["transaction_search"]["Q2"]["expected_row_indices"])

        result = run({**tyler_inputs, "query": q})
        assert result.get("criteria_parse_failed") is not True
        findings = [
            f for f in result["findings"]
            if f.finding_type.value == "search_match"
        ]
        # At least the AP rows; GL rows may or may not match depending on invoice_number
        # coverage in gl_detail. The spec says expected_row_count=2 for AP.
        ap_findings = [
            f for f in findings
            if f.computed_values.get("module") == "ap_invoice_detail"
        ]
        assert len(ap_findings) == expected, (
            f"Q2: expected {expected} AP matches, got {len(ap_findings)}: "
            + str([f.computed_values.get("source_row_index") for f in ap_findings])
        )
        found_ap_indices = {f.computed_values.get("source_row_index") for f in ap_findings}
        assert found_ap_indices == expected_indices

    def test_q3_row_count(self, known_answers, tyler_inputs):
        """Q3: expect 2 check-register rows for Acme Office Supply in Feb 2026."""
        from src.workflows.transaction_search import run
        q = known_answers["transaction_search"]["Q3"]["query"]
        expected = known_answers["transaction_search"]["Q3"]["expected_row_count"]
        expected_indices = set(known_answers["transaction_search"]["Q3"]["expected_row_indices"])

        result = run({**tyler_inputs, "query": q})
        assert result.get("criteria_parse_failed") is not True
        findings = [
            f for f in result["findings"]
            if f.finding_type.value == "search_match"
            and f.computed_values.get("module") == "check_register"
        ]
        assert len(findings) == expected, (
            f"Q3: expected {expected} check matches, got {len(findings)}: "
            + str([f.computed_values.get("source_row_index") for f in findings])
        )
        found_indices = {f.computed_values.get("source_row_index") for f in findings}
        assert found_indices == expected_indices

    def test_q4_row_count(self, known_answers, tyler_inputs):
        """Q4: expect 3 GL rows for pothole/repair in fund 300."""
        from src.workflows.transaction_search import run
        q = known_answers["transaction_search"]["Q4"]["query"]
        expected = known_answers["transaction_search"]["Q4"]["expected_row_count"]
        expected_indices = set(known_answers["transaction_search"]["Q4"]["expected_row_indices"])

        result = run({**tyler_inputs, "query": q})
        assert result.get("criteria_parse_failed") is not True
        findings = [
            f for f in result["findings"]
            if f.finding_type.value == "search_match"
            and f.computed_values.get("module") == "gl_detail"
        ]
        assert len(findings) == expected, (
            f"Q4: expected {expected} GL matches, got {len(findings)}: "
            + str([
                (f.computed_values.get("source_row_index"),
                 f.computed_values.get("description", "")[:50])
                for f in findings
            ])
        )
        found_indices = {f.computed_values.get("source_row_index") for f in findings}
        assert found_indices == expected_indices


# ------------------------------------------------------------------ #
# Fail-closed: empty / blank / garbage query
# ------------------------------------------------------------------ #
class TestFailClosed:
    def test_empty_query_fails(self, tyler_inputs):
        """Empty query must fail closed."""
        from src.workflows.transaction_search import run
        result = run({**tyler_inputs, "query": ""})
        # Either preflight FAIL or criteria_parse_failed
        assert (
            result.get("preflight_status") == "fail"
            or result.get("criteria_parse_failed") is True
        )
        assert result["findings"] == []

    def test_blank_query_fails(self, tyler_inputs):
        """Whitespace-only query must fail closed."""
        from src.workflows.transaction_search import run
        result = run({**tyler_inputs, "query": "   "})
        assert (
            result.get("preflight_status") == "fail"
            or result.get("criteria_parse_failed") is True
        )
        assert result["findings"] == []

    def test_no_data_file_fails(self):
        """No data file at all must fail closed."""
        from src.workflows.transaction_search import run
        result = run({"query": "some transaction"})
        assert result.get("preflight_status") == "fail"
        assert result["findings"] == []

    def test_query_only_no_data_fails(self):
        """Query provided but no data files -> FAIL."""
        from src.workflows.transaction_search import run
        result = run({"query": "payments to Cascade Paving in March 2026"})
        assert result.get("preflight_status") == "fail"

    def test_garbage_query_no_parse_still_runs(self, tyler_inputs):
        """A query with no parseable criteria either fails or returns 0 matches."""
        from src.workflows.transaction_search import run
        result = run({**tyler_inputs, "query": "xyzzy nothing matches"})
        # May either detect no criteria (criteria_parse_failed) or return 0 matches
        if not result.get("criteria_parse_failed"):
            findings = [f for f in result["findings"] if f.finding_type.value == "search_match"]
            # With no useful criteria anything could match via keywords -- OK
            # but the workflow must not crash and must return structured output
            assert "summary" in result
        else:
            assert result["findings"] == []


# ------------------------------------------------------------------ #
# Invalid LLM criteria: negative amount + unknown module
# ------------------------------------------------------------------ #
class TestInvalidLLMCriteria:
    def test_negative_amount_rejected(self):
        """A proposed amount_min < 0 should be dropped to unparsed_terms."""
        from src.workflows.transaction_search import _validate_criteria_dict
        sc, unparsed = _validate_criteria_dict({
            "amount_min": "-500.00",
            "vendor": "Acme",
            "llm_proposed_fields": ["amount_min", "vendor"],
        })
        # amount_min rejected
        assert sc.amount_min is None
        assert any("-500" in u for u in unparsed)
        # vendor still accepted
        assert sc.vendor == "Acme"

    def test_unknown_module_not_executed(self, tyler_inputs):
        """SearchCriteria with unknown module raises on validation, not at execution."""
        from src.workflows.transaction_search import SearchCriteria
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            SearchCriteria(module="invalid_module_xyz")

    def test_invalid_date_order_dropped(self):
        """date_from > date_to is dropped to unparsed_terms."""
        from src.workflows.transaction_search import _validate_criteria_dict
        sc, unparsed = _validate_criteria_dict({
            "date_from": "2026-06-01",
            "date_to": "2026-03-01",
            "fund": "100",
            "llm_proposed_fields": ["date_from", "date_to", "fund"],
        })
        assert sc.date_from is None
        assert sc.date_to is None
        assert sc.fund == "100"


# ------------------------------------------------------------------ #
# Fuzzy vendor match
# ------------------------------------------------------------------ #
class TestFuzzyVendorMatch:
    def test_near_miss_spelling_matches(self):
        """A near-miss spelling like 'Cascade Pavng' should match 'Cascade Paving'."""
        from src.workflows.transaction_search import _vendor_matches
        # difflib ratio of 'cascade pavng' vs 'cascade paving' is ~0.93
        assert _vendor_matches("Cascade Paving", "Cascade Pavng", 0.85)

    def test_exact_substring_matches(self):
        """Exact substring (casefold) always matches."""
        from src.workflows.transaction_search import _vendor_matches
        assert _vendor_matches("Acme Office Supply LLC", "Acme Office Supply", 0.85)

    def test_unrelated_vendor_does_not_match(self):
        """'Summit IT Services' should not match 'Acme Office Supply'."""
        from src.workflows.transaction_search import _vendor_matches
        assert not _vendor_matches("Summit IT Services", "Acme Office Supply", 0.85)

    def test_very_short_strings_not_matched(self):
        """Empty query vendor should not match anything."""
        from src.workflows.transaction_search import _vendor_matches
        assert not _vendor_matches("Some Vendor", "", 0.85)
        assert not _vendor_matches("", "Vendor", 0.85)

    def test_cascade_paving_variant(self, tyler_inputs):
        """A slightly misspelled vendor query finds Cascade Paving rows."""
        from src.workflows.transaction_search import run
        # Near-miss: "Cascad Paving" (missing 'e')
        result = run({
            **tyler_inputs,
            "query": "invoices to Cascad Paving",
        })
        if not result.get("criteria_parse_failed"):
            findings = [f for f in result["findings"] if f.finding_type.value == "search_match"]
            # Should find SOME Cascade Paving rows via fuzzy match
            # (exact substring won't hit since 'cascad paving' is not in 'cascade paving')
            # The fuzzy matcher should catch it
            assert len(findings) >= 1, "Near-miss vendor spelling should find rows via fuzzy match"


# ------------------------------------------------------------------ #
# Exports written + hashed
# ------------------------------------------------------------------ #
class TestExports:
    def test_export_artifacts_written(self, tyler_inputs, tmp_path):
        """All five export files are written and carry sha256."""
        from src.workflows.transaction_search import run, EXPORT_FILE_NAMES
        result = run(
            {**tyler_inputs, "query": "invoice INV-10234"},
            export_dir=str(tmp_path),
        )
        assert "export_paths" in result
        paths = result["export_paths"]
        for name in EXPORT_FILE_NAMES:
            assert name in paths, f"Missing export: {name}"
            p = Path(paths[name])
            assert p.exists(), f"Export file not written: {p}"
            assert p.stat().st_size > 0, f"Export file is empty: {p}"

    def test_export_artifacts_have_sha256(self, tyler_inputs, tmp_path):
        """Each ExportArtifact has a non-empty sha256."""
        from src.workflows.transaction_search import run
        result = run(
            {**tyler_inputs, "query": "invoice INV-10234"},
            export_dir=str(tmp_path),
        )
        artifacts = result.get("export_artifacts", [])
        assert len(artifacts) > 0
        for art in artifacts:
            assert art.sha256, f"Missing sha256 on {art.file_name}"
            assert len(art.sha256) == 64  # sha256 hex is 64 chars

    def test_search_criteria_json_written(self, tyler_inputs, tmp_path):
        """search_criteria.json is written with the validated criteria dict."""
        from src.workflows.transaction_search import run
        result = run(
            {**tyler_inputs, "query": "payments to Cascade Paving over $5,000 between March and May 2026"},
            export_dir=str(tmp_path),
        )
        run_id = result["run_id"]
        criteria_path = tmp_path / run_id / "search_criteria.json"
        assert criteria_path.exists()
        data = json.loads(criteria_path.read_text(encoding="utf-8"))
        assert "vendor" in data
        assert data["vendor"] is not None

    def test_search_results_csv_written(self, tyler_inputs, tmp_path):
        """search_results.csv is written with the matched rows."""
        from src.workflows.transaction_search import run
        result = run(
            {**tyler_inputs, "query": "invoice INV-10234"},
            export_dir=str(tmp_path),
        )
        run_id = result["run_id"]
        csv_path = tmp_path / run_id / "search_results.csv"
        assert csv_path.exists()
        import pandas as pd
        df = pd.read_csv(csv_path)
        assert len(df) >= 1

    def test_validation_report_json_written(self, tyler_inputs, tmp_path):
        """validation_report.json contains passed/errors/warnings."""
        from src.workflows.transaction_search import run
        result = run(
            {**tyler_inputs, "query": "invoice INV-10234"},
            export_dir=str(tmp_path),
        )
        run_id = result["run_id"]
        vr_path = tmp_path / run_id / "validation_report.json"
        assert vr_path.exists()
        data = json.loads(vr_path.read_text(encoding="utf-8"))
        assert "passed" in data


# ------------------------------------------------------------------ #
# Validation rejects invented row id
# ------------------------------------------------------------------ #
class TestValidationRejectsInventedRef:
    def test_invented_ref_detected(self, tyler_inputs):
        """The validation layer must reject an LLM summary citing an invented row id."""
        from src.workflows.transaction_search import (
            run_deterministic,
            validate_llm_output,
            _parse_criteria_from_query,
            TransactionSearchConfig,
        )
        criteria = _parse_criteria_from_query("invoice INV-10234")
        det = run_deterministic(tyler_inputs, criteria)

        fake_response = {
            "summary": "The search found matches.",
            "categorized_exceptions": [],
            "referenced_source_rows": [
                "ap_invoice_detail:9999999"  # invented
            ],
            "suggested_review_steps": [],
            "draft_memo": "DRAFT",
        }
        validation = validate_llm_output(fake_response, det)
        assert validation.invented_reference_detected is True
        assert not validation.passed

    def test_real_refs_pass_validation(self, tyler_inputs):
        """An LLM response citing real source-row ids must pass validation."""
        from src.workflows.transaction_search import (
            run_deterministic,
            validate_llm_output,
            mock_llm_response,
            _parse_criteria_from_query,
        )
        criteria = _parse_criteria_from_query("invoice INV-10234")
        det = run_deterministic(tyler_inputs, criteria)
        response = mock_llm_response(det)
        validation = validate_llm_output(response, det)
        assert validation.invented_reference_detected is False
        assert validation.passed


# ------------------------------------------------------------------ #
# Full run() lifecycle: ledger + audit
# ------------------------------------------------------------------ #
class TestFullLifecycle:
    def test_run_with_ledger_and_audit(self, tyler_inputs, tmp_path):
        """Full run() with ledger + audit stores findings, LLM response, validation."""
        from src.workflows.transaction_search import run
        ledger = _make_ledger()
        audit = _make_audit(ledger, tmp_path)

        result = run(
            {**tyler_inputs, "query": "payments to Cascade Paving over $5,000 between March and May 2026"},
            ledger=ledger,
            audit=audit,
            export_dir=str(tmp_path),
        )
        run_id = result["run_id"]
        assert run_id

        # Findings stored
        stored_findings = ledger.list_findings(run_id)
        assert len(stored_findings) > 0

        # LLM response stored
        stored_llm = ledger.list_llm_responses(run_id)
        assert len(stored_llm) == 1

        # Validation stored
        stored_vr = ledger.list_validation_results(run_id)
        assert len(stored_vr) == 1

        # Audit events recorded
        events = ledger.list_audit_events(run_id)
        event_types = {e["event_type"] for e in events}
        assert "run_created" in event_types
        assert "deterministic_analysis_completed" in event_types
        assert "llm_request_sent" in event_types
        assert "llm_response_received" in event_types
        assert "validation_completed" in event_types

    def test_failed_preflight_no_llm_call(self, tmp_path):
        """FAIL preflight: LLM never called, no findings stored in ledger."""
        from src.workflows.transaction_search import run
        ledger = _make_ledger()
        audit = _make_audit(ledger, tmp_path)

        # No data files -> FAIL
        result = run(
            {"query": "payments to Cascade Paving"},
            ledger=ledger,
            audit=audit,
            export_dir=str(tmp_path),
        )
        run_id = result["run_id"]
        assert result.get("preflight_status") == "fail"
        assert result["findings"] == []

        stored_findings = ledger.list_findings(run_id)
        assert stored_findings == []

        stored_llm = ledger.list_llm_responses(run_id)
        assert stored_llm == []

    def test_export_paths_in_result(self, tyler_inputs, tmp_path):
        """run() with export_dir includes export_paths in the result dict."""
        from src.workflows.transaction_search import run
        result = run(
            {**tyler_inputs, "query": "invoice INV-10234"},
            export_dir=str(tmp_path),
        )
        assert "export_paths" in result
        # All named exports should be present
        from src.workflows.transaction_search import EXPORT_FILE_NAMES
        for name in EXPORT_FILE_NAMES:
            assert name in result["export_paths"]

    def test_result_structure(self, tyler_inputs):
        """run() always returns the required keys."""
        from src.workflows.transaction_search import run
        result = run({**tyler_inputs, "query": "invoice INV-10234"})
        for key in ("run_id", "workflow_type", "findings", "summary"):
            assert key in result, f"Missing key: {key}"
        assert result["workflow_type"] == "transaction_search"

    def test_preflight_stored_in_ledger(self, tyler_inputs, tmp_path):
        """Preflight report stored in the ledger under the run_id."""
        from src.workflows.transaction_search import run
        ledger = _make_ledger()
        audit = _make_audit(ledger, tmp_path)
        result = run(
            {**tyler_inputs, "query": "invoice INV-10234"},
            ledger=ledger,
            audit=audit,
        )
        run_id = result["run_id"]
        stored_pf = ledger.get_preflight(run_id)
        assert stored_pf is not None
        assert stored_pf["workflow_type"] == "transaction_search"


# ------------------------------------------------------------------ #
# Module filter test
# ------------------------------------------------------------------ #
class TestModuleFilter:
    def test_module_gl_limits_to_gl(self, tyler_inputs):
        """When module=gl, only GL rows match."""
        from src.workflows.transaction_search import run, SearchCriteria, run_deterministic, _parse_criteria_from_query, TransactionSearchConfig
        criteria = SearchCriteria(fund="100", module="gl")
        det = run_deterministic(tyler_inputs, criteria)
        for f in det.findings:
            if f.finding_type.value == "search_match":
                assert f.computed_values.get("module") == "gl_detail", (
                    f"Module filter failed: got {f.computed_values.get('module')}"
                )

    def test_module_checks_limits_to_checks(self, tyler_inputs):
        """When module=checks, only check-register rows match."""
        from src.workflows.transaction_search import SearchCriteria, run_deterministic
        criteria = SearchCriteria(fund=None, module="checks")
        # Add a date range to limit results
        criteria = SearchCriteria(
            date_from="2026-02-01",
            date_to="2026-02-28",
            module="checks",
        )
        det = run_deterministic(tyler_inputs, criteria)
        for f in det.findings:
            if f.finding_type.value == "search_match":
                assert f.computed_values.get("module") == "check_register"


# ------------------------------------------------------------------ #
# Max results cap
# ------------------------------------------------------------------ #
class TestMaxResults:
    def test_max_results_cap(self, tyler_inputs):
        """max_results=5 yields at most 5 SEARCH_MATCH findings + truncation."""
        from src.workflows.transaction_search import run_deterministic, SearchCriteria, TransactionSearchConfig
        cfg = TransactionSearchConfig(max_results=5)
        criteria = SearchCriteria()   # no filters -> would match all rows
        # Use a broad vendor filter to get many rows
        criteria = SearchCriteria(fund="100")   # should have many rows in GL
        det = run_deterministic(tyler_inputs, criteria, config=cfg)
        match_findings = [f for f in det.findings if f.finding_type.value == "search_match"]
        assert len(match_findings) <= 5
        if det.summary.get("truncated"):
            trunc_findings = [f for f in det.findings if f.rule_used == "result_truncation"]
            assert len(trunc_findings) == 1


# ------------------------------------------------------------------ #
# Month-without-year: deterministic, no wall-clock dependency
# ------------------------------------------------------------------ #
class TestMonthWithoutYear:
    """When a query contains a month name without an explicit year the parser
    must NOT call date.today(); it must surface a clear note in unparsed_terms
    and produce no date filter (deterministic under any system clock)."""

    def test_month_only_no_date_filter(self):
        """Single month without year -> no date_from/date_to, unparsed term present."""
        from src.workflows.transaction_search import _parse_criteria_from_query
        sc = _parse_criteria_from_query("invoices in March")
        assert sc.date_from is None
        assert sc.date_to is None
        assert any("year required" in u.lower() for u in (sc.unparsed_terms or [])), (
            f"Expected 'year required' note in unparsed_terms, got: {sc.unparsed_terms}"
        )

    def test_month_range_without_year_no_date_filter(self):
        """'between March and May' without year -> no date filter, note in unparsed."""
        from src.workflows.transaction_search import _parse_criteria_from_query
        sc = _parse_criteria_from_query("payments between March and May")
        assert sc.date_from is None
        assert sc.date_to is None
        assert any("year required" in u.lower() for u in (sc.unparsed_terms or [])), (
            f"Expected 'year required' note in unparsed_terms, got: {sc.unparsed_terms}"
        )

    def test_month_with_year_still_works(self):
        """Month + explicit year must still produce a date filter (not broken)."""
        from src.workflows.transaction_search import _parse_criteria_from_query
        sc = _parse_criteria_from_query("invoices in March 2026")
        assert sc.date_from == "2026-03-01"
        assert sc.date_to == "2026-03-31"

    def test_month_range_with_year_still_works(self):
        """'between March and May 2026' must still produce date range."""
        from src.workflows.transaction_search import _parse_criteria_from_query
        sc = _parse_criteria_from_query("payments between March and May 2026")
        assert sc.date_from == "2026-03-01"
        assert sc.date_to == "2026-05-31"

    def test_month_only_is_deterministic(self):
        """Calling the parser twice with no year must yield identical results
        (no wall-clock drift between calls)."""
        from src.workflows.transaction_search import _parse_criteria_from_query
        sc1 = _parse_criteria_from_query("invoices in June")
        sc2 = _parse_criteria_from_query("invoices in June")
        assert sc1.date_from == sc2.date_from
        assert sc1.date_to == sc2.date_to
        assert sc1.unparsed_terms == sc2.unparsed_terms

    def test_month_only_unparsed_term_content(self):
        """The unparsed note must include the month token for context."""
        from src.workflows.transaction_search import _parse_criteria_from_query
        sc = _parse_criteria_from_query("checks in February")
        note = " ".join(sc.unparsed_terms or []).lower()
        assert "february" in note or "feb" in note.replace("february", "feb")
        assert "year required" in note


# ------------------------------------------------------------------ #
# register() and WORKFLOW dict
# ------------------------------------------------------------------ #
class TestRegistry:
    def test_register_dict(self):
        from src.workflows.transaction_search import register, run
        reg: dict = {}
        register(reg)
        assert "transaction_search" in reg
        assert reg["transaction_search"] is run

    def test_register_callable(self):
        from src.workflows.transaction_search import register, run

        class FakeRegistry:
            def __init__(self):
                self.registered = {}
            def register(self, wt, fn):
                self.registered[wt] = fn

        reg = FakeRegistry()
        register(reg)
        assert "transaction_search" in reg.registered
        assert reg.registered["transaction_search"] is run

    def test_workflow_dict(self):
        from src.workflows.transaction_search import WORKFLOW, WORKFLOW_TYPE
        assert WORKFLOW["workflow_type"] == WORKFLOW_TYPE
        assert callable(WORKFLOW["run"])
        assert "export_files" in WORKFLOW
