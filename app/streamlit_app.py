"""Municipal Finance AI Workflow Tool — Streamlit MVP UI (Phase 6).

A plain, usable interface for non-technical finance staff. NOT a chatbot: it is
a controlled workflow runner. All financial logic lives in ``src`` (deterministic
calculations, matching, validation, exports, audit). This file only renders the
UI and drives the shared pipeline in ``app.workflow_registry``.

Pages: Home, Run Workflow, Workflow History, Review Run, Export Center,
Settings, About / Safety.

Design notes
------------
* Every page body is a function. Importing this module is side-effect free —
  the dispatcher (``main``) runs only when Streamlit executes the script as the
  entry point (``__name__ == "__main__"``) or via the explicit ``main()`` call
  guarded at the bottom. This lets tests import the page functions without a
  running Streamlit server.
* Mock LLM mode is the default; no API key or internet is required.
* Human-review actions persist through the ledger's HumanReviewAction store
  plus an audit event (see ``workflow_registry.record_human_review_action``).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is importable when Streamlit runs this file directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402

from app.app_settings import AppSettings  # noqa: E402
from app import role_views  # noqa: E402
from app import workflow_registry as wfr  # noqa: E402
from src.core.audit_log import AuditLog  # noqa: E402
from src.core import ai_usage_log as ai_log  # noqa: E402
from src.core import diffing  # noqa: E402
from src.core import pdf_export  # noqa: E402
from src.core import redaction  # noqa: E402
from src.core import review_packet as rp  # noqa: E402
from src.core import scheduler  # noqa: E402
from src.core.run_ledger import RunLedger  # noqa: E402
from src.core.schemas import RetentionCategory  # noqa: E402

PAGES = (
    "Home",
    "Run Workflow",
    "Workflow History",
    "Review Run",
    "Export Center",
    "AI Audit Log",
    "Scheduled runs",
    "Redaction assist",
    "Settings",
    "About / Safety",
)

# Records-retention category options (value, human label) for the UI selectors.
RETENTION_CHOICES: tuple[tuple[str, str], ...] = (
    (RetentionCategory.DRAFT_WORKING.value, "Draft / working"),
    (RetentionCategory.TRANSITORY.value, "Transitory"),
    (RetentionCategory.ADMINISTRATIVE.value, "Administrative record"),
    (RetentionCategory.AUDIT_RECORD.value, "Audit record"),
    (RetentionCategory.PERMANENT.value, "Permanent"),
)
_RETENTION_VALUES = [v for v, _ in RETENTION_CHOICES]
_RETENTION_LABELS = {v: lbl for v, lbl in RETENTION_CHOICES}

DATA_SAFETY_WARNING = (
    "Use SYNTHETIC or scrubbed sample data only. Do NOT upload real bank "
    "statements, vendor records, employee records, taxpayer data, credentials, "
    "or any sensitive city financial data."
)
HUMAN_REVIEW_WARNING = (
    "Every AI output is an advisory DRAFT. A human finance staff member must "
    "review every finding and explanation before it is used or finalized. The "
    "AI never performs authoritative calculations or final approvals."
)


# --------------------------------------------------------------------------- #
# Shared resources (cached so the SQLite ledger is opened once per session)
# --------------------------------------------------------------------------- #
def _ledger_db_path() -> str:
    return str(REPO_ROOT / "runs" / "ledger.db")


@st.cache_resource
def get_ledger() -> RunLedger:
    return RunLedger(_ledger_db_path())


@st.cache_resource
def get_audit(_ledger: RunLedger) -> AuditLog:
    return AuditLog(_ledger, audit_dir=str(REPO_ROOT / "runs" / "audit"))


@st.cache_resource
def get_schedule_store() -> scheduler.ScheduleStore:
    return scheduler.ScheduleStore(str(REPO_ROOT / "runs" / "schedules.json"))


def get_settings() -> AppSettings:
    if "settings" not in st.session_state:
        st.session_state["settings"] = AppSettings.load()
    return st.session_state["settings"]


def _settings_to_config(settings: AppSettings) -> dict:
    """Map Settings onto the run config threaded into each workflow.

    Uploaded config/threshold files always take precedence over these (see
    ``workflow_registry._config_for``)."""
    return {
        "amount_tolerance": settings.amount_tolerance,
        "date_tolerance_days": settings.date_tolerance_days,
        "variance_dollar_threshold": settings.variance_dollar_threshold,
        "variance_threshold_pct": settings.variance_threshold_pct,
    }


# --------------------------------------------------------------------------- #
# Home
# --------------------------------------------------------------------------- #
def render_home() -> None:
    settings = get_settings()
    st.title("Municipal Finance AI Workflow Tool")
    st.caption(f"Local-first finance review assistant — {settings.city_name}")

    st.subheader("What this tool does")
    st.markdown(
        "- Turns recurring finance tasks into **auditable, source-linked review "
        "workflows**.\n"
        "- Performs all parsing, matching, and **calculations deterministically "
        "in code**.\n"
        "- Uses AI only to **explain, summarize, draft, and flag** — every claim "
        "cites the source rows it came from.\n"
        "- Logs every run, validates AI output against your data, and exports a "
        "**review packet a human can verify**."
    )

    st.subheader("What this tool does NOT do")
    st.markdown(
        "- It is **not a chatbot** and does not answer open-ended questions.\n"
        "- The AI **does not calculate** variances or matches, decide final "
        "matches, or invent accounts, funds, vendors, amounts, or dates.\n"
        "- It does **not** produce final, approved official language without "
        "human review.\n"
        "- It is **not** an ERP, an authentication system, or a system of record."
    )

    st.subheader("Supported workflows")
    for d in wfr.list_descriptors():
        status = "" if d.available else "  _(not available in this build)_"
        st.markdown(f"- **{d.title}**{status} — {d.description}")

    st.warning("Data safety: " + DATA_SAFETY_WARNING, icon="⚠️")
    st.warning("Human review required: " + HUMAN_REVIEW_WARNING, icon="⚠️")


# --------------------------------------------------------------------------- #
# Run Workflow
# --------------------------------------------------------------------------- #
def _save_upload(tmp_dir: Path, key: str, uploaded) -> str:
    """Persist a Streamlit UploadedFile to a temp dir and return its path."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / f"{key}__{uploaded.name}"
    dest.write_bytes(uploaded.getbuffer())
    return str(dest)


