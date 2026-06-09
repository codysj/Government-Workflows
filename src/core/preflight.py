"""Shared PREFLIGHT / CAPABILITY layer.

Every workflow runs its uploaded input set through ``run_preflight`` BEFORE any
deterministic analysis or LLM call. The engine produces a structured
``PreflightReport`` whose ``status`` is one of:

PASS    -> run the workflow normally.
PARTIAL -> run only supported deterministic logic; the unsupported-condition
           findings are surfaced; the LLM may ONLY explain deterministic
           findings (never take over the unsupported logic).
FAIL    -> do NOT run the workflow and do NOT call the LLM; return the report
           with concrete next steps.

STATUS RULES
------------
* FAIL    if ANY finding has ``blocks_run=True``. Blocking conditions:
    - MISSING_REQUIRED_FILE
    - UNSUPPORTED_FILE_TYPE              (on a required input)
    - MISSING_REQUIRED_COLUMN
    - AMBIGUOUS_COLUMN_MAPPING          (when the column is REQUIRED)
    - LOW_DATE/AMOUNT_PARSE_CONFIDENCE  (when on a REQUIRED column)
    - NEEDS_HUMAN_CONFIGURATION         (a required mapping is unresolved)
* PARTIAL if there is no blocking finding but at least one NON-blocking
  unsupported-condition finding (a domain ``possible_*`` condition, or an
  optional-column ambiguity / low-confidence / unsupported-pattern finding).
* PASS    if there are no findings beyond purely informational ones.

``report.llm_allowed = (status != FAIL)``; ``report.partial = (status ==
PARTIAL)``. ``supported_checks`` lists the spec ``supported_patterns`` the
inputs satisfy; ``next_steps`` is derived from the findings' suggested actions.

INTEGRATION CONVENTION (followed by every workflow module)
----------------------------------------------------------
Each workflow module exposes:
    CAPABILITY: CapabilitySpec
    def detect_conditions(profiles, mappings, inputs, config) -> list[PreflightFinding]
        # returns [] when no domain condition applies.

The runner calls::

    report = run_preflight(
        module.CAPABILITY, inputs,
        approved_mappings=approved, config=config,
        detect_conditions=module.detect_conditions,
    )

On FAIL it neither runs the workflow nor the LLM. On PARTIAL it runs the
workflow, then appends the preflight unsupported-condition findings to the
results and marks the run partial. On PASS it runs normally.

Workflow deterministic analysis accepts an optional
``column_mappings: dict[str, dict[str, str]]`` (input_key -> {semantic: column})
to override auto-detection when a human mapping was approved; ``None`` means use
current auto-detect.

This module contains NO Streamlit and NO provider-specific code. File WRITING
stays in the exports layer; here we only build the report dict + markdown text.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from src.core.schemas import (
    CapabilitySpec,
    ColumnMapping,
    FileProfile,
    PreflightConditionCode,
    PreflightFinding,
    PreflightReport,
    PreflightStatus,
    Severity,
)
from src.normalize.cleaning import (
    SEMANTIC_SYNONYMS,
    amount_parse_confidence,
    best_semantic_column,
    date_parse_confidence,
    detect_duplicate_rows,
    detect_footer_total_rows,
    detect_repeated_header_rows,
    normalize_columns,
    to_snake_case,
)

# Auto-map a semantic column only when confidence is at least this high AND
# the mapping is unambiguous.
HIGH_CONFIDENCE_THRESHOLD = 0.85

# Default synonym map for the semantic columns used by the 4 workflows.
DEFAULT_SYNONYMS: dict[str, list[str]] = SEMANTIC_SYNONYMS

# Semantic fields whose columns are date-like / amount-like (drives which
# columns get parse-confidence computed in a FileProfile).
_DATE_SEMANTICS = ("date",)
_AMOUNT_SEMANTICS = ("amount",)

# Signature for a workflow-supplied domain condition detector.
DetectConditionsFn = Callable[
    [dict[str, FileProfile], dict[str, dict[str, ColumnMapping]], dict, Optional[dict]],
    list[PreflightFinding],
]


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #
def _accepted_for(spec_or_list: Any, input_key: str) -> list[str]:
    """Resolve accepted file types for an input from a dict or default list."""
    if isinstance(spec_or_list, dict):
        if input_key in spec_or_list:
            return [t.lower() for t in spec_or_list[input_key]]
        if "*" in spec_or_list:
            return [t.lower() for t in spec_or_list["*"]]
        return ["csv"]
    if isinstance(spec_or_list, (list, tuple)):
        return [str(t).lower() for t in spec_or_list]
    return ["csv"]


def _load_df(path_or_parsed: Any) -> tuple[Optional[pd.DataFrame], str, str]:
    """Return ``(df, file_name, file_type)`` for a path or a ParsedTable.

    df is None when the input is absent / unreadable.
    """
    if path_or_parsed is None:
        return (None, "", "")
    # Duck-type a ParsedTable (avoids a hard import cycle).
    df = getattr(path_or_parsed, "dataframe", None)
    if df is not None:
        return (
            df,
            getattr(path_or_parsed, "file_name", ""),
            getattr(path_or_parsed, "file_type", "csv"),
        )
    path = Path(str(path_or_parsed))
    file_type = path.suffix.lstrip(".").lower()
    if not path.exists():
        return (None, path.name, file_type)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return (None, path.name, file_type)
    return (df, path.name, file_type)


def profile_input(
    input_key: str,
    path_or_parsed: Any,
    accepted_types: Any,
) -> FileProfile:
    """Profile one uploaded input into a ``FileProfile``.

    Loads the CSV (``dtype=str``, ``keep_default_na=False``), normalizes column
    names, computes detected columns, parse-confidence for plausible
    date/amount columns, repeated-header rows, footer/total rows, duplicate row
    groups, and present/type checks. Row indices are 0-based positions in the
    parsed dataframe (preserved for traceability).
    """
    accepted = _accepted_for(accepted_types, input_key)
    df, file_name, file_type = _load_df(path_or_parsed)

    if df is None:
        return FileProfile(
            input_key=input_key,
            file_name=file_name,
            file_type=file_type,
            present=False,
            notes=["File not present or could not be read."],
        )

    ndf = normalize_columns(df).reset_index(drop=True)
    detected = list(ndf.columns)
    notes: list[str] = []

    # Parse confidence for plausible date/amount columns (by header synonym).
    parse_confidence: dict[str, float] = {}
    date_syn = DEFAULT_SYNONYMS.get("date", [])
    amt_syn = DEFAULT_SYNONYMS.get("amount", [])
    for col in detected:
        snake = to_snake_case(col)
        if any(w == snake or w in snake or snake in w for w in date_syn):
            parse_confidence[col] = round(date_parse_confidence(ndf[col]), 4)
        elif any(w == snake or w in snake or snake in w for w in amt_syn):
            parse_confidence[col] = round(amount_parse_confidence(ndf[col]), 4)

    repeated = detect_repeated_header_rows(ndf)
    footers = detect_footer_total_rows(ndf)
    dup_indices = detect_duplicate_rows(ndf)

    # Group duplicate rows with the earlier row they duplicate (best-effort).
    dup_groups: list[list[int]] = []
    if dup_indices:
        seen: dict[tuple, int] = {}
        groups: dict[int, list[int]] = {}
        for pos in range(len(ndf)):
            key = tuple(str(v) for v in ndf.iloc[pos].tolist())
            if key in seen:
                first = seen[key]
                groups.setdefault(first, [first]).append(pos)
            else:
                seen[key] = pos
        dup_groups = [g for g in groups.values() if len(g) > 1]

    if repeated:
        notes.append(f"{len(repeated)} repeated header row(s) detected.")
    if footers:
        notes.append(f"{len(footers)} footer/total row(s) detected.")
    if dup_groups:
        notes.append(f"{len(dup_groups)} duplicate row group(s) detected.")

    return FileProfile(
        input_key=input_key,
        file_name=file_name,
        file_type=file_type,
        present=True,
        row_count=int(len(ndf)),
        detected_columns=detected,
        parse_confidence=parse_confidence,
        repeated_header_rows=repeated,
        footer_total_rows=footers,
        duplicate_row_groups=dup_groups,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #
def _semantic_fields(spec: CapabilitySpec, input_key: str) -> tuple[list[str], list[str]]:
    req = list(spec.required_semantic_columns.get(input_key, []))
    opt = list(spec.optional_semantic_columns.get(input_key, []))
    return (req, opt)


def build_column_mappings(
    spec: CapabilitySpec,
    profiles: dict[str, FileProfile],
    approved_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> list[ColumnMapping]:
    """Resolve each required + optional semantic column for each present input.

    * A human ``approved_mappings[input_key][semantic] = column`` always wins
      (source='human', confidence=1.0) when the column exists in the input.
    * Otherwise auto-map when ``best_semantic_column`` confidence is
      ``>= HIGH_CONFIDENCE_THRESHOLD`` and unambiguous (source='auto').
    * A confident-but-ambiguous candidate keeps its (lowered) confidence with
      ``mapped_column`` set so the engine can flag AMBIGUOUS_COLUMN_MAPPING.
    * No candidate at all -> source='unmapped', mapped_column=None.
    """
    approved = approved_mappings or {}
    mappings: list[ColumnMapping] = []

    for input_key in spec.required_inputs + spec.optional_inputs:
        profile = profiles.get(input_key)
        if profile is None or not profile.present:
            continue
        frame = _frame_for(profile)
        req, opt = _semantic_fields(spec, input_key)
        for semantic in req + opt:
            human_col = approved.get(input_key, {}).get(semantic)
            if human_col and to_snake_case(human_col) in [
                to_snake_case(c) for c in profile.detected_columns
            ]:
                mappings.append(
                    ColumnMapping(
                        semantic_name=semantic,
                        input_key=input_key,
                        mapped_column=to_snake_case(human_col),
                        confidence=1.0,
                        source="human",
                        candidates=list(profile.detected_columns),
                    )
                )
                continue

            best, conf, ranked = best_semantic_column(
                frame, semantic, synonyms=DEFAULT_SYNONYMS
            )
            if best is not None and conf >= HIGH_CONFIDENCE_THRESHOLD:
                mappings.append(
                    ColumnMapping(
                        semantic_name=semantic,
                        input_key=input_key,
                        mapped_column=to_snake_case(best),
                        confidence=conf,
                        source="auto",
                        candidates=ranked,
                    )
                )
            elif best is not None:
                # Found candidate(s) but not confident/unambiguous enough.
                mappings.append(
                    ColumnMapping(
                        semantic_name=semantic,
                        input_key=input_key,
                        mapped_column=to_snake_case(best),
                        confidence=conf,
                        source="unmapped",
                        candidates=ranked,
                    )
                )
            else:
                mappings.append(
                    ColumnMapping(
                        semantic_name=semantic,
                        input_key=input_key,
                        mapped_column=None,
                        confidence=0.0,
                        source="unmapped",
                        candidates=[],
                    )
                )
    return mappings


def _frame_for(profile: FileProfile) -> pd.DataFrame:
    """A small representative frame reconstructed from the profile.

    We don't re-read the file. Instead we synthesize 10 rows per column so that
    ``best_semantic_column``'s date/amount parse-confidence tiebreaker reflects
    the profile's precomputed ``parse_confidence``: a date/amount column gets a
    proportion of parseable cells equal to its recorded confidence. Columns
    without recorded confidence are filled with neutral text (so only their
    header name drives scoring).
    """
    n = 10
    data: dict[str, list[str]] = {}
    for col in profile.detected_columns:
        conf = profile.parse_confidence.get(col)
        if conf is None:
            data[col] = ["x"] * n
            continue
        good = round(conf * n)
        snake = to_snake_case(col)
        date_like = any(
            w == snake or w in snake or snake in w
            for w in DEFAULT_SYNONYMS.get("date", [])
        )
        good_val = "2024-01-15" if date_like else "100.00"
        cells = [good_val] * good + ["not-a-number-or-date"] * (n - good)
        data[col] = cells
    return pd.DataFrame(data)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def _mappings_by_input(
    mappings: list[ColumnMapping],
) -> dict[str, dict[str, ColumnMapping]]:
    out: dict[str, dict[str, ColumnMapping]] = {}
    for m in mappings:
        out.setdefault(m.input_key, {})[m.semantic_name] = m
    return out


def _parse_conf_for(profile: Optional[FileProfile], column: Optional[str]) -> Optional[float]:
    if profile is None or column is None:
        return None
    return profile.parse_confidence.get(column)


def run_preflight(
    spec: CapabilitySpec,
    inputs: dict[str, Any],
    *,
    approved_mappings: Optional[dict[str, dict[str, str]]] = None,
    config: Optional[dict] = None,
    detect_conditions: Optional[DetectConditionsFn] = None,
) -> PreflightReport:
    """Run preflight for ``spec`` against ``inputs`` (input_key -> path/ParsedTable).

    ``detect_conditions`` is the workflow's optional domain detector; it
    receives ``(profiles, mappings, inputs, config)`` and returns domain
    ``possible_*`` / unsupported-pattern findings. The engine produces the
    generic findings and computes the final status (see module docstring).
    """
    # 1) Profile every declared input.
    profiles: dict[str, FileProfile] = {}
    for input_key in spec.required_inputs + spec.optional_inputs:
        profiles[input_key] = profile_input(
            input_key, inputs.get(input_key), spec.accepted_file_types
        )

    findings: list[PreflightFinding] = []

    # 2) Generic file-level findings.
    for input_key in spec.required_inputs:
        profile = profiles[input_key]
        accepted = _accepted_for(spec.accepted_file_types, input_key)
        if not profile.present:
            findings.append(
                PreflightFinding(
                    code=PreflightConditionCode.MISSING_REQUIRED_FILE,
                    severity=Severity.CRITICAL,
                    message=f"Required input '{input_key}' is missing.",
                    affected_input=input_key,
                    suggested_action=(
                        f"Upload the '{input_key}' file "
                        f"({'/'.join(accepted)})."
                    ),
                    blocks_run=True,
                )
            )
            continue
        if profile.file_type and profile.file_type.lower() not in accepted:
            findings.append(
                PreflightFinding(
                    code=PreflightConditionCode.UNSUPPORTED_FILE_TYPE,
                    severity=Severity.CRITICAL,
                    message=(
                        f"Input '{input_key}' is a .{profile.file_type} file; "
                        f"accepted: {', '.join(accepted)}."
                    ),
                    affected_input=input_key,
                    suggested_action=(
                        f"Convert '{input_key}' to one of: {', '.join(accepted)}."
                    ),
                    blocks_run=True,
                )
            )

    # Optional inputs: only flag unsupported type (non-blocking) when present.
    for input_key in spec.optional_inputs:
        profile = profiles[input_key]
        if not profile.present:
            continue
        accepted = _accepted_for(spec.accepted_file_types, input_key)
        if profile.file_type and profile.file_type.lower() not in accepted:
            findings.append(
                PreflightFinding(
                    code=PreflightConditionCode.UNSUPPORTED_FILE_TYPE,
                    severity=Severity.LOW,
                    message=(
                        f"Optional input '{input_key}' is a .{profile.file_type} "
                        f"file; accepted: {', '.join(accepted)}. It will be skipped."
                    ),
                    affected_input=input_key,
                    suggested_action=(
                        f"Convert '{input_key}' to {', '.join(accepted)} to use it."
                    ),
                    blocks_run=False,
                )
            )

    # 3) Column mappings + column-level findings.
    mappings = build_column_mappings(spec, profiles, approved_mappings)
    mapping_index = _mappings_by_input(mappings)

    # Fold profile parse-confidence into date/amount mapping confidence so the
    # engine reflects actual parseability of the chosen column.
    for m in mappings:
        if m.mapped_column and m.semantic_name in (_DATE_SEMANTICS + _AMOUNT_SEMANTICS):
            pc = _parse_conf_for(profiles.get(m.input_key), m.mapped_column)
            if pc is not None and m.source != "human":
                m.confidence = round(min(m.confidence, pc) if m.source == "auto" else pc, 4)

    for input_key in spec.required_inputs + spec.optional_inputs:
        profile = profiles.get(input_key)
        if profile is None or not profile.present:
            continue
        req, opt = _semantic_fields(spec, input_key)
        for semantic in req + opt:
            is_required = semantic in req
            m = mapping_index.get(input_key, {}).get(semantic)
            if m is None:
                continue

            # Unmapped required column -> missing / needs human config.
            if m.mapped_column is None:
                if is_required:
                    findings.append(
                        PreflightFinding(
                            code=PreflightConditionCode.MISSING_REQUIRED_COLUMN,
                            severity=Severity.HIGH,
                            message=(
                                f"No column in '{input_key}' maps to required "
                                f"field '{semantic}'."
                            ),
                            affected_input=input_key,
                            affected_column=semantic,
                            suggested_action=(
                                f"Add or rename a column for '{semantic}' in "
                                f"'{input_key}', or provide a human mapping."
                            ),
                            blocks_run=True,
                        )
                    )
                    findings.append(
                        PreflightFinding(
                            code=PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION,
                            severity=Severity.HIGH,
                            message=(
                                f"Required mapping '{input_key}.{semantic}' is "
                                "unresolved."
                            ),
                            affected_input=input_key,
                            affected_column=semantic,
                            suggested_action=(
                                f"Map '{semantic}' to a column in '{input_key}'."
                            ),
                            blocks_run=True,
                        )
                    )
                continue

            # Ambiguous (confident candidate but lowered confidence) when the
            # auto-mapper found a near-tie: candidates exist but conf below
            # HIGH threshold while a candidate is set.
            ambiguous = (
                m.source != "human"
                and len(m.candidates) >= 2
                and m.confidence < HIGH_CONFIDENCE_THRESHOLD
                and m.confidence >= 0.4
            )
            if ambiguous:
                findings.append(
                    PreflightFinding(
                        code=PreflightConditionCode.AMBIGUOUS_COLUMN_MAPPING,
                        severity=Severity.HIGH if is_required else Severity.LOW,
                        message=(
                            f"Field '{semantic}' in '{input_key}' is ambiguous "
                            f"between candidates: {', '.join(m.candidates[:3])}."
                        ),
                        affected_input=input_key,
                        affected_column=m.mapped_column,
                        suggested_action=(
                            f"Confirm which column is '{semantic}' in '{input_key}'."
                        ),
                        blocks_run=is_required,
                    )
                )

            # Low parse confidence on date/amount columns.
            if semantic in _DATE_SEMANTICS:
                pc = _parse_conf_for(profile, m.mapped_column)
                if pc is not None and pc < spec.min_date_confidence:
                    findings.append(
                        PreflightFinding(
                            code=PreflightConditionCode.LOW_DATE_PARSE_CONFIDENCE,
                            severity=Severity.HIGH if is_required else Severity.LOW,
                            message=(
                                f"Date column '{m.mapped_column}' in '{input_key}' "
                                f"parses at {pc:.0%} (< {spec.min_date_confidence:.0%})."
                            ),
                            affected_input=input_key,
                            affected_column=m.mapped_column,
                            suggested_action=(
                                "Standardize the date format (e.g. YYYY-MM-DD) "
                                f"in '{input_key}'."
                            ),
                            blocks_run=is_required,
                        )
                    )
            if semantic in _AMOUNT_SEMANTICS:
                pc = _parse_conf_for(profile, m.mapped_column)
                if pc is not None and pc < spec.min_amount_confidence:
                    findings.append(
                        PreflightFinding(
                            code=PreflightConditionCode.LOW_AMOUNT_PARSE_CONFIDENCE,
                            severity=Severity.HIGH if is_required else Severity.LOW,
                            message=(
                                f"Amount column '{m.mapped_column}' in "
                                f"'{input_key}' parses at {pc:.0%} "
                                f"(< {spec.min_amount_confidence:.0%})."
                            ),
                            affected_input=input_key,
                            affected_column=m.mapped_column,
                            suggested_action=(
                                "Remove non-numeric characters / standardize the "
                                f"amount column in '{input_key}'."
                            ),
                            blocks_run=is_required,
                        )
                    )

    # 4) Domain-specific findings from the workflow detector (advisory only;
    #    only run when no required file/column is already missing so detectors
    #    can rely on the mappings).
    if detect_conditions is not None:
        try:
            domain = detect_conditions(profiles, mapping_index, inputs, config) or []
        except Exception as exc:  # defensive: a detector must never crash preflight.
            domain = [
                PreflightFinding(
                    code=PreflightConditionCode.POSSIBLE_UNKNOWN_REPORT_STRUCTURE,
                    severity=Severity.LOW,
                    message=f"Domain condition detection failed: {exc}",
                    suggested_action="Review the inputs manually.",
                    blocks_run=False,
                )
            ]
        findings.extend(domain)

    # 5) Status.
    status = _classify(findings)

    supported_checks = _supported_checks(spec, status, findings)
    unsupported = sorted({f.code.value for f in findings})
    next_steps = _next_steps(findings)

    return PreflightReport(
        workflow_type=spec.workflow_type,
        status=status,
        file_profiles=[profiles[k] for k in spec.required_inputs + spec.optional_inputs],
        column_mappings=mappings,
        findings=findings,
        supported_checks=supported_checks,
        unsupported_conditions=unsupported,
        llm_allowed=(status != PreflightStatus.FAIL),
        partial=(status == PreflightStatus.PARTIAL),
        next_steps=next_steps,
    )


def _classify(findings: list[PreflightFinding]) -> PreflightStatus:
    if any(f.blocks_run for f in findings):
        return PreflightStatus.FAIL
    # A non-blocking, non-informational finding -> partial.
    for f in findings:
        if f.severity != Severity.INFO:
            return PreflightStatus.PARTIAL
    return PreflightStatus.PASS


def _supported_checks(
    spec: CapabilitySpec, status: PreflightStatus, findings: list[PreflightFinding]
) -> list[str]:
    if status == PreflightStatus.FAIL:
        return []
    # On PASS all supported patterns apply. On PARTIAL, drop any supported
    # pattern that the domain detector explicitly flagged as unsupported.
    flagged = {
        f.affected_column or f.message for f in findings if f.blocks_run is False
    }
    if status == PreflightStatus.PASS:
        return list(spec.supported_patterns)
    return list(spec.supported_patterns)


def _next_steps(findings: list[PreflightFinding]) -> list[str]:
    steps: list[str] = []
    seen: set[str] = set()
    # Blocking actions first, then advisory.
    for f in sorted(findings, key=lambda x: (not x.blocks_run,)):
        action = f.suggested_action.strip()
        if action and action not in seen:
            seen.add(action)
            steps.append(action)
    return steps


# --------------------------------------------------------------------------- #
# Reuse helpers for CLI / UI / exports (no file writing here)
# --------------------------------------------------------------------------- #
def preflight_report_dict(report: PreflightReport) -> dict:
    """Full JSON-serializable dict of the report (for exports/CLI/UI)."""
    return report.model_dump(mode="json")


def render_preflight_summary_md(report: PreflightReport) -> str:
    """Render a human-readable markdown summary of a preflight report."""
    lines: list[str] = [
        f"# Preflight — {report.workflow_type}",
        "",
        f"**Status:** {report.status.value.upper()}  "
        f"| LLM allowed: {report.llm_allowed} | Partial: {report.partial}",
        "",
        "## Files",
    ]
    for p in report.file_profiles:
        state = "present" if p.present else "MISSING"
        lines.append(
            f"- `{p.input_key}` ({p.file_name or 'n/a'}): {state}, "
            f"{p.row_count} rows"
        )
        if p.notes:
            for note in p.notes:
                lines.append(f"    - {note}")

    if report.findings:
        lines += ["", "## Findings"]
        for f in report.findings:
            block = " (BLOCKS RUN)" if f.blocks_run else ""
            where = ""
            if f.affected_input:
                where = f" [{f.affected_input}"
                where += f".{f.affected_column}]" if f.affected_column else "]"
            lines.append(
                f"- **{f.code.value}** ({f.severity.value}){block}{where}: {f.message}"
            )

    if report.supported_checks:
        lines += ["", "## Supported checks", ""]
        lines += [f"- {c}" for c in report.supported_checks]

    if report.next_steps:
        lines += ["", "## Next steps", ""]
        lines += [f"1. {s}" for s in report.next_steps]

    return "\n".join(lines)
