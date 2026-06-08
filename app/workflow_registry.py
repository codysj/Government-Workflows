"""Workflow registry + uniform run adapter for the Streamlit MVP UI.

This module is the ONLY place that knows how to drive each concrete workflow
module's (heterogeneous) entry point. It exposes a single uniform pipeline,
``run_workflow(...)``, that:

  1. creates a run-ledger entry (WorkflowRun),
  2. records input-file metadata + audit events,
  3. invokes the selected workflow (mock LLM by default),
  4. persists findings / LLM response / validation result,
  5. generates the spec-required export artifacts,
  6. writes audit events at every stage,

returning a plain dict the UI renders. It deliberately contains NO Streamlit
imports so it is unit-testable and so the UI layer stays separated from core
logic (spec sections 0.3 / 1.3). Provider-specific code never leaks into the
``src/workflows`` modules — only the mock default is used here unless a real
provider object is injected.

Each workflow module was built by a separate agent with a slightly different
public surface (see the integration notes), so this adapter pins each one
explicitly rather than assuming a single shared protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    DeterministicFinding,
    LLMResponse,
    RunStatus,
    ValidationResult,
    WorkflowRun,
    make_id,
)
from src.ingest.csv_loader import load_csv, to_input_file
from src.workflows import budget_variance, freeform, report_review

# Repo root (this file lives in app/).
REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"


# --------------------------------------------------------------------------- #
# Workflow descriptors
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class UploadField:
    """A single upload slot shown on the Run Workflow page."""

    key: str            # the inputs[...] key the workflow expects
    label: str          # plain-language label for finance staff
    required: bool
    file_types: tuple[str, ...] = ("csv",)
    help: str = ""


@dataclass(frozen=True)
class WorkflowDescriptor:
    workflow_type: str
    title: str
    description: str
    uploads: tuple[UploadField, ...]
    example_files: dict[str, str]   # inputs key -> path under data/synthetic
    available: bool = True
    note: str = ""


# Bank reconciliation has no implemented module / synthetic data in this build
# (no Expand agent delivered it). We surface it as a known-but-unavailable
# workflow so the UI is honest and never imports a missing module.
BANK_RECONCILIATION = WorkflowDescriptor(
    workflow_type="bank_reconciliation",
    title="Bank reconciliation",
    description=(
        "Compare a bank statement against a ledger export, match transactions "
        "deterministically by amount and date, and produce a reviewable "
        "exception packet. All matching is done by code; the AI only explains "
        "the unmatched items."
    ),
    uploads=(
        UploadField("bank_statement", "Bank statement (CSV)", True),
        UploadField("ledger", "Ledger export (CSV)", True),
        UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
        UploadField("config", "Reconciliation config (JSON)", False, ("json",)),
    ),
    example_files={},
    available=False,
    note=(
        "Not available in this build. The deterministic bank-reconciliation "
        "module has not been implemented yet."
    ),
)

BUDGET_VARIANCE = WorkflowDescriptor(
    workflow_type="budget_variance",
    title="Budget-to-actual variance review",
    description=(
        "Join a budget file to an actuals file by fund/account/department/"
        "object, compute dollar and percentage variances, and flag the lines "
        "that exceed your thresholds. All math is deterministic; the AI only "
        "drafts plain-language commentary citing the flagged rows."
    ),
    uploads=(
        UploadField("budget", "Budget (CSV)", True),
        UploadField("actuals", "Actuals (CSV)", True),
        UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
        UploadField("thresholds", "Variance thresholds (JSON)", False, ("json",)),
    ),
    example_files={
        "budget": "budget_variance/budget.csv",
        "actuals": "budget_variance/actuals.csv",
        "chart_of_accounts": "budget_variance/chart_of_accounts.csv",
        "thresholds": "budget_variance/thresholds.json",
    },
)

REPORT_REVIEW = WorkflowDescriptor(
    workflow_type="report_review",
    title="Financial report consistency review",
    description=(
        "Check a draft financial report or schedule for inconsistencies before "
        "a human finalizes it: subtotal mismatches, invalid account codes, "
        "duplicate lines, missing sections, large changes from a prior version, "
        "and inconsistent naming. The AI only explains the flagged issues."
    ),
    uploads=(
        UploadField("report_table", "Report table (CSV)", True),
        UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
        UploadField("prior_version", "Prior version (CSV)", False),
        UploadField("checklist_config", "Review checklist (JSON)", False, ("json",)),
    ),
    example_files={
        "report_table": "report_review/report_table.csv",
        "chart_of_accounts": "report_review/chart_of_accounts.csv",
        "prior_version": "report_review/prior_version.csv",
        "checklist_config": "report_review/checklist_config.json",
    },
)

FREEFORM = WorkflowDescriptor(
    workflow_type="freeform",
    title="Guided freeform task",
    description=(
        "A controlled fallback for tasks that are not yet a formal workflow. "
        "You describe the task with structured fields (not a chat box); the "
        "request is routed through the same logging and validation system. "
        "Output is always a DRAFT requiring human review."
    ),
    uploads=(
        UploadField("uploaded_files", "Supporting files (optional)", False,
                    ("csv", "xlsx", "txt", "md", "pdf", "json")),
    ),
    example_files={},
)

DESCRIPTORS: dict[str, WorkflowDescriptor] = {
    d.workflow_type: d
    for d in (BANK_RECONCILIATION, BUDGET_VARIANCE, REPORT_REVIEW, FREEFORM)
}

# Display order for the Run Workflow page.
WORKFLOW_ORDER = (
    "bank_reconciliation",
    "budget_variance",
    "report_review",
    "freeform",
)


def list_descriptors() -> list[WorkflowDescriptor]:
    return [DESCRIPTORS[k] for k in WORKFLOW_ORDER]


def get_descriptor(workflow_type: str) -> WorkflowDescriptor:
    return DESCRIPTORS[workflow_type]


def example_path(rel: str) -> Path:
    """Resolve a synthetic example file path under data/synthetic."""
    return SYNTHETIC_DIR / rel


def make_id_safe() -> str:
    """A filesystem-safe unique id (for temp upload subdirectories)."""
    return make_id()


# --------------------------------------------------------------------------- #
# Uniform run result
# --------------------------------------------------------------------------- #
@dataclass
class UniformRunResult:
    run_id: str
    workflow_type: str
    findings: list[DeterministicFinding]
    summary: dict[str, Any]
    result_tables: dict[str, Any]
    llm_response: Optional[LLMResponse]
    validation: Optional[ValidationResult]
    export_paths: dict[str, str]
    refused: bool = False
    refusal_reason: str = ""


# --------------------------------------------------------------------------- #
# Internal helpers to normalize each workflow's output
# --------------------------------------------------------------------------- #
def _input_files_for(inputs: dict[str, Any]) -> list:
    """Build InputFile metadata for any CSV paths present in ``inputs``."""
    out = []
    for key, val in inputs.items():
        if not isinstance(val, (str, Path)):
            continue
        p = Path(val)
        if p.is_file() and p.suffix.lower() == ".csv":
            try:
                parsed = load_csv(p, table_name=key)
                out.append(to_input_file(p, parsed))
            except Exception:
                # Metadata best-effort; never block a run on it.
                pass
    return out


def _run_budget_variance(inputs, provider, export_dir, run_id):
    wf = budget_variance.BudgetVarianceWorkflow()
    result = wf.run(inputs, provider=provider)
    det = result.deterministic
    export_paths: dict[str, str] = {}
    if export_dir is not None:
        artifacts = budget_variance.export_artifacts(
            result, export_dir, run_id=run_id
        )
        export_paths = {a.file_name: a.path for a in artifacts}
    return UniformRunResult(
        run_id=run_id,
        workflow_type="budget_variance",
        findings=det.findings,
        summary=det.summary,
        result_tables=det.result_tables,
        llm_response=result.llm_response,
        validation=result.validation,
        export_paths=export_paths,
    )


def _run_report_review(inputs, provider, export_dir, run_id, ledger, audit, actor):
    res = report_review.run(
        inputs,
        provider=provider,
        ledger=ledger,
        audit=audit,
        run_id=run_id,
        actor=actor,
        export_dir=export_dir,
    )
    det = res["deterministic"]
    return UniformRunResult(
        run_id=run_id,
        workflow_type="report_review",
        findings=res["findings"],
        summary=res["summary"],
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res["llm_response"],
        validation=res["validation"],
        export_paths={k: str(v) for k, v in res.get("export_paths", {}).items()},
    )


def _run_freeform(inputs, provider, export_dir, run_id, ledger, audit, actor):
    res = freeform.run(
        inputs,
        provider=provider,
        ledger=ledger,
        audit=audit,
        run_id=run_id,
        actor=actor,
        export_dir=export_dir,
    )
    det = res["deterministic"]
    return UniformRunResult(
        run_id=run_id,
        workflow_type="freeform",
        findings=res["findings"],
        summary=res["summary"],
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res["llm_response"],
        validation=res["validation"],
        export_paths={k: str(v) for k, v in res.get("export_paths", {}).items()},
    )


# --------------------------------------------------------------------------- #
# Uniform pipeline entry point
# --------------------------------------------------------------------------- #
def run_workflow(
    workflow_type: str,
    inputs: dict[str, Any],
    *,
    ledger: RunLedger,
    audit: Optional[AuditLog] = None,
    provider: Any = None,
    actor: str = "finance_staff",
    export_dir: Optional[str | Path] = None,
) -> UniformRunResult:
    """Drive any registered workflow through the shared ledger/audit/export
    pipeline and return a uniform result.

    ``provider`` defaults to None, which means each workflow uses its built-in
    mock LLM (no API key / no internet) — the spec's default path.
    """
    descriptor = DESCRIPTORS.get(workflow_type)
    if descriptor is None or not descriptor.available:
        raise ValueError(f"Workflow '{workflow_type}' is not available.")

    run_id = make_id()
    if export_dir is not None:
        export_dir = Path(export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ledger entry + input-file metadata.
    input_files = _input_files_for(inputs)
    run = WorkflowRun(
        run_id=run_id,
        workflow_type=workflow_type,
        created_by=actor,
        input_files=input_files,
        status=RunStatus.RUNNING,
    )
    ledger.create_run(run)
    if audit is not None:
        audit.run_created(run_id, actor, workflow_type=workflow_type)
        for f in input_files:
            audit.file_uploaded(run_id, actor, file_name=f.file_name,
                                file_hash=f.file_hash)

    try:
        if workflow_type == "budget_variance":
            result = _run_budget_variance(inputs, provider, export_dir, run_id)
            # budget_variance does not self-persist; do it here.
            ledger.store_findings(run_id, result.findings)
            if audit is not None:
                audit.deterministic_analysis_completed(
                    run_id, actor, finding_count=len(result.findings))
            if result.llm_response is not None:
                ledger.store_llm_response(run_id, result.llm_response)
                if audit is not None:
                    audit.llm_request_sent(
                        run_id, actor,
                        template=result.llm_response.prompt_template_version)
                    audit.llm_response_received(
                        run_id, actor,
                        model_name=result.llm_response.model_name)
            if result.validation is not None:
                ledger.store_validation_result(run_id, result.validation)
                if audit is not None:
                    audit.validation_completed(
                        run_id, actor, passed=result.validation.passed)
        elif workflow_type == "report_review":
            # report_review self-persists findings/llm/validation when given
            # ledger+audit; it does not write a run row (we did that above).
            result = _run_report_review(
                inputs, provider, export_dir, run_id, ledger, audit, actor)
        elif workflow_type == "freeform":
            result = _run_freeform(
                inputs, provider, export_dir, run_id, ledger, audit, actor)
        else:  # pragma: no cover - guarded above
            raise ValueError(workflow_type)
    except freeform.SensitivityNotConfirmedError as exc:
        ledger.update_run_status(run_id, RunStatus.FAILED.value)
        return UniformRunResult(
            run_id=run_id,
            workflow_type=workflow_type,
            findings=[],
            summary={},
            result_tables={},
            llm_response=None,
            validation=None,
            export_paths={},
            refused=True,
            refusal_reason=str(exc) or "Sensitivity confirmation required.",
        )
    except Exception:
        ledger.update_run_status(run_id, RunStatus.FAILED.value)
        if audit is not None:
            audit.run_failed(run_id, actor)
        raise

    # 4/5. Persist export artifacts to the ledger (report_review/freeform
    # already store their own; budget_variance + any others recorded here).
    if export_dir is not None and result.export_paths:
        if audit is not None:
            audit.export_generated(
                run_id, actor, artifacts=sorted(result.export_paths))

    # 6. Mark completed + persist summary + validation status.
    val = result.validation
    validation_status = (
        "passed" if (val is not None and val.passed)
        else "warnings" if (val is not None and val.warnings and not val.errors)
        else "failed" if val is not None else "n/a"
    )
    summary = dict(result.summary or {})
    summary["validation_status"] = validation_status
    ledger.update_run_status(
        run_id, RunStatus.COMPLETED.value, summary=summary)
    if audit is not None:
        audit.run_completed(run_id, actor,
                            validation_status=validation_status)
    result.summary = summary
    return result


# --------------------------------------------------------------------------- #
# Human review actions
# --------------------------------------------------------------------------- #
# The set of actions the spec requires per finding.
HUMAN_REVIEW_ACTIONS = (
    ("mark_reviewed", "Mark reviewed"),
    ("mark_resolved", "Mark resolved"),
    ("needs_follow_up", "Needs follow-up"),
    ("add_note", "Add note"),
    ("reject_ai_explanation", "Reject AI explanation"),
    ("approve_draft", "Approve draft for export"),
)


def record_human_review_action(
    ledger: RunLedger,
    audit: Optional[AuditLog],
    *,
    run_id: str,
    action: str,
    actor: str,
    finding_id: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """Persist a HumanReviewAction to the ledger + an audit event.

    Returns the new action_id. This is the single path the UI uses for every
    per-finding review control so persistence + audit always stay in sync.
    """
    from src.core.schemas import HumanReviewAction

    hra = HumanReviewAction(
        run_id=run_id,
        finding_id=finding_id,
        action=action,
        actor=actor,
        note=note,
    )
    ledger.store_human_review_action(run_id, hra)
    if audit is not None:
        audit.human_review_action(
            run_id, actor, action=action, finding_id=finding_id, note=note)
    return hra.action_id