def _collect_inputs(descriptor, run_tmp: Path, use_example: bool) -> dict:
    """Build the inputs dict for a workflow from uploads or example files."""
    inputs: dict = {}
    for field in descriptor.uploads:
        if field.key == "uploaded_files":
            continue  # handled separately for freeform
        if use_example and field.key in descriptor.example_files:
            inputs[field.key] = str(wfr.example_path(descriptor.example_files[field.key]))
            continue
        uploaded = st.session_state.get(f"upload__{descriptor.workflow_type}__{field.key}")
        if uploaded is not None:
            inputs[field.key] = _save_upload(run_tmp, field.key, uploaded)
    return inputs


def _is_csv_data_field(u) -> bool:
    """CSV data inputs get a source-format selector (not JSON configs/freeform)."""
    return u.key != "uploaded_files" and tuple(u.file_types) == ("csv",)


def _render_upload_field(descriptor, u) -> None:
    """Render a file uploader plus, for CSV data inputs, a source-format selector."""
    st.file_uploader(
        u.label, type=list(u.file_types),
        key=f"upload__{descriptor.workflow_type}__{u.key}", help=u.help,
    )
    if _is_csv_data_field(u):
        st.selectbox(
            f"↳ {u.label} — source format",
            range(len(wfr.SOURCE_FORMAT_CHOICES)),
            format_func=lambda i: wfr.SOURCE_FORMAT_CHOICES[i][0],
            key=f"fmt__{descriptor.workflow_type}__{u.key}",
            help=("If this file uses ERP-style column names (e.g. 'Posting "
                  "Date', 'Transaction Amount'), pick its source format to "
                  "normalize the columns before analysis. Standard files need "
                  "no preset."),
        )


def _collect_source_formats(descriptor) -> dict:
    """Read the per-upload source-format selections into {input_key: preset}."""
    out: dict = {}
    for u in descriptor.uploads:
        if not _is_csv_data_field(u):
            continue
        idx = st.session_state.get(f"fmt__{descriptor.workflow_type}__{u.key}", 0) or 0
        preset = wfr.SOURCE_FORMAT_CHOICES[idx][1]
        if preset:
            out[u.key] = preset
    return out


