"""Consolidated review packet (Tier 1 roadmap: improved export packets).

A completed run already exports its workflow-specific, spec-named artifacts
(e.g. ``reconciliation_summary.md``, ``flagged_variances.csv``). This module adds
ONE more layer on top: a single, human-readable **review packet** plus a
machine-readable **run manifest** that consolidate everything a reviewer or a
public-records reviewer needs, with each kind of content clearly separated:

  1. Run metadata (ids, actor, status, validation status, draft-vs-final)
  2. Source input files + SHA-256 hashes
  3. Deterministic findings (computed in code)
  4. AI-assisted DRAFT language (clearly labelled) + model metadata + prompt
     template version
  5. Validation results (passed / errors / warnings / invented-ref / numeric
     claims checked)
  6. Reviewer notes + human-review actions
  7. Approval / rejection status
  8. Audit history

Everything here is DETERMINISTIC and built only from data already persisted in
the run ledger + audit log — it never calls an LLM and never fabricates values.
It reuses the shared writers in :mod:`src.core.exports`. No Streamlit, no
provider code, so it stays testable and respects the UI/core separation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.core.exports import write_json, write_markdown
from src.core.schemas import ExportArtifact

REVIEW_PACKET_MD = "review_packet.md"
RUN_MANIFEST_JSON = "run_manifest.json"
PREFLIGHT_REPORT_JSON = "preflight_report.json"
PREFLIGHT_SUMMARY_MD = "preflight_summary.md"
PACKET_FILE_NAMES = (REVIEW_PACKET_MD, RUN_MANIFEST_JSON)
# A failed-preflight packet contains ONLY the two preflight artifacts (the
# workflow never ran, so there are no findings / AI draft / validation).
FAILED_PREFLIGHT_PACKET_FILE_NAMES = (PREFLIGHT_REPORT_JSON, PREFLIGHT_SUMMARY_MD)

# A human "approve_draft" action promotes the AI draft to "final (human
# approved)"; a "reject_ai_explanation" marks it rejected. Until then every AI
# output stays a DRAFT (the core safety principle).
_APPROVE_ACTION = "approve_draft"
_REJECT_ACTION = "reject_ai_explanation"


def _is_qa(llm_response: dict) -> bool:
    """True if a stored llm_response row is a run-scoped Q&A turn (run_qa.*)."""
    return str(llm_response.get("prompt_template_version", "")).startswith("run_qa")


# --------------------------------------------------------------------------- #
# Status derivation
# --------------------------------------------------------------------------- #
def derive_draft_status(actions: list[dict]) -> str:
    """Return 'final (human-approved)', 'rejected', or 'draft'.

    Derived purely from recorded human-review actions — the AI never sets this.
    """
    action_names = {a.get("action") for a in actions or []}
    if _APPROVE_ACTION in action_names:
        return "final (human-approved)"
    if _REJECT_ACTION in action_names:
        return "rejected"
    return "draft"


def validation_status_of(run: dict) -> str:
    """Best-effort validation status for a run (summary first, else result)."""
    summary = run.get("summary") or {}
    if summary.get("validation_status"):
        return str(summary["validation_status"])
    vrs = run.get("validation_results") or []
    if not vrs:
        return "n/a"
    v = vrs[-1]
    if v.get("invented_reference_detected") or v.get("errors"):
        return "failed"
    if v.get("warnings"):
        return "warnings"
    return "passed" if v.get("passed") else "failed"


# --------------------------------------------------------------------------- #
# Markdown packet
# --------------------------------------------------------------------------- #
def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a GitHub-flavored markdown table (no external deps)."""
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        cells = [str(c).replace("\n", " ").replace("|", "\\|") for c in r]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _preflight_md_section(preflight: Optional[dict]) -> list[str]:
    """Markdown for the preflight/capability block (empty list if none)."""
    lines = ["## 1b. Preflight / capability check", ""]
    if not preflight:
        lines += ["_No preflight check recorded for this run._", ""]
        return lines
    status = str(preflight.get("status", "")).upper()
    lines += [
        f"- Status: **{status}**",
        f"- LLM allowed: {preflight.get('llm_allowed')}",
        f"- Partial: {preflight.get('partial')}",
    ]
    supported = preflight.get("supported_checks") or []
    if supported:
        lines.append(
            "- Supported checks run: " + ", ".join(str(s) for s in supported))
    unsupported = preflight.get("unsupported_conditions") or []
    if unsupported:
        lines.append(
            "- Unsupported conditions detected: "
            + ", ".join(str(u) for u in unsupported))
    findings = preflight.get("findings") or []
    if findings:
        lines += ["", "Preflight findings:"]
        for f in findings:
            block = " (BLOCKS RUN)" if f.get("blocks_run") else ""
            lines.append(
                f"- **{f.get('code')}** ({f.get('severity')}){block}: "
                f"{f.get('message', '')}")
    next_steps = preflight.get("next_steps") or []
    if next_steps:
        lines += ["", "Next steps:"]
        lines += [f"{i}. {s}" for i, s in enumerate(next_steps, 1)]
    lines.append("")
    return lines


