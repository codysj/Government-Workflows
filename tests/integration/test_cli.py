"""Integration tests for the generic workflow CLI (cli/run_workflow.py).

These drive the CLI two ways:
  * via ``main([...])`` with an in-process argv (fast, captures stdout), and
  * via a real ``subprocess`` invocation of the venv python (end-to-end, proves
    the script runs as a standalone process with exit code 0).

All runs use the bundled synthetic --sample data and the default mock LLM
(no API key, no internet). Exports are written under tmp_path so the test does
not depend on any shared on-disk DB.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI = _REPO_ROOT / "cli" / "run_workflow.py"

# Import main() for the in-process path.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from cli.run_workflow import main  # noqa: E402


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
    assert "Run ID:" in out
    assert "Validation:" in out
    assert "Export paths:" in out
    # A run id is a 32-char uuid4 hex; just assert a non-empty value was printed.
    run_id_line = next(l for l in out.splitlines() if l.startswith("Run ID:"))
    assert run_id_line.split(":", 1)[1].strip()
    # Exports were actually written.
    assert (export_dir / "reconciliation_summary.md").exists()
    assert (export_dir / "validation_report.json").exists()


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
    assert "Run ID:" in proc.stdout
    run_id_line = next(l for l in proc.stdout.splitlines() if l.startswith("Run ID:"))
    assert run_id_line.split(":", 1)[1].strip()
    assert "Validation:" in proc.stdout
    assert "Export paths:" in proc.stdout
    assert (export_dir / "reconciliation_summary.md").exists()


@pytest.mark.parametrize("workflow", ["budget-variance", "report-review"])
def test_other_samples_in_process(capsys, tmp_path, workflow):
    rc = main([workflow, "--sample", "--export", str(tmp_path / workflow)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Run ID:" in out
    assert "Validation:" in out
    assert "Export paths:" in out


def test_unknown_workflow_returns_error():
    rc = main(["list"])
    assert rc == 0
    # argparse rejects an entirely unknown subcommand with SystemExit(2).
    with pytest.raises(SystemExit):
        main(["definitely-not-a-workflow", "--sample"])
