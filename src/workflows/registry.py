"""Shared workflow registry / bootstrap.

This is the single place that imports every workflow module so their import-time
self-registration runs, then exposes a UNIFORM descriptor for each workflow that
the CLI (and any other caller) can drive generically.

Why a bootstrap is needed
--------------------------
The Expand agents each built a self-contained workflow with a slightly different
surface (per their integration notes / docs/decisions.md):

  * ``report_review``, ``freeform``, ``bank_reconciliation`` expose a module-level
    ``run(inputs, *, provider, ledger, audit, run_id, export_dir, config?) -> dict``
    that already returns ``run_id`` / ``summary`` / ``validation`` / ``export_paths``.
  * ``budget_variance`` exposes a ``BudgetVarianceWorkflow`` class whose ``run()``
    returns a ``WorkflowResult`` and a separate ``export_artifacts``.

This module adapts ``budget_variance`` to the same dict-returning ``run`` shape so
the CLI never needs workflow-specific branches. It contains NO Streamlit and NO
provider-specific code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.core.schemas import make_id

# Import every workflow module so its import-time registration runs.
from src.workflows import bank_reconciliation as _bank
from src.workflows import budget_variance as _budget
from src.workflows import freeform as _freeform
from src.workflows import report_review as _report

# Repo-relative location of the bundled synthetic sample data.
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "synthetic"


# --------------------------------------------------------------------------- #
# Uniform run signature
# --------------------------------------------------------------------------- #
# Every registered ``run`` is a callable:
#     run(inputs: dict, *, provider, ledger, audit, run_id, actor,
#         export_dir, config) -> dict
# returning at least: run_id, workflow_type, summary, validation, and
# (when export_dir is given) export_paths.


@dataclass
class WorkflowSpec:
    """A uniform, CLI-drivable description of one workflow."""

    workflow_type: str          # registry key, e.g. "bank_reconciliation"
    cli_name: str               # CLI subcommand, e.g. "bank-reconciliation"
    aliases: tuple[str, ...]    # extra accepted names
    run: Callable[..., dict]    # uniform dict-returning entry point
    sample_dir: Optional[Path]  # synthetic sample data dir (None for freeform)
    sample_inputs: Callable[[], dict]  # build the --sample inputs dict
    input_help: str             # human description of expected --input args
    help: str                   # one-line description


def _sample_dir(name: str) -> Path:
    return _DATA_ROOT / name


# --------------------------------------------------------------------------- #
# budget_variance adapter (class -> uniform dict-returning run)
# --------------------------------------------------------------------------- #
def _run_budget_variance(
    inputs: dict[str, Any],
    *,
    provider: Any = None,
    ledger: Any = None,
    audit: Any = None,
    run_id: Optional[str] = None,
    actor: str = "system",
    export_dir: str | Path | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """Adapt ``BudgetVarianceWorkflow`` to the shared dict-returning contract."""
    run_id = run_id or make_id()
    wf = _budget.BudgetVarianceWorkflow()

    # ``config`` (CLI) may carry thresholds (dict or path); merge into inputs.
    wf_inputs = dict(inputs)
    if config is not None and "thresholds" not in wf_inputs:
        wf_inputs["thresholds"] = config

    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=_budget.BudgetVarianceWorkflow.workflow_type)

    det = wf.run_deterministic(wf_inputs)
    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(run_id, actor, finding_count=len(det.findings))

    if audit is not None and hasattr(audit, "llm_request_sent"):
        audit.llm_request_sent(run_id, actor, template=_budget.PROMPT_TEMPLATE_VERSION)
    llm = wf.build_llm_output(det, provider)
    if ledger is not None and hasattr(ledger, "store_llm_response"):
        ledger.store_llm_response(run_id, llm)
    if audit is not None and hasattr(audit, "llm_response_received"):
        audit.llm_response_received(run_id, actor, model_name=llm.model_name)

    validation = wf.validate_llm(det, llm)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": wf.workflow_type,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm,
        "response_json": llm.response_json,
        "validation": validation,
    }

    if export_dir is not None:
        wf_result = _budget.WorkflowResult(
            workflow_type=wf.workflow_type,
            deterministic=det,
            llm_response=llm,
            validation=validation,
        )
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifacts = wf.export_artifacts(
            wf_result, export_dir, run_id=run_id, audit_events=audit_events
        )
        if ledger is not None and hasattr(ledger, "store_export_artifact"):
            for a in artifacts:
                ledger.store_export_artifact(run_id, a)
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(run_id, actor, artifacts=[a.file_name for a in artifacts])
        result["export_artifacts"] = artifacts
        result["export_paths"] = {a.file_name: a.path for a in artifacts}

    if audit is not None and hasattr(audit, "run_completed"):
        audit.run_completed(run_id, actor, passed=validation.passed)

    return result


# --------------------------------------------------------------------------- #
# --sample input builders (point at the bundled synthetic data)
# --------------------------------------------------------------------------- #
def _sample_bank_reconciliation() -> dict:
    d = _sample_dir("bank_reconciliation")
    return {
        "bank": str(d / "bank.csv"),
        "ledger": str(d / "ledger.csv"),
        "reconciliation_config": str(d / "reconciliation_config.json"),
    }


def _sample_budget_variance() -> dict:
    d = _sample_dir("budget_variance")
    return {
        "budget": str(d / "budget.csv"),
        "actuals": str(d / "actuals.csv"),
        "chart_of_accounts": str(d / "chart_of_accounts.csv"),
        "thresholds": str(d / "thresholds.json"),
    }


def _sample_report_review() -> dict:
    d = _sample_dir("report_review")
    return {
        "report_table": str(d / "report_table.csv"),
        "chart_of_accounts": str(d / "chart_of_accounts.csv"),
        "prior_version": str(d / "prior_version.csv"),
        "checklist_config": str(d / "checklist_config.json"),
    }


def _sample_freeform() -> dict:
    # Freeform has no tabular sample; supply a synthetic structured request that
    # confirms no real sensitive data (required) so the sample run completes.
    return {
        "task_type": "grant_reimbursement_summary",
        "desired_output": "A short plain-language reimbursement summary memo (draft).",
        "relevant_context": "Synthetic sample request for the bundled demo only.",
        "uploaded_files": [],
        "sensitivity_confirmation": True,
        "human_review_confirmation": True,
    }


# --------------------------------------------------------------------------- #
# Registry assembly
# --------------------------------------------------------------------------- #
_SPECS: list[WorkflowSpec] = [
    WorkflowSpec(
        workflow_type=_bank.WORKFLOW_TYPE,
        cli_name="bank-reconciliation",
        aliases=("bank_reconciliation", "reconciliation", "rec"),
        run=_bank.run,
        sample_dir=_sample_dir("bank_reconciliation"),
        sample_inputs=_sample_bank_reconciliation,
        input_help="bank=<bank.csv> ledger=<ledger.csv> [reconciliation_config=<config.json>]",
        help="Compare a bank statement to a ledger and flag exceptions.",
    ),
    WorkflowSpec(
        workflow_type=_budget.BudgetVarianceWorkflow.workflow_type,
        cli_name="budget-variance",
        aliases=("budget_variance", "variance"),
        run=_run_budget_variance,
        sample_dir=_sample_dir("budget_variance"),
        sample_inputs=_sample_budget_variance,
        input_help="budget=<budget.csv> actuals=<actuals.csv> "
        "[chart_of_accounts=<coa.csv>] [thresholds=<thresholds.json>]",
        help="Compute budget-to-actual variances and flag those over threshold.",
    ),
    WorkflowSpec(
        workflow_type=_report.WORKFLOW_TYPE,
        cli_name="report-review",
        aliases=("report_review", "report", "consistency"),
        run=_report.run,
        sample_dir=_sample_dir("report_review"),
        sample_inputs=_sample_report_review,
        input_help="report_table=<report.csv> [chart_of_accounts=<coa.csv>] "
        "[prior_version=<prior.csv>] [checklist_config=<config.json>]",
        help="Flag consistency / error issues in a financial report.",
    ),
    WorkflowSpec(
        workflow_type=_freeform.WORKFLOW_TYPE,
        cli_name="freeform",
        aliases=("guided-freeform", "guided_freeform"),
        run=_freeform.run,
        sample_dir=None,
        sample_inputs=_sample_freeform,
        input_help="task_type=<label> [desired_output=<text>] "
        "[relevant_context=<text>] sensitivity_confirmation=true",
        help="Guided freeform fallback for ad-hoc, source-linked draft tasks.",
    ),
]


# name -> spec (canonical cli name + aliases + workflow_type all resolve)
_BY_NAME: dict[str, WorkflowSpec] = {}
for _spec in _SPECS:
    _BY_NAME[_spec.cli_name] = _spec
    _BY_NAME[_spec.workflow_type] = _spec
    for _a in _spec.aliases:
        _BY_NAME[_a] = _spec


def list_specs() -> list[WorkflowSpec]:
    """Return all workflow specs in stable CLI order."""
    return list(_SPECS)


def get_spec(name: str) -> Optional[WorkflowSpec]:
    """Resolve a CLI name / alias / workflow_type to its spec (or None)."""
    return _BY_NAME.get(name) or _BY_NAME.get(name.replace("_", "-"))


def cli_names() -> list[str]:
    """Canonical CLI subcommand names, in order."""
    return [s.cli_name for s in _SPECS]