def build_review_packet_markdown(run: dict, audit_events: list[dict]) -> str:
    """Assemble the consolidated, human-readable review packet (markdown)."""
    summary = run.get("summary") or {}
    actions = run.get("human_review_actions") or []
    findings = run.get("findings") or []
    # Q&A turns are stored as llm_responses tagged run_qa.*; keep them out of the
    # workflow-draft section (4) and surface them in their own section (9).
    all_llms = run.get("llm_responses") or []
    qa_turns = [r for r in all_llms if _is_qa(r)]
    llms = [r for r in all_llms if not _is_qa(r)]
    vrs = run.get("validation_results") or []
    files = run.get("input_files") or []
    draft_status = derive_draft_status(actions)
    retention = (
        run.get("retention_category")
        or summary.get("retention_category")
        or "draft_working"
    )

    lines: list[str] = []
    lines += [
        f"# Review Packet — {run.get('workflow_type', 'workflow')}",
        "",
        "> Deterministic findings and figures are computed in code. AI text is an "
        "advisory DRAFT and is labelled as such. A human finance reviewer must "
        "verify everything before use.",
        "",
        "## 1. Run metadata",
        "",
        f"- Run ID: `{run.get('run_id', '')}`",
        f"- Workflow: {run.get('workflow_type', '')}",
        f"- Created at: {run.get('created_at', '')}",
        f"- Created by: {run.get('created_by', '')}",
        f"- Status: {run.get('status', '')}",
        f"- Validation status: {validation_status_of(run)}",
        f"- AI draft status: **{draft_status}**",
        f"- Retention category: {retention}",
        "",
    ]

    # 1b. Preflight / capability check.
    lines += _preflight_md_section(run.get("preflight"))

    # 2. Source files + hashes.
    lines += ["## 2. Source input files (SHA-256)", ""]
    if files:
        lines.append(_md_table(
            ["file_name", "type", "rows", "sha256"],
            [[f.get("file_name", ""), f.get("file_type", ""),
              f.get("row_count", ""), f.get("file_hash", "")] for f in files],
        ))
    else:
        lines.append("_No input file metadata recorded._")
    source_formats = summary.get("source_formats") or {}
    if source_formats:
        lines.append("")
        lines.append(
            "Source-format presets applied before analysis (by input): "
            + ", ".join(f"`{k}` -> `{v}`" for k, v in source_formats.items())
            + ". The hashes above are of the ORIGINAL uploaded files; aliasing "
            "only renamed columns to canonical names.")
    lines.append("")

    # 3. Deterministic findings.
    lines += ["## 3. Deterministic findings (computed in code)", ""]
    if findings:
        rows = []
        for f in findings:
            srcs = f.get("source_rows", []) or []
            refs = "; ".join(
                f"{s.get('table_name')}:{s.get('row_index')}" for s in srcs)
            rows.append([
                (f.get("finding_id", "") or "")[:8],
                f.get("finding_type", ""),
                f.get("severity", ""),
                f.get("rule_used", ""),
                f.get("description", ""),
                refs,
                f.get("requires_human_review", False),
            ])
        lines.append(_md_table(
            ["id", "type", "severity", "rule", "description",
             "source_rows", "needs_review"], rows))
    else:
        lines.append("_No deterministic findings._")
    lines.append("")

    # 4. AI-assisted draft language.
    lines += ["## 4. AI-assisted draft language (DRAFT — human review required)", ""]
    if llms:
        last = llms[-1]
        rj = last.get("response_json", {}) or {}
        lines += [
            f"- Model provider: {last.get('model_provider', '')}",
            f"- Model name: {last.get('model_name', '')}",
            f"- Prompt template version: {last.get('prompt_template_version', '')}",
            f"- Referenced source rows: "
            f"{', '.join(last.get('referenced_source_rows', []) or []) or '(none)'}",
            "",
        ]
        if rj.get("summary"):
            lines += ["**AI summary (draft):**", "", str(rj["summary"]), ""]
        for key in ("draft_memo", "draft", "review_checklist",
                    "variance_commentary", "commentary"):
            val = rj.get(key)
            if val:
                title = key.replace("_", " ").title()
                body = val if isinstance(val, str) else json.dumps(val, indent=2)
                lines += [f"**{title} (draft):**", "", str(body), ""]
    else:
        lines.append("_No AI explanation recorded._")
    lines.append("")

    # 5. Validation results.
    lines += ["## 5. Validation results", ""]
    if vrs:
        v = vrs[-1]
        lines += [
            f"- Passed: {v.get('passed')}",
            f"- Invented reference detected: {v.get('invented_reference_detected')}",
            f"- Numeric claims checked: {v.get('numeric_claims_checked')}",
            f"- Checked source refs: "
            f"{', '.join(v.get('checked_source_refs', []) or []) or '(none)'}",
        ]
        for e in v.get("errors", []) or []:
            lines.append(f"- ERROR: {e}")
        for w in v.get("warnings", []) or []:
            lines.append(f"- WARNING: {w}")
    else:
        lines.append("_No validation result recorded._")
    lines.append("")

    # 6. Reviewer notes + actions.
    lines += ["## 6. Reviewer notes & human-review actions", ""]
    if actions:
        lines.append(_md_table(
            ["action", "finding", "actor", "note", "at"],
            [[a.get("action", ""), (a.get("finding_id") or "")[:8],
              a.get("actor", ""), a.get("note") or "", a.get("created_at", "")]
             for a in actions]))
    else:
        lines.append("_No human-review actions recorded yet._")
    lines.append("")

    # 7. Approval / rejection status.
    lines += [
        "## 7. Approval / rejection status",
        "",
        f"- AI draft status: **{draft_status}**",
        f"- Run human-review status: {run.get('human_review_status', 'pending')}",
        "",
    ]

    # 8. Audit history.
    lines += ["## 8. Audit history", ""]
    if audit_events:
        lines.append(_md_table(
            ["timestamp", "event", "actor"],
            [[e.get("timestamp", ""), e.get("event_type", ""),
              e.get("actor", "")] for e in audit_events]))
    else:
        lines.append("_No audit events recorded._")
    lines.append("")

    # 9. Run Q&A (advisory). The bounded "ask about this run" assistant; each
    # answer is a DRAFT grounded only in this run's data and validated like the
    # drafted commentary above.
    lines += ["## 9. Run Q&A (advisory — DRAFT, human review required)", ""]
    if qa_turns:
        for r in qa_turns:
            t = r.get("response_json", {}) or {}
            v = t.get("validation", {}) or {}
            refs = ", ".join(t.get("referenced_source_rows", []) or []) or "(none)"
            status = "PASSED" if v.get("passed", True) else "FLAGGED"
            lines += [
                f"**Q:** {t.get('question', '')}",
                "",
                f"**A (draft):** {t.get('answer', '')}",
                f"- Answerable from this run: {t.get('answerable')}",
                f"- Cited source rows: {refs}",
                f"- Validation: {status}",
            ]
            for e in v.get("errors", []) or []:
                lines.append(f"  - ERROR: {e}")
            for w in v.get("warnings", []) or []:
                lines.append(f"  - WARNING: {w}")
            lines.append("")
    else:
        lines.append("_No run questions recorded._")
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# JSON manifest
# --------------------------------------------------------------------------- #
def build_run_manifest(run: dict, audit_events: list[dict]) -> dict:
    """Machine-readable bundle of everything needed to review/defend a run."""
    actions = run.get("human_review_actions") or []
    all_llms = run.get("llm_responses") or []
    qa_turns = [r for r in all_llms if _is_qa(r)]
    llms = [r for r in all_llms if not _is_qa(r)]
    vrs = run.get("validation_results") or []
    last_llm = llms[-1] if llms else {}
    last_v = vrs[-1] if vrs else {}
    preflight = run.get("preflight") or {}
    return {
        "run_id": run.get("run_id"),
        "workflow_type": run.get("workflow_type"),
        "preflight": {
            "status": preflight.get("status"),
            "llm_allowed": preflight.get("llm_allowed"),
            "partial": preflight.get("partial"),
            "supported_checks": preflight.get("supported_checks", []),
            "unsupported_conditions": preflight.get("unsupported_conditions", []),
            "next_steps": preflight.get("next_steps", []),
        } if preflight else None,
        "created_at": run.get("created_at"),
        "created_by": run.get("created_by"),
        "status": run.get("status"),
        "retention_category": (
            run.get("retention_category")
            or (run.get("summary") or {}).get("retention_category")
            or "draft_working"
        ),
        "validation_status": validation_status_of(run),
        "ai_draft_status": derive_draft_status(actions),
        "human_review_status": run.get("human_review_status"),
        "summary": run.get("summary") or {},
        "source_formats": (run.get("summary") or {}).get("source_formats", {}),
        "source_files": [
            {"file_name": f.get("file_name"), "file_type": f.get("file_type"),
             "row_count": f.get("row_count"), "sha256": f.get("file_hash")}
            for f in (run.get("input_files") or [])
        ],
        "deterministic_finding_count": len(run.get("findings") or []),
        "model": {
            "provider": last_llm.get("model_provider"),
            "name": last_llm.get("model_name"),
            "prompt_template_version": last_llm.get("prompt_template_version"),
            "referenced_source_rows": last_llm.get("referenced_source_rows", []),
        },
        "validation": {
            "passed": last_v.get("passed"),
            "invented_reference_detected": last_v.get("invented_reference_detected"),
            "numeric_claims_checked": last_v.get("numeric_claims_checked"),
            "errors": last_v.get("errors", []),
            "warnings": last_v.get("warnings", []),
        },
        "human_review_actions": actions,
        "run_qa": [
            {
                "question": (r.get("response_json", {}) or {}).get("question", ""),
                "answer": (r.get("response_json", {}) or {}).get("answer", ""),
                "answerable": (r.get("response_json", {}) or {}).get("answerable"),
                "referenced_source_rows": (
                    r.get("response_json", {}) or {}).get("referenced_source_rows", []),
                "validation": (r.get("response_json", {}) or {}).get("validation", {}),
            }
            for r in qa_turns
        ],
        "export_history": [
            {"file_name": a.get("file_name"), "sha256": a.get("sha256"),
             "type": a.get("artifact_type")}
            for a in (run.get("export_artifacts") or [])
        ],
        "audit_events": audit_events,
    }


