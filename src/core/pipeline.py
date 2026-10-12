"""The single run pipeline every caller uses (API, CLI, eval harness, MCP).

``run_workflow`` drives any registered workflow through the same steps:

  1. create the run-ledger entry and record input-file provenance,
  2. PREFLIGHT the inputs (FAIL -> stop: no workflow, no LLM call),
  3. run the workflow module (deterministic analysis -> LLM draft ->
     validation), mock LLM unless a provider is injected,
  4. on PARTIAL, surface the unsupported conditions as findings,
  5. record export artifacts, mark the run complete, stamp provenance,
  6. build the consolidated review packet.

It also owns the human-review action surface. No UI, HTTP, or provider code
lives here.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.core import preflight as preflight_engine
from src.core.audit_log import AuditLog
from src.core.review_packet import (
    generate_failed_preflight_packet,
    generate_review_packet,
)
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    DeterministicFinding,
    FindingType,
    HumanReviewAction,
    LLMResponse,
    PreflightReport,
    PreflightStatus,
    RetentionCategory,
    RunStatus,
    Severity,
    ValidationResult,
    WorkflowRun,
    make_id,
)
from src.ingest.csv_loader import to_input_file
from src.ingest.excel_loader import load_table
from src.llm.provider import get_provider, llm_mode
from src.workflows.freeform import SensitivityNotConfirmedError
from src.workflows.registry import DESCRIPTORS, WorkflowDescriptor, get_descriptor


MOCK = "mock"  # run_workflow(provider=MOCK): offline mock regardless of LLM_MODE


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
    preflight: Optional[PreflightReport] = None
    preflight_status: str = "pass"   # pass | partial | fail
    partial: bool = False
    blocked: bool = False            # True when preflight FAILed (workflow not run)
    llm_called: bool = False


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
def run_preflight(
    workflow_type: str,
    inputs: dict[str, Any],
    *,
    config: Optional[dict] = None,
    column_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> PreflightReport:
    """Run ONLY the capability check (no workflow, no LLM, nothing persisted).

    Raises KeyError for an unknown workflow type.
    """
    descriptor = get_descriptor(workflow_type)
    return preflight_engine.run_preflight(
        descriptor.module.CAPABILITY,
        inputs,
        approved_mappings=column_mappings,
        config=config,
        detect_conditions=getattr(descriptor.module, "detect_conditions", None),
        labels={u.key: u.label for u in descriptor.uploads},
    )


def _approved_mappings_from_report(
    report: PreflightReport,
) -> dict[str, dict[str, str]]:
    """Columns a human chose or preflight matched confidently, as
    ``{input_key: {semantic: column}}``.

    Forwarded so the workflow reads the columns preflight confidently matched
    (e.g. an ERP export's "Posting Date"). Uncertain best guesses are NOT
    forwarded: the workflow's own detection decides those (it knows, e.g.,
    that a combined budget-to-actual file's budget side is "Revised Budget",
    not the only column containing "actual").
    """
    out: dict[str, dict[str, str]] = {}
    for m in report.column_mappings:
        if m.source in ("human", "auto") and m.mapped_column:
            out.setdefault(m.input_key, {})[m.semantic_name] = m.mapped_column
    return out


def _preflight_findings_as_deterministic(
    report: PreflightReport,
) -> list[DeterministicFinding]:
    """Non-blocking, non-info preflight findings as reviewable findings (PARTIAL)."""
    return [
        DeterministicFinding(
            finding_type=FindingType.OTHER,
            severity=f.severity,
            description=f"[preflight] {f.message}",
            source_rows=[],
            computed_values={
                "preflight_code": f.code.value,
                "affected_input": f.affected_input or "",
                "affected_column": f.affected_column or "",
                "suggested_action": f.suggested_action,
            },
            rule_used=f"preflight:{f.code.value}",
            requires_human_review=True,
        )
        for f in report.findings
        if not f.blocks_run and f.severity != Severity.INFO
    ]


def _record_preflight_metadata(
    summary: dict[str, Any],
    report: PreflightReport,
    *,
    approved_mappings: dict[str, dict[str, str]],
    llm_called: bool,
) -> None:
    """Stamp preflight provenance onto the run summary (in place)."""
    summary["preflight_status"] = report.status.value
    summary["partial"] = report.partial
    summary["llm_called"] = llm_called
    summary["detected_columns"] = {
        p.input_key: list(p.detected_columns) for p in report.file_profiles
    }
    resolved: dict[str, dict[str, str]] = {}
    for m in report.column_mappings:
        if m.source != "unmapped" and m.mapped_column:
            resolved.setdefault(m.input_key, {})[m.semantic_name] = m.mapped_column
    summary["column_mappings_resolved"] = resolved
    summary["column_mappings_used"] = approved_mappings
    summary["parse_confidence"] = {
        p.input_key: dict(p.parse_confidence) for p in report.file_profiles
    }
    summary["supported_checks"] = list(report.supported_checks)
    summary["unsupported_conditions"] = list(report.unsupported_conditions)


# --------------------------------------------------------------------------- #
# Run helpers
# --------------------------------------------------------------------------- #
def _input_files_for(inputs: dict[str, Any], file_keys: set[str]) -> list:
    """InputFile provenance (name, hash, rows) for every CSV/Excel file input.
    Only declared file slots: a text field that looks like a path is not read."""
    out = []
    for key, val in inputs.items():
        if key not in file_keys or not isinstance(val, (str, Path)):
            continue
        p = Path(val)
        if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xls"):
            try:
                out.append(to_input_file(p, load_table(p, table_name=key)))
            except Exception:
                pass  # provenance is best-effort; never block a run on it
    return out


class _GuardedProvider:
    """Provider wrapper: a provider failure (timeout, HTTP error, missing key,
    unparseable output) must never discard the deterministic results, which
    are the authoritative output. The draft becomes an explicit "unavailable"
    note that cites nothing, so validation marks it failed and the reviewer
    sees why. Only the exception TYPE is shown (its text may carry a URL)."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.model_provider = getattr(inner, "model_provider", "unknown")
        self.model_name = getattr(inner, "model_name", "unknown")

    def _unavailable(self, exc: Exception) -> dict:
        return {
            "summary": (f"AI draft unavailable ({type(exc).__name__}). The "
                        "deterministic findings are complete; review them directly."),
            "referenced_source_rows": [],
            "ai_unavailable": True,
        }

    def generate_structured_response(self, prompt: str, schema: Any = None) -> dict:
        try:
            return self._inner.generate_structured_response(prompt, schema)
        except Exception as exc:  # noqa: BLE001 - any provider failure
            return self._unavailable(exc)

    def generate_text_response(self, prompt: str) -> str:
        try:
            return self._inner.generate_text_response(prompt)
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)["summary"]

    def mock_response(self, prompt: str, schema: Any = None) -> dict:
        return self._inner.mock_response(prompt, schema)