def render_run_workflow() -> None:
    settings = get_settings()
    ledger = get_ledger()
    audit = get_audit(ledger)

    st.title("Run a workflow")
    st.info(DATA_SAFETY_WARNING, icon="⚠️")

    options = [d for d in wfr.list_descriptors()]
    labels = [
        d.title + ("" if d.available else "  (unavailable)")
        for d in options
    ]
    idx = st.selectbox(
        "Choose a workflow", range(len(options)),
        format_func=lambda i: labels[i],
    )
    descriptor = options[idx]

    st.markdown(f"### {descriptor.title}")
    st.write(descriptor.description)

    if not descriptor.available:
        st.warning(descriptor.note or "This workflow is not available.", icon="🚧")
        return

    # Required / optional uploads.
    required = [u for u in descriptor.uploads if u.required]
    optional = [u for u in descriptor.uploads if not u.required]
    has_csv_field = any(_is_csv_data_field(u) for u in descriptor.uploads)
    if has_csv_field:
        st.caption("Tip: if a CSV uses ERP-style column names, set its **source "
                   "format** below the uploader to normalize the columns first.")
    if required:
        st.markdown("**Required uploads**")
        for u in required:
            if u.key == "uploaded_files":
                continue
            _render_upload_field(descriptor, u)
    if optional:
        st.markdown("**Optional uploads**")
        for u in optional:
            if u.key == "uploaded_files":
                continue
            _render_upload_field(descriptor, u)

    # Freeform structured fields (Phase 5).
    freeform_inputs: dict = {}
    if descriptor.workflow_type == "freeform":
        st.markdown("**Describe the task (structured — not a chat box)**")
        freeform_inputs["task_type"] = st.text_input(
            "Task type", help="A short label, e.g. 'reconcile petty cash'.")
        freeform_inputs["desired_output"] = st.text_area("Desired output")
        freeform_inputs["relevant_context"] = st.text_area("Relevant context")
        ff_files = st.file_uploader(
            "Supporting files (optional)", accept_multiple_files=True,
            key="upload__freeform__uploaded_files",
        )
        freeform_inputs["_uploaded"] = ff_files or []
        freeform_inputs["sensitivity_confirmation"] = st.checkbox(
            "I confirm this contains NO real sensitive data (synthetic/scrubbed only).")
        freeform_inputs["human_review_confirmation"] = st.checkbox(
            "I understand the output is a DRAFT requiring human review.")

    use_example = False
    if descriptor.example_files:
        use_example = st.checkbox(
            "Use example files (load synthetic data)",
            help="Runs the workflow on the bundled synthetic dataset.",
        )

    # Per-run records-retention category (defaults to the Settings value).
    default_ret = settings.default_retention_category
    ret_index = (
        _RETENTION_VALUES.index(default_ret)
        if default_ret in _RETENTION_VALUES else 0
    )
    retention_category = st.selectbox(
        "Records-retention category",
        _RETENTION_VALUES,
        index=ret_index,
        format_func=lambda v: _RETENTION_LABELS.get(v, v),
        help=("Tags this run for public-records / retention-schedule purposes. "
              "Deterministic metadata only — the AI never sets it. Defaults to "
              "your Settings value."),
    )

    if st.button("Run workflow", type="primary"):
        run_tmp = REPO_ROOT / "runs" / "uploads" / wfr.make_id_safe()
        if descriptor.workflow_type == "freeform":
            inputs = {
                "task_type": freeform_inputs.get("task_type", ""),
                "desired_output": freeform_inputs.get("desired_output", ""),
                "relevant_context": freeform_inputs.get("relevant_context", ""),
                "sensitivity_confirmation": freeform_inputs.get("sensitivity_confirmation", False),
                "human_review_confirmation": freeform_inputs.get("human_review_confirmation", False),
            }
            paths = []
            for up in freeform_inputs.get("_uploaded", []):
                paths.append(_save_upload(run_tmp, "freeform", up))
            inputs["uploaded_files"] = paths
        else:
            inputs = _collect_inputs(descriptor, run_tmp, use_example)
            missing = [
                u.label for u in descriptor.uploads
                if u.required and u.key != "uploaded_files" and u.key not in inputs
            ]
            if missing and not use_example:
                st.error("Please provide required files: " + ", ".join(missing))
                return

        # Source-format presets apply to uploaded files (example files are
        # already in standard format).
        source_formats = (
            None if (descriptor.workflow_type == "freeform" or use_example)
            else (_collect_source_formats(descriptor) or None)
        )
        export_dir = Path(settings.export_dir)
        provider = None  # mock default (no key / no internet)
        with st.spinner("Running deterministic analysis, then AI drafting…"):
            try:
                result = wfr.run_workflow(
                    descriptor.workflow_type,
                    inputs,
                    ledger=ledger,
                    audit=audit,
                    provider=provider,
                    actor=settings.default_actor,
                    export_dir=export_dir,
                    config=_settings_to_config(settings),
                    source_formats=source_formats,
                    retention_category=retention_category,
                )
            except Exception as exc:  # surface errors plainly to staff
                st.error(f"Run failed: {exc}")
                st.caption("Check that the uploaded files have date/amount (or "
                           "account) columns and try the example files first.")
                return

        if result.refused:
            st.error("Run refused: " + result.refusal_reason)
            st.caption("Guided freeform requires the sensitivity confirmation "
                       "(synthetic/scrubbed data only).")
            return

        st.success(f"Run complete. Run ID: {result.run_id}")
        st.session_state["last_run_id"] = result.run_id
        st.session_state["selected_run_id"] = result.run_id
        c1, c2, c3 = st.columns(3)
        c1.metric("Findings", len(result.findings))
        c2.metric("Validation",
                  (result.summary or {}).get("validation_status", "n/a"))
        c3.metric("Artifacts", len(result.export_paths))
        val = result.validation
        if val is not None and val.invented_reference_detected:
            st.error("Validator flagged an invented source reference in the AI "
                     "draft — review carefully before use.")
        elif val is not None and val.passed:
            st.success("AI draft validated against the source data (no invented "
                       "references; numeric claims checked).")
        applied_fmt = (result.summary or {}).get("source_formats")
        if applied_fmt:
            st.caption("Applied source-format preset(s): "
                       + ", ".join(f"{k} → {v}" for k, v in applied_fmt.items())
                       + " (original file hashes preserved in the audit trail).")
        st.info("Open the **Review Run** page to inspect findings, the AI "
                "draft, validation warnings, human-review controls, and the "
                "exported review packet.")


