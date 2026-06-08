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
from app import workflow_registry as wfr  # noqa: E402
from src.core.audit_log import AuditLog  # noqa: E402
from src.core.run_ledger import RunLedger  # noqa: E402

PAGES = (
    "Home",
    "Run Workflow",
    "Workflow History",
    "Review Run",
    "Export Center",
    "Settings",
    "About / Safety",
)

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


def get_settings() -> AppSettings:
    if "settings" not in st.session_state:
        st.session_state["settings"] = AppSettings.load()
    return st.session_state["settings"]


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
    if required:
        st.markdown("**Required uploads**")
        for u in required:
            if u.key == "uploaded_files":
                continue
            st.file_uploader(
                u.label, type=list(u.file_types), key=f"upload__{descriptor.workflow_type}__{u.key}",
                help=u.help,
            )
    if optional:
        st.markdown("**Optional uploads**")
        for u in optional:
            if u.key == "uploaded_files":
                continue
            st.file_uploader(
                u.label, type=list(u.file_types), key=f"upload__{descriptor.workflow_type}__{u.key}",
                help=u.help,
            )

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

        export_dir = Path(settings.export_dir)
        provider = None  # mock default (no key / no internet)
        try:
            result = wfr.run_workflow(
                descriptor.workflow_type,
                inputs,
                ledger=ledger,
                audit=audit,
                provider=provider,
                actor=settings.default_actor,
                export_dir=export_dir,
            )
        except Exception as exc:  # surface errors plainly to staff
            st.error(f"Run failed: {exc}")
            return

        if result.refused:
            st.error("Run refused: " + result.refusal_reason)
            return

        st.success(f"Run complete. Run ID: {result.run_id}")
        st.session_state["last_run_id"] = result.run_id
        st.session_state["selected_run_id"] = result.run_id
        st.metric("Findings", len(result.findings))
        st.info("Open the **Review Run** page to inspect findings, the AI "
                "explanation, validation warnings, and human-review controls.")


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
        rows.append({
            "run_id": r["run_id"],
            "type": r["workflow_type"],
            "created_at": r["created_at"],
            "status": r["status"],
            "validation": summary.get("validation_status", "n/a"),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

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
    c1, c2, c3 = st.columns(3)
    c1.metric("Status", run.get("status", ""))
    c2.metric("Workflow", run.get("workflow_type", ""))
    c3.metric("Validation", summary.get("validation_status", "n/a"))

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

    # --- Deterministic findings (review TABLE, prioritized) -------------- #
    st.subheader("Deterministic findings")
    findings = run.get("findings", []) or []
    if findings:
        st.dataframe(_finding_table_rows(findings),
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


def _download_artifact(a: dict) -> None:
    path = Path(a.get("path", ""))
    name = a.get("file_name", path.name)
    if path.is_file():
        try:
            data = path.read_bytes()
            st.download_button(f"Download {name}", data=data, file_name=name,
                               key=f"dl__{a.get('artifact_id', name)}")
        except OSError:
            st.caption(f"{name} (unreadable)")
    else:
        st.caption(f"{name} (missing on disk: {path})")


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
        for a in artifacts:
            _download_artifact(a)
    else:
        st.caption("No artifacts recorded for this run.")

    st.subheader("Regenerate export packet")
    st.caption("Re-runs export generation for this run from its stored "
               "findings, AI response, and validation result.")
    if st.button("Generate export packet"):
        ok = _regenerate_exports(ledger, audit, run, settings)
        if ok:
            st.success("Export packet generated. Reload to see download links.")
        else:
            st.warning("Could not regenerate this run's exports automatically. "
                       "Re-run the workflow to refresh artifacts.")


def _regenerate_exports(ledger, audit, run, settings) -> bool:
    """Best-effort re-export from stored ledger data.

    report_review and freeform can rebuild artifacts from stored data; for
    others we report that a re-run is needed (keeps deterministic guarantees —
    we never fabricate result tables that were not persisted)."""
    if run is None:
        return False
    run_id = run["run_id"]
    wtype = run.get("workflow_type")
    out_dir = Path(settings.export_dir) / run_id
    findings = run.get("findings", []) or []
    llms = run.get("llm_responses", []) or []
    validations = run.get("validation_results", []) or []
    if wtype not in ("report_review", "freeform") or not findings:
        return False
    # Rebuild typed objects from stored payloads.
    from src.core.schemas import DeterministicFinding, ValidationResult
    det_findings = [DeterministicFinding(**f) for f in findings]
    response_json = llms[-1].get("response_json", {}) if llms else {}
    validation = (ValidationResult(**validations[-1]) if validations
                  else ValidationResult(passed=True))
    events = audit.list_events(run_id)
    if wtype == "report_review":
        from src.workflows import report_review as rr

        class _Det:
            findings = det_findings
            summary = run.get("summary", {}) or {}
        paths = rr.export_artifacts(out_dir, _Det(), response_json, validation, events)
        artifacts = _artifacts_from_paths(run_id, paths)
    else:  # freeform
        from src.workflows import freeform as ff
        det = ff.run_deterministic(
            ff.FreeformRequest(task_type=str((run.get("summary") or {}).get(
                "task_type", "freeform")), sensitivity_confirmation=True))
        det.findings = det_findings
        artifacts = ff.export_artifacts(
            out_dir, det, response_json, validation, events, run_id=run_id)
    for a in artifacts:
        ledger.store_export_artifact(run_id, a)
    audit.export_generated(run_id, settings.default_actor,
                           artifacts=[a.file_name for a in artifacts])
    return True


def _artifacts_from_paths(run_id, paths: dict):
    """Wrap a name->path mapping (report_review style) as ExportArtifacts."""
    import hashlib

    from src.core.schemas import ArtifactType, ExportArtifact
    type_map = {".md": ArtifactType.MARKDOWN, ".csv": ArtifactType.CSV,
                ".json": ArtifactType.JSON}
    out = []
    for name, p in paths.items():
        p = Path(p)
        sha = (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "")
        out.append(ExportArtifact(
            run_id=run_id,
            artifact_type=type_map.get(p.suffix, ArtifactType.OTHER),
            file_name=name, path=str(p), sha256=sha))
    return out


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def render_settings() -> None:
    settings = get_settings()
    st.title("Settings")
    st.caption("No authentication in the MVP. Settings persist to "
               "app_settings.json at the repo root.")

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
        submitted = st.form_submit_button("Save settings")
        if submitted:
            new = AppSettings(
                city_name=city, default_actor=actor, llm_provider=provider,
                mock_mode=mock, date_tolerance_days=int(date_tol),
                amount_tolerance=float(amt_tol),
                variance_threshold_pct=float(var_pct),
                variance_dollar_threshold=float(var_dollar),
                export_dir=export_dir)
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
# Dispatcher
# --------------------------------------------------------------------------- #
PAGE_RENDERERS = {
    "Home": render_home,
    "Run Workflow": render_run_workflow,
    "Workflow History": render_history,
    "Review Run": render_review_run,
    "Export Center": render_export_center,
    "Settings": render_settings,
    "About / Safety": render_about,
}


def main() -> None:
    st.set_page_config(page_title="Municipal Finance AI Workflow Tool",
                       layout="wide")
    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Go to", PAGES)
    st.sidebar.markdown("---")
    st.sidebar.caption("Mock LLM mode is the default. Synthetic data only.")
    PAGE_RENDERERS[page]()


if __name__ == "__main__":
    main()