# --------------------------------------------------------------------------- #
# Generation (writes + persists)
# --------------------------------------------------------------------------- #
def generate_review_packet(
    ledger: Any,
    audit: Any,
    run_id: str,
    out_dir: str | Path,
    *,
    actor: str = "system",
) -> list[ExportArtifact]:
    """Build + write the consolidated review packet for a run.

    Reads the run from the ledger (so it reflects all persisted findings, AI
    output, validation, reviewer actions, and exports), writes
    ``review_packet.md`` + ``run_manifest.json`` into ``out_dir/<run_id>``,
    records them as export artifacts in the ledger, and emits an audit event.
    Returns the artifact manifests. Returns ``[]`` if the run is unknown.
    """
    run = ledger.get_run(run_id)
    if run is None:
        return []
    audit_events = (
        audit.list_events(run_id)
        if audit is not None and hasattr(audit, "list_events") else []
    )
    out = Path(out_dir) / str(run_id)
    out.mkdir(parents=True, exist_ok=True)

    artifacts: list[ExportArtifact] = []
    artifacts.append(write_markdown(
        out / REVIEW_PACKET_MD,
        build_review_packet_markdown(run, audit_events),
        run_id=run_id,
    ))
    artifacts.append(write_json(
        out / RUN_MANIFEST_JSON,
        build_run_manifest(run, audit_events),
        run_id=run_id,
    ))
    # Every packet additionally carries the preflight report + summary when a
    # preflight check was recorded for the run.
    artifacts += _write_preflight_artifacts(run.get("preflight"), out, run_id)

    if ledger is not None and hasattr(ledger, "store_export_artifact"):
        for a in artifacts:
            ledger.store_export_artifact(run_id, a)
    if audit is not None and hasattr(audit, "export_generated"):
        audit.export_generated(
            run_id, actor, artifacts=[a.file_name for a in artifacts],
            packet="review_packet")
    return artifacts


