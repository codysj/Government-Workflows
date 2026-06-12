"""Tyler/Munis ERP readiness tests for the EXISTING four workflows.

Proves that bank_reconciliation, budget_variance, report_review and freeform
(plus the shared ingest/normalize/preflight paths) cleanly ingest Tyler/Munis-
style local CSV/Excel exports:

  * separate Debit/Credit columns -> deterministic signed `amount` derivation
    (`derive_signed_amount`, mirroring src/ingest/tyler.py semantics), surfaced
    in the preflight profile notes and the reconciliation summary;
  * exact-name semantic precedence so a real/derived `amount` column is not
    falsely flagged ambiguous against the debit/credit synonyms;
  * .xlsx acceptance made real (preflight loads xlsx; workflows dispatch
    csv/xlsx via src.ingest.excel_loader.load_table);
  * a SINGLE Munis combined budget-to-actual export usable for budget variance
    (pass the same file as both `budget` and `actuals`; revised/original budget
    vs YTD actual columns are recognized deterministically);
  * report review on a Tyler-style report + chart of accounts via approved
    preflight column mappings (including the new COA mapping override);
  * Munis account strings ("100-5100-5010") flowing through findings,
    validation and exports unchanged.

Known-answer fixtures live in data/synthetic/*/tyler_style_*.csv and are
modeled on the data/synthetic/tyler conventions. Every scenario must preflight
PASS (or a justified PARTIAL) -- never FAIL.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.core.preflight import (
    HIGH_CONFIDENCE_THRESHOLD,
    profile_input,
    run_preflight,
)
from src.core.schemas import FindingType, PreflightStatus
from src.ingest.excel_loader import load_table
from src.ingest.presets import apply_preset
from src.normalize.cleaning import (
    best_semantic_column,
    derive_signed_amount,
    normalize_columns,
)
from src.workflows import bank_reconciliation as bankrec
from src.workflows import budget_variance as budvar
from src.workflows import freeform
from src.workflows import report_review as report

_REPO = Path(__file__).resolve().parents[2]
_BANK_DIR = _REPO / "data" / "synthetic" / "bank_reconciliation"
_BUDGET_DIR = _REPO / "data" / "synthetic" / "budget_variance"
_REPORT_DIR = _REPO / "data" / "synthetic" / "report_review"

TYLER_BANK = _BANK_DIR / "tyler_style_bank.csv"
TYLER_LEDGER = _BANK_DIR / "tyler_style_ledger.csv"
TYLER_B2A = _BUDGET_DIR / "tyler_style_budget_to_actual.csv"
TYLER_REPORT = _REPORT_DIR / "tyler_style_report.csv"
TYLER_COA = _REPORT_DIR / "tyler_style_chart_of_accounts.csv"


def _mappings_from_report(preflight_report) -> dict[str, dict[str, str]]:
    """Mimic the runner: approved mappings = resolved, non-'unmapped' columns."""
    out: dict[str, dict[str, str]] = {}
    for m in preflight_report.column_mappings:
        if m.mapped_column and m.source != "unmapped":
            out.setdefault(m.input_key, {})[m.semantic_name] = m.mapped_column
    return out


# --------------------------------------------------------------------------- #
# derive_signed_amount (shared helper)
# --------------------------------------------------------------------------- #
class TestDeriveSignedAmount:
    def test_derives_debit_minus_credit(self):
        df = pd.DataFrame(
            {
                "debit": ["5000.00", "", "$1,200.00", ""],
                "credit": ["", "480.00", "", ""],
            }
        )
        out, derived = derive_signed_amount(df)
        assert derived is True
        assert out["amount"].tolist() == ["5000.00", "-480.00", "1200.00", ""]
        # Originals kept; no rows dropped or reordered.
        assert list(out.columns) == ["debit", "credit", "amount"]
        assert len(out) == len(df)

    def test_noop_when_amount_already_present(self):
        df = pd.DataFrame({"debit": ["1"], "credit": ["2"], "amount": ["9"]})
        out, derived = derive_signed_amount(df)
        assert derived is False
        assert out is df  # untouched

    def test_noop_without_both_sides(self):
        out, derived = derive_signed_amount(pd.DataFrame({"debit": ["1"]}))
        assert derived is False

    def test_parens_negative_and_both_sides(self):
        df = pd.DataFrame({"debit": ["(100.00)"], "credit": ["50.00"]})
        out, derived = derive_signed_amount(df)
        assert derived is True
        assert out["amount"].tolist() == ["-150.00"]


# --------------------------------------------------------------------------- #
# Exact-name semantic precedence (amount vs debit/credit synonyms)
# --------------------------------------------------------------------------- #
class TestExactNamePrecedence:
    def test_amount_beats_debit_credit_synonyms(self):
        df = normalize_columns(
            pd.DataFrame(
                {
                    "Amount": ["100.00", "-50.00"],
                    "Debit": ["100.00", ""],
                    "Credit": ["", "50.00"],
                }
            )
        )
        col, conf, ranked = best_semantic_column(df, "amount")
        assert col == "amount"
        assert conf >= HIGH_CONFIDENCE_THRESHOLD
        assert len(ranked) >= 3

    def test_debit_credit_alone_stays_ambiguous(self):
        # Existing conservative behavior unchanged: without an exact 'amount'
        # column the debit/credit pair is still flagged as ambiguous.
        df = normalize_columns(
            pd.DataFrame({"Debit": ["100.00"], "Credit": ["50.00"]})
        )
        col, conf, ranked = best_semantic_column(df, "amount")
        assert col is not None
        assert conf < HIGH_CONFIDENCE_THRESHOLD


# --------------------------------------------------------------------------- #
# Preflight profiles Tyler-shaped files (CSV + XLSX)
# --------------------------------------------------------------------------- #
class TestPreflightTylerProfiles:
    def test_profile_derives_amount_and_notes_it(self):
        prof = profile_input("ledger", TYLER_LEDGER, {"*": ["csv", "xlsx"]})
        assert prof.present
        assert "amount" in prof.detected_columns
        assert "debit" in prof.detected_columns  # originals preserved
        assert any("Derived signed 'amount'" in n for n in prof.notes)
        assert prof.parse_confidence.get("amount") == 1.0

    def test_profile_reads_xlsx(self, tmp_path):
        df = pd.read_csv(TYLER_LEDGER, dtype=str, keep_default_na=False)
        xlsx = tmp_path / "tyler_style_ledger.xlsx"
        df.to_excel(xlsx, index=False)
        prof = profile_input("ledger", xlsx, {"*": ["csv", "xlsx"]})
        assert prof.present
        assert prof.row_count == 6
        assert "amount" in prof.detected_columns


# --------------------------------------------------------------------------- #
# Bank reconciliation on a Tyler GL-detail ledger
# --------------------------------------------------------------------------- #
class TestBankRecTyler:
    _INPUTS = {"bank": str(TYLER_BANK), "ledger": str(TYLER_LEDGER)}
    _CONFIG = bankrec.ReconciliationConfig(date_tolerance_days=3)

    def test_preflight_never_fails(self):
        rep = run_preflight(
            bankrec.CAPABILITY,
            self._INPUTS,
            detect_conditions=bankrec.detect_conditions,
        )
        assert rep.status == PreflightStatus.PASS
        assert rep.llm_allowed is True
        # The debit/credit derivation is surfaced in the ledger profile notes.
        ledger_prof = {p.input_key: p for p in rep.file_profiles}["ledger"]
        assert any("Derived signed 'amount'" in n for n in ledger_prof.notes)

    def test_known_answers(self):
        out = bankrec.reconcile(
            str(TYLER_BANK), str(TYLER_LEDGER), config=self._CONFIG
        )
        s = out.summary
        assert s["bank_rows"] == 6
        assert s["ledger_rows"] == 6
        assert s["matched"] == 4
        assert s["timing_differences"] == 1
        assert s["unmatched_bank"] == 1  # bank interest 12.40
        assert s["unmatched_ledger"] == 1  # outstanding check 50236 (-900.00)
        assert s["duplicate_bank_pairs"] == 0
        assert s["duplicate_ledger_pairs"] == 0
        # The derivation is recorded for the audit trail (ledger side only).
        assert s["derived_amount_columns"] == {"ledger": "amount = debit - credit"}
        # Signed totals tie out to the derived debit-credit math.
        assert Decimal(s["total_ledger_amount"]) == Decimal("4634.25")
        assert Decimal(s["total_bank_amount"]) == Decimal("5546.65")

    def test_unmatched_ledger_cites_real_source_row(self):
        out = bankrec.reconcile(
            str(TYLER_BANK), str(TYLER_LEDGER), config=self._CONFIG
        )
        unmatched_ledger = [
            f
            for f in out.findings
            if f.finding_type == FindingType.UNMATCHED_LEDGER
        ]
        assert len(unmatched_ledger) == 1
        ref = unmatched_ledger[0].source_rows[0]
        assert ref.table_name == "ledger"
        assert ref.row_index == 4  # check 50236 row (0-based)
        # Source values cite the file's REAL columns (debit/credit), not the
        # derived column.
        assert "credit" in ref.source_values
        assert "amount" not in ref.source_values

    def test_end_to_end_run_with_exports(self, tmp_path):
        result = bankrec.run(
            dict(self._INPUTS),
            config=self._CONFIG,
            export_dir=tmp_path,
        )
        assert result["validation"].passed is True
        for name in bankrec.EXPORT_FILE_NAMES:
            assert (tmp_path / name).exists()

    def test_xlsx_ledger_same_results(self, tmp_path):
        df = pd.read_csv(TYLER_LEDGER, dtype=str, keep_default_na=False)
        xlsx = tmp_path / "tyler_style_ledger.xlsx"
        df.to_excel(xlsx, index=False)

        rep = run_preflight(
            bankrec.CAPABILITY,
            {"bank": str(TYLER_BANK), "ledger": str(xlsx)},
            detect_conditions=bankrec.detect_conditions,
        )
        assert rep.status == PreflightStatus.PASS

        out = bankrec.reconcile(str(TYLER_BANK), str(xlsx), config=self._CONFIG)
        assert out.summary["matched"] == 4
        assert out.summary["timing_differences"] == 1
        assert out.summary["unmatched_bank"] == 1
        assert out.summary["unmatched_ledger"] == 1

    def test_clean_two_file_sample_unchanged(self):
        # Regression: the standard sample still reconciles identically and the
        # derivation key does not appear when nothing was derived.
        out = bankrec.reconcile(
            str(_BANK_DIR / "bank.csv"), str(_BANK_DIR / "ledger.csv")
        )
        assert "derived_amount_columns" not in out.summary


# --------------------------------------------------------------------------- #
# Budget variance on a SINGLE combined Munis budget-to-actual export
# --------------------------------------------------------------------------- #
class TestBudgetVarianceTyler:
    _INPUTS = {"budget": str(TYLER_B2A), "actuals": str(TYLER_B2A)}

    def test_preflight_never_fails(self):
        rep = run_preflight(
            budvar.CAPABILITY,
            self._INPUTS,
            detect_conditions=budvar.detect_conditions,
        )
        assert rep.status == PreflightStatus.PASS
        assert rep.llm_allowed is True

    def test_known_answers(self):
        det = budvar.analyze(str(TYLER_B2A), str(TYLER_B2A))
        s = det.summary
        # Combined file: budget side reads Revised Budget, actuals side reads
        # YTD Actual -- deterministic and recorded in the summary.
        assert s["budget_amount_column"] == "revised_budget"
        assert s["actual_amount_column"] == "ytd_actual"
        assert s["join_keys"] == ["fund", "object"]
        assert s["joined_lines"] == 6
        assert s["flagged_variances"] == 4
        assert s["budget_only"] == 0
        assert s["actual_only"] == 0

        flagged = det.result_tables["flagged_variances"]
        flagged_keys = {
            (r["fund"], r["object"]) for _, r in flagged.iterrows()
        }
        assert flagged_keys == {
            ("100", "5010"),  # salaries -120,000 / -20%
            ("100", "5310"),  # contract services +16,250 / +36%
            ("200", "5610"),  # fuel -5,200 / -17.3% (pct threshold)
            ("300", "5710"),  # capital outlay -150,000 / -100%
        }

    def test_munis_account_strings_in_findings(self):
        det = budvar.analyze(str(TYLER_B2A), str(TYLER_B2A))
        # Every variance finding carries source rows on both sides of the join.
        for f in det.findings:
            assert f.finding_type == FindingType.VARIANCE
            assert len(f.source_rows) == 2
            assert all(r.row_index >= 0 for r in f.source_rows)

    def test_end_to_end_run_validates(self, tmp_path):
        wf = budvar.BudgetVarianceWorkflow()
        result = wf.run(dict(self._INPUTS))
        assert result.validation is not None
        assert result.validation.passed is True
        artifacts = wf.export_artifacts(result, tmp_path)
        names = {a.file_name for a in artifacts}
        assert "variance_summary.md" in names
        assert "flagged_variances.csv" in names

    def test_two_file_contract_unchanged(self):
        # Regression: the existing two-file sample picks the classic columns.
        det = budvar.analyze(
            str(_BUDGET_DIR / "budget.csv"), str(_BUDGET_DIR / "actuals.csv")
        )
        assert det.summary["budget_amount_column"] == "budget_amount"
        assert det.summary["actual_amount_column"] == "actual_amount"


# --------------------------------------------------------------------------- #
# Report review on a Tyler-style report + chart of accounts
# --------------------------------------------------------------------------- #
class TestReportReviewTyler:
    _INPUTS = {
        "report_table": str(TYLER_REPORT),
        "chart_of_accounts": str(TYLER_COA),
    }

    def test_preflight_never_fails_and_automaps(self):
        rep = run_preflight(
            report.CAPABILITY,
            self._INPUTS,
            detect_conditions=report.detect_conditions,
        )
        assert rep.status == PreflightStatus.PASS
        mappings = _mappings_from_report(rep)
        assert mappings["report_table"]["section"] == "report_section"
        assert mappings["report_table"]["line_type"] == "row_type"
        assert mappings["report_table"]["account_code"] == "account"
        assert mappings["report_table"]["account_name"] == "account_description"
        assert mappings["chart_of_accounts"]["account_code"] == "account"

    def test_known_answers(self, tmp_path):
        rep = run_preflight(
            report.CAPABILITY,
            self._INPUTS,
            detect_conditions=report.detect_conditions,
        )
        mappings = _mappings_from_report(rep)
        result = report.run(
            dict(self._INPUTS),
            column_mappings=mappings,
            export_dir=tmp_path,
        )
        assert result["validation"].passed is True
        by_rule: dict[str, int] = result["summary"]["findings_by_rule"]
        assert by_rule.get("subtotal_equals_line_item_sum") == 1
        assert by_rule.get("no_duplicate_account_lines") == 1
        assert by_rule.get("account_code_in_chart_of_accounts") == 1
        assert result["summary"]["total_findings"] == 3

        # The Munis account string flows through to the finding unchanged.
        invalid = [
            f
            for f in result["findings"]
            if f.rule_used == "account_code_in_chart_of_accounts"
        ]
        assert invalid[0].computed_values["account_code"] == "999-9999-9999"

        for name in report.EXPORT_FILE_NAMES:
            assert (tmp_path / name).exists()

    def test_xlsx_report_same_findings(self, tmp_path):
        df = pd.read_csv(TYLER_REPORT, dtype=str, keep_default_na=False)
        xlsx = tmp_path / "tyler_style_report.xlsx"
        df.to_excel(xlsx, index=False)
        inputs = {
            "report_table": str(xlsx),
            "chart_of_accounts": str(TYLER_COA),
        }
        rep = run_preflight(
            report.CAPABILITY, inputs, detect_conditions=report.detect_conditions
        )
        assert rep.status == PreflightStatus.PASS
        mappings = _mappings_from_report(rep)
        det = report.run_deterministic(
            xlsx,
            chart_of_accounts_path=TYLER_COA,
            column_mappings=mappings,
        )
        assert det.summary["total_findings"] == 3


# --------------------------------------------------------------------------- #
# Freeform: Tyler files attached as metadata only (no tabular requirement)
# --------------------------------------------------------------------------- #
class TestFreeformTyler:
    def test_preflight_passes_with_tyler_context(self):
        rep = run_preflight(
            freeform.CAPABILITY,
            {
                "task_type": "summarize_tyler_gl_export_columns",
                "sensitivity_confirmation": True,
                "uploaded_files": [str(TYLER_LEDGER)],
            },
            detect_conditions=freeform.detect_conditions,
        )
        assert rep.status == PreflightStatus.PASS
        assert rep.llm_allowed is True


# --------------------------------------------------------------------------- #
# Loaders + presets
# --------------------------------------------------------------------------- #
class TestLoadersAndPresets:
    def test_load_table_dispatches_by_extension(self, tmp_path):
        csv_parsed = load_table(TYLER_LEDGER)
        assert csv_parsed.file_type == "csv"
        assert csv_parsed.row_count == 6

        df = pd.read_csv(TYLER_LEDGER, dtype=str, keep_default_na=False)
        xlsx = tmp_path / "ledger.xlsx"
        df.to_excel(xlsx, index=False)
        xlsx_parsed = load_table(xlsx)
        assert xlsx_parsed.file_type == "excel"
        assert xlsx_parsed.row_count == 6
        assert list(xlsx_parsed.dataframe.columns) == list(
            csv_parsed.dataframe.columns
        )

    def test_tyler_preset_maps_check_register_headers(self):
        df = pd.DataFrame(
            {
                "Check Number": ["50100"],
                "Check Date": ["02/05/2026"],
                "Vendor Name": ["Acme Supply Co"],
                "Check Amount": ["1,250.75"],
            }
        )
        out = apply_preset(df, "tyler_munis_style")
        assert "date" in out.columns
        assert "amount" in out.columns
        assert "description" in out.columns  # vendor_name -> description
        assert "check_number" in out.columns  # reference field preserved

    def test_tyler_preset_does_not_clobber_existing_canonicals(self):
        df = pd.DataFrame(
            {"Date": ["2026-01-01"], "Check Date": ["2026-02-01"], "Amount": ["1"]}
        )
        out = apply_preset(df, "tyler_munis_style")
        # Existing canonical 'date'/'amount' win; check_date stays itself.
        assert out["date"].tolist() == ["2026-01-01"]
        assert "check_date" in out.columns