class _LifecycleSuppressingAudit:
    """Audit proxy that forwards granular events but drops runner-owned ones.

    Workflow modules emit their own ``run_created`` / ``run_completed`` /
    ``run_failed`` / ``export_generated``; the pipeline is the single owner of
    those, so the module's copies are suppressed. Granular events
    (deterministic_analysis_completed, llm_*, validation_completed) pass through.
    """

    _OWNED_BY_RUNNER = {"run_created", "run_completed", "run_failed", "export_generated"}

    def __init__(self, inner: AuditLog):
        self._inner = inner

    def __getattr__(self, name: str):
        if name in self._OWNED_BY_RUNNER:
            return lambda *a, **k: None
        return getattr(self._inner, name)


_ARTIFACT_TYPE_BY_SUFFIX = {".md": "markdown", ".csv": "csv", ".json": "json", ".zip": "zip"}


def _ensure_export_artifacts_recorded(
    ledger: RunLedger, run_id: str, export_paths: dict[str, str]
) -> None:
    """Record any export file the workflow wrote but did not store (idempotent)."""
    from src.core.exports import sha256_file
    from src.core.schemas import ArtifactType, ExportArtifact

    existing = {a.get("file_name") for a in ledger.list_export_artifacts(run_id)}
    for name, path in export_paths.items():
        p = Path(path)
        if name in existing or not p.is_file():
            continue
        atype = _ARTIFACT_TYPE_BY_SUFFIX.get(p.suffix.lower(), "other")
        ledger.store_export_artifact(run_id, ExportArtifact(
            run_id=run_id, artifact_type=ArtifactType(atype),
            file_name=name, path=str(p), sha256=sha256_file(p)))


