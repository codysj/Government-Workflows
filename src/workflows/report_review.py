"""Workflow 3 — Financial Report Consistency / Error-Flagging Review.

Spec: docs/Project_Outline_Master.md, "Workflow 3 — Financial Report
Consistency / Error-Flagging Review".

DETERMINISTIC code performs every check below and owns all calculations,
source-row tracking, validation, export formatting, and audit logging:

  * Totals that do not tie out (grand total vs sum of subtotals).
  * Subtotals that do not equal their section line-item sums.
  * Missing required sections.
  * Duplicate account lines.
  * Negative values where not expected.
  * Account codes not in the chart of accounts.
  * Large unexplained changes from a prior version.
  * Inconsistent fund/account naming.

The LLM may ONLY explain flagged issues, draft a checklist/memo, and suggest
human follow-up. It must never decide whether the report is correct, calculate,
or invent values, and it must cite the deterministic findings / source rows.

This module contains NO Streamlit and NO provider-specific code. It plugs into
the shared pipeline through:

  * ``WORKFLOW_TYPE`` — the registry key.
  * ``register(registry)`` — optional helper for a dict/callable registry.
  * ``run(...)`` — the workflow entry point used by the workflow runner.

When the shared LLM provider / validation / export / ledger modules are
available they are used; otherwise self-contained mock-LLM, validation, and
export fallbacks keep the workflow runnable with no API key and no internet
(mock mode is the DEFAULT path).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from src.core.schemas import (
    DeterministicFinding,
    FindingType,
    LLMResponse,
    Severity,
    SourceRowRef,
    ValidationResult,
    make_id,
)
from src.core.exports import write_json, write_markdown
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.ingest.csv_loader import load_csv
from src.normalize.cleaning import normalize_columns, parse_amount, to_snake_case

WORKFLOW_TYPE = "report_review"
PROMPT_TEMPLATE_VERSION = "report_review.v1"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class ReportReviewConfig:
    """Checklist / threshold config. All deterministic, no LLM input."""

    required_sections: list[str] = field(default_factory=list)
    negative_not_allowed_sections: list[str] = field(default_factory=list)
    prior_change_pct_threshold: float = 25.0
    prior_change_min_amount: float = 10000.0
    section_column: str = "section"
    account_code_column: str = "account_code"
    account_name_column: str = "account_name"
    line_type_column: str = "line_type"
    amount_column: str = "amount"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportReviewConfig":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_json(cls, path: str | Path) -> "ReportReviewConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Deterministic output container (shape expected by the shared LLM layer:
# exposes ``.findings`` and ``.summary``).
# --------------------------------------------------------------------------- #
@dataclass
class ReportReviewOutput:
    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _src_ref(table_name: str, file_id: str, row_index: int, row: dict[str, Any]) -> SourceRowRef:
    return SourceRowRef(
        file_id=file_id,
        table_name=table_name,
        row_index=int(row_index),
        column_names=list(row.keys()),
        source_values={k: ("" if v is None else str(v)) for k, v in row.items()},
    )


def _short_ref(ref: SourceRowRef) -> str:
    """Compact source-row id used by the LLM and validation, e.g. 'report:5'."""
    return f"{ref.table_name}:{ref.row_index}"


def _load_report_frame(path: str | Path, table_name: str) -> tuple[pd.DataFrame, str]:
    """Load a report CSV, normalize columns, return (df, file_id).

    ``row_index`` (the dataframe positional index) is preserved as the
    SourceRowRef anchor for every finding.
    """
    parsed = load_csv(path, table_name=table_name)
    df = normalize_columns(parsed.dataframe).reset_index(drop=True)
    return df, parsed.file_id


def _amt(value: Any) -> Optional[Decimal]:
    return parse_amount(value)


# --------------------------------------------------------------------------- #
# Deterministic checks
# --------------------------------------------------------------------------- #
def run_deterministic(
    report_path: str | Path,
    *,
    chart_of_accounts_path: str | Path | None = None,
    prior_version_path: str | Path | None = None,
    config: ReportReviewConfig | None = None,
) -> ReportReviewOutput:
    """Run every deterministic consistency check and return findings + summary."""
    config = config or ReportReviewConfig()
    df, file_id = _load_report_frame(report_path, "report")

    sec_col = to_snake_case(config.section_column)
    code_col = to_snake_case(config.account_code_column)
    name_col = to_snake_case(config.account_name_column)
    type_col = to_snake_case(config.line_type_column)
    amt_col = to_snake_case(config.amount_column)

    findings: list[DeterministicFinding] = []

    def row_dict(i: int) -> dict[str, Any]:
        return {c: df.at[i, c] for c in df.columns}

    def ref(i: int) -> SourceRowRef:
        return _src_ref("report", file_id, i, row_dict(i))

    # --- Subtotal vs line-item sums ------------------------------------- #
    subtotal_mismatches = 0
    for sec, group in df.groupby(sec_col, sort=False):
        line_idx = [i for i in group.index if str(group.at[i, type_col]).lower() == "line_item"]
        sub_idx = [i for i in group.index if str(group.at[i, type_col]).lower() == "subtotal"]
        line_sum = sum((_amt(df.at[i, amt_col]) or Decimal(0)) for i in line_idx)
        for si in sub_idx:
            stated = _amt(df.at[si, amt_col]) or Decimal(0)
            if stated != line_sum:
                subtotal_mismatches += 1
                src = [ref(i) for i in line_idx] + [ref(si)]
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.CONSISTENCY,
                        severity=Severity.HIGH,
                        description=(
                            f"Subtotal for section '{sec}' is {stated}, but the "
                            f"line items sum to {line_sum} "
                            f"(difference {stated - line_sum})."
                        ),
                        source_rows=src,
                        computed_values={
                            "section": str(sec),
                            "stated_subtotal": str(stated),
                            "computed_line_item_sum": str(line_sum),
                            "difference": str(stated - line_sum),
                        },
                        rule_used="subtotal_equals_line_item_sum",
                        requires_human_review=True,
                    )
                )

    # --- Grand total ties out to sum of subtotals ----------------------- #
    grand_idx = [i for i in df.index if str(df.at[i, type_col]).lower() == "grand_total"]
    sub_all_idx = [i for i in df.index if str(df.at[i, type_col]).lower() == "subtotal"]
    if grand_idx:
        sub_sum = sum((_amt(df.at[i, amt_col]) or Decimal(0)) for i in sub_all_idx)
        for gi in grand_idx:
            stated = _amt(df.at[gi, amt_col]) or Decimal(0)
            if stated != sub_sum:
                src = [ref(i) for i in sub_all_idx] + [ref(gi)]
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.CONSISTENCY,
                        severity=Severity.HIGH,
                        description=(
                            f"Grand total {stated} does not tie out to the sum of "
                            f"subtotals {sub_sum} (difference {stated - sub_sum})."
                        ),
                        source_rows=src,
                        computed_values={
                            "stated_grand_total": str(stated),
                            "computed_subtotal_sum": str(sub_sum),
                            "difference": str(stated - sub_sum),
                        },
                        rule_used="grand_total_ties_out",
                        requires_human_review=True,
                    )
                )

    # --- Missing required sections -------------------------------------- #
    present_sections = {str(s) for s in df[sec_col].unique()}
    missing_sections = [s for s in config.required_sections if s not in present_sections]
    for s in missing_sections:
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.CONSISTENCY,
                severity=Severity.HIGH,
                description=f"Required section '{s}' is missing from the report.",
                source_rows=[],
                computed_values={
                    "missing_section": s,
                    "present_sections": sorted(present_sections),
                },
                rule_used="required_section_present",
                requires_human_review=True,
            )
        )

    # --- Duplicate account lines (exact duplicate line-item rows) ------- #
    line_mask = df[type_col].astype(str).str.lower() == "line_item"
    line_df = df[line_mask]
    dup_subset = [c for c in (sec_col, code_col, name_col, amt_col) if c in df.columns]
    seen: dict[tuple, int] = {}
    for i in line_df.index:
        key = tuple(str(df.at[i, c]) for c in dup_subset)
        if key in seen:
            first = seen[key]
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.DUPLICATE,
                    severity=Severity.MEDIUM,
                    description=(
                        f"Duplicate account line: account "
                        f"'{df.at[i, code_col]}' in section "
                        f"'{df.at[i, sec_col]}' appears more than once with the "
                        f"same values."
                    ),
                    source_rows=[ref(first), ref(i)],
                    computed_values={
                        "account_code": str(df.at[i, code_col]),
                        "section": str(df.at[i, sec_col]),
                        "amount": str(df.at[i, amt_col]),
                    },
                    rule_used="no_duplicate_account_lines",
                    requires_human_review=True,
                )
            )
        else:
            seen[key] = i

    # --- Negative values where not expected ----------------------------- #
    neg_sections = set(config.negative_not_allowed_sections)
    if neg_sections:
        for i in line_df.index:
            sec = str(df.at[i, sec_col])
            if sec not in neg_sections:
                continue
            amount = _amt(df.at[i, amt_col])
            if amount is not None and amount < 0:
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.CONSISTENCY,
                        severity=Severity.MEDIUM,
                        description=(
                            f"Negative value {amount} in section '{sec}' where "
                            f"negatives are not expected."
                        ),
                        source_rows=[ref(i)],
                        computed_values={
                            "section": sec,
                            "account_code": str(df.at[i, code_col]),
                            "amount": str(amount),
                        },
                        rule_used="no_unexpected_negative_values",
                        requires_human_review=True,
                    )
                )

    # --- Account codes not in chart of accounts ------------------------- #
    if chart_of_accounts_path is not None:
        coa_df, _ = _load_report_frame(chart_of_accounts_path, "chart_of_accounts")
        coa_code_col = to_snake_case(config.account_code_column)
        valid_codes = {
            str(c).strip() for c in coa_df[coa_code_col].tolist() if str(c).strip()
        }
        for i in line_df.index:
            code = str(df.at[i, code_col]).strip()
            if not code:
                continue
            if code not in valid_codes:
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.CONSISTENCY,
                        severity=Severity.HIGH,
                        description=(
                            f"Account code '{code}' is not present in the chart "
                            f"of accounts."
                        ),
                        source_rows=[ref(i)],
                        computed_values={"account_code": code},
                        rule_used="account_code_in_chart_of_accounts",
                        requires_human_review=True,
                    )
                )

    # --- Inconsistent fund/account naming ------------------------------- #
    code_to_names: dict[str, dict[str, int]] = {}
    for i in line_df.index:
        code = str(df.at[i, code_col]).strip()
        name = str(df.at[i, name_col]).strip()
        if not code:
            continue
        code_to_names.setdefault(code, {})
        # keep the first row index seen for each (code,name)
        if name not in code_to_names[code]:
            code_to_names[code][name] = i
    for code, names in code_to_names.items():
        if len(names) > 1:
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.CONSISTENCY,
                    severity=Severity.MEDIUM,
                    description=(
                        f"Inconsistent naming: account code '{code}' is labeled "
                        f"with multiple names: {sorted(names)}."
                    ),
                    source_rows=[ref(i) for i in names.values()],
                    computed_values={
                        "account_code": code,
                        "names": sorted(names),
                    },
                    rule_used="consistent_account_naming",
                    requires_human_review=True,
                )
            )

    # --- Large unexplained changes from prior version ------------------- #
    if prior_version_path is not None:
        prior_df, _ = _load_report_frame(prior_version_path, "prior")
        prior_line = prior_df[
            prior_df[type_col].astype(str).str.lower() == "line_item"
        ]
        prior_by_code: dict[str, Decimal] = {}
        for i in prior_line.index:
            code = str(prior_df.at[i, code_col]).strip()
            if code:
                prior_by_code[code] = _amt(prior_df.at[i, amt_col]) or Decimal(0)
        thr_pct = Decimal(str(config.prior_change_pct_threshold))
        thr_min = Decimal(str(config.prior_change_min_amount))
        for i in line_df.index:
            code = str(df.at[i, code_col]).strip()
            if code not in prior_by_code:
                continue
            cur = _amt(df.at[i, amt_col]) or Decimal(0)
            prev = prior_by_code[code]
            change = cur - prev
            if abs(change) < thr_min:
                continue
            pct = (abs(change) / abs(prev) * Decimal(100)) if prev != 0 else Decimal(0)
            if pct >= thr_pct:
                findings.append(
                    DeterministicFinding(
                        finding_type=FindingType.CONSISTENCY,
                        severity=Severity.MEDIUM,
                        description=(
                            f"Large change for account '{code}': prior {prev} -> "
                            f"current {cur} (change {change}, {pct:.1f}%)."
                        ),
                        source_rows=[ref(i)],
                        computed_values={
                            "account_code": code,
                            "prior_amount": str(prev),
                            "current_amount": str(cur),
                            "change": str(change),
                            "pct_change": f"{pct:.1f}",
                        },
                        rule_used="large_change_from_prior_version",
                        requires_human_review=True,
                    )
                )

    summary = _build_summary(findings)
    return ReportReviewOutput(findings=findings, summary=summary)


def _build_summary(findings: list[DeterministicFinding]) -> dict[str, Any]:
    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule_used] = by_rule.get(f.rule_used, 0) + 1
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1
    return {
        "workflow_type": WORKFLOW_TYPE,
        "total_findings": len(findings),
        "findings_by_rule": by_rule,
        "findings_by_severity": by_severity,
        "requires_human_review": any(f.requires_human_review for f in findings),
    }


# --------------------------------------------------------------------------- #
# LLM prompt (advisory only — references deterministic findings + source rows)
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY explain, summarize, draft, classify, and flag.\n"
    "- You MUST NOT decide whether the report is correct, calculate, or invent "
    "account numbers, funds, vendors, amounts, or dates.\n"
    "- Only use numbers and source-row ids that already appear in the findings "
    "below.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- All output is a DRAFT for human review; do NOT produce final-approval "
    "language.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def _findings_block(det: ReportReviewOutput) -> str:
    rows = []
    for f in det.findings:
        rows.append(
            {
                "finding_id": f.finding_id,
                "finding_type": f.finding_type.value,
                "rule_used": f.rule_used,
                "severity": f.severity.value,
                "description": f.description,
                "computed_values": f.computed_values,
                "source_row_ids": [_short_ref(s) for s in f.source_rows],
            }
        )
    return json.dumps(rows, default=str, indent=2)


def build_report_review_prompt(det: ReportReviewOutput, context: Any = None) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Financial report consistency / error-flagging review.\n"
        + "TASK: Summarize the detected inconsistencies, draft a review "
        "checklist, explain why each flagged issue may matter, suggest human "
        "follow-up steps, and draft a plain-language review memo. Do NOT decide "
        "whether the report is correct.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM (default path; no API key, no internet). The shared provider is used
# when injected, otherwise this deterministic mock keeps the workflow runnable.
# --------------------------------------------------------------------------- #
def mock_llm_response(det: ReportReviewOutput) -> dict[str, Any]:
    """Build a structured, source-cited mock response from findings only.

    Cites ONLY source-row ids that exist in the deterministic findings, so the
    output always passes validation. Invents no numbers.
    """
    refs: list[str] = []
    categorized = []
    for f in det.findings:
        f_refs = [_short_ref(s) for s in f.source_rows]
        refs.extend(f_refs)
        categorized.append(
            {
                "category": f.rule_used,
                "description": f.description,
                "referenced_source_rows": f_refs,
            }
        )
    refs = sorted(set(refs))
    n = det.summary.get("total_findings", len(det.findings))
    return {
        "summary": (
            f"Deterministic checks flagged {n} potential issue(s) for human "
            f"review. See the categorized list; each item references the source "
            f"rows that triggered it."
        ),
        "categorized_exceptions": categorized,
        "referenced_source_rows": refs,
        "suggested_review_steps": [
            "Confirm each flagged subtotal against the underlying line items.",
            "Verify any account code not found in the chart of accounts.",
            "Resolve duplicate or inconsistently named account lines.",
            "Add any missing required section before finalizing.",
        ],
        "draft_memo": (
            "DRAFT — for human review only. The automated consistency review "
            "identified items that require finance staff confirmation prior to "
            "finalizing this report. This memo does not certify the report as "
            "correct."
        ),
    }


def _call_llm(det: ReportReviewOutput, provider: Any = None) -> tuple[dict[str, Any], str, str]:
    """Return (response_json, model_provider, model_name).

    Uses the injected shared provider's structured-response method if present;
    otherwise falls back to the local deterministic mock (default path).
    """
    if provider is not None:
        prompt = build_report_review_prompt(det)
        for meth in ("generate_structured_response", "mock_response"):
            fn = getattr(provider, meth, None)
            if callable(fn):
                try:
                    resp = fn(prompt, schema="WorkflowLLMOutput")
                except TypeError:
                    resp = fn(prompt)
                data = getattr(resp, "response_json", resp)
                if isinstance(data, str):
                    data = json.loads(data)
                p = getattr(provider, "model_provider", "mock")
                m = getattr(provider, "model_name", "mock")
                return data, str(p), str(m)
    return mock_llm_response(det), "mock", "mock-report-review"


# --------------------------------------------------------------------------- #
# Validation (deterministic; mirrors spec Phase 2 rules). The canonical
# implementation lives in src.core.validation; this is a thin wrapper.
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: dict[str, Any], det: ReportReviewOutput
) -> ValidationResult:
    """Thin wrapper: the canonical validator lives in ``src.core.validation``.

    Report review is the STRICT spec interpretation: invented references,
    omitted required references, and final-approval language are all hard
    errors; unsupported numeric claims are warnings. Those are the canonical
    validator's defaults, so this simply delegates.
    """
    return _core_validate_llm_output(response_json, det)


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
EXPORT_FILE_NAMES = (
    "report_review_summary.md",
    "flagged_issues.csv",
    "review_checklist.md",
    "validation_report.json",
    "audit_log.json",
)


def export_artifacts(
    out_dir: str | Path,
    det: ReportReviewOutput,
    response_json: dict[str, Any],
    validation: ValidationResult,
    audit_events: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Path]:
    """Write the five report-review artifacts. Returns name -> path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    # report_review_summary.md
    summary_md = ["# Report Review Summary", ""]
    summary_md.append(f"Total findings: {det.summary.get('total_findings', 0)}")
    summary_md.append("")
    summary_md.append("## LLM Summary (DRAFT — human review required)")
    summary_md.append(str(response_json.get("summary", "")))
    summary_md.append("")
    summary_md.append("## Flagged Issues")
    for f in det.findings:
        refs = ", ".join(_short_ref(s) for s in f.source_rows) or "(none)"
        summary_md.append(
            f"- **[{f.severity.value}] {f.rule_used}** — {f.description} "
            f"(source rows: {refs})"
        )
    # File-writing + hashing for md/json go through the shared core export
    # primitives. The CSV is written via pandas to_csv to preserve the header
    # row on an empty frame (the spec-named file must keep its column header).
    paths["report_review_summary.md"] = Path(
        write_markdown(
            out / "report_review_summary.md", "\n".join(summary_md) + "\n"
        ).path
    )

    # flagged_issues.csv
    rows = []
    for f in det.findings:
        rows.append(
            {
                "finding_id": f.finding_id,
                "rule_used": f.rule_used,
                "finding_type": f.finding_type.value,
                "severity": f.severity.value,
                "description": f.description,
                "source_rows": ";".join(_short_ref(s) for s in f.source_rows),
                "computed_values": json.dumps(f.computed_values, default=str),
                "requires_human_review": f.requires_human_review,
            }
        )
    issues_df = pd.DataFrame(
        rows,
        columns=[
            "finding_id",
            "rule_used",
            "finding_type",
            "severity",
            "description",
            "source_rows",
            "computed_values",
            "requires_human_review",
        ],
    )
    p = out / "flagged_issues.csv"
    issues_df.to_csv(p, index=False)
    paths["flagged_issues.csv"] = p

    # review_checklist.md
    checklist_md = ["# Review Checklist (DRAFT)", ""]
    steps = response_json.get("suggested_review_steps") or []
    for s in steps:
        checklist_md.append(f"- [ ] {s}")
    checklist_md.append("")
    checklist_md.append("## Issue-specific checks")
    for f in det.findings:
        checklist_md.append(f"- [ ] {f.rule_used}: {f.description}")
    paths["review_checklist.md"] = Path(
        write_markdown(
            out / "review_checklist.md", "\n".join(checklist_md) + "\n"
        ).path
    )

    # validation_report.json (written verbatim from the pydantic JSON dump)
    paths["validation_report.json"] = Path(
        write_json(
            out / "validation_report.json", validation.model_dump_json(indent=2)
        ).path
    )

    # audit_log.json
    paths["audit_log.json"] = Path(
        write_json(
            out / "audit_log.json",
            json.dumps(audit_events or [], default=str, indent=2),
        ).path
    )

    return paths


