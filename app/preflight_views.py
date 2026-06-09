"""Streamlit views for the preflight / capability layer (Phase 6+).

Renders the structured preflight report a workflow produces BEFORE it runs:
status badge (PASS / PARTIAL / FAIL), per-input file profile, column-mapping
status, date/amount parse confidence, supported checks, unsupported conditions
(code + message + suggested next step), and recommended next steps.

Two entry points the pages use:

* ``preview_preflight`` — runs ``preflight_engine.run_preflight`` on the inputs a
  user has provided (read-only, no run recorded) so the Run Workflow page can
  show the status and offer a lightweight human-approved column-mapping UI for
  ambiguous/unmapped REQUIRED columns BEFORE the real run.
* ``render_preflight_report`` — renders a stored report dict (from the ledger,
  ``run["preflight"]``) on the Review Run page.

FAIL-CLOSED rule (critical): for a FAILED preflight the caller must NOT show any
AI explanation as if the workflow ran. This module renders the structured report
+ next steps only; Guided Freeform is offered elsewhere as a separately-labeled,
draft-only mode (never an automatic fallback).

This module is import-time side-effect free and contains no provider-specific
code. It depends only on Streamlit + the public preflight API in ``src.core``.
"""
from __future__ import annotations

from typing import Any, Optional

import streamlit as st

from app import workflow_registry as wfr
from src.core import preflight as preflight_engine
from src.core.schemas import PreflightStatus

# Codes whose presence on a REQUIRED column means a human mapping could resolve
# (or clarify) the blocker. Used to decide when to surface the mapping UI.
_MAPPABLE_CODES = {
    "missing_required_column",
    "ambiguous_column_mapping",
    "needs_human_configuration",
}

_STATUS_HELP = {
    "pass": "All required inputs and columns are present and confidently "
            "parsed. The workflow runs normally.",
    "partial": "The workflow can run its supported deterministic checks, but "
               "some conditions are not fully supported. Output is marked "
               "PARTIAL and the AI may only explain deterministic findings.",
    "fail": "The workflow cannot run on these files. No AI explanation is "
            "produced. Follow the next steps below, then re-run.",
}


# --------------------------------------------------------------------------- #
# Status badge
# --------------------------------------------------------------------------- #
def render_status_badge(status: str, *, key_prefix: str = "") -> None:
    """Render a colored PASS / PARTIAL / FAIL badge with a one-line meaning."""
    status = (status or "").lower()
    help_text = _STATUS_HELP.get(status, "")
    if status == "pass":
        st.success(f"Preflight: PASS — {help_text}", icon="✅")
    elif status == "partial":
        st.warning(f"Preflight: PARTIAL — {help_text}", icon="⚠️")
    elif status == "fail":
        st.error(f"Preflight: FAIL — {help_text}", icon="⛔")
    else:
        st.info(f"Preflight: {status or 'n/a'}")


# --------------------------------------------------------------------------- #
# Report dict rendering (shared by preview + stored report)
# --------------------------------------------------------------------------- #
def _file_profile_rows(profiles: list[dict]) -> list[dict]:
    rows = []
    for p in profiles:
        cols = p.get("detected_columns") or []
        rows.append({
            "input": p.get("input_key", ""),
            "file": p.get("file_name", "") or "—",
            "type": p.get("file_type", "") or "—",
            "present": "yes" if p.get("present") else "NO",
            "rows": p.get("row_count", 0),
            "columns": len(cols),
        })
    return rows


def _parse_confidence_rows(profiles: list[dict]) -> list[dict]:
    rows = []
    for p in profiles:
        for col, conf in (p.get("parse_confidence") or {}).items():
            try:
                pct = f"{float(conf) * 100:.0f}%"
            except (TypeError, ValueError):
                pct = str(conf)
            rows.append({
                "input": p.get("input_key", ""),
                "column": col,
                "parse_confidence": pct,
            })
    return rows


def _mapping_rows(mappings: list[dict]) -> list[dict]:
    rows = []
    for m in mappings:
        try:
            conf = f"{float(m.get('confidence', 0)) * 100:.0f}%"
        except (TypeError, ValueError):
            conf = str(m.get("confidence", ""))
        rows.append({
            "input": m.get("input_key", ""),
            "semantic_field": m.get("semantic_name", ""),
            "mapped_column": m.get("mapped_column") or "(unmapped)",
            "confidence": conf,
            "source": m.get("source", ""),
        })
    return rows


