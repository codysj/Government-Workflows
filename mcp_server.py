"""MCP server: the finance workflows as tools for any MCP-capable chat host.

    .venv\\Scripts\\python.exe mcp_server.py        (stdio; local only)

A thin adapter like ``api/`` and ``cli/``: every tool calls the same
``src.core.pipeline`` and writes to the same run ledger / audit log / exports
as the console, so runs started from a chat appear in the console's history.
No business logic lives here.

Trust boundary: findings and numbers are computed by deterministic code, and
the tool's own AI draft / Q&A answers pass the same validation as in the
console. Whatever the HOST model writes around these results is outside that
boundary, so the tool descriptions tell it to quote figures, not recompute them.
Files are read from local paths the user names; the server runs over stdio on
the user's machine and never listens on a network port.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from api.services.runs import build_run_detail, preflight_response_from_dict
from api.services.settings import ApiSettings
from src.core import pipeline, preflight as preflight_engine, run_qa
from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.workflows.registry import get_descriptor, list_descriptors

_INSTRUCTIONS = (
    "Municipal finance review tools. Typical flow: list_workflows -> preflight "
    "(check the user's files) -> run_workflow -> get_run / ask_about_run. "
    "All figures in results come from deterministic code: quote them exactly "
    "and never recompute, round, or invent amounts, dates, accounts, or row ids. "
    "The AI draft inside a run is a DRAFT for human review."
)
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _descriptor(workflow_type: str):
    try:
        return get_descriptor(workflow_type)
    except KeyError:
        names = ", ".join(d.workflow_type for d in list_descriptors())
        raise ToolError(f"Unknown workflow {workflow_type!r}. Use one of: {names}.") from None


def _inputs(workflow_type: str, files: Optional[dict[str, str]], text: Optional[dict[str, Any]],
            use_sample: bool) -> dict[str, Any]:
    """Build pipeline inputs: bundled sample (optional), then the user's files/text."""
    d = _descriptor(workflow_type)
    inputs: dict[str, Any] = d.sample_inputs() if use_sample else {}
    file_keys = {u.key for u in d.uploads}
    text_keys = {t.key for t in d.text_inputs}
    for key, path in (files or {}).items():
        if key not in file_keys:
            raise ToolError(f"Unknown file slot {key!r} for {workflow_type}; "
                             f"expected one of {sorted(file_keys)}.")
        p = Path(path).expanduser()
        if not p.is_file():
            raise ToolError(f"File not found: {path}")
        inputs[key] = str(p.resolve())
    for key, value in (text or {}).items():
        if key not in text_keys:
            raise ToolError(f"Unknown text field {key!r} for {workflow_type}; "
                             f"expected one of {sorted(text_keys)}.")
        inputs[key] = value
    if not inputs:
        raise ToolError("Give files (slot -> local path) or set use_sample=true.")
    return inputs


def _finding_brief(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": f.get("severity"),
        "type": f.get("finding_type"),
        "description": f.get("description"),
        "source_rows": [f"{s['table_name']}:{s['row_index']}" for s in f.get("source_rows", [])],
    }


def build_server(settings: Optional[ApiSettings] = None) -> MCPServer:
    """Create the server over a ledger/audit/export location (tests pass tmp paths)."""
    settings = settings or ApiSettings.from_app_settings()
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    ledger = RunLedger(str(settings.ledger_db_path))
    audit = AuditLog(ledger, audit_dir=settings.audit_dir)
    server = MCPServer(name="govworkflows", instructions=_INSTRUCTIONS, version=settings.app_version)

    @server.tool()
    def list_workflows() -> list[dict[str, Any]]:
        """List the finance review workflows, their file slots and text fields."""
        return [{
            "workflow_type": d.workflow_type,
            "title": d.title,
            "description": d.description,
            "files": {u.key: f"{u.label}{'' if u.required else ' (optional)'}" for u in d.uploads},
            "text_fields": {t.key: t.label for t in d.text_inputs},
            "has_sample_data": d.has_sample,
        } for d in list_descriptors()]

    @server.tool()
    def preflight(workflow_type: str, files: Optional[dict[str, str]] = None,
                  text: Optional[dict[str, Any]] = None, use_sample: bool = False) -> dict[str, Any]:
        """Check files BEFORE running (no AI, nothing saved). Returns PASS / PARTIAL /
        FAIL with plain-language next steps. `files` maps a file slot (see
        list_workflows) to a local path."""
        report = pipeline.run_preflight(workflow_type, _inputs(workflow_type, files, text, use_sample))
        return preflight_response_from_dict(
            preflight_engine.preflight_report_dict(report)).model_dump(mode="json")

    @server.tool()
    def run_workflow(workflow_type: str, files: Optional[dict[str, str]] = None,
                     text: Optional[dict[str, Any]] = None, use_sample: bool = False,
                     config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Run a workflow end to end (preflight -> deterministic checks -> validated AI
        draft -> exports). Returns the run id, status, counts, the most severe
        findings, and the draft's validation. Quote figures exactly."""
        result = pipeline.run_workflow(
            workflow_type, _inputs(workflow_type, files, text, use_sample),
            ledger=ledger, audit=audit, actor="mcp", export_dir=settings.export_dir,
            config=config, retention_category=settings.default_retention_category)
        detail = build_run_detail(ledger, result.run_id).model_dump(mode="json")
        findings = sorted(detail["findings"],
                          key=lambda f: _SEVERITY_ORDER.get(f.get("severity"), 5))
        return {
            "run_id": result.run_id,
            "status": detail["status"],
            "preflight": {"status": result.preflight_status,
                          "next_steps": (detail["preflight"] or {}).get("next_steps", [])},
            "findings_total": len(findings),
            "top_findings": [_finding_brief(f) for f in findings[:25]],
            "ai_draft": (detail["ai"] or {}).get("response", {}).get("summary"),
            "ai_validation": detail["validation"],
            "artifacts": [a["file_name"] for a in detail["artifacts"]],
            "note": "Use get_run for every finding; ask_about_run for questions.",
        }

    @server.tool()
    def get_run(run_id: str, max_findings: int = 50) -> dict[str, Any]:
        """Full detail of a past run (findings with their source rows, the AI draft and
        its validation, review status, artifacts), capped at `max_findings`."""
        detail = build_run_detail(ledger, run_id)
        if detail is None:
            raise ToolError(f"No run with id {run_id!r}.")
        out = detail.model_dump(mode="json")
        out["findings_total"] = len(out["findings"])
        out["findings"] = out["findings"][:max(0, max_findings)]
        return out

    @server.tool()
    def ask_about_run(run_id: str, question: str) -> dict[str, Any]:
        """Ask a question answered ONLY from one run's data. The answer is validated
        (invented numbers, dates, ids, or rows are rejected) and audit-logged."""
        if not question.strip():
            raise ToolError("Ask a non-empty question.")
        turn = run_qa.answer_question(ledger, audit, run_id, question,
                                      export_dir=settings.export_dir, actor="mcp")
        if turn is None:
            raise ToolError(f"No run with id {run_id!r}.")
        return turn

    return server


if __name__ == "__main__":
    from src.core.env import load_env_file

    load_env_file(Path(__file__).resolve().parent / ".env")
    build_server().run("stdio")