# Deterministic config keys each Tyler-era workflow documents. Only these are
# threaded from a caller config into the run, so one workflow's settings never
# leak into another's thresholds.
_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "transaction_search": ("max_results", "fuzzy_threshold", "missing_po_threshold"),
    "ap_duplicate_review": (
        "near_date_window_days", "amount_tolerance", "split_threshold",
        "split_window_days", "missing_po_min_amount", "vendor_similarity_threshold",
    ),
    "je_upload_prep": (
        "fiscal_period_start", "fiscal_period_end", "allow_inactive_accounts",
        "round_dollar_warning_threshold", "min_description_length",
    ),
    "po_invoice_review": (
        "unit_price_tolerance_pct", "qty_tolerance", "invoice_over_po_tolerance_pct",
        "closed_po_grace_days", "missing_po_min_amount",
    ),
}


def _config_for(workflow_type: str, inputs: dict, config: Optional[dict]) -> Any:
    """Map caller config (tolerances/thresholds) onto each workflow's shape.

    An uploaded config/threshold file in ``inputs`` always wins (returns None).
    """
    config = config or {}
    if workflow_type in _CONFIG_KEYS:
        if inputs.get("config"):
            return None
        out = {k: config[k] for k in _CONFIG_KEYS[workflow_type] if k in config}
        return out or None
    if workflow_type == "bank_reconciliation":
        if inputs.get("reconciliation_config"):
            return None
        out: dict[str, Any] = {}
        if "amount_tolerance" in config:
            out["amount_tolerance"] = str(config["amount_tolerance"])
        if "date_tolerance_days" in config:
            out["date_tolerance_days"] = int(config["date_tolerance_days"])
        return out or None
    if workflow_type == "budget_variance":
        if inputs.get("thresholds") or not (
            "variance_dollar_threshold" in config or "variance_threshold_pct" in config
        ):
            return None
        return {
            "dollar_threshold": config.get("variance_dollar_threshold"),
            "pct_threshold": config.get("variance_threshold_pct"),
        }
    return None


def _call_workflow(
    descriptor: WorkflowDescriptor,
    inputs: dict[str, Any],
    *,
    export_dir: Optional[Path],
    run_export_dir: Optional[Path],
    **kwargs: Any,
) -> UniformRunResult:
    """Call the module's shared ``run()`` and normalize its result dict.

    Only ``config`` / ``column_mappings`` / ``preflight_report`` may be
    skipped (freeform takes no config; the Tyler-era modules take no mappings;
    only they reuse the pipeline's preflight). Any other keyword the module
    does not accept is a contract bug and raises.
    """
    if run_export_dir is None:
        wf_export = None
    elif descriptor.appends_run_id_to_export_dir:
        wf_export = export_dir  # the module nests <run_id> itself
    else:
        wf_export = run_export_dir
    params = inspect.signature(descriptor.module.run).parameters
    missing = set(kwargs) - set(params) - {"config", "column_mappings", "preflight_report"}
    if missing:
        raise TypeError(f"{descriptor.workflow_type}.run() lacks {sorted(missing)}")
    res = descriptor.module.run(
        inputs, export_dir=wf_export,
        **{k: v for k, v in kwargs.items() if k in params},
    )
    det = res.get("deterministic")
    return UniformRunResult(
        run_id=kwargs["run_id"],
        workflow_type=descriptor.workflow_type,
        findings=list(res.get("findings") or []),
        summary=dict(res.get("summary") or {}),
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res.get("llm_response"),
        validation=res.get("validation"),
        export_paths={k: str(v) for k, v in (res.get("export_paths") or {}).items()},
    )


