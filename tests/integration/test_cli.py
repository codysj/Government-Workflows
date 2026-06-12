"""Integration tests for the generic workflow CLI (cli/run_workflow.py).

These drive the CLI two ways:
  * via ``main([...])`` with an in-process argv (fast, captures stdout), and
  * via a real ``subprocess`` invocation of the venv python (end-to-end, proves
    the script runs as a standalone process with exit code 0).

All runs use the bundled synthetic --sample data and the default mock LLM
(no API key, no internet). Exports are written under tmp_path so the test does
not depend on any shared on-disk DB.

Every run is routed through the shared runner, which performs the PREFLIGHT /
capability check BEFORE any deterministic analysis or LLM call. The CLI surfaces
the preflight status (PASS / PARTIAL / FAIL), the file profile, column mappings,
parse confidence, supported / unsupported conditions, the deterministic findings,
whether the LLM was called, and the export paths. On FAIL it prints the
structured report + next steps and NO AI explanation (exit 0 with a clear
``STATUS: FAILED`` banner so the run stays scriptable).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI = _REPO_ROOT / "cli" / "run_workflow.py"
_BANK_CSV = _REPO_ROOT / "data" / "synthetic" / "bank_reconciliation" / "bank.csv"
_LEDGER_CSV = _REPO_ROOT / "data" / "synthetic" / "bank_reconciliation" / "ledger.csv"

# Import main() for the in-process path.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from cli.run_workflow import main  # noqa: E402


def _run_id(out: str) -> str:
    line = next(l for l in out.splitlines() if l.startswith("Run ID:"))
    return line.split(":", 1)[1].strip()


def test_list_command_in_process(capsys):
    rc = main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bank-reconciliation" in out
    assert "budget-variance" in out
    assert "report-review" in out


def test_bank_reconciliation_sample_in_process(capsys, tmp_path):
    export_dir = tmp_path / "bank_out"
    rc = main(
        [
            "bank-reconciliation",
            "--sample",
            "--export",
            str(export_dir),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0

    # Preflight section + a PASS status line are surfaced.
    assert "PREFLIGHT:" in out
    assert "PREFLIGHT:  PASS" in out
    assert "STATUS:     PASS" in out
    # File profile (present / type / rows / columns) is printed.
    assert "File profiles:" in out
    assert "Column mappings (semantic -> column):" in out
    assert "Parse confidence" in out
    assert "Supported checks:" in out
    # Deterministic findings + run id + validation are printed.
    assert "Deterministic findings:" in out
    assert "Run ID:" in out
    assert "Validation:" in out
    # LLM-call status is explicit, and on a clean PASS the LLM was called.
    assert "LLM called: True" in out
    assert "Export paths:" in out
    run_id = _run_id(out)
    assert run_id
    # Exports were actually written under the per-run subdirectory, including
    # the preflight artifacts the runner now adds to every PASS/PARTIAL packet.
    run_dir = export_dir / run_id
    assert (run_dir / "reconciliation_summary.md").exists()
    assert (run_dir / "validation_report.json").exists()
    assert (run_dir / "preflight_report.json").exists()
    assert (run_dir / "preflight_summary.md").exists()


def test_bank_reconciliation_sample_subprocess(tmp_path):
    """End-to-end: invoke the CLI as a real process and assert exit 0 + run id."""
    export_dir = tmp_path / "bank_out_subprocess"
    proc = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "bank-reconciliation",
            "--sample",
            "--export",
            str(export_dir),
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "PREFLIGHT:  PASS" in proc.stdout
    assert "STATUS:     PASS" in proc.stdout
    assert "Run ID:" in proc.stdout
    run_id = _run_id(proc.stdout)
    assert run_id
    assert "Validation:" in proc.stdout
    assert "LLM called: True" in proc.stdout
    assert "Export paths:" in proc.stdout
    assert (export_dir / run_id / "reconciliation_summary.md").exists()


@pytest.mark.parametrize("workflow", ["budget-variance", "report-review"])
def test_other_samples_in_process(capsys, tmp_path, workflow):
    rc = main([workflow, "--sample", "--export", str(tmp_path / workflow)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREFLIGHT:" in out
    assert "STATUS:     PASS" in out
    assert "Run ID:" in out
    assert "Validation:" in out
    assert "LLM called:" in out
    assert "Export paths:" in out


def test_unknown_workflow_returns_error():
    rc = main(["list"])
    assert rc == 0
    # argparse rejects an entirely unknown subcommand with SystemExit(2).
    with pytest.raises(SystemExit):
        main(["definitely-not-a-workflow", "--sample"])


# --------------------------------------------------------------------------- #
# PREFLIGHT surfacing
# --------------------------------------------------------------------------- #
def test_preflight_fail_prints_report_and_no_ai(capsys, tmp_path):
    """A crafted FAIL input (missing the required 'ledger' file) must:
      * print a FAILED preflight banner + the blocking condition + next steps,
      * NOT print any AI explanation,
      * NOT run the LLM, and
      * still exit 0 (a preflight FAIL is a clean determination, not a crash).
    """
    export_dir = tmp_path / "fail_out"
    rc = main(
        [
            "bank-reconciliation",
            "--input",
            f"bank={_BANK_CSV}",
            "--export",
            str(export_dir),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0  # exit 0 with a FAILED banner (scriptable)
    assert "PREFLIGHT:  FAIL" in out
    assert "STATUS:     FAILED (preflight)" in out
    assert "The workflow was NOT run and the LLM was NOT called." in out
    # The structured report names the blocking condition + concrete next steps.
    assert "Blocking conditions (workflow NOT run):" in out
    assert "missing_required_file" in out
    assert "Next steps:" in out
    assert "ledger" in out
    # Absolutely NO AI explanation may be printed on a FAIL.
    assert "AI explanation" not in out
    # The failed run did not claim an LLM call.
    assert "LLM called: True" not in out
    # The failed-preflight export packet contains ONLY the two preflight files.
    run_id = _run_id(out)
    run_dir = export_dir / run_id
    assert (run_dir / "preflight_report.json").exists()
    assert (run_dir / "preflight_summary.md").exists()
    assert not (run_dir / "reconciliation_summary.md").exists()


def test_preflight_fail_subprocess_no_ai(tmp_path):
    """End-to-end FAIL: real process exits 0, prints FAILED, prints no AI text."""
    proc = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "bank-reconciliation",
            "--input",
            f"bank={_BANK_CSV}",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "PREFLIGHT:  FAIL" in proc.stdout
    assert "STATUS:     FAILED (preflight)" in proc.stdout
    assert "AI explanation" not in proc.stdout


def test_preflight_partial_labels_output_and_explains(capsys, tmp_path):
    """A sign-convention mismatch yields PARTIAL: the output is clearly labelled
    PARTIAL, the unsupported condition is surfaced with a next step, and the AI
    explanation is labelled as explaining deterministic findings only."""
    data = tmp_path / "partial"
    data.mkdir()
    bank = data / "bank.csv"
    ledger = data / "ledger.csv"
    bank.write_text(
        "date,description,amount\n"
        "2026-01-03,Check 1001,-100.00\n"
        "2026-01-04,Check 1002,-200.00\n"
        "2026-01-05,Check 1003,-300.00\n"
        "2026-01-06,Check 1004,-400.00\n",
        encoding="utf-8",
    )
    ledger.write_text(
        "date,description,amount\n"
        "2026-01-03,Check 1001,100.00\n"
        "2026-01-04,Check 1002,200.00\n"
        "2026-01-05,Check 1003,300.00\n"
        "2026-01-06,Check 1004,400.00\n",
        encoding="utf-8",
    )
    rc = main(
        [
            "bank-reconciliation",
            "--input",
            f"bank={bank}",
            "--input",
            f"ledger={ledger}",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREFLIGHT:  PARTIAL" in out
    assert "STATUS:     PARTIAL" in out
    assert "Unsupported conditions detected:" in out
    assert "possible_sign_convention_mismatch" in out
    assert "next step:" in out
    # The LLM is allowed on PARTIAL but only to explain deterministic findings.
    assert "AI explanation (PARTIAL" in out


def test_preflight_only_flag_skips_workflow(capsys):
    """--preflight-only prints the preflight report and does NOT run the
    workflow or the LLM."""
    rc = main(["bank-reconciliation", "--sample", "--preflight-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREFLIGHT:  PASS" in out
    assert "preflight-only; workflow NOT run" in out
    # No workflow output: no deterministic findings, no AI explanation.
    assert "Deterministic findings:" not in out
    assert "AI explanation" not in out


# --------------------------------------------------------------------------- #
# Tyler-era workflows (transaction search, AP duplicates, JE prep, PO review)
# --------------------------------------------------------------------------- #
_TYLER_DIR = _REPO_ROOT / "data" / "synthetic" / "tyler"
_JE_DIR = _REPO_ROOT / "data" / "synthetic" / "je_upload_prep"

import json  # noqa: E402


def _export_files(out: str, export_dir: Path) -> Path:
    """Return the per-run export subdirectory for a printed CLI run."""
    return export_dir / _run_id(out)


def test_list_includes_tyler_workflows(capsys):
    rc = main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    for name in ("transaction-search", "ap-duplicate-review",
                 "je-upload-prep", "po-invoice-review"):
        assert name in out, f"'{name}' missing from CLI list"


_TYLER_SAMPLE_CASES = [
    # (cli name, export files that must exist in the per-run dir)
    ("transaction-search",
     ("search_criteria.json", "search_results.csv", "search_summary.md",
      "validation_report.json", "audit_log.json")),
    ("ap-duplicate-review",
     ("flagged_payments.csv", "duplicate_groups.csv", "ap_review_summary.md",
      "review_notes_draft.md", "validation_report.json", "audit_log.json")),
    ("je-upload-prep",
     ("je_upload.xlsx", "je_upload.csv", "source_mapping.csv",
      "je_prep_summary.md", "validation_report.json", "audit_log.json")),
    ("po-invoice-review",
     ("po_invoice_exceptions.csv", "matched_po_invoices.csv",
      "po_review_summary.md", "review_notes_draft.md",
      "validation_report.json", "audit_log.json")),
]


@pytest.mark.parametrize("workflow,expected_files", _TYLER_SAMPLE_CASES)
def test_tyler_workflow_sample_in_process(capsys, tmp_path, workflow,
                                          expected_files):
    """Each new workflow runs end-to-end on --sample: preflight PASS, LLM
    called, validation passed, and all documented export files written."""
    export_dir = tmp_path / workflow
    rc = main([workflow, "--sample", "--export", str(export_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREFLIGHT:  PASS" in out
    assert "STATUS:     PASS" in out
    assert "Run ID:" in out
    assert "Validation: PASSED" in out
    assert "LLM called: True" in out
    assert "Export paths:" in out
    run_dir = _export_files(out, export_dir)
    for name in expected_files:
        assert (run_dir / name).is_file(), f"missing export: {name}"
    # validation_report.json records a passing validation.
    report = json.loads(
        (run_dir / "validation_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    # The consolidated review packet + preflight artifacts are present too.
    assert (run_dir / "review_packet.md").is_file()
    assert (run_dir / "preflight_report.json").is_file()


def test_tyler_workflow_sample_subprocess(tmp_path):
    """One of the new workflows end-to-end as a real process (exit 0)."""
    export_dir = tmp_path / "po_subprocess"
    proc = subprocess.run(
        [sys.executable, str(_CLI), "po-invoice-review", "--sample",
         "--export", str(export_dir)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "PREFLIGHT:  PASS" in proc.stdout
    assert "STATUS:     PASS" in proc.stdout
    assert "LLM called: True" in proc.stdout


def test_transaction_search_query_via_input_kv(capsys, tmp_path):
    """The free-text query passes through the generic --input key=value path."""
    export_dir = tmp_path / "ts_kv"
    rc = main([
        "transaction-search",
        "--input", "query=invoices to Cascade Paving over $5,000",
        "--input", f"ap_invoices={_TYLER_DIR / 'ap_invoice_detail.csv'}",
        "--export", str(export_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREFLIGHT:  PASS" in out
    assert "STATUS:     PASS" in out
    run_dir = _export_files(out, export_dir)
    criteria = json.loads(
        (run_dir / "search_criteria.json").read_text(encoding="utf-8"))
    # The parsed criteria reflect the query deterministically.
    assert criteria.get("vendor")
    assert criteria.get("amount_min") == "5000"


def test_transaction_search_fails_closed_without_data_file(capsys):
    """A query with NO data file is a preflight FAIL: no run, no LLM, no AI."""
    rc = main(["transaction-search", "--input", "query=anything at all"])
    out = capsys.readouterr().out
    assert rc == 0  # clean determination, scriptable banner
    assert "PREFLIGHT:  FAIL" in out
    assert "STATUS:     FAILED (preflight)" in out
    assert "AI explanation" not in out
    assert "LLM called: True" not in out


def test_je_upload_prep_invalid_draft_fails_closed(capsys, tmp_path):
    """The INVALID draft completes as a run (exit 0) but is NOT upload-ready:
    je_upload.xlsx / je_upload.csv are NOT written; the structured error
    report (je_validation_errors.csv) is."""
    export_dir = tmp_path / "je_invalid"
    rc = main([
        "je-upload-prep",
        "--input", f"je_draft={_JE_DIR / 'je_draft_invalid.csv'}",
        "--input", f"chart_of_accounts={_TYLER_DIR / 'chart_of_accounts.csv'}",
        "--input", f"gl_detail={_TYLER_DIR / 'gl_detail.csv'}",
        "--input", f"config={_JE_DIR / 'je_config.json'}",
        "--export", str(export_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    # The deterministic outcome is upload_ready: False in the summary.
    assert "upload_ready: False" in out
    run_dir = _export_files(out, export_dir)
    # FAIL CLOSED: no upload workbook may exist anywhere under the export dir.
    assert not (run_dir / "je_upload.xlsx").exists()
    assert not (run_dir / "je_upload.csv").exists()
    assert not list(export_dir.rglob("je_upload.xlsx"))
    # The structured error report and summary ARE exported.
    assert (run_dir / "je_validation_errors.csv").is_file()
    assert (run_dir / "je_prep_summary.md").is_file()


def test_mappings_flag_inline_json(capsys, tmp_path):
    """--mappings accepts inline JSON of human-approved column mappings and the
    run completes; the pinned mapping is reported as a human source."""
    import json

    mapping = {"bank": {"amount": "amount"}, "ledger": {"amount": "amount"}}
    rc = main(
        [
            "bank-reconciliation",
            "--sample",
            "--mappings",
            json.dumps(mapping),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREFLIGHT:  PASS" in out
    assert "source=human" in out
