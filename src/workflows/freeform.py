"""Phase 5 — Guided Freeform Mode.

A CONTROLLED fallback for finance tasks that are not yet formal workflows. This
is NOT an open chatbot. The user supplies a small set of STRUCTURED fields (not
a free chat box); deterministic code records the structured request and the
attached file metadata as a minimal finding set, auto-injects available
city/context references, builds a versioned structured prompt, calls the LLM
provider (mock by default), validates source references when possible, logs the
interaction through the SAME ledger + audit pipeline used by the other
workflows, and appends the task_type to a discovery log for future workflow
discovery.

Architecture rules honored (spec 0.3 / 1 / Phase 5):
  * DETERMINISTIC code owns: collecting/validating structured input, recording
    the request + file metadata as findings (each with a SourceRowRef),
    building the prompt, validation, audit logging, export formatting, and
    appending to the discovery log.
  * The LLM may ONLY explain/summarize/draft/classify/flag. It NEVER calculates,
    decides, or invents accounts/funds/vendors/amounts/dates/policy, and it must
    cite source-row references. The mock provider derives its output ONLY from
    the deterministic request record (no fabricated numbers).
  * Refuses to run unless ``sensitivity_confirmation`` is given (no real
    sensitive data). Never promises authoritative answers; all output is a DRAFT
    requiring human review.

This module contains NO Streamlit and NO provider-specific code.

Integration note
----------------
The Foundation agent's integration contract was truncated (stream idle timeout)
and the shared ``context_loader``, ``provider.py``, ``workflow_runner.py`` were
not all on disk at implementation time (see docs/decisions.md). Following the
same pattern already established by ``report_review.py`` and
``budget_variance.py``, this module is self-contained but plug-in friendly: it
exposes ``run(...)``, ``register(registry)``, ``WORKFLOW``, ``WORKFLOW_TYPE``,
and ``WORKFLOW_REGISTRY``. Injected ``provider`` / ``ledger`` / ``audit`` /
``context_loader`` are used when present (by the method names those modules
already expose); otherwise local fallbacks keep mock mode (the default) runnable
with no API key and no internet.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core.schemas import (
    ArtifactType,
    DeterministicFinding,
    ExportArtifact,
    FindingType,
    LLMResponse,
    Severity,
    SourceRowRef,
    ValidationResult,
    make_id,
)
from src.core.exports import write_json, write_markdown
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.llm.provider import MockLLMProvider as _CoreMockLLMProvider
from src.llm.provider import _extract_findings_from_prompt

# --------------------------------------------------------------------------- #
# Module constants / registry (single import line for the Integration agent)
# --------------------------------------------------------------------------- #
WORKFLOW_TYPE = "freeform"
PROMPT_TEMPLATE_VERSION = "freeform.v1"

WORKFLOW_REGISTRY: dict[str, Any] = {}

# Discovery log: task_type patterns are appended here for future workflow
# discovery (spec Phase 5 "Discovery output").
DISCOVERY_LOG_PATH = Path("docs/research/freeform_task_observations.md")

EXPORT_FILE_NAMES = (
    "freeform_summary.md",
    "freeform_request.json",
    "freeform_draft.md",
    "validation_report.json",
    "audit_log.json",
)

# The STRUCTURED input fields (spec Phase 5 "Required input fields").
REQUIRED_INPUT_FIELDS = (
    "task_type",
    "uploaded_files",
    "desired_output",
    "relevant_context",
    "sensitivity_confirmation",
    "human_review_confirmation",
)


# --------------------------------------------------------------------------- #
# Structured input
# --------------------------------------------------------------------------- #
@dataclass
class FreeformRequest:
    """The STRUCTURED freeform request (NOT a free chat box).

    ``sensitivity_confirmation`` must be True to certify that no real sensitive
    data is being used; the run is refused otherwise.
    """

    task_type: str
    desired_output: str = ""
    relevant_context: str = ""
    uploaded_files: list[dict[str, Any]] = field(default_factory=list)
    sensitivity_confirmation: bool = False
    human_review_confirmation: bool = False

    @classmethod
    def from_inputs(cls, inputs: dict[str, Any]) -> "FreeformRequest":
        return cls(
            task_type=str(inputs.get("task_type", "")).strip(),
            desired_output=str(inputs.get("desired_output", "")).strip(),
            relevant_context=str(inputs.get("relevant_context", "")).strip(),
            uploaded_files=_normalize_uploaded_files(inputs.get("uploaded_files")),
            sensitivity_confirmation=bool(inputs.get("sensitivity_confirmation", False)),
            human_review_confirmation=bool(inputs.get("human_review_confirmation", False)),
        )


class SensitivityNotConfirmedError(RuntimeError):
    """Raised when a freeform run is attempted without sensitivity confirmation.

    Enforces the spec prohibition on accepting real sensitive data: if the user
    has not confirmed that no real sensitive data is present, we REFUSE to run.
    """


def _normalize_uploaded_files(raw: Any) -> list[dict[str, Any]]:
    """Coerce assorted ``uploaded_files`` shapes into a list of metadata dicts.

    Accepts: None, a single path/str, a path-like, or a list whose items are
    paths/strings or already-formed metadata dicts. Deterministic code records
    only file METADATA (name, type, size, sha256 when a real path is given) —
    it never reads/parses content here; that is a future per-task concern.
    """
    if raw is None:
        return []
    if isinstance(raw, (str, Path)):
        raw = [raw]
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            meta = dict(item)
            meta.setdefault("file_name", meta.get("name", ""))
            out.append(meta)
            continue
        p = Path(str(item))
        meta = {"file_name": p.name, "file_type": p.suffix.lstrip(".") or "unknown"}
        try:
            if p.is_file():
                data = p.read_bytes()
                meta["size_bytes"] = len(data)
                meta["sha256"] = hashlib.sha256(data).hexdigest()
        except OSError:
            pass
        out.append(meta)
    return out


# --------------------------------------------------------------------------- #
# Deterministic output container
# --------------------------------------------------------------------------- #
@dataclass
class FreeformDeterministicOutput:
    """Minimal finding set for freeform.

    Exposes ``.findings`` and ``.summary`` — the same shape the shared prompt
    builder / LLM layer consumes — so freeform flows through validation/audit/
    export exactly like the tabular workflows.
    """

    request: FreeformRequest
    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    context_refs: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Context auto-injection (spec Phase 5 step 4)
# --------------------------------------------------------------------------- #
def _load_context_refs(context_loader: Any = None) -> dict[str, Any]:
    """Auto-inject available city/context references.

    Uses an injected ``context_loader`` when given. Otherwise attempts the
    shared ``src.context.context_loader`` module; if that is unavailable (the
    Foundation contract was truncated) it returns an empty, safe default so mock
    mode still runs with no internet. Only NON-sensitive reference data should
    ever appear here (city profile, chart of accounts), never task payloads.
    """
    loader = context_loader
    if loader is None:
        try:  # pragma: no cover - import guard for parallel-build ordering
            from src.context import context_loader as _ctx  # type: ignore

            loader = _ctx
        except Exception:
            loader = None
    if loader is None:
        return {}

    for attr in ("load_context", "load_available_context", "load", "get_context"):
        fn = getattr(loader, attr, None)
        if callable(fn):
            try:
                refs = fn()
                if isinstance(refs, dict):
                    return refs
                return {"context": refs}
            except Exception:
                return {}
    return {}


# --------------------------------------------------------------------------- #
# Deterministic stage
# --------------------------------------------------------------------------- #
def run_deterministic(
    request: FreeformRequest, *, context_loader: Any = None
) -> FreeformDeterministicOutput:
    """Record the structured request + file metadata as a minimal finding set.

    No calculation is performed. Each finding carries a SourceRowRef so the LLM
    output can be validated against real references, exactly like the other
    workflows. Refuses to run without sensitivity confirmation.
    """
    if not request.sensitivity_confirmation:
        raise SensitivityNotConfirmedError(
            "Freeform run refused: sensitivity_confirmation is required to "
            "certify that no real sensitive data is used."
        )
    if not request.task_type:
        raise ValueError("Freeform run refused: 'task_type' is required.")

    context_refs = _load_context_refs(context_loader)
    findings: list[DeterministicFinding] = []

    # Finding 1: the structured request itself (the single "request" table row).
    request_values = {
        "task_type": request.task_type,
        "desired_output": request.desired_output,
        "relevant_context": request.relevant_context,
        "human_review_confirmation": request.human_review_confirmation,
        "sensitivity_confirmation": request.sensitivity_confirmation,
    }
    request_ref = SourceRowRef(
        file_id="freeform_request",
        table_name="freeform_request",
        row_index=0,
        column_names=sorted(request_values.keys()),
        source_values=request_values,
    )
    findings.append(
        DeterministicFinding(
            finding_type=FindingType.OTHER,
            severity=Severity.INFO,
            description=(
                f"Structured freeform request recorded for task_type "
                f"'{request.task_type}'. Desired output: "
                f"{request.desired_output or '(unspecified)'}."
            ),
            source_rows=[request_ref],
            computed_values={
                "task_type": request.task_type,
                "uploaded_file_count": len(request.uploaded_files),
                "human_review_confirmation": request.human_review_confirmation,
            },
            rule_used="freeform: record structured request (deterministic)",
            requires_human_review=True,
        )
    )

    # Finding(s): one per attached file's METADATA (no content parsing here).
    for i, meta in enumerate(request.uploaded_files):
        file_ref = SourceRowRef(
            file_id=str(meta.get("sha256") or meta.get("file_name") or f"file_{i}"),
            table_name="freeform_files",
            row_index=i,
            column_names=sorted(meta.keys()),
            source_values=dict(meta),
        )
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.OTHER,
                severity=Severity.INFO,
                description=(
                    f"Attached file recorded: {meta.get('file_name', '(unnamed)')}."
                ),
                source_rows=[file_ref],
                computed_values={
                    "file_name": meta.get("file_name", ""),
                    "file_type": meta.get("file_type", ""),
                },
                rule_used="freeform: record attached file metadata (deterministic)",
                requires_human_review=True,
            )
        )

    summary = {
        "task_type": request.task_type,
        "desired_output": request.desired_output,
        "uploaded_file_count": len(request.uploaded_files),
        "uploaded_files": [m.get("file_name", "") for m in request.uploaded_files],
        "human_review_confirmation": request.human_review_confirmation,
        "context_refs_available": sorted(context_refs.keys()),
        "finding_count": len(findings),
    }
    return FreeformDeterministicOutput(
        request=request,
        findings=findings,
        summary=summary,
        context_refs=context_refs,
    )


# --------------------------------------------------------------------------- #
# Versioned structured prompt
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team, "
    "operating in GUIDED FREEFORM mode (a controlled fallback, NOT a chatbot).\n"
    "STRICT RULES:\n"
    "- You may ONLY explain, summarize, draft, classify, and flag.\n"
    "- You MUST NOT calculate, decide, or invent account numbers, funds, "
    "vendors, amounts, dates, or official policy.\n"
    "- Use ONLY the structured request, attached file metadata, and context "
    "references provided below. Do not assume any other data exists.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- Do NOT promise authoritative financial answers and do NOT produce "
    "final/official language; ALL output is a DRAFT for human review.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), draft (str), "
    "suggested_review_steps (list of str), "
    "referenced_source_rows (list of str), "
    "clarifying_questions (list of str), needs_human_review (bool).\n"
)


def _findings_block(det: FreeformDeterministicOutput) -> str:
    rows = []
    for f in det.findings:
        rows.append(
            {
                "finding_id": f.finding_id,
                "finding_type": f.finding_type.value,
                "description": f.description,
                "computed_values": f.computed_values,
                "source_row_ids": [
                    f"{s.table_name}:{s.row_index}" for s in f.source_rows
                ],
            }
        )
    return json.dumps(rows, default=str, indent=2)


def build_prompt(det: FreeformDeterministicOutput) -> str:
    req = det.request
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Guided freeform task (controlled fallback).\n"
        + f"TASK TYPE: {req.task_type}\n"
        + f"DESIRED OUTPUT: {req.desired_output or '(unspecified)'}\n"
        + f"RELEVANT CONTEXT (user-provided): {req.relevant_context or '(none)'}\n\n"
        + "TASK: Draft the requested output as plain language, summarize what "
        "the request is asking for, suggest human review steps, and list any "
        "clarifying questions. Do NOT compute values or invent data.\n\n"
        + f"AUTO-INJECTED CONTEXT REFERENCES:\n"
        + json.dumps(det.context_refs, default=str, indent=2)
        + "\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM provider (DEFAULT path: no API key, no internet)
# --------------------------------------------------------------------------- #
class MockLLMProvider(_CoreMockLLMProvider):
    """Deterministic mock for guided freeform.

    Subclasses the canonical ``src.llm.provider.MockLLMProvider`` (sharing its
    method surface and offline contract) and overrides ``_build`` to produce the
    freeform-specific output contract (draft, clarifying_questions,
    needs_human_review). Derived ONLY from the structured request / deterministic
    findings; it never calculates or invents data and it cites the real
    source-row ids embedded in the prompt.
    """

    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        ref_ids: list[str] = []
        for f in findings:
            ref_ids.extend(f.get("source_row_ids", []))
        task_type = _extract_line(prompt, "TASK TYPE:")
        desired = _extract_line(prompt, "DESIRED OUTPUT:")
        return {
            "summary": (
                f"DRAFT (for human review): freeform request of type "
                f"'{task_type or 'unspecified'}'. This is a controlled draft, "
                "not an authoritative financial answer."
            ),
            "draft": (
                "DRAFT — requires human review before any use.\n"
                f"Requested output: {desired or '(unspecified)'}.\n"
                "A finance staff member must verify all figures against source "
                "documents; this assistant did not compute or invent any values."
            ),
            "suggested_review_steps": [
                "Confirm the intended task and required source files.",
                "Verify every figure against the underlying source documents.",
                "Have finance staff edit and approve before any official use.",
            ],
            "referenced_source_rows": sorted(set(ref_ids)),
            "clarifying_questions": [
                "Which specific source files should be used for this task?",
                "What is the exact desired output format?",
            ],
            "needs_human_review": True,
        }


def _extract_line(prompt: str, marker: str) -> str:
    idx = prompt.find(marker)
    if idx == -1:
        return ""
    tail = prompt[idx + len(marker):]
    return tail.splitlines()[0].strip() if tail else ""


def _call_llm(
    det: FreeformDeterministicOutput, provider: Any = None
) -> tuple[dict, str, str]:
    provider = provider or MockLLMProvider()
    prompt = build_prompt(det)
    raw = provider.generate_structured_response(prompt, schema=None)
    if not isinstance(raw, dict):
        raw = {"summary": str(raw), "referenced_source_rows": []}
    return (
        raw,
        getattr(provider, "model_provider", "mock"),
        getattr(provider, "model_name", "mock-llm"),
    )


# --------------------------------------------------------------------------- #
# Validation (deterministic guardrail check)
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: dict, det: FreeformDeterministicOutput
) -> ValidationResult:
    """Thin wrapper: the canonical validator lives in ``src.core.validation``.

    Freeform treats missing references / missing draft / a missing human-review
    flag as WARNINGS (the request itself may legitimately have a single source
    row), so those knobs are set accordingly.
    """
    return _core_validate_llm_output(
        response_json,
        det,
        require_references=True,
        missing_references_is_error=False,
        check_numeric_claims=False,
        require_draft=True,
        require_human_review_flag=True,
    )


# --------------------------------------------------------------------------- #
# Discovery log (spec Phase 5 "Discovery output")
# --------------------------------------------------------------------------- #
def append_discovery_observation(
    task_type: str,
    *,
    desired_output: str = "",
    uploaded_file_count: int = 0,
    run_id: Optional[str] = None,
    log_path: str | Path | None = None,
) -> Path:
    """Append the freeform task_type to the discovery log for future workflow
    discovery. Append-only; the header is created if the file is new."""
    path = Path(log_path) if log_path is not None else DISCOVERY_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    ts = datetime.now(timezone.utc).isoformat()
    line = (
        f"| {ts} | {run_id or '-'} | {task_type} | "
        f"{desired_output or '-'} | {uploaded_file_count} |\n"
    )
    with path.open("a", encoding="utf-8") as fh:
        if is_new:
            fh.write(_DISCOVERY_HEADER)
        fh.write(line)
    return path


_DISCOVERY_HEADER = (
    "# Guided Freeform — Task Observations\n"
    "\n"
    "Append-only discovery log (spec Phase 5 \"Discovery output\"). Each guided "
    "freeform run records its `task_type` here so recurring patterns can be "
    "promoted into dedicated, deterministic workflow templates later. No task "
    "payloads or sensitive data are stored — only the task type and lightweight "
    "metadata.\n"
    "\n"
    "## Example entry\n"
    "\n"
    "`| 2026-01-01T00:00:00+00:00 | example-run-id | grant_reimbursement_summary "
    "| one-page reimbursement memo | 2 |`\n"
    "\n"
    "## Observations\n"
    "\n"
    "| timestamp_utc | run_id | task_type | desired_output | uploaded_file_count |\n"
    "| --- | --- | --- | --- | --- |\n"
)


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def export_artifacts(
    out_dir: str | Path,
    det: FreeformDeterministicOutput,
    response_json: dict,
    validation: ValidationResult,
    audit_events: Optional[list[dict]] = None,
    *,
    run_id: Optional[str] = None,
) -> list[ExportArtifact]:
    """Write the freeform review packet and return artifact manifests.

    File-writing + hashing go through the shared ``src.core.exports`` primitives.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportArtifact] = []

    def _write(name: str, text: str, atype: ArtifactType) -> None:
        if atype == ArtifactType.MARKDOWN:
            artifacts.append(write_markdown(out / name, text, run_id=run_id))
        else:
            artifacts.append(write_json(out / name, text, run_id=run_id))

    req = det.request
    summary_md = [
        "# Guided Freeform Task Summary",
        "",
        f"- Task type: {req.task_type}",
        f"- Desired output: {req.desired_output or '(unspecified)'}",
        f"- Attached files: {det.summary.get('uploaded_file_count', 0)}",
        f"- Human review confirmed: {req.human_review_confirmation}",
        f"- Context references available: "
        f"{', '.join(det.summary.get('context_refs_available', [])) or '(none)'}",
        "",
        "_This is a controlled freeform task, not an authoritative financial "
        "answer. All AI output is a DRAFT for human review._",
    ]
    _write("freeform_summary.md", "\n".join(summary_md), ArtifactType.MARKDOWN)

    request_record = {
        "task_type": req.task_type,
        "desired_output": req.desired_output,
        "relevant_context": req.relevant_context,
        "uploaded_files": req.uploaded_files,
        "sensitivity_confirmation": req.sensitivity_confirmation,
        "human_review_confirmation": req.human_review_confirmation,
        "context_refs": det.context_refs,
    }
    _write(
        "freeform_request.json",
        json.dumps(request_record, default=str, indent=2),
        ArtifactType.JSON,
    )

    draft_md = ["# Freeform Draft (DRAFT — for human review)", ""]
    draft_md.append(response_json.get("summary", ""))
    draft_md.append("")
    if response_json.get("draft"):
        draft_md.append(response_json["draft"])
        draft_md.append("")
    if response_json.get("suggested_review_steps"):
        draft_md.append("## Suggested review steps")
        for s in response_json["suggested_review_steps"]:
            draft_md.append(f"- {s}")
        draft_md.append("")
    if response_json.get("clarifying_questions"):
        draft_md.append("## Clarifying questions")
        for q in response_json["clarifying_questions"]:
            draft_md.append(f"- {q}")
    _write("freeform_draft.md", "\n".join(draft_md), ArtifactType.MARKDOWN)

    _write(
        "validation_report.json",
        json.dumps(validation.model_dump(), default=str, indent=2),
        ArtifactType.JSON,
    )
    _write(
        "audit_log.json",
        json.dumps(audit_events or [], default=str, indent=2),
        ArtifactType.JSON,
    )
    return artifacts