def _preflight_summary_md_from_dict(preflight: dict) -> str:
    """Render the preflight summary markdown from a stored report dict.

    Reconstructs a ``PreflightReport`` and delegates to the canonical renderer so
    the packet's summary matches the CLI/UI exactly. Falls back to a minimal
    summary if the dict cannot be validated (defensive; never fails a packet).
    """
    try:
        from src.core.preflight import render_preflight_summary_md
        from src.core.schemas import PreflightReport

        return render_preflight_summary_md(
            PreflightReport.model_validate(preflight))
    except Exception:
        status = str(preflight.get("status", "")).upper()
        return (
            f"# Preflight — {preflight.get('workflow_type', 'workflow')}\n\n"
            f"**Status:** {status}\n"
        )


def _write_preflight_artifacts(
    preflight: Optional[dict], out: Path, run_id: str
) -> list[ExportArtifact]:
    """Write ``preflight_report.json`` + ``preflight_summary.md`` (if any)."""
    if not preflight:
        return []
    return [
        write_json(out / PREFLIGHT_REPORT_JSON, preflight, run_id=run_id),
        write_markdown(
            out / PREFLIGHT_SUMMARY_MD,
            _preflight_summary_md_from_dict(preflight),
            run_id=run_id,
        ),
    ]


def generate_failed_preflight_packet(
    ledger: Any,
    audit: Any,
    run_id: str,
    out_dir: str | Path,
    *,
    preflight: Optional[dict] = None,
    actor: str = "system",
) -> list[ExportArtifact]:
    """Write the minimal packet for a FAILED-preflight run.

    A failed preflight means the workflow + LLM never ran, so there are no
    findings / AI draft / validation to package — ONLY ``preflight_report.json``
    + ``preflight_summary.md`` (with the blocking conditions + next steps). The
    preflight dict is taken from ``preflight`` if given, else from the ledger
    (``get_preflight`` / ``get_run(...)['preflight']``). Records the artifacts in
    the ledger and emits an audit event. Returns the artifact manifests.
    """
    if preflight is None and ledger is not None:
        if hasattr(ledger, "get_preflight"):
            preflight = ledger.get_preflight(run_id)
        else:
            run = ledger.get_run(run_id) if hasattr(ledger, "get_run") else None
            preflight = (run or {}).get("preflight")
    if not preflight:
        return []

    out = Path(out_dir) / str(run_id)
    out.mkdir(parents=True, exist_ok=True)
    artifacts = _write_preflight_artifacts(preflight, out, run_id)

    if ledger is not None and hasattr(ledger, "store_export_artifact"):
        for a in artifacts:
            ledger.store_export_artifact(run_id, a)
    if audit is not None and hasattr(audit, "export_generated"):
        audit.export_generated(
            run_id, actor, artifacts=[a.file_name for a in artifacts],
            packet="failed_preflight")
    return artifacts


__all__ = [
    "REVIEW_PACKET_MD",
    "RUN_MANIFEST_JSON",
    "PREFLIGHT_REPORT_JSON",
    "PREFLIGHT_SUMMARY_MD",
    "PACKET_FILE_NAMES",
    "FAILED_PREFLIGHT_PACKET_FILE_NAMES",
    "derive_draft_status",
    "validation_status_of",
    "build_review_packet_markdown",
    "build_run_manifest",
    "generate_review_packet",
    "generate_failed_preflight_packet",
]