# --------------------------------------------------------------------------- #
# Pipeline entry point
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
    config: Optional[dict] = None,
    retention_category: str = "draft_working",
    review_packet: bool = True,
    column_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> UniformRunResult:
    """Drive any registered workflow through the shared pipeline.

    ``provider=None`` means each workflow's built-in mock LLM (offline), or
    the configured real provider when ``LLM_MODE=real``; ``provider=MOCK``
    ("mock") always uses the offline mock (CLI default, eval, benchmarks).
    ``config`` carries caller tolerances/thresholds; uploaded config files in
    ``inputs`` take precedence. ``retention_category`` tags the run for
    records-retention (unknown values fall back to ``draft_working``).
    ``column_mappings`` are human-approved column overrides fed to preflight.
    Raises ValueError for an unknown workflow type.
    """
    descriptor = DESCRIPTORS.get(workflow_type)
    if descriptor is None:
        raise ValueError(f"Workflow '{workflow_type}' is not available.")

    if provider == MOCK:  # forced offline mock, whatever LLM_MODE says
        provider = None
    elif provider is None and llm_mode() == "real":
        provider = get_provider()
    if provider is not None:
        provider = _GuardedProvider(provider)
    run_id = make_id()
    run_export_dir: Optional[Path] = None
    if export_dir is not None:
        export_dir = Path(export_dir)
        run_export_dir = export_dir / run_id  # runs never overwrite each other
        run_export_dir.mkdir(parents=True, exist_ok=True)

    wf_config = _config_for(workflow_type, inputs, config)
    wf_audit = _LifecycleSuppressingAudit(audit) if audit is not None else None
    try:
        retention = RetentionCategory(retention_category)
    except ValueError:
        retention = RetentionCategory.DRAFT_WORKING

    # 1. Ledger entry + input-file provenance.
    input_files = _input_files_for(inputs, {u.key for u in get_descriptor(workflow_type).uploads})
    ledger.create_run(WorkflowRun(
        run_id=run_id,
        workflow_type=workflow_type,
        created_by=actor,
        input_files=input_files,
        status=RunStatus.RUNNING,
        retention_category=retention,
    ))
    if audit is not None:
        audit.run_created(run_id, actor, workflow_type=workflow_type,
                          retention_category=retention.value)
        for f in input_files:
            audit.file_uploaded(run_id, actor, file_name=f.file_name,
                                file_hash=f.file_hash)

    # 2. PREFLIGHT, before the workflow and before any LLM call.
    report = run_preflight(
        workflow_type, inputs,
        config=wf_config if isinstance(wf_config, dict) else config,
        column_mappings=column_mappings,
    )
    ledger.store_preflight(run_id, preflight_engine.preflight_report_dict(report))
    if audit is not None:
        audit.file_parsed(
            run_id, actor, stage="preflight",
            preflight_status=report.status.value,
            unsupported_conditions=report.unsupported_conditions)
    approved = _approved_mappings_from_report(report)

    # FAIL: do NOT run the workflow and do NOT call the LLM.
    if report.status == PreflightStatus.FAIL:
        summary: dict[str, Any] = {}
        _record_preflight_metadata(
            summary, report, approved_mappings=approved, llm_called=False)
        summary["validation_status"] = "n/a"
        summary["retention_category"] = retention.value
        summary["blocked"] = True
        ledger.update_run_status(
            run_id, RunStatus.FAILED.value, summary=summary,
            retention_category=retention.value)
        export_paths: dict[str, str] = {}
        if run_export_dir is not None:
            try:
                packet = generate_failed_preflight_packet(
                    ledger, audit, run_id, export_dir,
                    preflight=preflight_engine.preflight_report_dict(report),
                    actor=actor)
                export_paths = {a.file_name: a.path for a in packet}
            except Exception:
                pass
        if audit is not None:
            audit.run_failed(run_id, actor, reason="preflight_failed",
                             preflight_status="fail")
        return UniformRunResult(
            run_id=run_id,
            workflow_type=workflow_type,
            findings=[],
            summary=summary,
            result_tables={},
            llm_response=None,
            validation=None,
            export_paths=export_paths,
            refused=True,
            refusal_reason=(
                "Preflight failed; the workflow was not run. "
                + (report.next_steps[0] if report.next_steps else "")
            ).strip(),
            preflight=report,
            preflight_status="fail",
            blocked=True,
        )

    # 3. Run the workflow (it persists findings / LLM draft / validation).
    try:
        result = _call_workflow(
            descriptor, inputs,
            export_dir=export_dir, run_export_dir=run_export_dir,
            provider=provider, ledger=ledger, audit=wf_audit, run_id=run_id,
            actor=actor, config=wf_config, column_mappings=approved or None,
            preflight_report=report,
        )
    except SensitivityNotConfirmedError as exc:
        ledger.update_run_status(run_id, RunStatus.FAILED.value)
        if audit is not None:
            audit.run_failed(run_id, actor, reason="sensitivity_not_confirmed")
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

    # 4. PARTIAL: surface the unsupported conditions as reviewable findings.
    llm_called = result.llm_response is not None
    if report.status == PreflightStatus.PARTIAL:
        extra = _preflight_findings_as_deterministic(report)
        if extra:
            result.findings = list(result.findings) + extra
            ledger.store_findings(run_id, result.findings)

    # 5. Artifacts, completion, provenance.
    if run_export_dir is not None and result.export_paths:
        _ensure_export_artifacts_recorded(ledger, run_id, result.export_paths)
        if audit is not None:
            audit.export_generated(run_id, actor, artifacts=sorted(result.export_paths))

    val = result.validation
    validation_status = (
        "passed" if (val is not None and val.passed)
        else "warnings" if (val is not None and val.warnings and not val.errors)
        else "failed" if val is not None else "n/a"
    )
    summary = dict(result.summary or {})
    summary["validation_status"] = validation_status
    summary["retention_category"] = retention.value
    _record_preflight_metadata(
        summary, report, approved_mappings=approved, llm_called=llm_called)
    ledger.update_run_status(
        run_id, RunStatus.COMPLETED.value, summary=summary,
        retention_category=retention.value)
    if audit is not None:
        audit.run_completed(run_id, actor, validation_status=validation_status,
                            preflight_status=report.status.value)
    result.summary = summary
    result.preflight = report
    result.preflight_status = report.status.value
    result.partial = report.status == PreflightStatus.PARTIAL
    result.llm_called = llm_called

    # 6. Consolidated review packet (from persisted ledger data).
    if review_packet and export_dir is not None:
        try:
            for a in generate_review_packet(ledger, audit, run_id, export_dir, actor=actor):
                result.export_paths[a.file_name] = a.path
        except Exception:
            pass  # a packet failure must never fail an otherwise-successful run

    return result