def render_preflight_report(report: dict, *, key_prefix: str = "") -> None:
    """Render a preflight report dict (status, profiles, mappings, findings).

    ``report`` is the JSON-safe dict produced by
    ``preflight.preflight_report_dict`` (also what the ledger stores in
    ``run["preflight"]``). Renders nothing destructive and never shows AI text.
    """
    if not report:
        st.caption("No preflight report recorded for this run.")
        return

    status = report.get("status", "")
    render_status_badge(status, key_prefix=key_prefix)

    # File profiles ------------------------------------------------------- #
    profiles = report.get("file_profiles") or []
    st.markdown("**File profile** (per input: present / type / rows / columns)")
    if profiles:
        st.dataframe(_file_profile_rows(profiles),
                     use_container_width=True, hide_index=True)
        # Messy-data notes (repeated headers, footer totals, duplicates).
        for p in profiles:
            notes = p.get("notes") or []
            if notes:
                with st.expander(f"Data notes — {p.get('input_key', '')}"):
                    for n in notes:
                        st.caption(f"• {n}")
    else:
        st.caption("No input files profiled.")

    # Column-mapping status ----------------------------------------------- #
    mappings = report.get("column_mappings") or []
    if mappings:
        st.markdown("**Column mapping status**")
        st.dataframe(_mapping_rows(mappings),
                     use_container_width=True, hide_index=True)
        st.caption("source = how the column was resolved: auto (confident "
                   "detection), human (you approved it), or unmapped (the "
                   "workflow falls back to its own detection).")

    # Parse confidence ---------------------------------------------------- #
    conf_rows = _parse_confidence_rows(profiles)
    if conf_rows:
        st.markdown("**Parse confidence (date / amount columns)**")
        st.dataframe(conf_rows, use_container_width=True, hide_index=True)

    # Supported checks ---------------------------------------------------- #
    supported = report.get("supported_checks") or []
    if supported:
        st.markdown("**Supported checks for this run**")
        st.write(", ".join(supported))

    # Unsupported conditions (code + message + suggested next step) ------- #
    findings = report.get("findings") or []
    nonblocking = [f for f in findings if not f.get("blocks_run")]
    blocking = [f for f in findings if f.get("blocks_run")]
    if blocking:
        st.markdown("**Blocking conditions (must be resolved before running)**")
        for f in blocking:
            _render_finding(f, blocking=True)
    if nonblocking:
        st.markdown("**Unsupported / flagged conditions**")
        for f in nonblocking:
            _render_finding(f, blocking=False)
    if not findings:
        st.caption("No preflight conditions flagged.")

    # Recommended next steps ---------------------------------------------- #
    next_steps = report.get("next_steps") or []
    if next_steps:
        st.markdown("**Recommended next steps**")
        for s in next_steps:
            st.markdown(f"- {s}")


def _render_finding(f: dict, *, blocking: bool) -> None:
    code = f.get("code", "")
    msg = f.get("message", "")
    where = []
    if f.get("affected_input"):
        where.append(f"input `{f['affected_input']}`")
    if f.get("affected_column"):
        where.append(f"column `{f['affected_column']}`")
    suffix = (" — " + ", ".join(where)) if where else ""
    line = f"`{code}`: {msg}{suffix}"
    if blocking:
        st.error(line)
    else:
        st.warning(line)
    action = f.get("suggested_action")
    if action:
        st.caption(f"Suggested action: {action}")


# --------------------------------------------------------------------------- #
# Preview preflight (Run Workflow page) — read-only, no run recorded
# --------------------------------------------------------------------------- #
def preview_preflight(
    workflow_type: str,
    inputs: dict,
    *,
    approved_mappings: Optional[dict[str, dict[str, str]]] = None,
    config: Optional[dict] = None,
):
    """Run preflight on provided inputs WITHOUT recording a run.

    Returns the ``PreflightReport`` (or ``None`` if the workflow has no
    CAPABILITY, e.g. freeform). Pure read of the public preflight API; nothing is
    persisted. Used to drive the status display + column-mapping UI before the
    real ``run_workflow`` call.
    """
    module = wfr.WORKFLOW_MODULES.get(workflow_type)
    if module is None or not hasattr(module, "CAPABILITY"):
        return None
    try:
        return preflight_engine.run_preflight(
            module.CAPABILITY,
            inputs,
            approved_mappings=approved_mappings or None,
            config=config,
            detect_conditions=getattr(module, "detect_conditions", None),
        )
    except Exception:
        # A preview must never crash the page; the real run still does its own
        # preflight and will surface a clean error if inputs are unusable.
        return None