# --------------------------------------------------------------------------- #
# Workflow entry point (same shape as report_review.run)
# --------------------------------------------------------------------------- #
def run(
    inputs: dict[str, Any],
    *,
    provider: Any = None,
    ledger: Any = None,
    audit: Any = None,
    context_loader: Any = None,
    run_id: Optional[str] = None,
    actor: str = "system",
    export_dir: str | Path | None = None,
    discovery_log_path: str | Path | None = None,
) -> dict[str, Any]:
    """End-to-end guided freeform run, routed through the SAME ledger/audit/
    validation pipeline as the other workflows.

    ``inputs`` keys (STRUCTURED fields, not a chat box):
        task_type                 (required) short label for the task
        desired_output            (optional) what output the user wants
        relevant_context          (optional) user-provided context text
        uploaded_files            (optional) list of paths or metadata dicts
        sensitivity_confirmation  (REQUIRED True) certifies no real sensitive data
        human_review_confirmation (optional) acknowledges human review required

    Refuses to run (raises ``SensitivityNotConfirmedError``) when
    ``sensitivity_confirmation`` is not given.

    Returns a dict with run_id, deterministic output, llm response_json,
    validation result, the discovery-log path, and (when export_dir is set)
    artifact paths.
    """
    run_id = run_id or make_id()
    request = FreeformRequest.from_inputs(inputs)

    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=WORKFLOW_TYPE)

    # Deterministic stage (refuses without sensitivity confirmation).
    try:
        det = run_deterministic(request, context_loader=context_loader)
    except SensitivityNotConfirmedError:
        if audit is not None and hasattr(audit, "run_failed"):
            audit.run_failed(
                run_id, actor, reason="sensitivity_confirmation_missing"
            )
        raise

    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )

    # LLM assist (advisory; mock by default).
    if audit is not None and hasattr(audit, "llm_request_sent"):
        audit.llm_request_sent(run_id, actor, template=PROMPT_TEMPLATE_VERSION)
    response_json, model_provider, model_name = _call_llm(det, provider)
    llm_response = LLMResponse(
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        model_provider=model_provider,
        model_name=model_name,
        response_json=response_json,
        referenced_source_rows=list(response_json.get("referenced_source_rows", []) or []),
    )
    if ledger is not None and hasattr(ledger, "store_llm_response"):
        ledger.store_llm_response(run_id, llm_response)
    if audit is not None and hasattr(audit, "llm_response_received"):
        audit.llm_response_received(run_id, actor, model_name=model_name)

    # Validation.
    validation = validate_llm_output(response_json, det)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    # Discovery log (save task_type for future workflow discovery).
    discovery_path = append_discovery_observation(
        request.task_type,
        desired_output=request.desired_output,
        uploaded_file_count=len(request.uploaded_files),
        run_id=run_id,
        log_path=discovery_log_path,
    )

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
        "discovery_log_path": str(discovery_path),
    }

    # Exports.
    if export_dir is not None:
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifacts = export_artifacts(
            export_dir, det, response_json, validation, audit_events, run_id=run_id
        )
        if ledger is not None and hasattr(ledger, "store_export_artifact"):
            for a in artifacts:
                ledger.store_export_artifact(run_id, a)
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(
                run_id, actor, artifacts=[a.file_name for a in artifacts]
            )
        result["export_artifacts"] = artifacts
        result["export_paths"] = {a.file_name: a.path for a in artifacts}

    if audit is not None and hasattr(audit, "run_completed"):
        audit.run_completed(run_id, actor, passed=validation.passed)

    return result


# --------------------------------------------------------------------------- #
# Registry hooks (single import line for the Integration agent)
# --------------------------------------------------------------------------- #
def register(registry: Any) -> None:
    """Register this workflow with a dict-like or callable registry.

    Supports ``registry.register(WORKFLOW_TYPE, run)`` (object) or
    ``registry[WORKFLOW_TYPE] = run`` (mapping) styles.
    """
    if hasattr(registry, "register") and callable(registry.register):
        registry.register(WORKFLOW_TYPE, run)
    else:
        registry[WORKFLOW_TYPE] = run


# Self-register into the module-level registry at import time (mirrors the
# pattern used by budget_variance / report_review).
WORKFLOW_REGISTRY[WORKFLOW_TYPE] = run

# Convenience metadata for a registry that introspects modules.
WORKFLOW = {
    "workflow_type": WORKFLOW_TYPE,
    "run": run,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    "export_files": list(EXPORT_FILE_NAMES),
    "required_input_fields": list(REQUIRED_INPUT_FIELDS),
    "discovery_log_path": str(DISCOVERY_LOG_PATH),
}
