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

This module contains NO UI and NO provider-specific code. File WRITING
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
from src.ingest.csv_loader import read_csv_table, read_excel_table
from src.normalize.cleaning import (
    SEMANTIC_SYNONYMS,
    amount_parse_confidence,
    best_semantic_column,
    date_parse_confidence,
    derive_signed_amount,
    detect_duplicate_rows,
    detect_footer_total_rows,
    detect_repeated_header_rows,
    normalize_columns,
    to_snake_case,
)

# File types that the loaders read with pandas/openpyxl. ``ParsedTable`` from
# the excel loader reports its type as "excel"; treat it as the xlsx family
# when checking against a spec's accepted file types.
_EXCEL_TYPES = ("xlsx", "xlsm", "excel")


def _norm_file_type(file_type: str) -> str:
    """Normalize a profile file_type for accepted-type checks ('excel'->'xlsx')."""
    ft = (file_type or "").lower()
    return "xlsx" if ft in _EXCEL_TYPES else ft

# Auto-map a semantic column only when confidence is at least this high AND
# the mapping is unambiguous.
HIGH_CONFIDENCE_THRESHOLD = 0.85

# Default synonym map for the semantic columns the workflows declare.
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
        if file_type in ("xlsx", "xlsm"):
            # Excel reads mirror src.ingest.excel_loader: every cell as text,
            # blanks preserved as empty strings (no rows dropped).
            df = read_excel_table(path)
        else:
            df = read_csv_table(path)
    except Exception:
        return (None, path.name, file_type)
    return (df, path.name, file_type)


