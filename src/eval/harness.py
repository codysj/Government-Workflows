"""Evaluation harness (Phase 7).

Runs each workflow on its bundled synthetic known-answer dataset through
``src.core.pipeline.run_workflow`` -- the SAME entry point the API and CLI use
(preflight, config merge, validation, exports, review packet) -- with the
default mock LLM provider (no API key / no internet), an in-memory run ledger,
and an audit log. For each workflow it:

  1. resolves the workflow descriptor + its bundled sample inputs,
  2. runs the pipeline, timing the wall-clock runtime,
  3. derives the Phase-7 evaluation metrics (see ``src.eval.metrics``),
  4. compares deterministic output against the known-answer expectations,

then aggregates everything into a structured report (dict) and writes it to a
JSON file (default: ``runs/eval_report.json``).

Entry point
-----------
Programmatic::

    from src.eval.harness import run_eval
    report = run_eval(out_dir=Path("runs"))      # writes runs/eval_report.json

CLI::

    .venv\\Scripts\\python.exe -m src.eval.harness [--out runs/eval_report.json]
                                                   [--export]
                                                   [--workflow budget_variance ...]

The harness never references a specific LLM provider; the mock is the default.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.eval.metrics import (
    KnownAnswerResult,
    WorkflowMetrics,
    compute_metrics,
    known_answer_check,
)
from src.core.pipeline import MOCK, run_workflow
from src.workflows import registry as workflow_registry

# Repo root (this file lives in src/eval/).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = REPO_ROOT / "runs" / "eval_report.json"

# The workflows that have a synthetic known-answer dataset: the three MVP
# workflows plus the four Tyler/Munis-era workflows (whose samples run on the
# City of Riverbend dataset under data/synthetic/tyler, planted per
# data/synthetic/tyler/known_answers.json). Freeform has no tabular dataset /
# known-answer expectations, so it is excluded by default.
DEFAULT_WORKFLOWS: tuple[str, ...] = (
    "bank_reconciliation",
    "budget_variance",
    "report_review",
    "transaction_search",
    "ap_duplicate_review",
    "je_upload_prep",
    "po_invoice_review",
)


@dataclass
class WorkflowEvalResult:
    """The full evaluation outcome for one workflow."""

    workflow_type: str
    ran: bool
    metrics: Optional[WorkflowMetrics]
    known_answer: Optional[KnownAnswerResult]
    run_id: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_type": self.workflow_type,
            "ran": self.ran,
            "run_id": self.run_id,
            "error": self.error,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "known_answer": self.known_answer.to_dict() if self.known_answer else None,
        }


def run_workflow_eval(
    workflow_type: str,
    *,
    export_dir: Optional[Path] = None,
    provider: Any = None,
) -> WorkflowEvalResult:
    """Run one workflow on its synthetic dataset and evaluate it.

    Uses an in-memory ledger + a (non-JSONL) audit log so the harness leaves no
    shared on-disk state. ``provider`` defaults to None -> each workflow's mock
    LLM. ``export_dir`` (when given) is where export packets are written so the
    'export packets generated' metric is exercised end-to-end.
    """
    descriptor = workflow_registry.resolve(workflow_type)
    if descriptor is None:
        return WorkflowEvalResult(
            workflow_type=workflow_type,
            ran=False,
            metrics=None,
            known_answer=None,
            error=f"unknown workflow '{workflow_type}'",
        )

    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, write_jsonl=False)
    wf_export_dir = Path(export_dir) / workflow_type if export_dir is not None else None

    try:
        start = time.perf_counter()
        result = run_workflow(
            descriptor.workflow_type,
            descriptor.sample_inputs(),
            ledger=ledger,
            audit=audit,
            provider=provider if provider is not None else MOCK,
            actor="eval",
            export_dir=wf_export_dir,
        )
        runtime = time.perf_counter() - start
    except Exception as exc:  # pragma: no cover - defensive
        ledger.close()
        return WorkflowEvalResult(
            workflow_type=workflow_type,
            ran=False,
            metrics=None,
            known_answer=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    if result.refused or result.blocked:  # preflight FAIL / refusal: did not run
        ledger.close()
        return WorkflowEvalResult(
            workflow_type=workflow_type,
            ran=False,
            metrics=None,
            known_answer=None,
            run_id=result.run_id,
            error=result.refusal_reason or "run refused",
        )

    findings = result.findings
    summary = result.summary
    validation = result.validation
    export_paths = result.export_paths
    run_id = result.run_id

    metrics = compute_metrics(
        workflow_type=workflow_type,
        findings=findings,
        summary=summary,
        validation=validation,
        export_paths=export_paths,
        runtime_seconds=runtime,
    )
    ka = known_answer_check(
        workflow_type=workflow_type,
        metrics=metrics,
        summary=summary,
        findings=findings,
    )

    ledger.close()
    return WorkflowEvalResult(
        workflow_type=workflow_type,
        ran=True,
        metrics=metrics,
        known_answer=ka,
        run_id=run_id,
    )


def run_eval(
    workflows: Optional[list[str]] = None,
    *,
    out_dir: Optional[Path] = None,
    report_path: Optional[Path] = None,
    export: bool = True,
    write: bool = True,
    provider: Any = None,
) -> dict[str, Any]:
    """Run the full evaluation harness across the MVP workflows.

    Parameters
    ----------
    workflows:
        Workflow types to evaluate. Defaults to the three MVP known-answer
        workflows (bank_reconciliation, budget_variance, report_review).
    out_dir:
        Directory under which the report (and, when ``export`` is set, the
        per-workflow export packets) are written. Defaults to ``<repo>/runs``.
    report_path:
        Explicit path for the JSON report (overrides ``out_dir``).
    export:
        When True, run each workflow with an export directory so the
        'export packets generated' metric is exercised.
    write:
        When True (default), write the JSON report to disk.

    Returns the structured report dict.
    """
    names = list(workflows or DEFAULT_WORKFLOWS)
    base_out = Path(out_dir) if out_dir is not None else (REPO_ROOT / "runs")
    if report_path is None:
        report_path = base_out / "eval_report.json"
    else:
        report_path = Path(report_path)

    export_dir = (base_out / "eval_exports") if export else None

    results: list[WorkflowEvalResult] = [
        run_workflow_eval(name, export_dir=export_dir, provider=provider)
        for name in names
    ]

    workflows_passed = sum(
        1 for r in results if r.known_answer is not None and r.known_answer.passed
    )
    workflows_ran = sum(1 for r in results if r.ran)
    all_passed = all(
        r.ran and r.known_answer is not None and r.known_answer.passed
        for r in results
    )

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_mode": "mock" if provider is None else "injected",
        "totals": {
            "workflows_evaluated": len(results),
            "workflows_ran": workflows_ran,
            "workflows_known_answer_passed": workflows_passed,
            "all_passed": all_passed,
            "total_findings": sum(
                (r.metrics.findings_generated if r.metrics else 0) for r in results
            ),
            "total_export_packets": sum(
                (r.metrics.export_packets_generated if r.metrics else 0)
                for r in results
            ),
            "total_runtime_seconds": round(
                sum((r.metrics.runtime_seconds if r.metrics else 0.0) for r in results),
                6,
            ),
        },
        "workflows": {r.workflow_type: r.to_dict() for r in results},
    }

    if write:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        report["report_path"] = str(report_path)

    return report


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write a report dict to ``path`` as pretty JSON and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _format_console(report: dict[str, Any]) -> str:
    lines: list[str] = []
    totals = report["totals"]
    lines.append("Evaluation harness report")
    lines.append(
        f"  workflows: {totals['workflows_ran']}/{totals['workflows_evaluated']} ran, "
        f"{totals['workflows_known_answer_passed']} known-answer passed, "
        f"all_passed={totals['all_passed']}"
    )
    for wf_type, wf in report["workflows"].items():
        m = wf.get("metrics") or {}
        ka = wf.get("known_answer") or {}
        status = "PASS" if ka.get("passed") else "FAIL" if wf.get("ran") else "ERROR"
        lines.append(
            f"  [{status}] {wf_type}: "
            f"processed={m.get('transactions_processed')} "
            f"matched={m.get('rows_matched')} "
            f"unmatched={m.get('rows_unmatched')} "
            f"findings={m.get('findings_generated')} "
            f"warnings={m.get('validation_warnings')} "
            f"rejected={m.get('llm_outputs_rejected')} "
            f"exports={m.get('export_packets_generated')} "
            f"runtime={m.get('runtime_seconds')}s"
        )
        if wf.get("error"):
            lines.append(f"        error: {wf['error']}")
    if "report_path" in report:
        lines.append(f"  report written: {report['report_path']}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Phase-7 evaluation harness over the MVP workflows."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Path for the JSON eval report (default: runs/eval_report.json).",
    )
    parser.add_argument(
        "--workflow",
        action="append",
        dest="workflows",
        help="Limit to a specific workflow (repeatable). Default: all MVP.",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Skip writing export packets while evaluating.",
    )
    args = parser.parse_args(argv)

    report = run_eval(
        workflows=args.workflows,
        report_path=args.out,
        export=not args.no_export,
        write=True,
    )
    print(_format_console(report))
    return 0 if report["totals"]["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