# --------------------------------------------------------------------------- #
# Human review
# --------------------------------------------------------------------------- #
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
    """Persist a HumanReviewAction plus its audit event; returns the action_id."""
    hra = HumanReviewAction(
        run_id=run_id, finding_id=finding_id, action=action, actor=actor, note=note)
    ledger.store_human_review_action(run_id, hra)
    if audit is not None:
        audit.human_review_action(
            run_id, actor, action=action, finding_id=finding_id, note=note)
    return hra.action_id


# Run-level review-status policy (the atomic write primitive lives in
# RunLedger.apply_human_review_status):
#   - approve_draft / reject_ai_explanation set the terminal status (latest wins)
#   - mark_reviewed / mark_resolved / needs_follow_up promote pending->in_review
#     only, never downgrading an approved/rejected run
#   - add_note and unknown actions never change status
_REVIEW_STATUS_DECISIONS = {"approve_draft": "approved", "reject_ai_explanation": "rejected"}
_REVIEW_ENGAGEMENT_ACTIONS = ("mark_reviewed", "mark_resolved", "needs_follow_up")


def apply_review_status_transition(ledger: RunLedger, run_id: str, action: str) -> str:
    """Advance a run's human_review_status for ``action``, atomically.

    Returns the status that actually persisted. No read-modify-write, so
    interleaved transitions cannot race.
    """
    if action in _REVIEW_STATUS_DECISIONS:
        result = ledger.apply_human_review_status(run_id, _REVIEW_STATUS_DECISIONS[action])
    elif action in _REVIEW_ENGAGEMENT_ACTIONS:
        result = ledger.apply_human_review_status(
            run_id, "in_review", expected_current="pending")
    else:
        run = ledger.get_run(run_id) or {}
        result = run.get("human_review_status", "pending") or "pending"
    return result or "pending"
