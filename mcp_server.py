"""MCP server: the finance workflows as tools for Claude, ChatGPT, or any MCP host.

    .venv\\Scripts\\python.exe mcp_server.py        (stdio; local only)

A thin adapter like ``api/`` and ``cli/``: every tool calls the same
``src.core.pipeline`` and writes to the same run ledger / audit log / exports
as the console, so runs started from a chat appear in the console's history.
No business logic lives here. Host setup (Claude Desktop extension, ChatGPT via
OpenAI's Secure MCP Tunnel): docs/mcp.md.

Trust boundary: findings and numbers are computed by deterministic code, and
the tool's own AI draft / Q&A answers pass the same validation as in the
console. Whatever the HOST model writes around these results is outside that
boundary, so the tool descriptions tell it to quote figures, not recompute them.

Environment (all optional):
  GOVWF_DATA_DIR       the only folder files can be read from (list_files shows
                       it); without it, only sample data and chat attachments work
  GOVWF_HOME           where the ledger, audit log, exports and uploads live
                       (default: the repo's runs/)
  GOVWF_DOWNLOAD_HOSTS hosts that chat-attached files may be downloaded from
                       (default: oaiusercontent.com,openai.com; https only)
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from api.services.inputs import InputError, coerce_text_input, validate_required_inputs
from api.services.runs import build_run_detail, preflight_response_from_dict
from api.services.settings import ApiSettings
from src.core import pipeline, preflight as preflight_engine, run_qa
from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.workflows.registry import get_descriptor, list_descriptors

_INSTRUCTIONS = (
    "Municipal finance review tools. Typical flow: list_workflows -> list_files "
    "(the user's data folder) -> preflight (check the files) -> run_workflow -> "
    "get_run / ask_about_run. Files are local paths or names in the data folder; "
    "in ChatGPT, attached files go in uploaded_files. All figures in results come "
    "from deterministic code: quote them exactly and never recompute, round, or "
    "invent amounts, dates, accounts, or row ids. The AI draft inside a run is a "
    "DRAFT for human review."
)
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_VIEW_URI = "ui://govworkflows/run-view.html"
_VIEW_HTML = Path(__file__).resolve().parent / "mcp_views" / "run_view.html"
_DATA_SUFFIXES = {".csv", ".xlsx", ".xls", ".json"}
_MIME_SUFFIX = {
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/json": ".json",
}
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_DOWNLOAD_DEADLINE_S = 120
_WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                         *(f"lpt{i}" for i in range(10))}
_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
# Writes a run / audit entry (never changes or deletes the user's files); may
# download chat attachments and, with LLM_MODE=real, call the configured model.
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_FILE_PARAMS = {"openai/fileParams": ["uploaded_files"]}


class UploadedFile(BaseModel):
    """A file the user attached in the chat (ChatGPT passes these via openai/fileParams)."""

    download_url: str
    file_id: str
    mime_type: Optional[str] = None
    file_name: Optional[str] = None
    slot: Optional[str] = Field(
        default=None, description="File slot this is for (see list_workflows); inferred when obvious.")


def _descriptor(workflow_type: str):
    try:
        return get_descriptor(workflow_type)
    except KeyError:
        names = ", ".join(d.workflow_type for d in list_descriptors())
        raise ToolError(f"Unknown workflow {workflow_type!r}. Use one of: {names}.") from None


def _local_path(path: str, data_dir: Optional[Path]) -> str:
    """Resolve a user-named file; only data files inside the data folder are readable.

    The calling model may be steered by text inside files or web pages, so a
    path is untrusted: no data folder means no reads by name; network paths are
    refused before touching the filesystem (resolving a network path would
    already contact that host); only CSV / Excel / JSON files are read.
    """
    outside = ToolError(f"Only files inside the data folder ({data_dir}) can be used. "
                        "Copy the file there, or give its name relative to that folder.")
    if data_dir is None:
        raise ToolError("No data folder is set, so files can't be read by name. Set GOVWF_DATA_DIR "
                        "(the Claude extension asks for it), use the sample data, or attach the file.")
    raw = str(path).strip()
    if raw.startswith(("\\\\", "//")):
        raise ToolError("Network and device paths are not allowed; use a file in the data folder.")
    p = Path(raw).expanduser()
    if p.drive and p.drive.lower() != data_dir.drive.lower():
        raise outside
    if p.suffix.lower() not in _DATA_SUFFIXES:
        raise ToolError(f"{p.name!r} is not a CSV, Excel, or JSON file.")
    p = (p if p.is_absolute() else data_dir / p).resolve()
    if not p.is_relative_to(data_dir):
        raise outside
    if not p.is_file():
        raise ToolError(f"File not found: {path}")
    return str(p)


def _check_download_url(url: str, hosts: tuple[str, ...]) -> None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in hosts):
        raise ToolError(f"Refusing to download from {host or url!r}: attached files are only "
                        f"fetched over https from {', '.join(hosts)} (GOVWF_DOWNLOAD_HOSTS).")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect hop must stay on the download allowlist."""

    def __init__(self, hosts: tuple[str, ...]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_download_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_file_name(f: UploadedFile) -> str:
    """A plain local name for an attachment (no streams, devices, or odd characters)."""
    name = re.sub(r"[^\w.\- ]", "_", Path(f.file_name or f.file_id).name).strip(" .") or "upload"
    if Path(name).suffix.lower() not in _DATA_SUFFIXES:
        name += _MIME_SUFFIX.get((f.mime_type or "").split(";")[0].strip().lower(), "")
    if Path(name).suffix.lower() not in _DATA_SUFFIXES:
        raise ToolError(f"{name!r} is not a CSV, Excel, or JSON file.")
    if Path(name).stem.lower() in _WINDOWS_DEVICE_NAMES:
        name = "_" + name
    return name


def _download(f: UploadedFile, dest_dir: Path, hosts: tuple[str, ...]) -> str:
    """Fetch one chat-attached file into the upload folder (allowlisted hosts,
    every redirect re-checked, 50 MB cap, overall deadline)."""
    _check_download_url(f.download_url, hosts)
    name = _safe_file_name(f)
    folder = dest_dir / uuid.uuid4().hex
    folder.mkdir(parents=True)
    dest = folder / name
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_S
    try:
        with urllib.request.build_opener(_CheckedRedirects(hosts)).open(f.download_url, timeout=30) as r, \
                dest.open("wb") as out:
            total = 0
            while chunk := r.read(1 << 16):
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise ToolError(f"{name!r} is larger than 50 MB.")
                if time.monotonic() > deadline:
                    raise ToolError(f"Downloading {name!r} took too long.")
                out.write(chunk)
    except ToolError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as exc:  # network, TLS, truncated body, disk
        shutil.rmtree(folder, ignore_errors=True)
        raise ToolError(f"Could not download {name!r}: {exc}") from None
    return str(dest)


def _slot_for(d, f: UploadedFile, taken: set[str], unslotted: int) -> str:
    """The slot an attached file fills: given, a slot named in the file name, or
    the one required slot left. Never a slot already filled, or one that does
    not accept the file's type."""
    suffix = Path(_safe_file_name(f)).suffix.lower().lstrip(".")
    fits = [u for u in d.uploads if suffix in u.file_types]
    if f.slot is not None:
        if f.slot not in {u.key for u in d.uploads}:
            raise ToolError(f"Unknown file slot {f.slot!r} for {d.workflow_type}; "
                            f"expected one of {[u.key for u in d.uploads]}.")
        slot = f.slot
    else:
        open_ = [u for u in fits if u.key not in taken]
        words = set(re.split(r"[^a-z0-9]+", (f.file_name or "").lower()))
        by_name = [u.key for u in open_ if set(u.key.split("_")) <= words]
        required = [u.key for u in open_ if u.required]
        if len(by_name) == 1:
            slot = by_name[0]
        elif unslotted == 1 and len(required) == 1:
            slot = required[0]
        else:
            raise ToolError(f"Which file slot is {f.file_name or f.file_id!r} for? "
                            f"Set its `slot` to one of {[u.key for u in open_]}.")
    if slot in taken:
        raise ToolError(f"Two files were given for the {slot!r} slot.")
    if slot not in {u.key for u in fits}:
        raise ToolError(f"The {slot!r} slot does not take .{suffix} files.")
    return slot


def _is_link(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", lambda _: False)  # Python 3.12+
    return path.is_symlink() or is_junction(path)


def _finding_brief(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": f.get("severity"),
        "type": f.get("finding_type"),
        "description": f.get("description"),
        "source_rows": [f"{s['table_name']}:{s['row_index']}" for s in f.get("source_rows", [])],
    }


def _settings_from_env() -> ApiSettings:
    settings = ApiSettings.from_app_settings()
    home = os.environ.get("GOVWF_HOME")
    if home:  # e.g. the Claude extension keeps runs outside its install folder
        h = Path(home).expanduser()
        settings = dataclasses.replace(
            settings, ledger_db_path=h / "ledger.db", audit_dir=h / "audit",
            export_dir=h / "exports", upload_dir=h / "uploads", schedules_path=h / "schedules.json")
    return settings


def build_server(settings: Optional[ApiSettings] = None) -> MCPServer:
    """Create the server over a ledger/audit/export location (tests pass tmp paths)."""
    settings = settings or _settings_from_env()
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.ledger_db_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = (Path(os.environ["GOVWF_DATA_DIR"]).expanduser().resolve()
                if os.environ.get("GOVWF_DATA_DIR") else None)
    hosts = tuple(h.strip().lower() for h in os.environ.get(
        "GOVWF_DOWNLOAD_HOSTS", "oaiusercontent.com,openai.com").split(",") if h.strip())
    ledger = RunLedger(str(settings.ledger_db_path))
    audit = AuditLog(ledger, audit_dir=settings.audit_dir)
    apps = Apps()
    apps.add_html_resource(_VIEW_URI, _VIEW_HTML.read_text(encoding="utf-8"),
                           title="Finance review run", prefers_border=True)
    def inputs(workflow_type: str, files: Optional[dict[str, str]], text: Optional[dict[str, Any]],
               use_sample: bool, uploaded: Optional[list[UploadedFile]]) -> dict[str, Any]:
        """Pipeline inputs: bundled sample (optional), then local files, attachments, text."""
        d = _descriptor(workflow_type)
        result: dict[str, Any] = d.sample_inputs() if use_sample else {}
        file_keys = {u.key for u in d.uploads}
        text_keys = {t.key for t in d.text_inputs}
        for key, path in (files or {}).items():
            if key not in file_keys:
                raise ToolError(f"Unknown file slot {key!r} for {workflow_type}; "
                                f"expected one of {sorted(file_keys)}.")
            result[key] = _local_path(path, data_dir)
        taken = set(files or {})
        unslotted = sum(1 for f in uploaded or [] if f.slot is None)
        for f in uploaded or []:
            key = _slot_for(d, f, taken, unslotted)
            result[key] = _download(f, settings.upload_dir, hosts)
            taken.add(key)
        for key, value in (text or {}).items():
            if key not in text_keys:
                raise ToolError(f"Unknown text field {key!r} for {workflow_type}; "
                                f"expected one of {sorted(text_keys)}.")
            result[key] = coerce_text_input(workflow_type, key, value)
        if not result:
            raise ToolError("Give files (slot -> file name), attach files, or set use_sample=true.")
        return result

    # UI-bound tools first: the Apps extension collects them when the server is built.
    @apps.tool(resource_uri=_VIEW_URI, annotations=_WRITE, meta={
        **_FILE_PARAMS,
        "openai/toolInvocation/invoking": "Running the finance checks",
        "openai/toolInvocation/invoked": "Checks finished",
    })
    def run_workflow(workflow_type: str, files: Optional[dict[str, str]] = None,
                     text: Optional[dict[str, Any]] = None, use_sample: bool = False,
                     config: Optional[dict[str, Any]] = None,
                     uploaded_files: Optional[list[UploadedFile]] = None) -> dict[str, Any]:
        """Run a workflow end to end (preflight -> deterministic checks -> validated AI
        draft -> exports). Returns the run id, status, counts, the most severe
        findings, and the draft's validation. Quote figures exactly."""
        run_inputs = inputs(workflow_type, files, text, use_sample, uploaded_files)
        try:  # the console's checks too (e.g. freeform's two confirmations)
            validate_required_inputs(workflow_type, run_inputs)
        except InputError as exc:
            raise ToolError(str(exc)) from None
        result = pipeline.run_workflow(
            workflow_type, run_inputs,
            ledger=ledger, audit=audit, actor="mcp", export_dir=settings.export_dir,
            config={**settings.run_defaults, **(config or {})} or None,
            retention_category=settings.default_retention_category)
        detail = build_run_detail(ledger, result.run_id).model_dump(mode="json")
        findings = sorted(detail["findings"],
                          key=lambda f: _SEVERITY_ORDER.get(f.get("severity"), 5))
        return {
            "run_id": result.run_id,
            "workflow_type": workflow_type,
            "workflow_title": detail["workflow_title"],
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

    @apps.tool(resource_uri=_VIEW_URI, annotations=_READ)
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

    server = MCPServer(name="govworkflows", instructions=_INSTRUCTIONS,
                       version=settings.app_version, extensions=[apps])

    @server.tool(annotations=_READ)
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

    @server.tool(annotations=_READ)
    def list_files() -> dict[str, Any]:
        """List the CSV / Excel / JSON files in the user's data folder, newest first.
        Pass a file's `name` as a file slot value to preflight / run_workflow."""
        if data_dir is None:
            return {"data_folder": None, "files": [],
                    "note": "No data folder is set (GOVWF_DATA_DIR); pass full file paths."}
        found = []
        for root, dirs, names in os.walk(data_dir):  # links and junctions are not followed
            dirs[:] = [x for x in dirs if not _is_link(Path(root) / x)]
            found += [Path(root) / n for n in names if Path(n).suffix.lower() in _DATA_SUFFIXES]
        found = [p for p in found if p.resolve().is_relative_to(data_dir)]
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return {"data_folder": str(data_dir), "files": [
            {"name": p.relative_to(data_dir).as_posix(), "size_kb": round(p.stat().st_size / 1024, 1)}
            for p in found[:200]]}

    @server.tool(annotations=_READ)
    def preflight(workflow_type: str, files: Optional[dict[str, str]] = None,
                  text: Optional[dict[str, Any]] = None, use_sample: bool = False) -> dict[str, Any]:
        """Check data-folder files BEFORE running (no AI, nothing saved). Returns
        PASS / PARTIAL / FAIL with plain-language next steps. `files` maps a file
        slot (see list_workflows) to a file name in the data folder. Attached
        files go straight to run_workflow, which runs this same check first."""
        report = pipeline.run_preflight(
            workflow_type, inputs(workflow_type, files, text, use_sample, None))
        return preflight_response_from_dict(
            preflight_engine.preflight_report_dict(report)).model_dump(mode="json")

    @server.tool(annotations=_WRITE)
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