# --------------------------------------------------------------------------- #
# Workflow History
# --------------------------------------------------------------------------- #
def render_history() -> None:
    ledger = get_ledger()
    st.title("Workflow history")
    runs = ledger.list_runs()
    if not runs:
        st.info("No runs yet. Use the **Run Workflow** page to start one.")
        return

    rows = []
    for r in runs:
        summary = r.get("summary") or {}
        retention = (
            r.get("retention_category")
            or summary.get("retention_category")
            or "draft_working"
        )
        rows.append({
            "run_id": r["run_id"][:8],
            "type": r["workflow_type"],
            "created_at": r["created_at"],
            "status": r["status"],
            "validation": summary.get("validation_status", "n/a"),
            "retention": retention,
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(f"{len(runs)} run(s) recorded in the local ledger.")

    run_ids = [r["run_id"] for r in runs]
    chosen = st.selectbox("Select a run to review", run_ids)
    if st.button("Review selected run"):
        st.session_state["selected_run_id"] = chosen
        st.info("Open the **Review Run** page (left).")


# --------------------------------------------------------------------------- #
# Review Run
# --------------------------------------------------------------------------- #
def _finding_table_rows(findings: list[dict]) -> list[dict]:
    rows = []
    for f in findings:
        srcs = f.get("source_rows", []) or []
        refs = "; ".join(
            f"{s.get('table_name')}:{s.get('row_index')}" for s in srcs
        )
        rows.append({
            "finding_id": f.get("finding_id", "")[:8],
            "type": f.get("finding_type", ""),
            "severity": f.get("severity", ""),
            "rule": f.get("rule_used", ""),
            "description": f.get("description", ""),
            "source_rows": refs,
            "needs_review": f.get("requires_human_review", False),
        })
    return rows


def render_review_run() -> None:
    settings = get_settings()
    ledger = get_ledger()
    audit = get_audit(ledger)
    st.title("Review run")

    runs = ledger.list_runs()
    if not runs:
        st.info("No runs to review yet.")
        return
    run_ids = [r["run_id"] for r in runs]
    default = st.session_state.get("selected_run_id", run_ids[0])
    if default not in run_ids:
        default = run_ids[0]
    run_id = st.selectbox("Run", run_ids, index=run_ids.index(default))
    st.session_state["selected_run_id"] = run_id

    run = ledger.get_run(run_id)
    if run is None:
        st.error("Run not found.")
        return

    summary = run.get("summary") or {}
    actions = run.get("human_review_actions", []) or []
    draft_status = rp.derive_draft_status(actions)
    retention = (
        run.get("retention_category")
        or summary.get("retention_category")
        or "draft_working"
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Status", run.get("status", ""))
    c2.metric("Workflow", run.get("workflow_type", ""))
    c3.metric("Validation", summary.get("validation_status", "n/a"))
    c4.metric("AI draft", draft_status)
    c5.metric("Retention", _RETENTION_LABELS.get(retention, retention))
    if draft_status == "draft":
        st.caption("The AI text is an unapproved DRAFT. Use the per-finding "
                   "controls below to approve or reject it.")

    # Role-specific focus (display emphasis only; never hides data destructively).
    role_view = role_views.get_role_view(settings.role)
    st.caption(role_view.caption)

    # --- Validation warnings (PRIORITIZED) ------------------------------- #
    validations = run.get("validation_results", []) or []
    st.subheader("Validation warnings")
    if not validations:
        st.caption("No validation result recorded.")
    for v in validations:
        if v.get("invented_reference_detected"):
            st.error("Invented source reference detected — AI output rejected by "
                     "the validator.")
        for e in v.get("errors", []) or []:
            st.error(e)
        for w in v.get("warnings", []) or []:
            st.warning(w)
        if v.get("passed") and not (v.get("errors") or v.get("warnings")):
            st.success("Validation passed with no warnings.")

    # --- Deterministic findings (review TABLE, role-emphasized) ---------- #
    st.subheader("Deterministic findings")
    findings = run.get("findings", []) or []
    if findings:
        show_all = st.checkbox(
            "Show all findings (ignore role emphasis)",
            value=False,
            key=f"showall__{run_id}",
            help=("Roles reorder and may collapse low-severity rows for "
                  "readability. This never deletes data — toggle to see the "
                  "full unfiltered list."),
        )
        display = role_views.order_findings_for_role(
            findings, settings.role, show_all=show_all)
        if len(display) < len(findings):
            st.caption(
                f"Showing {len(display)} of {len(findings)} findings "
                f"emphasized for the {settings.role} role. Enable "
                "'Show all findings' above to see the rest.")
        st.dataframe(_finding_table_rows(display),
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No deterministic findings.")

    # --- Input files ------------------------------------------------------ #
    with st.expander("Input files"):
        files = run.get("input_files", []) or []
        if files:
            st.dataframe(
                [{"file_name": f.get("file_name"), "type": f.get("file_type"),
                  "rows": f.get("row_count"), "hash": (f.get("file_hash") or "")[:12]}
                 for f in files],
                use_container_width=True, hide_index=True)
        else:
            st.caption("No input file metadata recorded.")
        applied_fmt = summary.get("source_formats") or {}
        if applied_fmt:
            st.caption(
                "Source-format presets applied before analysis: "
                + ", ".join(f"{k} → {v}" for k, v in applied_fmt.items())
                + ". Hashes above are of the original uploads; aliasing only "
                "renamed columns.")

    # --- LLM explanation -------------------------------------------------- #
    st.subheader("AI explanation (DRAFT — human review required)")
    llms = run.get("llm_responses", []) or []
    if llms:
        rj = llms[-1].get("response_json", {}) or {}
        if rj.get("summary"):
            st.write(rj["summary"])
        for key in ("draft_memo", "draft", "review_checklist"):
            if rj.get(key):
                with st.expander(key.replace("_", " ").title()):
                    val = rj[key]
                    st.write(val if isinstance(val, str) else val)
        with st.expander("Full AI response (JSON)"):
            st.json(rj)
    else:
        st.caption("No AI explanation recorded.")

    # --- Human review controls (per finding) ----------------------------- #
    st.subheader("Human review controls")
    st.caption("Actions persist to the run ledger and the audit log.")
    _render_review_controls(ledger, audit, run_id, findings, settings.default_actor)

    # --- Export artifacts ------------------------------------------------- #
    st.subheader("Export artifacts")
    artifacts = run.get("export_artifacts", []) or []
    if artifacts:
        for a in artifacts:
            _download_artifact(a)
    else:
        st.caption("No export artifacts recorded for this run. Use the Export "
                   "Center to generate a packet.")

    # --- PDF summary (built deterministically from the review packet md) -- #
    _render_pdf_summary_button(run, audit, run_id)

    # --- Audit events ----------------------------------------------------- #
    with st.expander("Audit events"):
        events = audit.list_events(run_id)
        if events:
            st.dataframe(
                [{"timestamp": e.get("timestamp"), "event": e.get("event_type"),
                  "actor": e.get("actor")} for e in events],
                use_container_width=True, hide_index=True)
        else:
            st.caption("No audit events.")

    # --- Prior human review actions -------------------------------------- #
    with st.expander("Recorded human review actions"):
        actions = run.get("human_review_actions", []) or []
        if actions:
            st.dataframe(
                [{"action": a.get("action"), "finding": (a.get("finding_id") or "")[:8],
                  "actor": a.get("actor"), "note": a.get("note"),
                  "at": a.get("created_at")} for a in actions],
                use_container_width=True, hide_index=True)
        else:
            st.caption("No human review actions yet.")


def _render_review_controls(ledger, audit, run_id, findings, actor) -> None:
    """Per-finding review buttons + a run-level approve/reject control."""
    if not findings:
        st.caption("No findings to review.")
    for f in findings:
        fid = f.get("finding_id", "")
        label = f"{f.get('severity', '')} — {f.get('description', '')[:80]}"
        with st.expander(label):
            note = st.text_input("Note (optional)", key=f"note__{run_id}__{fid}")
            cols = st.columns(len(wfr.HUMAN_REVIEW_ACTIONS))
            for col, (action, action_label) in zip(cols, wfr.HUMAN_REVIEW_ACTIONS):
                with col:
                    if st.button(action_label, key=f"act__{run_id}__{fid}__{action}"):
                        wfr.record_human_review_action(
                            ledger, audit, run_id=run_id, action=action,
                            actor=actor, finding_id=fid,
                            note=note or None,
                        )
                        st.success(f"Recorded: {action_label}")


def _download_artifact(a: dict, suffix: str = "") -> None:
    path = Path(a.get("path", ""))
    name = a.get("file_name", path.name)
    if path.is_file():
        try:
            data = path.read_bytes()
            st.download_button(f"Download {name}{suffix}", data=data,
                               file_name=name,
                               key=f"dl__{a.get('artifact_id', name)}")
        except OSError:
            st.caption(f"{name} (unreadable)")
    else:
        st.caption(f"{name} (missing on disk: {path})")


def _render_pdf_summary_button(run: dict, audit, run_id: str) -> None:
    """Build a review-packet PDF on demand and offer it as a download.

    The markdown is assembled deterministically from the persisted run data via
    ``review_packet.build_review_packet_markdown`` (no LLM call); the PDF is
    rendered by the stdlib-only ``pdf_export.review_packet_pdf``. Bytes are read
    back and streamed through ``st.download_button``.
    """
    if st.button("Build PDF summary", key=f"pdfbtn__{run_id}"):
        import tempfile

        try:
            events = audit.list_events(run_id) if audit is not None else []
            md = rp.build_review_packet_markdown(run, events)
            tmp_dir = Path(tempfile.mkdtemp(prefix="govwf_pdf_"))
            out = pdf_export.review_packet_pdf(
                md, tmp_dir / f"review_packet_{run_id[:8]}.pdf",
                title=f"Review Packet — {run.get('workflow_type', '')}")
            data = Path(out).read_bytes()
        except Exception as exc:  # never crash the page on a render error
            st.error(f"Could not build PDF: {exc}")
            return
        st.download_button(
            "Download PDF summary", data=data,
            file_name=f"review_packet_{run_id[:8]}.pdf",
            mime="application/pdf", key=f"pdfdl__{run_id}")


# --------------------------------------------------------------------------- #
# Export Center
# --------------------------------------------------------------------------- #
def render_export_center() -> None:
    settings = get_settings()
    ledger = get_ledger()
    audit = get_audit(ledger)
    st.title("Export center")
    st.caption(f"Export directory: {settings.export_dir}")

    runs = ledger.list_runs()
    if not runs:
        st.info("No runs yet.")
        return
    run_ids = [r["run_id"] for r in runs]
    run_id = st.selectbox("Run", run_ids)
    run = ledger.get_run(run_id)
    artifacts = (run.get("export_artifacts", []) if run else []) or []

    st.subheader("Generated artifacts")
    if artifacts:
        # Surface the consolidated review packet first if present.
        ordered = sorted(
            artifacts,
            key=lambda a: a.get("file_name") not in rp.PACKET_FILE_NAMES)
        for a in ordered:
            label = ""
            if a.get("file_name") in rp.PACKET_FILE_NAMES:
                label = "  · consolidated review packet"
            _download_artifact(a, suffix=label)
    else:
        st.caption("No artifacts recorded for this run.")

    st.subheader("Regenerate consolidated review packet")
    st.caption(
        "Rebuilds review_packet.md + run_manifest.json from this run's stored "
        "findings, AI draft, validation result, reviewer notes, approval "
        "status, and audit history. Works for any run and reflects the latest "
        "human-review actions. No LLM call; nothing is fabricated.")
    if st.button("Generate review packet"):
        if run is None:
            st.warning("Run not found.")
        else:
            packet = rp.generate_review_packet(
                ledger, audit, run_id, settings.export_dir,
                actor=settings.default_actor)
            if packet:
                st.success(
                    "Review packet generated: "
                    + ", ".join(a.file_name for a in packet)
                    + ". Reload the page to see the download links.")
            else:
                st.warning("Could not generate a packet for this run.")

    st.subheader("PDF summary")
    st.caption("Render the consolidated review packet to a portable PDF "
               "(text-only, deterministic — no LLM call).")
    if run is not None:
        _render_pdf_summary_button(run, audit, run_id)


# --------------------------------------------------------------------------- #
# AI Audit Log (Tier 1: searchable AI interaction history)
# --------------------------------------------------------------------------- #
def render_ai_audit_log() -> None:
    ledger = get_ledger()
    st.title("AI audit log")
    st.caption(
        "Every AI interaction in one reviewable place: which run, workflow, "
        "model, and prompt template; whether validation passed; and whether the "
        "draft is still a draft or has been human-approved. Supports CPRA-style "
        "review of AI usage. This is not a public-records request platform.")

    interactions = ledger.list_llm_interactions()
    if not interactions:
        st.info("No AI interactions yet. Run a workflow to populate this log.")
        return

    # --- Filters --------------------------------------------------------- #
    wf_types = sorted({i["workflow_type"] for i in interactions if i["workflow_type"]})
    draft_states = sorted({i["ai_draft_status"] for i in interactions})
    f1, f2, f3 = st.columns(3)
    with f1:
        wf_filter = st.multiselect("Workflow", wf_types, default=wf_types)
    with f2:
        draft_filter = st.multiselect("AI draft status", draft_states,
                                      default=draft_states)
    with f3:
        query = st.text_input("Search (run id, model, template)")

    def _match(i: dict) -> bool:
        if i["workflow_type"] not in wf_filter:
            return False
        if i["ai_draft_status"] not in draft_filter:
            return False
        if query:
            hay = " ".join(str(i.get(k, "")) for k in (
                "run_id", "model_provider", "model_name",
                "prompt_template_version", "validation_status")).lower()
            if query.strip().lower() not in hay:
                return False
        return True

    filtered = [i for i in interactions if _match(i)]
    st.caption(f"Showing {len(filtered)} of {len(interactions)} AI interactions.")
    st.dataframe(
        [{"run_id": i["run_id"][:8], "workflow": i["workflow_type"],
          "model": f"{i['model_provider']}/{i['model_name']}",
          "template": i["prompt_template_version"],
          "validation": i["validation_status"],
          "ai_draft_status": i["ai_draft_status"],
          "created_at": i["created_at"]} for i in filtered],
        use_container_width=True, hide_index=True)
    st.caption("Open **Review Run** to inspect or approve/reject a specific "
               "AI draft.")

    # --- Download the full AI usage log (CSV / JSON) --------------------- #
    st.subheader("Download AI usage log")
    st.caption("Export every AI interaction (across all runs) for oversight / "
               "public-records review. Built deterministically from the ledger.")
    import tempfile

    try:
        export_dir = Path(tempfile.mkdtemp(prefix="govwf_ailog_"))
        paths = ai_log.export_ai_usage_log(ledger, export_dir, fmt="both")
        by_name = {p.name: p for p in paths}
        d1, d2 = st.columns(2)
        csv_p = by_name.get(ai_log.CSV_FILE_NAME)
        json_p = by_name.get(ai_log.JSON_FILE_NAME)
        if csv_p is not None:
            d1.download_button(
                "Download CSV", data=csv_p.read_bytes(),
                file_name=ai_log.CSV_FILE_NAME, mime="text/csv",
                key="ailog_csv")
        if json_p is not None:
            d2.download_button(
                "Download JSON", data=json_p.read_bytes(),
                file_name=ai_log.JSON_FILE_NAME, mime="application/json",
                key="ailog_json")
    except Exception as exc:
        st.error(f"Could not build AI usage log: {exc}")

    # --- Compare two AI interactions (prompt/response diff) -------------- #
    st.subheader("Compare two AI interactions")
    st.caption("Pick two runs to see how a prompt-template change, model swap, "
               "or re-run altered the AI draft and its cited source rows. "
               "Deterministic difflib comparison — no LLM call.")
    diff_run_ids = sorted({i["run_id"] for i in interactions})
    if len(diff_run_ids) < 2:
        st.info("Need at least two runs with AI interactions to compare.")
    else:
        ccol1, ccol2 = st.columns(2)
        with ccol1:
            run_a = st.selectbox("Run A", diff_run_ids, key="diff_run_a")
        with ccol2:
            run_b = st.selectbox(
                "Run B", diff_run_ids,
                index=1 if diff_run_ids[0] == run_a else 0,
                key="diff_run_b")
        if st.button("Compare", key="diff_compare"):
            ra = ledger.get_run(run_a)
            rb = ledger.get_run(run_b)
            if ra is None or rb is None:
                st.error("One of the selected runs was not found.")
            else:
                result = diffing.diff_runs(ra, rb)
                f1, f2 = st.columns(2)
                f1.metric("Template changed",
                          "yes" if result.get("template_changed") else "no")
                f2.metric("Model changed",
                          "yes" if result.get("model_changed") else "no")
                if not (result.get("has_response_a")
                        and result.get("has_response_b")):
                    st.warning("One or both runs have no AI response to compare.")
                summary_diff = result.get("summary_diff") or ""
                st.markdown("**AI summary diff**")
                if summary_diff.strip():
                    st.code(summary_diff, language="diff")
                else:
                    st.caption("No differences in the AI summary text.")
                added = result.get("referenced_rows_added") or []
                removed = result.get("referenced_rows_removed") or []
                st.markdown("**Referenced source rows**")
                st.write({"added": added, "removed": removed})


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def render_settings() -> None:
    settings = get_settings()
    st.title("Settings")
    st.caption("No authentication in the MVP. Settings persist to "
               "app_settings.json at the repo root.")
    st.info("Tolerances and thresholds below are applied to NEW runs (unless a "
            "run uploads its own config/threshold file, which takes precedence).",
            icon="⚙️")

    with st.form("settings_form"):
        city = st.text_input("City name", settings.city_name)
        actor = st.text_input("Default actor name", settings.default_actor)
        provider = st.selectbox(
            "LLM provider", ["mock", "anthropic", "openai"],
            index=["mock", "anthropic", "openai"].index(settings.llm_provider)
            if settings.llm_provider in ("mock", "anthropic", "openai") else 0)
        mock = st.checkbox("Mock mode (no API key / no internet)", settings.mock_mode)
        date_tol = st.number_input("Date tolerance (days)", min_value=0,
                                   value=int(settings.date_tolerance_days))
        amt_tol = st.number_input("Amount tolerance", min_value=0.0,
                                  value=float(settings.amount_tolerance),
                                  format="%.2f")
        var_pct = st.number_input("Variance threshold (%)", min_value=0.0,
                                  value=float(settings.variance_threshold_pct))
        var_dollar = st.number_input("Variance dollar threshold", min_value=0.0,
                                     value=float(settings.variance_dollar_threshold))
        export_dir = st.text_input("Export directory", settings.export_dir)
        cur_ret = settings.default_retention_category
        ret_idx = (_RETENTION_VALUES.index(cur_ret)
                   if cur_ret in _RETENTION_VALUES else 0)
        default_retention = st.selectbox(
            "Default records-retention category",
            _RETENTION_VALUES, index=ret_idx,
            format_func=lambda v: _RETENTION_LABELS.get(v, v),
            help="Pre-selected on the Run Workflow page for new runs.")
        submitted = st.form_submit_button("Save settings")
        if submitted:
            new = AppSettings(
                city_name=city, default_actor=actor, llm_provider=provider,
                mock_mode=mock, date_tolerance_days=int(date_tol),
                amount_tolerance=float(amt_tol),
                variance_threshold_pct=float(var_pct),
                variance_dollar_threshold=float(var_dollar),
                export_dir=export_dir,
                role=settings.role,
                default_retention_category=default_retention)
            new.save()
            st.session_state["settings"] = new
            st.success("Settings saved.")


# --------------------------------------------------------------------------- #
# About / Safety
# --------------------------------------------------------------------------- #
def render_about() -> None:
    st.title("About / Safety")
    st.subheader("Core principle")
    st.markdown(
        "The model may **explain, summarize, draft, classify, and flag**. The "
        "model must **not** perform authoritative financial calculations, decide "
        "final transaction matches, invent account numbers / funds / vendors / "
        "amounts / dates / policy, or produce final report language without "
        "human review.")
    st.subheader("Deterministic vs AI")
    st.markdown(
        "- **Code (deterministic):** parsing, cleaning, matching, all "
        "calculations, validation, source-row tracking, export formatting, "
        "audit logging.\n"
        "- **AI (advisory only):** plain-language explanation, summaries, "
        "drafts, classification, flagging — always citing source rows.")
    st.subheader("Data safety")
    st.warning(DATA_SAFETY_WARNING, icon="⚠️")
    st.subheader("Human review")
    st.warning(HUMAN_REVIEW_WARNING, icon="⚠️")
    st.subheader("Auditability")
    st.markdown(
        "Every run is recorded in a local SQLite ledger and an append-only "
        "audit log. Every AI output is validated against the source data and "
        "tied to the run ID, input files, prompt template version, model, and "
        "human-review status.")


# --------------------------------------------------------------------------- #
# Scheduled runs (Tier 1 #3 — local, manual-trigger recurring runs)
# --------------------------------------------------------------------------- #
# Cadence (value, label) options for the add-schedule form.
_CADENCE_CHOICES: tuple[tuple[str, str], ...] = (
    (scheduler.CadenceType.MONTHLY.value, "Monthly"),
    (scheduler.CadenceType.QUARTERLY.value, "Quarterly"),
    (scheduler.CadenceType.BEFORE_AGENDA.value, "Before agenda packet"),
    (scheduler.CadenceType.CUSTOM.value, "Custom (every N days)"),
)
_CADENCE_VALUES = [v for v, _ in _CADENCE_CHOICES]
_CADENCE_LABELS = {v: lbl for v, lbl in _CADENCE_CHOICES}


def render_scheduled_runs() -> None:
    from datetime import date as _date, datetime as _datetime

    settings = get_settings()
    ledger = get_ledger()
    audit = get_audit(ledger)
    store = get_schedule_store()
    st.title("Scheduled runs")
    st.caption(
        "Local, manual-trigger recurring runs (no background daemon, no cron). "
        "The app shows which configured workflows are DUE whenever you open it; "
        "you click 'Run now' to launch one on its bundled synthetic example "
        "files. Synthetic data only.")

    today = _date.today()  # system clock lives in the UI layer only
    schedules = store.list()
    due_ids = {s.schedule_id for s in store.due(today)}

    # --- Existing schedules --------------------------------------------- #
    st.subheader("Configured schedules")
    if not schedules:
        st.info("No schedules yet. Add one below.")
    else:
        st.dataframe(
            [{"label": s.label, "workflow": s.workflow_type,
              "cadence": _CADENCE_LABELS.get(s.cadence.value, s.cadence.value),
              "next_due": s.next_due.isoformat(),
              "last_run_at": (s.last_run_at.isoformat() if s.last_run_at
                              else "—"),
              "due?": "DUE" if s.schedule_id in due_ids else "",
              "active": s.active}
             for s in schedules],
            use_container_width=True, hide_index=True)
        n_due = len(due_ids)
        if n_due:
            st.warning(f"{n_due} schedule(s) due as of {today.isoformat()}.",
                       icon="⏰")
        else:
            st.caption(f"Nothing due as of {today.isoformat()}.")

        # --- Run now / remove controls ---------------------------------- #
        for s in schedules:
            badge = "  ·  DUE" if s.schedule_id in due_ids else ""
            with st.expander(f"{s.label} ({s.workflow_type}){badge}"):
                descriptor = wfr.DESCRIPTORS.get(s.workflow_type)
                can_run = bool(descriptor and descriptor.example_files
                               and descriptor.available)
                if not can_run:
                    st.caption("This workflow has no bundled example files to "
                               "run automatically; trigger it from Run Workflow.")
                cols = st.columns(2)
                with cols[0]:
                    if can_run and st.button(
                            "Run now", key=f"runsched__{s.schedule_id}"):
                        _run_scheduled(store, ledger, audit, settings, s,
                                       _datetime.now())
                with cols[1]:
                    if st.button("Remove", key=f"rmsched__{s.schedule_id}"):
                        store.remove(s.schedule_id)
                        st.success("Schedule removed. Reload to refresh the list.")

    # --- Add a schedule ------------------------------------------------- #
    st.subheader("Add a schedule")
    runnable = [d for d in wfr.list_descriptors()
                if d.example_files and d.available]
    if not runnable:
        st.caption("No runnable workflows with example data are available.")
        return
    with st.form("add_schedule_form"):
        wf_labels = {d.workflow_type: d.title for d in runnable}
        wf_type = st.selectbox(
            "Workflow", [d.workflow_type for d in runnable],
            format_func=lambda t: wf_labels.get(t, t))
        cadence = st.selectbox(
            "Cadence", _CADENCE_VALUES,
            format_func=lambda v: _CADENCE_LABELS.get(v, v))
        label = st.text_input("Label", value="")
        start = st.date_input("First due date (start)", value=today)
        interval = st.number_input(
            "Interval days (for Custom / Before-agenda cadences)",
            min_value=1, value=int(scheduler.DEFAULT_INTERVAL_DAYS))
        submitted = st.form_submit_button("Add schedule")
        if submitted:
            sched = scheduler.make_schedule(
                wf_type, scheduler.CadenceType(cadence),
                label or wf_labels.get(wf_type, wf_type),
                start, interval_days=int(interval))
            store.add(sched)
            st.success(f"Added schedule '{sched.label}'. Reload to refresh.")


def _run_scheduled(store, ledger, audit, settings, sched, when) -> None:
    """Run a schedule's workflow on its example files, then mark it run."""
    descriptor = wfr.DESCRIPTORS.get(sched.workflow_type)
    inputs = {
        key: str(wfr.example_path(rel))
        for key, rel in (descriptor.example_files or {}).items()
    }
    with st.spinner(f"Running '{sched.label}' on example files…"):
        try:
            result = wfr.run_workflow(
                sched.workflow_type, inputs,
                ledger=ledger, audit=audit, provider=None,
                actor=settings.default_actor,
                export_dir=Path(settings.export_dir),
                config=_settings_to_config(settings),
                retention_category=settings.default_retention_category,
            )
        except Exception as exc:
            st.error(f"Scheduled run failed: {exc}")
            return
    store.mark_run(sched.schedule_id, when)
    st.success(f"Run complete (run ID {result.run_id}). Next due: "
               f"{store.get(sched.schedule_id).next_due.isoformat()}. "
               "Open Review Run to inspect it.")


# --------------------------------------------------------------------------- #
# Redaction assist (Tier 1 #1 — prototype PII scrubber)
# --------------------------------------------------------------------------- #
def render_redaction_assist() -> None:
    ledger = get_ledger()
    st.title("Redaction assist")
    st.warning(
        "PROTOTYPE — synthetic data only. This is a best-effort regex PII "
        "scrubber to help a human spot obvious identifiers (SSNs, emails, "
        "phone/card/account numbers) before sharing text. It is NOT a "
        "certified redaction tool and may miss or over-match. Do NOT paste "
        "real sensitive data.", icon="⚠️")

    # Optionally seed the text area from a run's AI draft summary.
    seed = ""
    runs = ledger.list_runs()
    if runs:
        run_ids = ["(none)"] + [r["run_id"] for r in runs]
        chosen = st.selectbox("Load an AI draft from a run (optional)", run_ids)
        if chosen != "(none)":
            run = ledger.get_run(chosen)
            llms = (run.get("llm_responses") if run else None) or []
            if llms:
                rj = llms[-1].get("response_json", {}) or {}
                seed = rj.get("summary", "") or ""
                if not seed:
                    st.caption("That run's AI draft has no summary text to load.")

    text = st.text_area("Text to scan / redact", value=seed, height=200,
                        key="redaction_text")
    if st.button("Scan / redact", type="primary"):
        result = redaction.redact_text(text or "")
        st.subheader("Redacted text")
        st.text_area("Result", value=result.redacted_text, height=200,
                     key="redaction_result", disabled=True)
        st.subheader("Findings")
        if result.findings:
            st.dataframe(
                [{"type": f.pattern_type, "masked_preview": f.preview,
                  "start": f.start, "end": f.end} for f in result.findings],
                use_container_width=True, hide_index=True)
            st.markdown("**Counts by type**")
            st.write(result.counts_by_type)
        else:
            st.success("No PII patterns detected in the provided text.")


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
PAGE_RENDERERS = {
    "Home": render_home,
    "Run Workflow": render_run_workflow,
    "Workflow History": render_history,
    "Review Run": render_review_run,
    "Export Center": render_export_center,
    "AI Audit Log": render_ai_audit_log,
    "Scheduled runs": render_scheduled_runs,
    "Redaction assist": render_redaction_assist,
    "Settings": render_settings,
    "About / Safety": render_about,
}


def main() -> None:
    st.set_page_config(page_title="Municipal Finance AI Workflow Tool",
                       layout="wide")
    settings = get_settings()
    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Go to", PAGES)
    st.sidebar.markdown("---")
    # Role selector (display emphasis only — NO authentication). Persisted to
    # AppSettings so the choice survives reloads.
    cur_role = settings.role if settings.role in role_views.ROLE_ORDER else \
        role_views.DEFAULT_ROLE
    role = st.sidebar.selectbox(
        "Role", role_views.ROLE_ORDER,
        index=role_views.ROLE_ORDER.index(cur_role),
        help="Adjusts on-screen emphasis only. No access control; never hides "
             "data destructively.")
    if role != settings.role:
        settings.role = role
        settings.save()
        st.session_state["settings"] = settings
    st.sidebar.caption(role_views.get_role_view(role).caption)
    st.sidebar.markdown("---")
    st.sidebar.caption("Mock LLM mode is the default. Synthetic data only.")
    PAGE_RENDERERS[page]()


if __name__ == "__main__":
    main()