def _required_semantics(workflow_type: str) -> dict[str, list[str]]:
    """``{input_key: [required semantic names]}`` from the workflow CAPABILITY."""
    module = wfr.WORKFLOW_MODULES.get(workflow_type)
    spec = getattr(module, "CAPABILITY", None)
    if spec is None:
        return {}
    return dict(getattr(spec, "required_semantic_columns", {}) or {})


def needs_mapping_ui(report) -> bool:
    """True when a REQUIRED column is ambiguous/unmapped and a human could fix it.

    We only surface the mapping UI for required-column conditions where the
    relevant input file is present (so there ARE columns to choose from).
    """
    if report is None:
        return False
    required = _required_semantics(report.workflow_type)
    if not required:
        return False
    present_inputs = {
        p.input_key for p in report.file_profiles if p.present and p.detected_columns
    }
    for f in report.findings:
        if f.code.value not in _MAPPABLE_CODES:
            continue
        inp = f.affected_input
        if inp is None or inp in present_inputs:
            return True
    return False


def render_mapping_ui(report, *, key_prefix: str) -> dict[str, dict[str, str]]:
    """Render selectboxes mapping each unresolved REQUIRED semantic field.

    Returns ``{input_key: {semantic_name: chosen_column}}`` for the fields the
    user explicitly mapped (a non-blank choice). Auto-detected, confident columns
    are NOT shown here (they map silently); only required fields that are
    unmapped or ambiguous appear.
    """
    required = _required_semantics(report.workflow_type)
    profiles = {p.input_key: p for p in report.file_profiles}
    # Index existing mappings for default selection + to know what's resolved.
    mapping_by = {
        (m.input_key, m.semantic_name): m for m in report.column_mappings
    }

    st.markdown("**Map required columns**")
    st.caption(
        "Preflight could not confidently match one or more required columns. "
        "Pick the matching column from each file below, then run. Confident "
        "matches are detected automatically and not shown here.")

    chosen: dict[str, dict[str, str]] = {}
    for input_key, semantics in required.items():
        profile = profiles.get(input_key)
        if profile is None or not profile.present or not profile.detected_columns:
            continue
        detected = list(profile.detected_columns)
        unresolved = []
        for sem in semantics:
            m = mapping_by.get((input_key, sem))
            # Show the field when it is unmapped OR not confidently/auto resolved.
            if m is None or m.mapped_column is None or m.source == "unmapped":
                unresolved.append((sem, m))
        if not unresolved:
            continue
        st.markdown(f"_Input: `{input_key}`_")
        for sem, m in unresolved:
            options = ["(leave to auto-detect)"] + detected
            # Default to a ranked candidate if the engine found a plausible one.
            default_idx = 0
            if m is not None and m.mapped_column in detected:
                default_idx = options.index(m.mapped_column)
            elif m is not None and m.candidates:
                for cand in m.candidates:
                    if cand in detected:
                        default_idx = options.index(cand)
                        break
            sel = st.selectbox(
                f"{sem} →",
                range(len(options)),
                index=default_idx,
                format_func=lambda i, _o=options: _o[i],
                key=f"{key_prefix}__map__{input_key}__{sem}",
                help=f"Which column in '{input_key}' holds the {sem}?",
            )
            if sel != 0:
                chosen.setdefault(input_key, {})[sem] = options[sel]
    if chosen:
        st.caption("Approved mappings: " + "; ".join(
            f"{ik}: " + ", ".join(f"{s}→{c}" for s, c in cols.items())
            for ik, cols in chosen.items()))
    return chosen


# --------------------------------------------------------------------------- #
# Compact one-liners (history / run summary)
# --------------------------------------------------------------------------- #
def status_label(status: str) -> str:
    """Short emoji + word label for tables / metrics."""
    s = (status or "").lower()
    return {
        "pass": "✅ PASS",
        "partial": "⚠️ PARTIAL",
        "fail": "⛔ FAIL",
    }.get(s, status or "n/a")


def is_fail(status: str) -> bool:
    return (status or "").lower() == PreflightStatus.FAIL.value


def is_partial(status: str) -> bool:
    return (status or "").lower() == PreflightStatus.PARTIAL.value