def profile_input(
    input_key: str,
    path_or_parsed: Any,
    accepted_types: Any,
    tyler_type: Optional[str] = None,
) -> FileProfile:
    """Profile one uploaded input into a ``FileProfile``.

    Loads the CSV (``dtype=str``, ``keep_default_na=False``), normalizes column
    names, computes detected columns, parse-confidence for plausible
    date/amount columns, repeated-header rows, footer/total rows, duplicate row
    groups, and present/type checks. Row indices are 0-based positions in the
    parsed dataframe (preserved for traceability).
    """
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
    notes: list[str] = []
    if df.attrs.get("title_rows"):
        notes.append(f"Skipped {df.attrs['title_rows']} title line(s) above the column headers.")
    if tyler_type is not None:  # see CapabilitySpec.tyler_inputs
        from src.ingest.tyler import canonical_frame

        renamed = [f"{a} -> {b}" for a, b in zip(ndf.columns, canonical_frame(ndf.head(0), tyler_type).columns)
                   if a != b]
        ndf = canonical_frame(ndf, tyler_type).reset_index(drop=True)
        if renamed:
            notes.append(f"Read as a Tyler/Munis {tyler_type.replace('_', ' ')} export: "
                         + ", ".join(renamed) + ".")

    # Tyler/Munis-style exports carry separate Debit/Credit columns instead of
    # one signed amount. Derive the signed 'amount' (= debit - credit, same
    # semantics as src.ingest.tyler) so semantic mapping can resolve it; the
    # derivation is surfaced in the profile notes for the preflight report.
    ndf, amount_derived = derive_signed_amount(ndf)
    if amount_derived:
        notes.append(
            "Derived signed 'amount' column as debit - credit "
            "(Tyler/Munis-style debit/credit export); original debit/credit "
            "columns preserved."
        )

    detected = list(ndf.columns)

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
        # Only rows that take part in a duplicate need a key.
        involved = ndf.duplicated(keep=False).to_numpy().nonzero()[0]
        for pos, values in zip(involved, ndf.iloc[involved].itertuples(index=False, name=None)):
            pos = int(pos)
            key = tuple(str(v) for v in values)
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
    tyler_type = spec.tyler_inputs.get(input_key)
    if tyler_type is not None:  # the normalizer's own required columns, exactly
        from src.ingest.tyler import TYLER_DATASET_TYPES

        req += [c for c in TYLER_DATASET_TYPES[tyler_type].required_columns if c not in req]
        opt = [c for c in opt if c not in req]
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

            if input_key in spec.tyler_inputs:
                # Already aliased to canonical names (profile_input): the column
                # is there by exact name or the normalizer will reject the file.
                found = semantic in frame.columns
                best, conf, ranked = (semantic, 1.0, [semantic]) if found else (None, 0.0, [])
            else:
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
    labels: Optional[dict[str, str]] = None,
) -> PreflightReport:
    """Run preflight for ``spec`` against ``inputs`` (input_key -> path/ParsedTable).

    ``labels`` maps input keys to the names staff saw when uploading (e.g.
    ``bank`` -> "Bank statement (CSV)"), so messages read in their terms.

    ``detect_conditions`` is the workflow's optional domain detector; it
    receives ``(profiles, mappings, inputs, config)`` and returns domain
    ``possible_*`` / unsupported-pattern findings. The engine produces the
    generic findings and computes the final status (see module docstring).
    """
    # 1) Profile every declared input.
    profiles: dict[str, FileProfile] = {}
    for input_key in spec.required_inputs + spec.optional_inputs:
        profiles[input_key] = profile_input(
            input_key, inputs.get(input_key), spec.accepted_file_types,
            spec.tyler_inputs.get(input_key),
        )

    findings: list[PreflightFinding] = []

    def label(key: str) -> str:
        return f"'{(labels or {}).get(key, key)}'"

    # 2) Generic file-level findings.
    for input_key in spec.required_inputs:
        profile = profiles[input_key]
        accepted = _accepted_for(spec.accepted_file_types, input_key)
        if not profile.present:
            findings.append(
                PreflightFinding(
                    code=PreflightConditionCode.MISSING_REQUIRED_FILE,
                    severity=Severity.CRITICAL,
                    message=f"Required file {label(input_key)} is missing.",
                    affected_input=input_key,
                    suggested_action=(
                        f"Upload the {label(input_key)} file "
                        f"({'/'.join(accepted)})."
                    ),
                    blocks_run=True,
                )
            )
            continue
        if profile.file_type and _norm_file_type(profile.file_type) not in accepted:
            findings.append(
                PreflightFinding(
                    code=PreflightConditionCode.UNSUPPORTED_FILE_TYPE,
                    severity=Severity.CRITICAL,
                    message=(
                        f"{label(input_key)} is a .{profile.file_type} file; "
                        f"accepted: {', '.join(accepted)}."
                    ),
                    affected_input=input_key,
                    suggested_action=(
                        f"Save {label(input_key)} as one of: {', '.join(accepted)}."
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
        if profile.file_type and _norm_file_type(profile.file_type) not in accepted:
            findings.append(
                PreflightFinding(
                    code=PreflightConditionCode.UNSUPPORTED_FILE_TYPE,
                    severity=Severity.LOW,
                    message=(
                        f"Optional file {label(input_key)} is a .{profile.file_type} "
                        f"file; accepted: {', '.join(accepted)}. It will be skipped."
                    ),
                    affected_input=input_key,
                    suggested_action=(
                        f"Save {label(input_key)} as {', '.join(accepted)} to use it."
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
            # Parse quality can only lower an auto match's confidence; it must
            # never turn an uncertain name match into a confident one.
            if pc is not None and m.source == "auto":
                m.confidence = round(min(m.confidence, pc), 4)

    # One plain-language next step per file listing every missing column and
    # the columns the file actually has (shared by that file's findings, so
    # next_steps shows it once).
    missing_action: dict[str, str] = {}
    for input_key in spec.required_inputs + spec.optional_inputs:
        profile = profiles.get(input_key)
        if profile is None or not profile.present:
            continue
        req, _ = _semantic_fields(spec, input_key)
        missing = [sem for sem in req
                   if (m := mapping_index.get(input_key, {}).get(sem)) is not None
                   and m.mapped_column is None]
        if not missing:
            continue
        cols = ", ".join(profile.detected_columns[:12]) or "(none found)"
        if len(profile.detected_columns) > 12:
            cols += ", ..."
        wrong_file = (f"This may not be the right file for {label(input_key)}. "
                      if len(missing) >= 2 and len(missing) * 2 >= len(req) else "")
        missing_action[input_key] = (
            f"{wrong_file}{label(input_key)} has no column for "
            f"{', '.join(_human(sem) for sem in missing)}. Its columns are: {cols}. "
            "Rename the matching columns, or choose them in the column-mapping step."
        )

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
                                f"No column in {label(input_key)} could be used "
                                f"as '{_human(semantic)}'."
                            ),
                            affected_input=input_key,
                            affected_column=semantic,
                            suggested_action=missing_action[input_key],
                            blocks_run=True,
                        )
                    )
                    findings.append(
                        PreflightFinding(
                            code=PreflightConditionCode.NEEDS_HUMAN_CONFIGURATION,
                            severity=Severity.HIGH,
                            message=(
                                f"'{_human(semantic)}' in {label(input_key)} needs "
                                "a column chosen."
                            ),
                            affected_input=input_key,
                            affected_column=semantic,
                            suggested_action=missing_action[input_key],
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
                            f"'{_human(semantic)}' in {label(input_key)} could be "
                            f"any of: {', '.join(m.candidates[:3])}."
                        ),
                        affected_input=input_key,
                        affected_column=m.mapped_column,
                        suggested_action=(
                            f"Choose which column is '{_human(semantic)}' in "
                            f"{label(input_key)} in the column-mapping step."
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
                                f"Only {pc:.0%} of the dates in column "
                                f"'{m.mapped_column}' of {label(input_key)} could be "
                                f"read (need {spec.min_date_confidence:.0%})."
                            ),
                            affected_input=input_key,
                            affected_column=m.mapped_column,
                            suggested_action=(
                                f"Use one date format (e.g. 2026-03-31) in column "
                                f"'{m.mapped_column}' of {label(input_key)}."
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
                                f"Only {pc:.0%} of the amounts in column "
                                f"'{m.mapped_column}' of {label(input_key)} could be "
                                f"read (need {spec.min_amount_confidence:.0%})."
                            ),
                            affected_input=input_key,
                            affected_column=m.mapped_column,
                            suggested_action=(
                                f"Remove text and symbols other than $ , . - ( ) from "
                                f"column '{m.mapped_column}' of {label(input_key)}."
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


def _human(semantic: str) -> str:
    """'vendor_number' -> 'vendor number'."""
    return semantic.replace("_", " ")


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
    # The supported checks all run on PASS and PARTIAL (PARTIAL only adds
    # flagged conditions); none run on FAIL.
    return [] if status == PreflightStatus.FAIL else list(spec.supported_patterns)


def _next_steps(findings: list[PreflightFinding]) -> list[str]:
    """Deduplicated actions. When something blocks the run, only the blocking
    actions are listed: optional advice is noise until the file is usable."""
    steps: list[str] = []
    seen: set[str] = set()
    blocking = any(f.blocks_run for f in findings)
    for f in sorted(findings, key=lambda x: (not x.blocks_run,)):
        if blocking and not f.blocks_run:
            continue
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