# --------------------------------------------------------------------------- #
# Workflow entry point
# --------------------------------------------------------------------------- #
def run(
    inputs: dict[str, Any],
    *,
    provider: Any = None,
    ledger: Any = None,
    audit: Any = None,
    run_id: Optional[str] = None,
    actor: str = "system",
    export_dir: str | Path | None = None,
    config: ReportReviewConfig | None = None,
) -> dict[str, Any]:
    """End-to-end report-review run.

    ``inputs`` keys:
        report_table          (required) path to the report CSV
        chart_of_accounts     (optional) path to chart-of-accounts CSV
        prior_version         (optional) path to prior-version CSV
        checklist_config      (optional) path to checklist JSON (overrides config)

    Optional injected dependencies (shared pipeline). When ``provider`` is None
    the local mock LLM is used (default path). When ``ledger``/``audit`` are
    provided their findings/responses/validation/events are persisted via the
    same method names used elsewhere in the pipeline.

    Returns a dict with run_id, deterministic output, llm response_json,
    validation result, and (when export_dir is set) artifact paths.
    """
    run_id = run_id or make_id()

    if config is None and inputs.get("checklist_config"):
        config = ReportReviewConfig.from_json(inputs["checklist_config"])
    config = config or ReportReviewConfig()

    # Deterministic analysis.
    det = run_deterministic(
        inputs["report_table"],
        chart_of_accounts_path=inputs.get("chart_of_accounts"),
        prior_version_path=inputs.get("prior_version"),
        config=config,
    )
    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )

    # LLM assist (advisory).
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

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
    }

    # Exports.
    if export_dir is not None:
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        paths = export_artifacts(
            export_dir, det, response_json, validation, audit_events
        )
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(run_id, actor, artifacts=sorted(paths))
        result["export_paths"] = {k: str(v) for k, v in paths.items()}

    return result


# --------------------------------------------------------------------------- #
# Registry hook (single import line for the Integration agent)
# --------------------------------------------------------------------------- #
def register(registry: Any) -> None:
    """Register this workflow with a dict-like or callable registry.

    Supports either ``registry[WORKFLOW_TYPE] = run`` (mapping) or
    ``registry.register(WORKFLOW_TYPE, run)`` (object) styles so the Integration
    agent can wire it without this module knowing the concrete runner type.
    """
    if hasattr(registry, "register") and callable(registry.register):
        registry.register(WORKFLOW_TYPE, run)
    else:
        registry[WORKFLOW_TYPE] = run


# Convenience metadata used by a registry that introspects modules.
WORKFLOW = {
    "workflow_type": WORKFLOW_TYPE,
    "run": run,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    "export_files": list(EXPORT_FILE_NAMES),
}
